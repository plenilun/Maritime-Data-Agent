from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app import agent_memory, db
from app.agent import AgentRecorder, _usage_sum, run_data_agent
from app.agent_eval import run_agent_evals
from app.agent_planner import MaritimeAgentPlanner
from app.agent_tools import AgentTool, AgentToolRegistry, DEFAULT_TOOL_REGISTRY
from app.llm import ChatResult, ModelConfig


class AgentRecorderTests(unittest.TestCase):
    def test_plan_for_successful_route_contains_tool_workflow(self) -> None:
        recorder = AgentRecorder("今天进来吴淞 VTS 区域的有多少船？")

        plan = recorder.plan({"answer_status": "success"})

        self.assertTrue(any("识别意图" in item for item in plan))
        self.assertTrue(any("生成只读 SQL" in item for item in plan))
        self.assertTrue(any("生成中文回答" in item for item in plan))

    def test_metadata_records_steps_and_warnings(self) -> None:
        recorder = AgentRecorder("查询船舶数量")
        step = recorder.start("检索业务术语", "term_retrieval.retrieve_terms", {"dataset_id": "ds1"})

        step.finish({"matched_count": 2}, ["向量语义检索不可用，已回退关键词检索"])
        metadata = recorder.metadata()

        self.assertEqual(metadata["name"], "MaritimeDataAgent")
        self.assertEqual(metadata["steps"][0]["status"], "success")
        self.assertEqual(metadata["steps"][0]["output"]["matched_count"], 2)
        self.assertEqual(len(metadata["steps"][0]["warnings"]), 1)


class UsageAggregationTests(unittest.TestCase):
    def test_usage_sum_ignores_missing_values(self) -> None:
        calls = [
            ChatResult(content="{}", elapsed_ms=10, prompt_tokens=12, total_tokens=20),
            ChatResult(content="{}", elapsed_ms=20, prompt_tokens=None, total_tokens=None),
            ChatResult(content="{}", elapsed_ms=30, prompt_tokens=8, total_tokens=15),
        ]

        self.assertEqual(_usage_sum(calls, "prompt_tokens"), 20)
        self.assertEqual(_usage_sum(calls, "total_tokens"), 35)

    def test_usage_sum_returns_none_when_provider_omits_usage(self) -> None:
        calls = [ChatResult(content="{}", elapsed_ms=10), ChatResult(content="{}", elapsed_ms=20)]

        self.assertIsNone(_usage_sum(calls, "total_tokens"))


class AgentToolRegistryTests(unittest.TestCase):
    def test_default_registry_exposes_agent_tools(self) -> None:
        names = {item["name"] for item in DEFAULT_TOOL_REGISTRY.describe()}

        self.assertIn("context.load", names)
        self.assertIn("memory.recall", names)
        self.assertIn("memory.save", names)
        self.assertIn("sql.generate", names)
        self.assertIn("sql.repair", names)
        self.assertIn("answer.summarize", names)

    def test_registry_rejects_duplicate_tool_names(self) -> None:
        registry = AgentToolRegistry()
        tool = AgentTool(
            name="demo.echo",
            title="Echo",
            description="Return the input value.",
            input_keys=("value",),
            output_keys=("value",),
            handler=lambda value: {"value": value},
        )

        registry.register(tool)

        with self.assertRaises(ValueError):
            registry.register(tool)

    def test_agent_uses_registry_tool_sequence(self) -> None:
        dataset = {
            "id": "ds1",
            "name": "船舶表",
            "table_name": "ships",
            "columns": [{"name": "mmsi", "type": "TEXT"}],
        }
        calls: list[str] = []
        registry = AgentToolRegistry()

        def register(name: str, output: dict[str, object]) -> None:
            registry.register(AgentTool(
                name=name,
                title=name,
                description=f"fake {name}",
                input_keys=(),
                output_keys=tuple(output.keys()),
                handler=lambda **_: calls.append(name) or dict(output),
            ))

        register("context.load", {"datasets": [dataset], "terms": []})
        register("intent.route", {"route_info": {
            "answer_status": "success",
            "intent_type": "generic_sql_query",
            "intent_label": "通用查询",
            "dataset_id": "ds1",
            "dataset_name": "船舶表",
            "confidence": 1.0,
        }})
        register("memory.recall", {
            "memory_context": {"enabled": True, "recalled_count": 0, "memories": []},
        })
        register("table.plan", {
            "multi_table_context": {
                "enabled": False,
                "allowed_tables": ["ships"],
                "tables": [{"dataset_id": "ds1", "table_name": "ships"}],
                "join_plan": [],
                "selection_reasons": [],
            },
            "selected_ids": ["ds1"],
            "selected_datasets": [dataset],
            "dataset": dataset,
            "context_terms": [],
            "table_names": ["ships"],
        })
        register("terms.retrieve", {
            "matched_terms": [],
            "retrieval_traces": [],
            "term_retrieval": {"datasets": [], "method": ""},
        })
        register("codes.resolve", {
            "code_context": {"requests": [], "mappings": [], "warnings": []},
            "route_updates": {"code_lookup_requests": []},
        })
        register("model.configure", {
            "config": ModelConfig(api_key="sk-test", base_url="https://example.test/v1", model="demo"),
        })
        register("sql.generate", {
            "sql": 'SELECT COUNT(*) AS count FROM "ships"',
            "reasoning_summary": "统计记录数。",
            "chat_call": ChatResult(content="{}", elapsed_ms=10, total_tokens=12),
        })
        register("sql.validate", {"sql": 'SELECT COUNT(*) AS count FROM "ships"'})
        register("query.execute", {
            "columns": ["count"],
            "rows": [{"count": 2}],
            "truncated": False,
            "execution_time_ms": 1.0,
            "query_elapsed_ms": 1,
            "mapping_warnings": [],
        })
        register("answer.build", {
            "trace_record": {"query_id": "trace_1"},
            "answer_payload": {"answer_status": "success"},
        })
        register("answer.summarize", {
            "answer": "直接结论：\n共有 2 条记录。",
            "chat_call": ChatResult(content="直接结论：\n共有 2 条记录。", elapsed_ms=5, total_tokens=8),
        })
        register("memory.save", {"memory_id": "mem_1", "stored": True})

        result = asyncio.run(run_data_agent(
            "有多少条记录？",
            None,
            {"api_key": "sk-test", "base_url": "https://example.test/v1", "model": "demo"},
            registry=registry,
        ))

        self.assertEqual(result["answer_status"], "success")
        self.assertEqual(result["rows"], [{"count": 2}])
        self.assertEqual(result["metrics"]["total_tokens"], 20)
        self.assertEqual(result["agent"]["version"], "agent_step_5_evals")
        self.assertEqual(result["agent"]["state"]["planner"]["active_plan"]["mode"], "query")
        self.assertEqual(result["agent"]["state"]["memory"]["saved_memory_id"], "mem_1")
        self.assertIn("sql.generate", calls)
        self.assertEqual(calls[-1], "memory.save")


