from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .agent_planner import DEFAULT_PLANNER, AgentPlan, MaritimeAgentPlanner
from .agent_tools import DEFAULT_TOOL_REGISTRY, AgentToolRegistry
from .llm import ChatResult, ModelConfig


class AgentDatasetNotFound(Exception):
    """Raised when the routed or selected dataset no longer exists."""


@dataclass
class AgentStep:
    name: str
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    elapsed_ms: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    _started_at: float = field(default_factory=time.perf_counter, repr=False)

    def finish(self, output: dict[str, Any] | None = None, warnings: list[str] | None = None) -> None:
        self.status = "success"
        self.output = output or {}
        self.warnings = [item for item in (warnings or []) if item]
        self.elapsed_ms = round((time.perf_counter() - self._started_at) * 1000)

    def fail(self, error: Exception | str) -> None:
        self.status = "failed"
        self.error = str(error)
        self.elapsed_ms = round((time.perf_counter() - self._started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "warnings": self.warnings,
        }
        if self.error:
            payload["error"] = self.error
        return payload


class AgentRecorder:
    def __init__(self, question: str) -> None:
        self.question = question
        self.started_at = time.perf_counter()
        self.steps: list[AgentStep] = []
        self.state: dict[str, Any] = {
            "question": question,
            "phase": "initialized",
            "sql_attempts": 0,
            "sql_repaired": False,
        }

    def start(self, name: str, tool: str, input: dict[str, Any] | None = None) -> AgentStep:
        self.state["phase"] = name
        step = AgentStep(name=name, tool=tool, input=input or {})
        self.steps.append(step)
        return step

    async def use_tool(
        self,
        registry: AgentToolRegistry,
        tool_name: str,
        *,
        args: dict[str, Any] | None = None,
        trace_input: dict[str, Any] | None = None,
        summarize: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        warnings: Callable[[dict[str, Any]], list[str]] | None = None,
        step_name: str | None = None,
    ) -> dict[str, Any]:
        tool = registry.get(tool_name)
        step = self.start(step_name or tool.title, tool.name, trace_input)
        try:
            result = await registry.call(tool_name, **(args or {}))
        except Exception as exc:
            step.fail(exc)
            raise
        summary = summarize(result) if summarize else {
            key: result[key]
            for key in tool.output_keys
            if key in result and key not in {"rows", "datasets", "terms", "selected_datasets"}
        }
        step.finish(summary, warnings(result) if warnings else None)
        return result

    def plan(self, route_info: dict[str, Any]) -> list[str]:
        initial_plan = DEFAULT_PLANNER.initial_plan()
        active_plan = DEFAULT_PLANNER.plan_after_route(route_info)
        return [*initial_plan.user_steps(), *active_plan.user_steps()]

    def metadata(self) -> dict[str, Any]:
        return {
            "name": "MaritimeDataAgent",
            "version": "agent_step_5_evals",
            "description": "面向航运业务数据的可追溯问数 Agent，由 Planner 动态选择澄清、查询或 SQL 修复链路，通过统一工具注册表执行，保存可召回的查询记忆，并提供离线评测集验证关键行为。",
            "question": self.question,
            "state": self.state,
            "elapsed_ms": round((time.perf_counter() - self.started_at) * 1000),
            "steps": [step.to_dict() for step in self.steps],
        }


def _usage_sum(calls: list[ChatResult], attr: str) -> int | None:
    values = [getattr(call, attr) for call in calls]
    present = [int(value) for value in values if value is not None]
    return sum(present) if present else None


def _planned_input(plan: AgentPlan, tool_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    step = plan.get_step(tool_name)
    trace_input = {"planner_reason": step.reason if step else "Planner 未记录该工具的选择原因"}
    trace_input.update(payload or {})
    return trace_input


async def run_data_agent(
    question: str,
    requested_dataset_id: str | None,
    model_payload: dict[str, Any],
    *,
    dataset_ids: list[str] | None = None,
    secondary_dataset_id: str | None = None,
    registry: AgentToolRegistry | None = None,
    planner: MaritimeAgentPlanner | None = None,
) -> dict[str, Any]:
    registry = registry or DEFAULT_TOOL_REGISTRY
    planner = planner or DEFAULT_PLANNER
    initial_plan = planner.initial_plan()
    recorder = AgentRecorder(question)
    started_at = time.perf_counter()
    recorder.state["registered_tools"] = [tool["name"] for tool in registry.describe()]
    recorder.state["planner"] = {
        **planner.describe(),
        "initial_plan": initial_plan.to_dict(),
        "active_plan": None,
        "failure_plans": [],
    }
    recorder.state["plan"] = initial_plan.user_steps()

    context = await recorder.use_tool(
        registry,
        "context.load",
        trace_input=_planned_input(initial_plan, "context.load"),
        summarize=lambda result: {
            "dataset_count": len(result["datasets"]),
            "term_count": len(result["terms"]),
        },
    )
    datasets = context["datasets"]
    all_terms = context["terms"]
    recorder.state["available_datasets"] = [item["name"] for item in datasets]

    def route_summary(result: dict[str, Any]) -> dict[str, Any]:
        route = result["route_info"]
        return {
            "answer_status": route.get("answer_status", "success"),
            "intent_type": route.get("intent_type"),
            "intent_label": route.get("intent_label"),
            "dataset_id": route.get("dataset_id"),
            "dataset_name": route.get("dataset_name"),
            "confidence": route.get("confidence"),
        }

    route_result = await recorder.use_tool(
        registry,
        "intent.route",
        args={
            "question": question,
            "datasets": datasets,
            "terms": all_terms,
            "requested_dataset_id": requested_dataset_id,
        },
        trace_input={
            **_planned_input(initial_plan, "intent.route"),
            "requested_dataset_id": requested_dataset_id,
            "manual_dataset_ids": dataset_ids or [],
            "secondary_dataset_id": secondary_dataset_id,
        },
        summarize=route_summary,
        warnings=lambda result: result["route_info"].get("reasons", []),
    )
    route_info = route_result["route_info"]
    active_plan = planner.plan_after_route(route_info)
    recorder.state["planner"]["active_plan"] = active_plan.to_dict()
    recorder.state.update({
        "route": route_summary(route_result),
        "plan": [*initial_plan.user_steps(), *active_plan.user_steps()],
    })
    memory_context = {"enabled": False, "recalled_count": 0, "memories": []}
    if active_plan.get_step("memory.recall"):
        try:
            memory_result = await recorder.use_tool(
                registry,
                "memory.recall",
                args={"question": question, "route_info": route_info, "limit": 3},
                trace_input=_planned_input(active_plan, "memory.recall", {"limit": 3}),
                summarize=lambda result: {
                    "recalled_count": result["memory_context"]["recalled_count"],
                    "top_score": (
                        result["memory_context"]["memories"][0]["score"]
                        if result["memory_context"]["memories"]
                        else None
                    ),
                },
            )
            memory_context = memory_result["memory_context"]
        except Exception as exc:
            memory_context = {
                "enabled": False,
                "recalled_count": 0,
                "memories": [],
                "warning": f"历史记忆召回不可用：{exc}",
            }
    route_info["agent_memory"] = memory_context
    recorder.state["memory"] = {
        "recalled_count": memory_context.get("recalled_count", 0),
        "top_score": memory_context["memories"][0]["score"] if memory_context.get("memories") else None,
        "saved_memory_id": None,
    }

    if route_info.get("answer_status") == "need_clarification":
        clarification = await recorder.use_tool(
            registry,
            "answer.clarify",
            args={"question": question, "route_info": route_info},
            trace_input=_planned_input(active_plan, "answer.clarify", {"answer_status": "need_clarification"}),
            summarize=lambda result: {"trace_id": result["answer_payload"]["trace_summary"]["trace_id"]},
        )
        answer_payload = clarification["answer_payload"]
        clarification_metrics = {
            "total_elapsed_ms": round((time.perf_counter() - started_at) * 1000),
            "sql_generation_elapsed_ms": None,
            "query_elapsed_ms": None,
            "answer_generation_elapsed_ms": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "sql_generation_tokens": None,
            "answer_generation_tokens": None,
            "sql_generation_cached": False,
            "sql_repaired": False,
        }
        if active_plan.get_step("memory.save"):
            try:
                saved = await recorder.use_tool(
                    registry,
                    "memory.save",
                    args={
                        "question": question,
                        "answer_status": "need_clarification",
                        "route_info": route_info,
                        "answer": clarification["answer"],
                        "trace_record": answer_payload["trace_record"],
                        "metrics": clarification_metrics,
                    },
                    trace_input=_planned_input(active_plan, "memory.save", {"answer_status": "need_clarification"}),
                    summarize=lambda result: {"stored": result["stored"], "memory_id": result["memory_id"]},
                )
                recorder.state["memory"]["saved_memory_id"] = saved["memory_id"]
            except Exception as exc:
                recorder.state["memory"]["save_error"] = str(exc)
        recorder.state["phase"] = "need_clarification"
        return {
            "answer": clarification["answer"],
            "answer_status": "need_clarification",
            "sql": "",
            "columns": [],
            "rows": [],
            "truncated": False,
            "terms": [],
            "route": route_info,
            "answer_payload": answer_payload,
            "trace_record": answer_payload["trace_record"],
            "reasoning_summary": "未执行 SQL：问题需要先补充数据表或业务对象。",
            "agent": recorder.metadata(),
            "metrics": clarification_metrics,
        }

    try:
        table_plan = await recorder.use_tool(
            registry,
            "table.plan",
            args={
                "question": question,
                "datasets": datasets,
                "terms": all_terms,
                "route_info": route_info,
            },
            trace_input=_planned_input(active_plan, "table.plan", {"route_dataset_id": route_info.get("dataset_id")}),
            summarize=lambda result: {
                "enabled": result["multi_table_context"].get("enabled", False),
                "primary_table": result["dataset"]["table_name"],
                "allowed_tables": result["table_names"],
                "join_plan_count": len(result["multi_table_context"].get("join_plan", [])),
                "selection_reasons": result["multi_table_context"].get("selection_reasons", []),
            },
        )
    except LookupError as exc:
        raise AgentDatasetNotFound("数据集不存在")

    multi_table_context = table_plan["multi_table_context"]
    selected_datasets = table_plan["selected_datasets"]
    dataset = table_plan["dataset"]
    context_terms = table_plan["context_terms"]
    route_info["table_names"] = table_plan["table_names"]
    route_info["multi_table_context"] = multi_table_context
    recorder.state["tables_used"] = route_info["table_names"]

    term_result = await recorder.use_tool(
        registry,
        "terms.retrieve",
        args={
            "question": question,
            "selected_datasets": selected_datasets,
            "context_terms": context_terms,
        },
        trace_input=_planned_input(active_plan, "terms.retrieve", {"dataset_ids": [item["id"] for item in selected_datasets]}),
        summarize=lambda result: {
            "method": result["term_retrieval"]["method"],
            "matched_count": len(result["matched_terms"]),
            "retrieval_traces": result["retrieval_traces"],
        },
        warnings=lambda result: [trace["warning"] for trace in result["retrieval_traces"] if trace.get("warning")],
    )
    matched_terms = term_result["matched_terms"]
    retrieval_traces = term_result["retrieval_traces"]
    route_info["term_retrieval"] = term_result["term_retrieval"]
    recorder.state["terms_selected"] = len(matched_terms)

    code_result = await recorder.use_tool(
        registry,
        "codes.resolve",
        args={
            "question": question,
            "selected_datasets": selected_datasets,
            "route_info": route_info,
        },
        trace_input=_planned_input(active_plan, "codes.resolve", {"dataset_ids": [item["id"] for item in selected_datasets]}),
        summarize=lambda result: {
            "version": result["route_updates"].get("code_dictionary_version"),
            "request_count": len(result["code_context"].get("requests", [])),
            "mapping_count": len(result["code_context"].get("mappings", [])),
        },
        warnings=lambda result: result["code_context"].get("warnings", []),
    )
    code_context = code_result["code_context"]
    route_info.update(code_result["route_updates"])
    recorder.state["code_dictionary_version"] = route_info.get("code_dictionary_version")
    recorder.state["code_lookup_requests"] = len(code_context.get("requests", []))

    config_result = await recorder.use_tool(
        registry,
        "model.configure",
        args={"model_payload": model_payload},
        trace_input=_planned_input(
            active_plan,
            "model.configure",
            {
                "base_url": str(model_payload.get("base_url", "")).strip(),
                "model": str(model_payload.get("model", "")).strip(),
            },
        ),
        summarize=lambda result: {
            "base_url": result["config"].base_url,
            "model": result["config"].model,
        },
    )
    config: ModelConfig = config_result["config"]
    sql_calls: list[ChatResult] = []

    sql_result = await recorder.use_tool(
        registry,
        "sql.generate",
        args={
            "config": config,
            "question": question,
            "dataset": dataset,
            "matched_terms": matched_terms,
            "route_info": route_info,
            "code_context": code_context,
            "selected_datasets": selected_datasets,
            "multi_table_context": multi_table_context,
        },
        trace_input=_planned_input(
            active_plan,
            "sql.generate",
            {
                "table": dataset["table_name"],
                "term_count": len(matched_terms),
                "code_request_count": len(code_context.get("requests", [])),
                "table_count": len(selected_datasets),
            },
        ),
        summarize=lambda result: {
            "sql": result["sql"],
            "cached": result["chat_call"].cached,
            "reasoning_summary": result["reasoning_summary"],
            "elapsed_ms": result["chat_call"].elapsed_ms,
        },
    )
    sql = sql_result["sql"]
    reasoning_summary = sql_result["reasoning_summary"]
    sql_call = sql_result["chat_call"]
    sql_calls.append(sql_call)
    recorder.state["sql_attempts"] = 1

    max_attempts = planner.max_sql_attempts
    mapping_warnings: list[str] = []
    while True:
        try:
            validation = await recorder.use_tool(
                registry,
                "sql.validate",
                args={
                    "sql": sql,
                    "question": question,
                    "dataset": dataset,
                    "multi_table_context": multi_table_context,
                    "code_context": code_context,
                },
                trace_input=_planned_input(active_plan, "sql.validate", {"attempt": recorder.state["sql_attempts"], "sql": sql}),
                summarize=lambda result: {"status": "passed", "sql": result["sql"]},
            )
            sql = validation["sql"]
        except ValueError as exc:
            failure_plan = planner.plan_for_sql_failure("sql.validate", int(recorder.state["sql_attempts"]), str(exc))
            recorder.state["planner"]["failure_plans"].append(failure_plan.to_dict())
            if not failure_plan.get_step("sql.repair") or recorder.state["sql_attempts"] >= max_attempts:
                raise
            repair_result = await recorder.use_tool(
                registry,
                "sql.repair",
                args={
                    "config": config,
                    "question": question,
                    "failed_sql": sql,
                    "error_message": str(exc),
                    "dataset": dataset,
                    "matched_terms": matched_terms,
                    "route_info": route_info,
                    "code_context": code_context,
                    "selected_datasets": selected_datasets,
                    "multi_table_context": multi_table_context,
                },
                trace_input=_planned_input(
                    failure_plan,
                    "sql.repair",
                    {
                        "attempt": recorder.state["sql_attempts"] + 1,
                        "failed_sql": sql,
                        "error": str(exc),
                    },
                ),
                step_name="根据校验错误修复 SQL",
                summarize=lambda result: {
                    "sql": result["sql"],
                    "reasoning_summary": result["reasoning_summary"],
                    "elapsed_ms": result["chat_call"].elapsed_ms,
                },
            )
            sql = repair_result["sql"]
            reasoning_summary = repair_result["reasoning_summary"]
            repair_call = repair_result["chat_call"]
            sql_calls.append(repair_call)
            recorder.state["sql_attempts"] = int(recorder.state["sql_attempts"]) + 1
            recorder.state["sql_repaired"] = True
            continue

        try:
            query_result = await recorder.use_tool(
                registry,
                "query.execute",
                args={"sql": sql, "code_context": code_context},
                trace_input=_planned_input(active_plan, "query.execute", {"attempt": recorder.state["sql_attempts"], "sql": sql}),
                summarize=lambda result: {
                    "column_count": len(result["columns"]),
                    "row_count": len(result["rows"]),
                    "truncated": result["truncated"],
                    "elapsed_ms": result["query_elapsed_ms"],
                },
                warnings=lambda result: result["mapping_warnings"],
            )
            columns = query_result["columns"]
            rows = query_result["rows"]
            truncated = query_result["truncated"]
            execution_time_ms = query_result["execution_time_ms"]
            query_elapsed_ms = query_result["query_elapsed_ms"]
            mapping_warnings = query_result["mapping_warnings"]
            break
        except ValueError as exc:
            failure_plan = planner.plan_for_sql_failure("query.execute", int(recorder.state["sql_attempts"]), str(exc))
            recorder.state["planner"]["failure_plans"].append(failure_plan.to_dict())
            if not failure_plan.get_step("sql.repair") or recorder.state["sql_attempts"] >= max_attempts:
                raise
            repair_result = await recorder.use_tool(
                registry,
                "sql.repair",
                args={
                    "config": config,
                    "question": question,
                    "failed_sql": sql,
                    "error_message": str(exc),
                    "dataset": dataset,
                    "matched_terms": matched_terms,
                    "route_info": route_info,
                    "code_context": code_context,
                    "selected_datasets": selected_datasets,
                    "multi_table_context": multi_table_context,
                },
                trace_input=_planned_input(
                    failure_plan,
                    "sql.repair",
                    {
                        "attempt": recorder.state["sql_attempts"] + 1,
                        "failed_sql": sql,
                        "error": str(exc),
                    },
                ),
                step_name="根据执行错误修复 SQL",
                summarize=lambda result: {
                    "sql": result["sql"],
                    "reasoning_summary": result["reasoning_summary"],
                    "elapsed_ms": result["chat_call"].elapsed_ms,
                },
            )
            sql = repair_result["sql"]
            reasoning_summary = repair_result["reasoning_summary"]
            repair_call = repair_result["chat_call"]
            sql_calls.append(repair_call)
            recorder.state["sql_attempts"] = int(recorder.state["sql_attempts"]) + 1
            recorder.state["sql_repaired"] = True

    answer_artifacts = await recorder.use_tool(
        registry,
        "answer.build",
        args={
            "question": question,
            "route_info": route_info,
            "matched_terms": matched_terms,
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "execution_time_ms": execution_time_ms,
            "selected_datasets": selected_datasets,
            "retrieval_traces": retrieval_traces,
            "code_context": code_context,
            "mapping_warnings": mapping_warnings,
        },
        trace_input=_planned_input(
            active_plan,
            "answer.build",
            {
                "row_count": len(rows),
                "table_count": len(selected_datasets),
                "term_count": len(matched_terms),
            },
        ),
        summarize=lambda result: {
            "trace_id": result["trace_record"]["query_id"],
            "answer_status": result["answer_payload"]["answer_status"],
        },
    )
    trace_record = answer_artifacts["trace_record"]
    answer_payload = answer_artifacts["answer_payload"]

    summary_result = await recorder.use_tool(
        registry,
        "answer.summarize",
        args={
            "config": config,
            "question": question,
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "route_info": route_info,
            "trace_record": trace_record,
            "answer_payload": answer_payload,
        },
        trace_input=_planned_input(active_plan, "answer.summarize", {"row_count": len(rows), "truncated": truncated}),
        summarize=lambda result: {
            "elapsed_ms": result["chat_call"].elapsed_ms,
            "answer_length": len(result["answer"]),
        },
    )
    answer = summary_result["answer"]
    answer_call = summary_result["chat_call"]
    metrics = {
        "total_elapsed_ms": round((time.perf_counter() - started_at) * 1000),
        "sql_generation_elapsed_ms": sum(call.elapsed_ms for call in sql_calls),
        "query_elapsed_ms": query_elapsed_ms,
        "answer_generation_elapsed_ms": answer_call.elapsed_ms,
        "prompt_tokens": _usage_sum([*sql_calls, answer_call], "prompt_tokens"),
        "completion_tokens": _usage_sum([*sql_calls, answer_call], "completion_tokens"),
        "total_tokens": _usage_sum([*sql_calls, answer_call], "total_tokens"),
        "sql_generation_tokens": _usage_sum(sql_calls, "total_tokens"),
        "answer_generation_tokens": answer_call.total_tokens,
        "sql_generation_cached": all(call.cached for call in sql_calls),
        "sql_repaired": bool(recorder.state["sql_repaired"]),
    }
    if active_plan.get_step("memory.save"):
        try:
            saved = await recorder.use_tool(
                registry,
                "memory.save",
                args={
                    "question": question,
                    "answer_status": "success",
                    "route_info": route_info,
                    "sql": sql,
                    "reasoning_summary": reasoning_summary,
                    "answer": answer,
                    "rows": rows,
                    "truncated": truncated,
                    "trace_record": trace_record,
                    "metrics": metrics,
                },
                trace_input=_planned_input(
                    active_plan,
                    "memory.save",
                    {
                        "answer_status": "success",
                        "row_count": len(rows),
                        "sql_repaired": metrics["sql_repaired"],
                    },
                ),
                summarize=lambda result: {"stored": result["stored"], "memory_id": result["memory_id"]},
            )
            recorder.state["memory"]["saved_memory_id"] = saved["memory_id"]
        except Exception as exc:
            recorder.state["memory"]["save_error"] = str(exc)
    recorder.state["phase"] = "completed"

    return {
        "answer": answer,
        "answer_status": "success",
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "route": route_info,
        "answer_payload": answer_payload,
        "trace_record": trace_record,
        "terms": [
            {
                "term": item["term"],
                "definition": item["definition"],
                "dataset_id": item.get("dataset_id"),
                "match_sources": item.get("match_sources", []),
                "semantic_score": item.get("semantic_score"),
            }
            for item in matched_terms
        ],
        "reasoning_summary": reasoning_summary,
        "agent": recorder.metadata(),
        "metrics": metrics,
    }
