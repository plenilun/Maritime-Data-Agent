from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import agent_memory, code_dictionary, db
from .answer import build_answer_payload, build_need_clarification_payload, build_trace_record
from .intent import route_question
from .llm import ModelConfig, generate_sql, repair_sql, summarize
from .multitable import build_multitable_context, select_terms_for_context
from .query import execute_readonly, get_data_update_time, validate_question_semantics, validate_sql
from .term_retrieval import retrieve_terms

ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class AgentTool:
    name: str
    title: str
    description: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    handler: ToolHandler = field(repr=False, compare=False)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_keys": list(self.input_keys),
            "output_keys": list(self.output_keys),
        }


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"未知 Agent 工具：{name}") from exc

    def describe(self) -> list[dict[str, Any]]:
        return [tool.describe() for tool in self._tools.values()]

    async def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        tool = self.get(name)
        started_at = time.perf_counter()
        result = tool.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError(f"Agent 工具 {name} 必须返回 dict")
        result.setdefault("_tool_elapsed_ms", round((time.perf_counter() - started_at) * 1000))
        return result


def _format_clarification_answer(answer_payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"直接结论：\n{answer_payload['direct_answer']}",
            "\n统计范围：\n- 时间范围：未确定\n- 空间范围：未确定\n- 统计对象：未确定",
            f"\n结果明细：\n{answer_payload['detail']['description']}",
            f"\n统计口径：\n{answer_payload['methodology']['reason']}",
            f"\n可追溯信息：\n- 查询路线：AskUser\n- 追溯编号：{answer_payload['trace_summary']['trace_id']}",
        ]
    )


def load_context() -> dict[str, Any]:
    datasets = db.list_datasets()
    terms = db.list_terms()
    return {"datasets": datasets, "terms": terms}


def route_intent(
    question: str,
    datasets: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    requested_dataset_id: str | None,
) -> dict[str, Any]:
    route_info = route_question(question, datasets, terms, requested_dataset_id)
    route_info["question"] = question
    return {"route_info": route_info}


def build_clarification(question: str, route_info: dict[str, Any]) -> dict[str, Any]:
    answer_payload = build_need_clarification_payload(question, route_info)
    return {
        "answer": _format_clarification_answer(answer_payload),
        "answer_payload": answer_payload,
        "trace_record": answer_payload["trace_record"],
    }


def recall_memory(question: str, route_info: dict[str, Any], limit: int = 3) -> dict[str, Any]:
    memories = agent_memory.recall_memories(question, route_info, limit=limit)
    return {
        "memory_context": {
            "enabled": True,
            "recalled_count": len(memories),
            "memories": memories,
        }
    }


def save_memory(
    question: str,
    answer_status: str,
    route_info: dict[str, Any],
    sql: str = "",
    reasoning_summary: str = "",
    answer: str = "",
    rows: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    trace_record: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    memory_id = agent_memory.save_memory(
        question=question,
        answer_status=answer_status,
        route_info=route_info,
        sql=sql,
        reasoning_summary=reasoning_summary,
        answer=answer,
        rows=rows,
        truncated=truncated,
        trace_record=trace_record,
        metrics=metrics,
        error_message=error_message,
    )
    return {"memory_id": memory_id, "stored": True}


def configure_model(model_payload: dict[str, Any]) -> dict[str, Any]:
    return {"config": ModelConfig.from_payload(model_payload)}


def plan_tables(
    question: str,
    datasets: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any],
) -> dict[str, Any]:
    multi_table_context = build_multitable_context(question, datasets, terms, route_info)
    selected_ids = [item["dataset_id"] for item in multi_table_context.get("tables", [])]
    selected_datasets = [db.get_dataset(item) for item in selected_ids] or [db.get_dataset(route_info["dataset_id"])]
    if not selected_datasets or any(not dataset for dataset in selected_datasets):
        raise LookupError("数据集不存在")
    dataset = selected_datasets[0]
    context_terms = select_terms_for_context(terms, set(selected_ids), question)
    table_names = multi_table_context.get("allowed_tables", [dataset["table_name"]])
    return {
        "multi_table_context": multi_table_context,
        "selected_ids": selected_ids,
        "selected_datasets": selected_datasets,
        "dataset": dataset,
        "context_terms": context_terms,
        "table_names": table_names,
    }


