from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import agent_memory
from .agent_planner import MaritimeAgentPlanner
from .agent_tools import AgentToolRegistry, DEFAULT_TOOL_REGISTRY
from .query import validate_sql


@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    category: str
    title: str
    expected: str
    check: Callable[[], tuple[bool, dict[str, Any]]]
    severity: str = "must"


def _tool_index(sequence: list[str], tool_name: str) -> int:
    try:
        return sequence.index(tool_name)
    except ValueError:
        return -1


def _planner_query_memory_check(planner: MaritimeAgentPlanner) -> tuple[bool, dict[str, Any]]:
    plan = planner.plan_after_route({"answer_status": "success"})
    sequence = plan.tool_names()
    recall_index = _tool_index(sequence, "memory.recall")
    sql_index = _tool_index(sequence, "sql.generate")
    summarize_index = _tool_index(sequence, "answer.summarize")
    save_index = _tool_index(sequence, "memory.save")
    passed = (
        plan.mode == "query"
        and -1 not in (recall_index, sql_index, summarize_index, save_index)
        and recall_index < sql_index
        and summarize_index < save_index
    )
    return passed, {
        "mode": plan.mode,
        "tool_sequence": sequence,
        "memory_recall_before_sql": recall_index < sql_index if recall_index >= 0 and sql_index >= 0 else False,
        "memory_save_after_answer": summarize_index < save_index if summarize_index >= 0 and save_index >= 0 else False,
    }


def _planner_clarify_check(planner: MaritimeAgentPlanner) -> tuple[bool, dict[str, Any]]:
    plan = planner.plan_after_route({"answer_status": "need_clarification"})
    sequence = plan.tool_names()
    forbidden = {"sql.generate", "sql.validate", "query.execute"} & set(sequence)
    passed = plan.mode == "clarify" and "answer.clarify" in sequence and "memory.save" in sequence and not forbidden
    return passed, {
        "mode": plan.mode,
        "tool_sequence": sequence,
        "forbidden_sql_tools": sorted(forbidden),
    }


def _planner_repair_policy_check(planner: MaritimeAgentPlanner) -> tuple[bool, dict[str, Any]]:
    repair_plan = planner.plan_for_sql_failure("sql.validate", 1, "SQL 引用了未知字段")
    stop_plan = planner.plan_for_sql_failure("sql.validate", planner.max_sql_attempts, "SQL 引用了未知字段")
    unrelated_plan = planner.plan_for_sql_failure("answer.build", 1, "答案构建失败")
    passed = (
        repair_plan.mode == "repair"
        and "sql.repair" in repair_plan.tool_names()
        and stop_plan.mode == "fail"
        and unrelated_plan.mode == "fail"
    )
    return passed, {
        "repair_mode": repair_plan.mode,
        "repair_sequence": repair_plan.tool_names(),
        "stop_mode_at_max_attempts": stop_plan.mode,
        "unrelated_failure_mode": unrelated_plan.mode,
    }


def _tool_registry_contract_check(registry: AgentToolRegistry) -> tuple[bool, dict[str, Any]]:
    manifest = registry.describe()
    required = {
        "context.load",
        "intent.route",
        "memory.recall",
        "memory.save",
        "table.plan",
        "terms.retrieve",
        "codes.resolve",
        "model.configure",
        "sql.generate",
        "sql.validate",
        "sql.repair",
        "query.execute",
        "answer.build",
        "answer.summarize",
    }
    by_name = {item["name"]: item for item in manifest}
    missing = sorted(required - set(by_name))
    incomplete = sorted(
        name
        for name, item in by_name.items()
        if not item.get("title") or not item.get("description") or "input_keys" not in item or "output_keys" not in item
    )
    return not missing and not incomplete, {
        "tool_count": len(manifest),
        "required_count": len(required),
        "missing_tools": missing,
        "incomplete_tools": incomplete,
    }


def _readonly_sql_check() -> tuple[bool, dict[str, Any]]:
    sql = validate_sql('SELECT COUNT(*) AS count FROM "ships"', allowed_tables=["ships"])
    return sql == 'SELECT COUNT(*) AS count FROM "ships"', {"validated_sql": sql}