class MaritimeAgentPlannerTests(unittest.TestCase):
    def test_planner_chooses_clarification_path(self) -> None:
        planner = MaritimeAgentPlanner()

        plan = planner.plan_after_route({"answer_status": "need_clarification"})

        self.assertEqual(plan.mode, "clarify")
        self.assertEqual(plan.tool_names(), ["memory.recall", "answer.clarify", "memory.save"])

    def test_planner_chooses_query_path(self) -> None:
        planner = MaritimeAgentPlanner()

        plan = planner.plan_after_route({"answer_status": "success"})

        self.assertEqual(plan.mode, "query")
        self.assertIn("table.plan", plan.tool_names())
        self.assertIn("sql.generate", plan.tool_names())
        self.assertIn("answer.summarize", plan.tool_names())

    def test_planner_allows_one_sql_repair(self) -> None:
        planner = MaritimeAgentPlanner(max_sql_attempts=2)

        repair_plan = planner.plan_for_sql_failure("sql.validate", 1, "SQL 引用了未知字段")
        stop_plan = planner.plan_for_sql_failure("sql.validate", 2, "SQL 引用了未知字段")

        self.assertEqual(repair_plan.mode, "repair")
        self.assertIn("sql.repair", repair_plan.tool_names())
        self.assertEqual(stop_plan.mode, "fail")


class AgentMemoryTests(unittest.TestCase):
    def test_memory_save_and_recall_uses_temp_db(self) -> None:
        old_data_dir = db.DATA_DIR
        old_db_path = db.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            db.DATA_DIR = Path(folder)
            db.DB_PATH = Path(folder) / "smart_query.db"
            try:
                db.init_db()
                memory_id = agent_memory.save_memory(
                    question="今天进入吴淞 VTS 的船舶有多少？",
                    answer_status="success",
                    route_info={
                        "intent_type": "gate_crossing_stat",
                        "dataset_id": "ds1",
                        "table_names": ["cross_record_line"],
                    },
                    sql='SELECT COUNT(DISTINCT "mmsi") FROM "cross_record_line"',
                    reasoning_summary="按 MMSI 去重统计进入船舶。",
                    answer="直接结论：共有 12 艘船。",
                    rows=[{"count": 12}],
                    trace_record={"query_id": "trace_1"},
                    metrics={"sql_repaired": True},
                )

                matches = agent_memory.recall_memories(
                    "吴淞 VTS 今天进来的船有几艘？",
                    {"intent_type": "gate_crossing_stat", "dataset_id": "ds1", "table_names": ["cross_record_line"]},
                )

                self.assertEqual(matches[0]["id"], memory_id)
                self.assertTrue(matches[0]["sql_repaired"])
                self.assertEqual(matches[0]["row_count"], 1)
            finally:
                db.DATA_DIR = old_data_dir
                db.DB_PATH = old_db_path


class AgentEvalTests(unittest.TestCase):
    def test_offline_agent_evals_pass(self) -> None:
        report = run_agent_evals()

        self.assertTrue(report["passed"])
        self.assertEqual(report["version"], "agent_step_5_evals")
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertGreaterEqual(report["score"], 0.99)
        self.assertIn("memory", {case["category"] for case in report["cases"]})


if __name__ == "__main__":
    unittest.main()
