from __future__ import annotations

from typing import Any


JOIN_KEY_PAIRS: tuple[tuple[str, str], ...] = (
    ("target_id", "target_id"),
    ("mmsi", "mmsi"),
    ("target_id", "mmsi"),
    ("mmsi", "target_id"),
    ("imo", "imo"),
    ("event_uuid", "event_uuid"),
)

TABLE_PURPOSES: dict[str, dict[str, Any]] = {
    "cross_record_line": {
        "role": "VTS 报告线/口门穿越事件表",
        "keywords": ("进来", "进入", "进口", "出港", "出口", "报告线", "口门", "VTS", "CROSSING_IN"),
        "grain": "one_row_per_crossing_event",
    },
    "section_flow_judge": {
        "role": "截面流量穿越事件表",
        "keywords": ("截面", "流量", "上行", "下行", "穿越", "FLOW_UP", "FLOW_DOWN"),
        "grain": "one_row_per_section_flow_event",
    },
    "violation_record": {
        "role": "违规事件表",
        "keywords": ("违规", "违法", "AIS关闭", "AIS未", "会遇违规", "超宽靠泊", "主责"),
        "grain": "one_row_per_violation_event",
    },
    "risk_record": {
        "role": "风险事件表",
        "keywords": ("风险", "碰撞", "DCPA", "TCPA", "报警", "对遇", "追越"),
        "grain": "one_row_per_risk_event",
    },
    "data_real_time": {
        "role": "当前 AIS/船舶实时属性表",
        "keywords": ("吃水", "draught", "当前", "实时", "位置", "经纬度", "航速", "航向", "船长", "船宽"),
        "grain": "one_row_per_current_vessel",
    },
    "vessel_new_status_record": {
        "role": "船舶状态事件表",
        "keywords": ("航行", "锚泊", "靠泊", "状态", "锚地", "NAVIGATION", "ANCHOR", "BERTHING"),
        "grain": "one_row_per_status_event",
    },
}

REALTIME_ATTRIBUTE_KEYWORDS = ("吃水", "draught", "当前位置", "实时位置", "经纬度", "航速", "航向", "船长", "船宽")
EVENT_TABLE_HINTS = ("cross_record_line", "section_flow_judge", "violation_record", "risk_record", "vessel_new_status_record")


def _contains(question: str, value: str) -> bool:
    text = value.strip()
    return bool(text) and text.lower() in question.lower()


def _split_synonyms(value: str) -> list[str]:
    parts = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


def _dataset_identity(dataset: dict[str, Any]) -> str:
    return " ".join(
        str(dataset.get(key, ""))
        for key in ("id", "name", "table_name", "source_file")
        if dataset.get(key)
    )


def _table_hint(dataset: dict[str, Any]) -> str:
    identity = _dataset_identity(dataset).lower()
    for hint in TABLE_PURPOSES:
        if hint.lower() in identity:
            return hint
    return str(dataset.get("table_name", ""))


def _column_names(dataset: dict[str, Any]) -> set[str]:
    return {
        str(column.get("name", "")).strip()
        for column in dataset.get("columns", [])
        if str(column.get("name", "")).strip()
    }


def _shared_join_pair(left: dict[str, Any], right: dict[str, Any]) -> tuple[str, str] | None:
    left_columns = _column_names(left)
    right_columns = _column_names(right)
    for left_key, right_key in JOIN_KEY_PAIRS:
        if left_key in left_columns and right_key in right_columns:
            return left_key, right_key
    return None