def _sql_boundary_check() -> tuple[bool, dict[str, Any]]:
    unsafe_sqls = [
        'DELETE FROM "ships"',
        'SELECT * FROM "ships"; DROP TABLE "ships"',
        'SELECT * FROM "other_table"',
        'SELECT * FROM "ships" CROSS JOIN "ports"',
    ]
    blocked: list[dict[str, str]] = []
    for sql in unsafe_sqls:
        try:
            validate_sql(sql, allowed_tables=["ships", "ports"])
        except ValueError as exc:
            blocked.append({"sql": sql, "error": str(exc)})
    return len(blocked) == len(unsafe_sqls), {
        "unsafe_case_count": len(unsafe_sqls),
        "blocked_count": len(blocked),
        "blocked": blocked,
    }


def _memory_scoring_check() -> tuple[bool, dict[str, Any]]:
    route_info = {
        "intent_type": "gate_crossing_stat",
        "dataset_id": "ds1",
        "table_names": ["cross_record_line"],
    }
    candidate = {
        "normalized_question": agent_memory.normalize_question("今天进入吴淞 VTS 的船舶有多少？"),
        "intent_type": "gate_crossing_stat",
        "dataset_ids_json": '["ds1"]',
        "table_names_json": '["cross_record_line"]',
    }
    score = agent_memory.score_memory_candidate("吴淞 VTS 今天进来的船有几艘？", route_info, candidate)
    return score >= 0.12, {
        "score": score,
        "threshold": 0.12,
        "normalized_question": agent_memory.normalize_question("吴淞 VTS 今天进来的船有几艘？"),
    }


def run_agent_evals(
    *,
    planner: MaritimeAgentPlanner | None = None,
    registry: AgentToolRegistry | None = None,
) -> dict[str, Any]:
    active_planner = planner or MaritimeAgentPlanner()
    active_registry = registry or DEFAULT_TOOL_REGISTRY
    cases = (
        AgentEvalCase(
            "planner.query_memory_order",
            "planner",
            "查询链路先召回记忆再生成 SQL，回答后保存记忆",
            "query 计划包含 memory.recall、sql.generate、answer.summarize、memory.save，且顺序正确。",
            lambda: _planner_query_memory_check(active_planner),
        ),
        AgentEvalCase(
            "planner.clarify_no_sql",
            "planner",
            "澄清链路不进入 SQL 执行",
            "当路由要求补充条件时，Planner 只生成澄清和记忆保存链路。",
            lambda: _planner_clarify_check(active_planner),
        ),
        AgentEvalCase(
            "planner.repair_policy",
            "planner",
            "SQL 失败只在受控次数内触发修复",
            "校验/执行失败可修复一次，达到最大尝试次数或非 SQL 失败时停止。",
            lambda: _planner_repair_policy_check(active_planner),
        ),
        AgentEvalCase(
            "tools.registry_contract",
            "tools",
            "工具注册表暴露完整工具契约",
            "核心 Agent 工具都可在 manifest 中发现，并带有描述、输入字段和输出字段。",
            lambda: _tool_registry_contract_check(active_registry),
        ),
        AgentEvalCase(
            "sql.readonly_allowed",
            "safety",
            "合法只读 SQL 可以通过安全校验",
            "允许访问白名单表的单条 SELECT 查询。",
            _readonly_sql_check,
        ),
        AgentEvalCase(
            "sql.boundaries_blocked",
            "safety",
            "危险 SQL 和越权表访问会被拦截",
            "拦截写操作、多语句、白名单外表和 CROSS JOIN。",
            _sql_boundary_check,
        ),
        AgentEvalCase(
            "memory.similar_question_score",
            "memory",
            "相似历史问题能得到可召回分数",
            "相似问题在相同意图、数据集和表上下文下达到召回阈值。",
            _memory_scoring_check,
        ),
    )

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            passed, observed = case.check()
            error = ""
        except Exception as exc:
            passed = False
            observed = {}
            error = str(exc)
        results.append({
            "id": case.id,
            "category": case.category,
            "title": case.title,
            "severity": case.severity,
            "passed": passed,
            "expected": case.expected,
            "observed": observed,
            "error": error,
        })

    passed_count = sum(1 for item in results if item["passed"])
    total = len(results)
    return {
        "suite": "maritime_agent_offline_evals",
        "version": "agent_step_5_evals",
        "passed": passed_count == total,
        "score": round(passed_count / total, 4) if total else 0,
        "summary": {
            "total": total,
            "passed": passed_count,
            "failed": total - passed_count,
        },
        "planner": active_planner.describe(),
        "cases": results,
    }
