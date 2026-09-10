from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


TEMPLATE_SPECS: dict[str, dict[str, Any]] = {
    "gate_crossing_stat": {
        "template_spec_id": "gate_crossing_stat_v1",
        "template_name": "船舶数与事件数并存类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "distinct_vessel_count",
        "secondary_metrics": ["event_count"],
        "counting_rule": "主答案按 MMSI/target_id 去重，事件数作为补充统计。",
        "forbidden_behaviors": ["将事件数表述为船舶数", "省略时间范围", "基于当前位置替代穿越事件"],
    },
    "traffic_section_flow": {
        "template_spec_id": "traffic_section_flow_v1",
        "template_name": "截面流量类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "event_count",
        "secondary_metrics": ["distinct_vessel_count", "direction_breakdown"],
        "counting_rule": "默认统计截面穿越事件；用户问船舶数时按 MMSI/target_id 去重。",
        "forbidden_behaviors": ["混用上行和下行", "省略方向规则", "省略时间口径"],
    },
    "vessel_list_by_condition": {
        "template_spec_id": "vessel_list_by_condition_v1",
        "template_name": "船舶列表类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "count",
        "secondary_metrics": ["vessels"],
        "counting_rule": "按查询条件返回船舶明细，不合并同名船。",
        "forbidden_behaviors": ["补充不存在的船名", "自行推断 MMSI", "省略筛选条件"],
    },
    "violation_event_stat": {
        "template_spec_id": "violation_event_stat_v1",
        "template_name": "违规事件统计类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "event_count",
        "secondary_metrics": ["distinct_vessel_count", "category_breakdown"],
        "counting_rule": "默认统计违规事件记录；用户问船舶数时按 MMSI/target_id 去重。",
        "forbidden_behaviors": ["将违规事件数表述为船舶数", "省略违规时间字段"],
    },
    "risk_event_stat": {
        "template_spec_id": "risk_event_stat_v1",
        "template_name": "风险事件统计类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "event_count",
        "secondary_metrics": ["distinct_vessel_count", "risk_level_breakdown"],
        "counting_rule": "默认统计风险事件记录；当前有效风险需排除关闭事件或筛选有效风险等级。",
        "forbidden_behaviors": ["将关闭风险当成当前有效风险", "省略风险时间字段"],
    },
    "vessel_status_count": {
        "template_spec_id": "vessel_status_count_v1",
        "template_name": "数量统计类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "vessel_count",
        "secondary_metrics": ["category_breakdown"],
        "counting_rule": "当前状态优先筛选 event_type_code='OPEN'，按 MMSI/target_id 去重。",
        "forbidden_behaviors": ["把 CLOSE 记录纳入当前状态", "混用航行、锚泊、靠泊定义"],
    },
    "ship_type_stat": {
        "template_spec_id": "ship_type_stat_v1",
        "template_name": "分类统计类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "category_breakdown",
        "secondary_metrics": ["vessel_count"],
        "counting_rule": "按 ship_type 或当前表的船型字段分类统计。",
        "forbidden_behaviors": ["省略船型字段口径", "编造船型名称映射"],
    },
    "anchorage_anchor_stat": {
        "template_spec_id": "anchorage_anchor_stat_v1",
        "template_name": "空间位置/锚地统计类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "vessel_count",
        "secondary_metrics": ["anchorage_breakdown"],
        "counting_rule": "筛选锚泊状态后按 region_uuid 分组统计。",
        "forbidden_behaviors": ["把非锚泊船舶计入锚地锚泊数量", "省略 region_uuid 分组口径"],
    },
    "generic_sql_query": {
        "template_spec_id": "generic_sql_query_v1",
        "template_name": "通用 SQL 查询类",
        "required_sections": ["direct_answer", "scope", "detail", "methodology", "trace_summary"],
        "primary_metric": "query_result",
        "secondary_metrics": [],
        "counting_rule": "按用户问题、SQL 查询结果和命中术语解释。",
        "forbidden_behaviors": ["补充查询结果中不存在的信息"],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"q_{stamp}_{uuid.uuid4().hex[:8]}"


def template_spec(intent_type: str) -> dict[str, Any]:
    return TEMPLATE_SPECS.get(intent_type, TEMPLATE_SPECS["generic_sql_query"])


def _sql_time_range(sql: str) -> str:
    predicates = re.findall(
        r'((?:[A-Za-z_]\w*\.)?["`\[]?[A-Za-z_\u4e00-\u9fff]*(?:time|date|时间|日期)[A-Za-z_\u4e00-\u9fff]*["`\]]?\s*(?:BETWEEN\s+[^\s,)]+\s+AND\s+[^\s,)]+|(?:>=|<=|>|<|=)\s*[^\s,)]+))',
        sql,
        re.I,
    )
    return "；".join(dict.fromkeys(item.strip() for item in predicates)) or "由查询条件限定，SQL 未包含可提取的显式时间条件"


def _result_summary(columns: list[str], rows: list[dict[str, Any]], truncated: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "returned_row_count": len(rows),
        "truncated": truncated,
    }
    if len(rows) == 1:
        summary["single_row"] = rows[0]
    elif rows:
        summary["preview_rows"] = rows[:10]
    numeric_values: dict[str, Any] = {}
    for row in rows[:1]:
        for col in columns:
            value = row.get(col)
            if isinstance(value, (int, float)):
                numeric_values[col] = value
    if numeric_values:
        summary["numeric_values"] = numeric_values
    return summary


def build_trace_record(
    *,
    question: str,
    route_info: dict[str, Any],
    matched_terms: list[dict[str, Any]],
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    execution_time_ms: float,
    data_update_time: str | None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "query_id": new_trace_id(),
        "user_question": question,
        "normalized_question": question.strip(),
        "answer_status": route_info.get("answer_status", "success"),
        "intent_type": route_info.get("intent_type", "generic_sql_query"),
        "intent_label": route_info.get("intent_label", "通用 SQL 查询"),
        "route": route_info.get("route", "SQL"),
        "time_range": {"description": _sql_time_range(sql)},
        "spatial_scope": {
            "description": route_info.get("spatial_scope") or "由数据表与问题共同确定",
        },
        "subject": {
            "type": route_info.get("subject", "数据记录"),
            "key": "target_id/MMSI" if "船" in route_info.get("subject", "") else "record",
        },
        "metric": route_info.get("metric", "查询结果"),
        "filters": {"description": "详见生成 SQL 与命中术语"},
        "ontology_terms": [
            {
                "term": item["term"],
                "definition": item["definition"],
                "dataset_id": item.get("dataset_id"),
                "match_sources": item.get("match_sources", []),
                "semantic_score": item.get("semantic_score"),
            }
            for item in matched_terms
        ],
        "term_retrieval": route_info.get("term_retrieval", {}),
        "business_rules": route_info.get("methodology", []),
        "tables_used": route_info.get("table_names") or ([route_info.get("table_name")] if route_info.get("table_name") else []),
        "relationship_used": route_info.get("relationships", []),
        "join_fields": ([
            {"left": f'{item["left_table_name"]}.{item["left_field"]}',
             "right": f'{item["right_table_name"]}.{item["right_field"]}', "meaning": item["meaning"]}
            for item in route_info.get("relationships", [])
        ][0] if len(route_info.get("relationships", [])) == 1 else [
            {"left": f'{item["left_table_name"]}.{item["left_field"]}',
             "right": f'{item["right_table_name"]}.{item["right_field"]}', "meaning": item["meaning"]}
            for item in route_info.get("relationships", [])
        ]),
        "record_grains": [
            {"left": item["left_grain"], "right": item["right_grain"]}
            for item in route_info.get("relationships", [])
        ],
        "deduplication": route_info.get("deduplication"),
        "fields_used": columns,
        "generated_sql": sql,
        "sql_check_result": {"status": "passed", "readonly": True},
        "execution_status": "success",
        "execution_time_ms": round(execution_time_ms, 2),
        "data_update_time": data_update_time,
        "result_summary": _result_summary(columns, rows, truncated),
        "warnings": warnings or [],
        "created_at": utc_now(),
    }


def build_need_clarification_payload(question: str, route_info: dict[str, Any]) -> dict[str, Any]:
    trace_id = new_trace_id()
    candidates = route_info.get("candidates", [])
    candidate_text = "、".join(item.get("dataset_name", "") for item in candidates if item.get("dataset_name")) or "无"
    return {
        "answer_status": "need_clarification",
        "direct_answer": route_info.get("reasons", ["当前无法确定应该使用哪张数据表，请指定数据表或补充问题条件。"])[0],
        "scope": {
            "time_range": "未确定",
            "spatial_scope": "未确定",
            "subject": "未确定",
            "metric": "未确定",
        },
        "detail": {
            "candidate_datasets": candidates,
            "description": f"候选数据表：{candidate_text}",
        },
        "methodology": {
            "reason": "问题未命中明确的数据表、字段、术语或业务意图。",
            "required_action": "在问题中说明业务对象，或在数据表下拉框中手动选择。",
        },
        "trace_summary": {
            "route": "AskUser",
            "trace_id": trace_id,
            "warnings": route_info.get("reasons", []),
        },
        "trace_record": {
            "query_id": trace_id,
            "user_question": question,
            "answer_status": "need_clarification",
            "intent_type": route_info.get("intent_type", "unknown"),
            "route": "AskUser",
            "execution_status": "not_executed",
            "warnings": route_info.get("reasons", []),
            "created_at": utc_now(),
        },
    }


def build_answer_payload(
    *,
    route_info: dict[str, Any],
    trace_record: dict[str, Any],
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
) -> dict[str, Any]:
    spec = template_spec(route_info.get("intent_type", "generic_sql_query"))
    return {
        "answer_status": "success",
        "direct_answer": "",
        "scope": {
            "time_range": trace_record["time_range"],
            "spatial_scope": route_info.get("spatial_scope", "由问题和数据表共同确定"),
            "subject": route_info.get("subject", "数据记录"),
            "metric": route_info.get("metric", "查询结果"),
        },
        "detail": {
            "columns": columns,
            "rows": rows[:30],
            "result_summary": trace_record["result_summary"],
            "truncated": truncated,
        },
        "methodology": {
            "deduplication": route_info.get("deduplication", "按问题和术语确定"),
            "business_rules": route_info.get("methodology", []),
            "counting_rule": spec.get("counting_rule"),
        },
        "trace_summary": {
            "route": trace_record["route"],
            "data_update_time": trace_record.get("data_update_time"),
            "trace_id": trace_record["query_id"],
            "tables_used": trace_record.get("tables_used", []),
            "intent_type": trace_record["intent_type"],
            "intent_label": trace_record.get("intent_label"),
            "warnings": trace_record.get("warnings", []),
        },
    }


def answer_generation_prompt(
    *,
    question: str,
    answer_payload: dict[str, Any],
    trace_record: dict[str, Any],
    route_info: dict[str, Any],
) -> str:
    spec = template_spec(route_info.get("intent_type", "generic_sql_query"))
    payload_text = json.dumps(answer_payload, ensure_ascii=False, default=str, indent=2)
    compact_trace = {key: trace_record.get(key) for key in (
        "query_id", "intent_type", "intent_label", "route", "tables_used", "time_range",
        "spatial_scope", "subject", "metric", "deduplication", "data_update_time",
        "result_summary", "warnings",
    )}
    trace_text = json.dumps(compact_trace, ensure_ascii=False, default=str, indent=2)
    spec_text = json.dumps(spec, ensure_ascii=False, default=str, indent=2)
    compact_route = {key: route_info.get(key) for key in (
        "intent_type", "intent_label", "route", "subject", "metric", "spatial_scope",
        "deduplication", "methodology", "table_names",
    )}
    return f"""根据以下输入生成智能问数回答。

用户原始问题：
{question}

系统识别结果：
{json.dumps(compact_route, ensure_ascii=False, default=str, indent=2)}

回答模板约束：
{spec_text}

结构化答案：
{payload_text}

追溯记录：
{trace_text}

生成要求：
1. 必须输出这五个段落，段落标题固定为：直接结论、统计范围、结果明细、统计口径、可追溯信息。
2. 直接结论控制在 1-2 句，先给业务结论，再用一句话说明关键限制；不要把详细口径、SQL 过程、字段解释塞进首段。
3. 结果明细只给业务可读摘要：如果结果超过 10 行，不要逐条枚举所有行，只概述总行数、关键最大/最小/前几项，并说明完整结果以查询结果表为准。
4. 明确说明时间范围、空间范围、统计对象、统计指标；无法从 SQL 或结果确定时写“由查询条件限定”。
5. 船舶数、事件数、记录数不能混用；模板约束中的 counting_rule 必须落实到统计口径段落。
6. 统计口径只描述本次实际 SQL 用到的数据表、字段、筛选、分组和聚合；不要混入未实际使用的“优先规则”，避免出现“优先用 A 表但本次用了 B 表”的冲突表达。
7. 可追溯信息必须包含：识别意图、使用数据表、查询路线、数据更新时间、追溯编号。
8. 不输出 JSON，不输出 Markdown 表格，不新增输入中不存在的数字、船名、时间、空间对象或结论。
9. 如果 rows 为空，要明确说明未查到符合条件的数据。
10. 如果存在编码无法映射为中文名称、时间范围未显式给出、结果被截断等限制，在可追溯信息后补充“注意事项”段落。
"""