async def retrieve_business_terms(
    question: str,
    selected_datasets: list[dict[str, Any]],
    context_terms: list[dict[str, Any]],
) -> dict[str, Any]:
    matched_terms: list[dict[str, Any]] = []
    retrieval_traces: list[dict[str, Any]] = []
    seen_term_ids: set[str] = set()
    for selected in selected_datasets:
        terms_for_dataset, trace = await asyncio.to_thread(retrieve_terms, question, selected["id"])
        retrieval_traces.append({"dataset_id": selected["id"], "dataset_name": selected["name"], **trace})
        for item in terms_for_dataset:
            if item["id"] in seen_term_ids:
                continue
            seen_term_ids.add(item["id"])
            matched_terms.append(
                {
                    **item,
                    "applicable_dataset_name": selected["name"] if item.get("dataset_id") else "全局",
                }
            )
    for item in context_terms:
        if item["id"] in seen_term_ids:
            continue
        matched_terms.append(
            {
                **item,
                "applicable_dataset_name": "全局" if not item.get("dataset_id") else next(
                    (
                        selected["name"]
                        for selected in selected_datasets
                        if selected["id"] == item.get("dataset_id")
                    ),
                    "关联数据表",
                ),
            }
        )
        seen_term_ids.add(item["id"])
    term_retrieval = {
        "datasets": retrieval_traces,
        "method": "+".join(dict.fromkeys(item["method"] for item in retrieval_traces)),
    }
    return {
        "matched_terms": matched_terms,
        "retrieval_traces": retrieval_traces,
        "term_retrieval": term_retrieval,
    }


def resolve_codes(
    question: str,
    selected_datasets: list[dict[str, Any]],
    route_info: dict[str, Any],
) -> dict[str, Any]:
    code_context = code_dictionary.resolve_code_contexts(question, selected_datasets, route_info)
    route_updates = {"code_lookup_requests": code_context.get("requests", [])}
    if code_context.get("version"):
        route_updates["code_dictionary_version"] = code_context["version"]["version_number"]
    return {"code_context": code_context, "route_updates": route_updates}


async def generate_sql_tool(
    config: ModelConfig,
    question: str,
    dataset: dict[str, Any],
    matched_terms: list[dict[str, Any]],
    route_info: dict[str, Any],
    code_context: dict[str, Any],
    selected_datasets: list[dict[str, Any]],
    multi_table_context: dict[str, Any],
) -> dict[str, Any]:
    sql, reasoning_summary, chat_call = await generate_sql(
        config,
        question,
        dataset["table_name"],
        dataset["columns"],
        matched_terms,
        route_info,
        code_context,
        selected_datasets,
        multi_table_context,
    )
    return {"sql": sql, "reasoning_summary": reasoning_summary, "chat_call": chat_call}


def validate_sql_tool(
    sql: str,
    question: str,
    dataset: dict[str, Any],
    multi_table_context: dict[str, Any],
    code_context: dict[str, Any],
) -> dict[str, Any]:
    validated_sql = validate_sql(
        sql,
        allowed_tables=multi_table_context.get("allowed_tables", [dataset["table_name"]]),
    )
    validate_question_semantics(validated_sql, question, multi_table_context)
    code_dictionary.validate_required_filters(validated_sql, code_context)
    return {"sql": validated_sql}


def execute_sql_tool(sql: str, code_context: dict[str, Any]) -> dict[str, Any]:
    query_started_at = time.perf_counter()
    columns, rows, truncated = execute_readonly(sql)
    rows, mapping_warnings = code_dictionary.translate_result(columns, rows, code_context)
    execution_time_ms = (time.perf_counter() - query_started_at) * 1000
    return {
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "execution_time_ms": execution_time_ms,
        "query_elapsed_ms": round(execution_time_ms),
        "mapping_warnings": mapping_warnings,
    }


