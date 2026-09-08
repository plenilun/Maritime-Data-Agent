from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntentSpec:
    intent_type: str
    label: str
    dataset_hints: tuple[str, ...]
    keywords: tuple[str, ...]
    route: str
    subject: str
    metric: str
    spatial_scope: str
    deduplication: str
    methodology: tuple[str, ...]
    time_fields: tuple[str, ...] = ()


INTENT_SPECS: tuple[IntentSpec, ...] = (
    IntentSpec(
        intent_type="gate_crossing_stat",
        label="VTS 报告线进入统计",
        dataset_hints=("cross_record_line",),
        keywords=("进来", "进入", "来过", "报告线", "vts", "VTS", "吴淞VTS", "crossing", "CROSSING_IN"),
        route="TrajectoryEvent",
        subject="船舶",
        metric="不同船舶数 / 穿越事件数",
        spatial_scope="吴淞 VTS 报告线 / 吴淞 VTS 区域",
        deduplication="问船舶数时按 target_id/MMSI 去重；问事件数或次数时统计事件记录。",
        methodology=(
            "进入事件以 event_name_code='VTS_CROSSING_REPORT_LINE' 且 event_type_code='CROSSING_IN' 为准。",
            "时间字段使用 event_time。",
            "报告线区域可用 region_uuid 区分 L1/L4/L6。",
        ),
        time_fields=("event_time", "update_time"),
    ),
    IntentSpec(
        intent_type="traffic_section_flow",
        label="截面上行/下行流量统计",
        dataset_hints=("section_flow_judge",),
        keywords=("截面", "流量", "上行", "下行", "FLOW_UP", "FLOW_DOWN", "穿越", "吴淞黄浦江", "南漕航道"),
        route="TrajectoryEvent",
        subject="截面穿越船舶",
        metric="截面流量 / 去重船舶数",
        spatial_scope="吴淞黄浦江截面或南漕航道上段截面",
        deduplication="默认统计穿越事件 COUNT(*)；用户问船舶数时按 target_id/MMSI 去重。",
        methodology=(
            "上行使用 event_type_code='FLOW_UP'，下行使用 event_type_code='FLOW_DOWN'。",
            "时间字段使用 event_time。",
            "指定截面时按 region_uuid 过滤。",
        ),
        time_fields=("event_time", "update_time"),
    ),
    IntentSpec(
        intent_type="violation_event_stat",
        label="违规事件统计",
        dataset_hints=("violation_record",),
        keywords=("违规", "违法", "AIS关闭", "AIS未", "violation", "主责", "会遇违规", "超宽靠泊"),
        route="SQL",
        subject="违规事件",
        metric="违规事件数",
        spatial_scope="吴淞 VTS 区域",
        deduplication="默认统计违规事件记录 COUNT(*)；用户问船舶数时按 target_id/MMSI 去重。",
        methodology=(
            "违规发生时间使用 violation_time。",
            "violation_name_code 表示违规名称，violation_type_code 表示违规类型。",
        ),
        time_fields=("violation_time", "update_time"),
    ),
    IntentSpec(
        intent_type="risk_event_stat",
        label="风险事件统计",
        dataset_hints=("risk_record",),
        keywords=("风险", "碰撞", "DCPA", "TCPA", "risk", "报警", "对遇", "追越"),
        route="SQL",
        subject="风险事件",
        metric="风险事件数",
        spatial_scope="吴淞 VTS 区域",
        deduplication="默认统计风险事件记录 COUNT(*)；用户问船舶数时按 target_id/MMSI 去重。",
        methodology=(
            "风险发生时间使用 risk_time。",
            "当前有效风险优先排除 risk_type_code='CLOSE' 或筛选 risk_level_code > 0。",
        ),
        time_fields=("risk_time", "update_time"),
    ),
    IntentSpec(
        intent_type="vessel_list_by_condition",
        label="实时船舶条件列表",
        dataset_hints=("data_real_time",),
        keywords=("吃水", "draught", "超过", "实时", "当前在", "当前船舶", "位置", "经纬度", "AIS在线"),
        route="SQL",
        subject="当前船舶",
        metric="满足条件的船舶列表",
        spatial_scope="当前 VTS 监控区域",
        deduplication="实时快照按 target_id/MMSI 识别船舶；同一 MMSI 只保留当前记录。",
        methodology=(
            "吃水使用 draught 字段，单位为米。",
            "位置使用 lon/lat，更新时间使用 update_time 或 received_at。",
            "data_real_time 没有 district_code 字段，默认全部记录属于当前 VTS 监控区域。",
        ),
        time_fields=("update_time", "received_at"),
    ),
    IntentSpec(
        intent_type="vessel_status_count",
        label="船舶航行/锚泊/靠泊状态统计",
        dataset_hints=("vessel_new_status_record",),
        keywords=("航行", "锚泊", "靠泊", "状态", "当前处于", "NAVIGATION", "ANCHOR", "BERTHING"),
        route="SQL",
        subject="船舶状态事件",
        metric="当前状态船舶数",
        spatial_scope="吴淞 VTS 区域",
        deduplication="问当前状态时优先筛选 event_type_code='OPEN'，并按 target_id/MMSI 去重。",
        methodology=(
            "event_name_code='NAVIGATION' 表示航行，'ANCHOR' 表示锚泊，'BERTHING' 表示靠泊。",
            "问当前处于某状态时优先筛选 event_type_code='OPEN'。",
        ),
        time_fields=("event_time", "update_time"),
    ),
    IntentSpec(
        intent_type="ship_type_stat",
        label="船型统计",
        dataset_hints=("vessel_new_status_record", "data_real_time"),
        keywords=("船型", "ship_type", "类型统计", "lloyds_ship_type"),
        route="SQL",
        subject="船舶",
        metric="按船型分类统计",
        spatial_scope="当前 VTS 监控区域",
        deduplication="默认按 ship_type 分类；问当前时优先使用 event_type_code='OPEN' 的状态记录。",
        methodology=("船型统计优先使用 vessel_new_status_record.ship_type。",),
        time_fields=("event_time", "update_time", "received_at"),
    ),
    IntentSpec(
        intent_type="anchorage_anchor_stat",
        label="锚地锚泊数量统计",
        dataset_hints=("vessel_new_status_record",),
        keywords=("锚地", "锚泊", "各个锚地", "anchor", "region_uuid"),
        route="SQL",
        subject="锚泊船舶",
        metric="各锚地锚泊船舶数",
        spatial_scope="各锚地 / region_uuid",
        deduplication="问当前各锚地锚泊数量时筛选 event_name_code='ANCHOR' 和 event_type_code='OPEN'，按 target_id/MMSI 去重。",
        methodology=("锚地分组字段使用 region_uuid。",),
        time_fields=("event_time", "update_time"),
    ),
)