def _score_terms(question: str, dataset: dict[str, Any], terms: list[dict[str, Any]]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    dataset_id = dataset.get("id")
    for term in terms:
        if term.get("dataset_id") != dataset_id:
            continue
        term_name = str(term.get("term", ""))
        if "时间字段" in term_name:
            continue
        texts = [term_name, *_split_synonyms(str(term.get("synonyms", "")))]
        matched = [text for text in texts if _contains(question, text)]
        if matched:
            score += 5
            reasons.append(f"命中术语 {term.get('term')}")
    return score, reasons


def _score_columns(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for column in dataset.get("columns", []):
        name = str(column.get("name", "")).strip()
        if not name:
            continue
        if _contains(question, name):
            score += 3
            reasons.append(f"命中字段 {name}")
    return score, reasons


def _score_purpose(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    hint = _table_hint(dataset)
    purpose = TABLE_PURPOSES.get(hint)
    if not purpose:
        return 0, []
    matched = [keyword for keyword in purpose["keywords"] if _contains(question, keyword)]
    if not matched:
        return 0, []
    return min(12, len(matched) * 4), [f"命中业务线索：{', '.join(matched[:6])}"]


def _score_route_candidate(dataset: dict[str, Any], route_info: dict[str, Any]) -> tuple[int, list[str]]:
    for candidate in route_info.get("candidates", []):
        if candidate.get("dataset_id") == dataset.get("id") and candidate.get("score", 0) > 0:
            return min(6, int(candidate["score"])), [f"进入意图候选表，得分 {candidate['score']}"]
    return 0, []


def _role(dataset: dict[str, Any]) -> str:
    return str(TABLE_PURPOSES.get(_table_hint(dataset), {}).get("role") or "关联数据表")


def _grain(dataset: dict[str, Any]) -> str:
    return str(TABLE_PURPOSES.get(_table_hint(dataset), {}).get("grain") or "unknown")


def _is_event_like(dataset: dict[str, Any]) -> bool:
    hint = _table_hint(dataset)
    return hint in EVENT_TABLE_HINTS


def _relationship_cardinality(primary: dict[str, Any], related: dict[str, Any]) -> str:
    if _table_hint(related) == "data_real_time" and _is_event_like(primary):
        return "many_to_one"
    if _table_hint(primary) == "data_real_time" and _is_event_like(related):
        return "one_to_many"
    if _is_event_like(primary) and _is_event_like(related):
        return "many_to_many"
    return "unknown"


def _realtime_attribute_needed(question: str) -> bool:
    return any(_contains(question, keyword) for keyword in REALTIME_ATTRIBUTE_KEYWORDS)


def _score_related_dataset(
    question: str,
    primary: dict[str, Any],
    related: dict[str, Any],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any],
) -> tuple[int, list[str]]:
    if primary.get("id") == related.get("id"):
        return 0, []
    if not _shared_join_pair(primary, related):
        return 0, []

    direct_scores: list[tuple[int, list[str]]] = [
        _score_terms(question, related, terms),
        _score_columns(question, related),
        _score_purpose(question, related),
    ]
    score = sum(item[0] for item in direct_scores)
    reasons = [reason for _, group in direct_scores for reason in group]

    related_hint = _table_hint(related)
    primary_hint = _table_hint(primary)
    if related_hint == "data_real_time" and primary_hint != "data_real_time" and _realtime_attribute_needed(question):
        score += 8
        reasons.append("问题需要当前船舶实时属性")
    if primary_hint == "data_real_time" and related_hint != "data_real_time":
        _, purpose_reasons = _score_purpose(question, related)
        if purpose_reasons:
            score += 6
            reasons.append("主表为实时船舶，问题还涉及事件类条件")

    if score > 0:
        route_score, route_reasons = _score_route_candidate(related, route_info)
        score += route_score
        reasons.extend(route_reasons)

    return score, reasons


def build_multitable_context(
    question: str,
    datasets: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any],
    *,
    max_related_tables: int = 3,
) -> dict[str, Any]:
    primary = next((item for item in datasets if item.get("id") == route_info.get("dataset_id")), None)
    if not primary:
        return {"enabled": False, "reason": "primary dataset not found"}

    candidates = []
    for dataset in datasets:
        score, reasons = _score_related_dataset(question, primary, dataset, terms, route_info)
        if score >= 6:
            candidates.append({"dataset": dataset, "score": score, "reasons": reasons[:6]})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    related_items = candidates[:max_related_tables]
    if not related_items:
        return {
            "enabled": False,
            "reason": "question can be answered by primary table only",
            "allowed_tables": [primary["table_name"]],
        }

    tables = [
        {
            "dataset_id": primary["id"],
            "dataset_name": primary["name"],
            "table_name": primary["table_name"],
            "role": "主事实表",
            "grain": _grain(primary),
            "columns": primary.get("columns", []),
        }
    ]
    join_plan = []
    selection_reasons = []
    for item in related_items:
        dataset = item["dataset"]
        pair = _shared_join_pair(primary, dataset)
        if not pair:
            continue
        primary_key, related_key = pair
        tables.append(
            {
                "dataset_id": dataset["id"],
                "dataset_name": dataset["name"],
                "table_name": dataset["table_name"],
                "role": _role(dataset),
                "grain": _grain(dataset),
                "columns": dataset.get("columns", []),
            }
        )
        join_plan.append(
            {
                "left_table": primary["table_name"],
                "right_table": dataset["table_name"],
                "left_key": primary_key,
                "right_key": related_key,
                "join_condition": f'"{primary["table_name"]}"."{primary_key}" = "{dataset["table_name"]}"."{related_key}"',
                "cardinality": _relationship_cardinality(primary, dataset),
                "business_meaning": f'{primary["name"]} 通过 {primary_key}/{related_key} 关联 {dataset["name"]}',
            }
        )
        selection_reasons.append(
            {
                "dataset_name": dataset["name"],
                "table_name": dataset["table_name"],
                "score": item["score"],
                "reasons": item["reasons"],
            }
        )

    allowed_tables = [table["table_name"] for table in tables]
    return {
        "enabled": len(tables) > 1,
        "primary_dataset_id": primary["id"],
        "primary_table": primary["table_name"],
        "tables": tables,
        "allowed_tables": allowed_tables,
        "join_plan": join_plan,
        "selection_reasons": selection_reasons,
        "sql_rules": _build_sql_rules(primary, join_plan, route_info),
    }


def _build_sql_rules(
    primary: dict[str, Any],
    join_plan: list[dict[str, Any]],
    route_info: dict[str, Any],
) -> list[str]:
    primary_table = primary["table_name"]
    primary_columns = _column_names(primary)
    vessel_key = "target_id" if "target_id" in primary_columns else "mmsi" if "mmsi" in primary_columns else ""
    rules = [
        f'主表为 "{primary_table}"，统计事件数或记录数时以主表记录为准。',
        "JOIN 条件必须来自关系注册表，不得自行猜测字段关系。",
        "当关联表只是提供筛选条件时，优先使用 EXISTS 半连接，避免一对多或多对多 JOIN 放大主表记录。",
        "当必须 JOIN 一对多或多对多关系时，统计船舶数使用 DISTINCT，统计主表事件数使用主表唯一字段或 DISTINCT 主表 uuid。",
    ]
    if vessel_key:
        rules.append(f'问船舶数、多少船、去重船舶时，使用 COUNT(DISTINCT "{primary_table}"."{vessel_key}")。')
    time_fields = [field for field in route_info.get("time_fields", []) if field in primary_columns]
    if time_fields:
        primary_time = time_fields[0]
        rules.append(
            f'“今天/今日”固定写成 substr("{primary_table}"."{primary_time}",1,10) = '
            f'(SELECT MAX(substr("{primary_time}",1,10)) FROM "{primary_table}")；'
            '不要使用 date() 解析带 +08 时区后缀的时间字符串。'
        )
    risky = [item for item in join_plan if item["cardinality"] in {"one_to_many", "many_to_many"}]
    if risky:
        rules.append("当前查询存在一对多或多对多关系，SQL 必须显式去重或使用 EXISTS。")
    return rules


def select_terms_for_context(
    all_terms: list[dict[str, Any]],
    dataset_ids: set[str],
    question: str,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    matched = []
    fallback = []
    for term in all_terms:
        dataset_id = term.get("dataset_id")
        if dataset_id not in dataset_ids and dataset_id is not None:
            continue
        texts = [str(term.get("term", "")), *_split_synonyms(str(term.get("synonyms", "")))]
        if any(_contains(question, text) for text in texts):
            matched.append(term)
        elif dataset_id in dataset_ids:
            fallback.append(term)
    return [*matched, *fallback][:limit]