async def repair_sql_tool(
    config: ModelConfig,
    question: str,
    failed_sql: str,
    error_message: str,
    dataset: dict[str, Any],
    matched_terms: list[dict[str, Any]],
    route_info: dict[str, Any],
    code_context: dict[str, Any],
    selected_datasets: list[dict[str, Any]],
    multi_table_context: dict[str, Any],
) -> dict[str, Any]:
    sql, reasoning_summary, chat_call = await repair_sql(
        config=config,
        question=question,
        failed_sql=failed_sql,
        error_message=error_message,
        table_name=dataset["table_name"],
        columns=dataset["columns"],
        terms=matched_terms,
        route_info=route_info,
        code_context=code_context,
        selected_datasets=selected_datasets,
        multi_table_context=multi_table_context,
    )
    return {"sql": sql, "reasoning_summary": reasoning_summary, "chat_call": chat_call}


def build_answer_artifacts(
    question: str,
    route_info: dict[str, Any],
    matched_terms: list[dict[str, Any]],
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    execution_time_ms: float,
    selected_datasets: list[dict[str, Any]],
    retrieval_traces: list[dict[str, Any]],
    code_context: dict[str, Any],
    mapping_warnings: list[str],
) -> dict[str, Any]:
    update_times = [
        get_data_update_time(item["table_name"], [column["name"] for column in item["columns"]])
        for item in selected_datasets
    ]
    data_update_time = max((value for value in update_times if value), default=None)
    trace_record = build_trace_record(
        question=question,
        route_info=route_info,
        matched_terms=matched_terms,
        sql=sql,
        columns=columns,
        rows=rows,
        truncated=truncated,
        execution_time_ms=execution_time_ms,
        data_update_time=data_update_time,
        warnings=[
            *[trace["warning"] for trace in retrieval_traces if trace.get("warning")],
            *code_context.get("warnings", []),
            *mapping_warnings,
        ],
    )
    answer_payload = build_answer_payload(
        route_info=route_info,
        trace_record=trace_record,
        columns=columns,
        rows=rows,
        truncated=truncated,
    )
    return {"trace_record": trace_record, "answer_payload": answer_payload}


async def summarize_answer(
    config: ModelConfig,
    question: str,
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    route_info: dict[str, Any],
    trace_record: dict[str, Any],
    answer_payload: dict[str, Any],
) -> dict[str, Any]:
    answer_call = await summarize(
        config,
        question,
        sql,
        columns,
        rows,
        truncated,
        route_info,
        trace_record,
        answer_payload,
    )
    return {"answer": answer_call.content, "chat_call": answer_call}