GENERIC_INTENT = IntentSpec(
    intent_type="generic_sql_query",
    label="通用单表 SQL 查询",
    dataset_hints=(),
    keywords=(),
    route="SQL",
    subject="数据记录",
    metric="查询结果",
    spatial_scope="用户选择或系统识别的数据表范围",
    deduplication="按用户问题与业务术语确定统计口径。",
    methodology=("仅访问系统识别出的单张数据表。",),
)


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


def _spec_matches_dataset(spec: IntentSpec, dataset: dict[str, Any]) -> bool:
    identity = _dataset_identity(dataset).lower()
    return any(hint.lower() in identity for hint in spec.dataset_hints)


def _score_columns(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for col in dataset.get("columns", []):
        name = str(col.get("name", ""))
        if _contains(question, name):
            score += 2
            reasons.append(f"命中字段 {name}")
            continue
        for part in name.split("_"):
            if len(part) >= 4 and _contains(question, part):
                score += 1
                reasons.append(f"命中字段片段 {part}")
                break
    return score, reasons


def _score_terms(question: str, dataset: dict[str, Any], terms: list[dict[str, Any]]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    dataset_id = dataset.get("id")
    for term in terms:
        term_dataset = term.get("dataset_id")
        if term_dataset not in (None, dataset_id):
            continue
        texts = [str(term.get("term", "")), *_split_synonyms(str(term.get("synonyms", "")))]
        matched = [text for text in texts if _contains(question, text)]
        if not matched:
            continue
        if term_dataset == dataset_id:
            score += 5
            reasons.append(f"命中术语 {term.get('term')}")
        else:
            score += 1
    return score, reasons


def _score_specs(question: str, dataset: dict[str, Any]) -> tuple[int, IntentSpec, list[str]]:
    best_score = 0
    best_spec = GENERIC_INTENT
    reasons: list[str] = []
    for spec in INTENT_SPECS:
        if not _spec_matches_dataset(spec, dataset):
            continue
        matches = [keyword for keyword in spec.keywords if _contains(question, keyword)]
        if not matches:
            continue
        score = len(matches) * 3
        if score > best_score:
            best_score = score
            best_spec = spec
            reasons = [f"命中意图关键词：{', '.join(matches[:6])}"]
    return best_score, best_spec, reasons


def _score_dataset_name(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for key in ("id", "name", "table_name", "source_file"):
        value = str(dataset.get(key, ""))
        if value and _contains(question, value):
            score += 8
            reasons.append(f"命中数据表标识 {value}")
            break
    return score, reasons


def _confidence(score: int, second_score: int, manual: bool) -> float:
    if manual:
        return 1.0
    if score <= 0:
        return 0.0
    margin = max(score - second_score, 0)
    return round(min(0.98, 0.45 + score / 24 + margin / 20), 2)


def _make_candidate(dataset: dict[str, Any], score: int, spec: IntentSpec, reasons: list[str]) -> dict[str, Any]:
    return {
        "dataset_id": dataset["id"],
        "dataset_name": dataset["name"],
        "table_name": dataset["table_name"],
        "score": score,
        "intent_type": spec.intent_type,
        "intent_label": spec.label,
        "reasons": reasons[:8],
    }


def route_question(
    question: str,
    datasets: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    requested_dataset_id: str | None = None,
) -> dict[str, Any]:
    if not datasets:
        return {
            "answer_status": "need_clarification",
            "intent_type": "no_dataset",
            "intent_label": "尚未上传数据表",
            "dataset_id": None,
            "dataset_name": "",
            "table_name": "",
            "confidence": 0,
            "route": "AskUser",
            "reasons": ["当前没有可用数据表"],
            "candidates": [],
        }

    manual_dataset = next((item for item in datasets if item["id"] == requested_dataset_id), None)
    scored: list[dict[str, Any]] = []
    for dataset in datasets:
        name_score, name_reasons = _score_dataset_name(question, dataset)
        term_score, term_reasons = _score_terms(question, dataset, terms)
        column_score, column_reasons = _score_columns(question, dataset)
        spec_score, spec, spec_reasons = _score_specs(question, dataset)
        total = name_score + term_score + column_score + spec_score
        scored.append(_make_candidate(dataset, total, spec, [*name_reasons, *spec_reasons, *term_reasons, *column_reasons]))

    scored.sort(key=lambda item: item["score"], reverse=True)

    if manual_dataset:
        manual = next(item for item in scored if item["dataset_id"] == manual_dataset["id"])
        spec = next((item for item in INTENT_SPECS if item.intent_type == manual["intent_type"]), GENERIC_INTENT)
        return {
            **manual,
            "answer_status": "success",
            "confidence": _confidence(manual["score"], 0, manual=True),
            "route": spec.route,
            "subject": spec.subject,
            "metric": spec.metric,
            "spatial_scope": spec.spatial_scope,
            "deduplication": spec.deduplication,
            "methodology": list(spec.methodology),
            "time_fields": list(spec.time_fields),
            "selection_mode": "manual",
            "candidates": scored[:3],
        }

    top = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0
    if top["score"] <= 0 and len(datasets) > 1:
        return {
            **top,
            "answer_status": "need_clarification",
            "confidence": 0,
            "route": "AskUser",
            "subject": "数据记录",
            "metric": "查询结果",
            "spatial_scope": "未确定",
            "deduplication": "未确定",
            "methodology": ["问题中没有足够线索判断应该使用哪张数据表。"],
            "time_fields": [],
            "selection_mode": "auto",
            "reasons": ["未识别到明确的数据表、术语或字段线索"],
            "candidates": scored[:3],
        }

    if top["score"] == second_score and top["score"] < 8 and len(datasets) > 1:
        return {
            **top,
            "answer_status": "need_clarification",
            "confidence": 0.35,
            "route": "AskUser",
            "subject": "数据记录",
            "metric": "查询结果",
            "spatial_scope": "未确定",
            "deduplication": "未确定",
            "methodology": ["候选数据表得分接近，需要用户指定数据表。"],
            "time_fields": [],
            "selection_mode": "auto",
            "reasons": ["候选数据表不唯一"],
            "candidates": scored[:3],
        }

    spec = next((item for item in INTENT_SPECS if item.intent_type == top["intent_type"]), GENERIC_INTENT)
    return {
        **top,
        "answer_status": "success",
        "confidence": _confidence(top["score"], second_score, manual=False),
        "route": spec.route,
        "subject": spec.subject,
        "metric": spec.metric,
        "spatial_scope": spec.spatial_scope,
        "deduplication": spec.deduplication,
        "methodology": list(spec.methodology),
        "time_fields": list(spec.time_fields),
        "selection_mode": "auto",
        "candidates": scored[:3],
    }