def build_default_registry() -> AgentToolRegistry:
    registry = AgentToolRegistry()
    registry.register(AgentTool(
        name="context.load",
        title="读取数据目录与术语库",
        description="读取当前已上传数据集和业务术语，为后续路由和检索提供上下文。",
        input_keys=(),
        output_keys=("datasets", "terms"),
        handler=load_context,
    ))
    registry.register(AgentTool(
        name="intent.route",
        title="识别问题意图与主数据表",
        description="根据问题、数据集、术语和手动选择结果识别意图与主数据表。",
        input_keys=("question", "datasets", "terms", "requested_dataset_id"),
        output_keys=("route_info",),
        handler=route_intent,
    ))
    registry.register(AgentTool(
        name="answer.clarify",
        title="生成补充条件提示",
        description="当问题无法确定数据表或业务对象时，生成可追溯的补充条件提示。",
        input_keys=("question", "route_info"),
        output_keys=("answer", "answer_payload", "trace_record"),
        handler=build_clarification,
    ))
    registry.register(AgentTool(
        name="memory.recall",
        title="召回历史问数记忆",
        description="按问题、意图、表名和数据集召回相似历史查询，辅助后续 SQL 生成与可追溯说明。",
        input_keys=("question", "route_info", "limit"),
        output_keys=("memory_context",),
        handler=recall_memory,
    ))
    registry.register(AgentTool(
        name="memory.save",
        title="保存本次问数记忆",
        description="保存问题、路由、SQL、结果摘要、修复状态和追溯编号，供后续问数召回。",
        input_keys=("question", "answer_status", "route_info", "sql", "reasoning_summary", "answer", "rows", "truncated", "trace_record", "metrics", "error_message"),
        output_keys=("memory_id", "stored"),
        handler=save_memory,
    ))
    registry.register(AgentTool(
        name="model.configure",
        title="读取模型配置",
        description="校验浏览器提交的模型 Base URL、模型名称和临时 API Key。",
        input_keys=("model_payload",),
        output_keys=("config",),
        handler=configure_model,
    ))
    registry.register(AgentTool(
        name="table.plan",
        title="规划单表或多表数据访问",
        description="基于主表、问题线索和受控 JOIN 规则选择可访问表，并生成多表上下文。",
        input_keys=("question", "datasets", "terms", "route_info"),
        output_keys=("multi_table_context", "selected_datasets", "dataset", "context_terms", "table_names"),
        handler=plan_tables,
    ))
    registry.register(AgentTool(
        name="terms.retrieve",
        title="混合检索业务术语",
        description="合并关键词匹配与本地向量语义检索结果，为 SQL 生成补充业务口径。",
        input_keys=("question", "selected_datasets", "context_terms"),
        output_keys=("matched_terms", "retrieval_traces", "term_retrieval"),
        handler=retrieve_business_terms,
    ))
    registry.register(AgentTool(
        name="codes.resolve",
        title="解析字段编码映射",
        description="根据问题和已绑定字段解析业务名称到数据库真实编码的映射。",
        input_keys=("question", "selected_datasets", "route_info"),
        output_keys=("code_context", "route_updates"),
        handler=resolve_codes,
    ))
    registry.register(AgentTool(
        name="sql.generate",
        title="生成受控 SQLite SQL",
        description="用模型在受控 schema、术语、编码和 JOIN 计划下生成只读 SQLite SQL。",
        input_keys=("config", "question", "dataset", "matched_terms", "route_info", "code_context", "selected_datasets", "multi_table_context"),
        output_keys=("sql", "reasoning_summary", "chat_call"),
        handler=generate_sql_tool,
    ))
    registry.register(AgentTool(
        name="sql.validate",
        title="校验 SQL 安全、语义和编码边界",
        description="执行只读、多表白名单、语义去重和编码筛选校验。",
        input_keys=("sql", "question", "dataset", "multi_table_context", "code_context"),
        output_keys=("sql",),
        handler=validate_sql_tool,
    ))
    registry.register(AgentTool(
        name="query.execute",
        title="只读执行查询并翻译编码结果",
        description="在 SQLite 只读模式执行 SQL，并把结果中的编码值翻译为业务名称。",
        input_keys=("sql", "code_context"),
        output_keys=("columns", "rows", "truncated", "execution_time_ms", "query_elapsed_ms", "mapping_warnings"),
        handler=execute_sql_tool,
    ))
    registry.register(AgentTool(
        name="sql.repair",
        title="根据错误反馈修复 SQL",
        description="把校验或执行错误反馈给模型，对失败 SQL 做最小修复。",
        input_keys=("config", "question", "failed_sql", "error_message", "dataset", "matched_terms", "route_info", "code_context", "selected_datasets", "multi_table_context"),
        output_keys=("sql", "reasoning_summary", "chat_call"),
        handler=repair_sql_tool,
    ))
    registry.register(AgentTool(
        name="answer.build",
        title="生成追溯记录和结构化答案事实",
        description="根据查询结果、路由、术语、编码和执行信息生成答案事实与追溯记录。",
        input_keys=("question", "route_info", "matched_terms", "sql", "columns", "rows", "truncated", "execution_time_ms", "selected_datasets", "retrieval_traces", "code_context", "mapping_warnings"),
        output_keys=("trace_record", "answer_payload"),
        handler=build_answer_artifacts,
    ))
    registry.register(AgentTool(
        name="answer.summarize",
        title="生成可追溯中文回答",
        description="基于结构化答案事实生成面向用户的中文回答。",
        input_keys=("config", "question", "sql", "columns", "rows", "truncated", "route_info", "trace_record", "answer_payload"),
        output_keys=("answer", "chat_call"),
        handler=summarize_answer,
    ))
    return registry


DEFAULT_TOOL_REGISTRY = build_default_registry()
