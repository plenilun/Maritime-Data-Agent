from __future__ import annotations

from typing import Any

# 定义可用的关联键对，每个元组表示左表和右表可以用哪两个字段进行JOIN
# 例如 ("target_id", "mmsi") 表示左表的 target_id 可以关联右表的 mmsi
JOIN_KEY_PAIRS: tuple[tuple[str, str], ...] = (
    ("target_id", "target_id"), ("mmsi", "mmsi"), ("target_id", "mmsi"),
    ("mmsi", "target_id"), ("imo", "imo"), ("event_uuid", "event_uuid"))

# 定义每张表的业务语义信息：key 是表名（或表标识），value 包含：
# role：表的中文业务角色描述
# keywords：触发该表的关键词列表（中文 + 英文）
# grain：表的粒度（每行代表什么）
TABLE_PURPOSES: dict[str, dict[str, Any]] = {
    "cross_record_line": {"role": "VTS 报告线/口门穿越事件表", "keywords": ("进来", "进入", "进口", "出港", "出口", "报告线", "口门", "VTS", "CROSSING_IN"), "grain": "one_row_per_crossing_event"},
    "section_flow_judge": {"role": "截面流量穿越事件表", "keywords": ("截面", "流量", "上行", "下行", "穿越", "FLOW_UP", "FLOW_DOWN"), "grain": "one_row_per_section_flow_event"},
    "violation_record": {"role": "违规事件表", "keywords": ("违规", "违法", "AIS关闭", "AIS未", "会遇违规", "超宽靠泊", "主责"), "grain": "one_row_per_violation_event"},
    "risk_record": {"role": "风险事件表", "keywords": ("风险", "碰撞", "DCPA", "TCPA", "报警", "对遇", "追越"), "grain": "one_row_per_risk_event"},
    "data_real_time": {"role": "当前 AIS/船舶实时属性表", "keywords": ("吃水", "draught", "当前", "实时", "位置", "经纬度", "航速", "航向", "船长", "船宽"), "grain": "one_row_per_current_vessel"},
    "vessel_new_status_record": {"role": "船舶状态事件表", "keywords": ("航行", "锚泊", "靠泊", "状态", "锚地", "NAVIGATION", "ANCHOR", "BERTHING"), "grain": "one_row_per_status_event"},
}

# 定义与实时属性相关的关键词，用于判断问题是否需要实时表
REALTIME_ATTRIBUTE_KEYWORDS = ("吃水", "draught", "实时位置", "当前位置", "经纬度", "航速", "航向", "船长", "船宽")

# 标记哪些表是事件表（粒度是事件记录），用于判断多事件表关联时的基数关系
EVENT_TABLE_HINTS = ("cross_record_line", "section_flow_judge", "violation_record", "risk_record", "vessel_new_status_record")

# 判断 value 是否出现在 question 中（忽略大小写，且 value 非空）
def _contains(question: str, value: str) -> bool:
    return bool(value.strip()) and value.strip().lower() in question.lower()

# 将同义词字符串按逗号/分号拆分成列表，支持中英文分隔符
def _split_synonyms(value: str) -> list[str]:
    return [part.strip() for part in value.replace("，", ",").replace("；", ",").replace(";", ",").split(",") if part.strip()]

# 从数据集的id/name/table_name/source_file拼接成一个字符串，然后查找是否包含 TABLE_PURPOSES 中的某个 key（如 "cross_record_line"）
# 如果命中则返回该 key，否则返回表名本身
def _table_hint(dataset: dict[str, Any]) -> str:
    identity = " ".join(str(dataset.get(key, "")) for key in ("id", "name", "table_name", "source_file")).lower()
    return next((hint for hint in TABLE_PURPOSES if hint in identity), str(dataset.get("table_name", "")))

# 提取数据集中的所有字段名，返回集合
def _column_names(dataset: dict[str, Any]) -> set[str]:
    return {str(column.get("name", "")).strip() for column in dataset.get("columns", []) if column.get("name")}

# 检查两张表是否共享任意一对合法的JOIN键
# 返回第一个匹配的 (左表字段, 右表字段)，否则返回 None
def _shared_join_pair(left: dict[str, Any], right: dict[str, Any]) -> tuple[str, str] | None:
    left_columns, right_columns = _column_names(left), _column_names(right)
    return next(((left_key, right_key) for left_key, right_key in JOIN_KEY_PAIRS
                 if left_key in left_columns and right_key in right_columns), None)

# 计算术语匹配得分：遍历术语表，如果某个术语属于当前数据集且不是“时间字段”，且术语或其同义词出现在问题中，则加 5 分，并记录原因
def _score_terms(question: str, dataset: dict[str, Any], terms: list[dict[str, Any]]) -> tuple[int, list[str]]:
    score, reasons = 0, []
    for term in terms:
        if term.get("dataset_id") != dataset.get("id") or "时间字段" in str(term.get("term", "")):
            continue
        texts = [str(term.get("term", "")), *_split_synonyms(str(term.get("synonyms", "")))]
        if any(_contains(question, text) for text in texts):
            score += 5; reasons.append(f"命中术语 {term.get('term')}")
    return score, reasons

# 字段名匹配得分：问题中每出现一个该表的字段名，加 3 分
def _score_columns(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    matched = [name for name in _column_names(dataset) if _contains(question, name)]
    return len(matched) * 3, [f"命中字段 {name}" for name in matched]

# 业务关键词匹配得分：如果问题命中该表定义的关键词，每个关键词 4 分，上限 12 分
def _score_purpose(question: str, dataset: dict[str, Any]) -> tuple[int, list[str]]:
    purpose = TABLE_PURPOSES.get(_table_hint(dataset))
    if not purpose:
        return 0, []
    matched = [keyword for keyword in purpose["keywords"] if _contains(question, keyword)]
    return (min(12, len(matched) * 4), [f"命中业务线索：{', '.join(matched[:6])}"]) if matched else (0, [])

# 计算关联表的总得分（核心评分函数）：
# 跳过主表本身或无法关联的表
# 累加术语、字段、业务关键词三项得分
# 如果问题是查询实时属性且关联表是实时表，额外加 8 分
# 如果该表已经在 route_info 的候选列表中，额外加最多 6 分（意图模块的置信度加成）
def _score_related_dataset(question: str, primary: dict[str, Any], related: dict[str, Any],
                           terms: list[dict[str, Any]], route_info: dict[str, Any]) -> tuple[int, list[str]]:
    if primary.get("id") == related.get("id") or not _shared_join_pair(primary, related):
        return 0, []
    parts = [_score_terms(question, related, terms), _score_columns(question, related), _score_purpose(question, related)]
    score = sum(item[0] for item in parts)
    reasons = [reason for _, group in parts for reason in group]
    if _table_hint(related) == "data_real_time" and _table_hint(primary) != "data_real_time" and any(
        _contains(question, keyword) for keyword in REALTIME_ATTRIBUTE_KEYWORDS
    ):
        score += 8; reasons.append("问题需要当前船舶实时属性")
    if score:
        candidate = next((item for item in route_info.get("candidates", []) if item.get("dataset_id") == related.get("id")), None)
        if candidate and candidate.get("score", 0) > 0:
            score += min(6, int(candidate["score"])); reasons.append(f"进入意图候选表，得分 {candidate['score']}")
    return score, reasons

# 主函数：构建多表查询上下文
def build_multitable_context(question: str, datasets: list[dict[str, Any]], terms: list[dict[str, Any]],
                             route_info: dict[str, Any], *, max_related_tables: int | None = None) -> dict[str, Any]:
    
    # 从数据集列表中找出主表（由 route_info 指定），如果找不到则返回禁用状态
    primary = next((item for item in datasets if item.get("id") == route_info.get("dataset_id")), None)
    if not primary:
        return {"enabled": False, "reason": "primary dataset not found"}
    
    # 遍历所有数据集，计算每个表相对主表的关联得分，得分 ≥ 6 的进入候选列表
    candidates = []
    for dataset in datasets:
        score, reasons = _score_related_dataset(question, primary, dataset, terms, route_info)
        if score >= 6:
            candidates.append({"dataset": dataset, "score": score, "reasons": reasons[:6]})
    
    # 按得分降序排序，并截取前 max_related_tables 个
    candidates.sort(key=lambda item: item["score"], reverse=True)
    if max_related_tables is not None:
        candidates = candidates[:max_related_tables]
    
    # 初始化 tables 列表，第一个元素是主表信息
    tables = [{"dataset_id": primary["id"], "dataset_name": primary["name"], "table_name": primary["table_name"],
               "role": "主事实表", "grain": TABLE_PURPOSES.get(_table_hint(primary), {}).get("grain", "unknown"),
               "columns": primary.get("columns", [])}]
    
    # 遍历候选表，如果无法找到 JOIN 键则跳过
    join_plan, selection_reasons = [], []
    for item in candidates:
        dataset = item["dataset"]
        pair = _shared_join_pair(primary, dataset)
        if not pair:
            continue

        # 将关联表加入 tables，生成 JOIN 计划（包括左表、右表、关联字段、JOIN 条件、基数估算、业务含义），并记录选择原因
        tables.append({"dataset_id": dataset["id"], "dataset_name": dataset["name"], "table_name": dataset["table_name"],
                       "role": TABLE_PURPOSES.get(_table_hint(dataset), {}).get("role", "关联数据表"),
                       "grain": TABLE_PURPOSES.get(_table_hint(dataset), {}).get("grain", "unknown"),
                       "columns": dataset.get("columns", [])})
        join_plan.append({"left_table": primary["table_name"], "right_table": dataset["table_name"],
                          "left_key": pair[0], "right_key": pair[1],
                          "join_condition": f'"{primary["table_name"]}"."{pair[0]}" = "{dataset["table_name"]}"."{pair[1]}"',
                          "cardinality": "many_to_many" if _table_hint(primary) in EVENT_TABLE_HINTS and _table_hint(dataset) in EVENT_TABLE_HINTS else "unknown",
                          "business_meaning": f'{primary["name"]} 通过 {pair[0]}/{pair[1]} 关联 {dataset["name"]}'})
        selection_reasons.append({"dataset_name": dataset["name"], "table_name": dataset["table_name"],
                                  "score": item["score"], "reasons": item["reasons"]})
    
    # 返回最终的多表上下文，包含：
    # enabled：是否启用多表（至少 2 张表）
    # 主表信息、所有表列表、允许使用的表名列表、JOIN 计划、选择原因、SQL 生成规则提示
    return {"enabled": len(tables) > 1, "primary_dataset_id": primary["id"], "primary_table": primary["table_name"],
            "tables": tables, "allowed_tables": [item["table_name"] for item in tables],
            "join_plan": join_plan, "selection_reasons": selection_reasons,
            "sql_rules": ["JOIN 条件必须来自 join_plan，不得猜测字段关系。",
                          "关联表仅提供筛选条件时优先使用 EXISTS，避免记录放大。",
                          "问船舶数时使用 COUNT(DISTINCT 主表船舶标识)。",
                          "多个事件表应先按关联键分别聚合，再关联聚合结果。"]}

# 从全局术语表中筛选出与问题字面匹配且属于指定数据集的术语，最多返回 limit 个
def select_terms_for_context(all_terms: list[dict[str, Any]], dataset_ids: set[str], question: str,
                             *, limit: int = 12) -> list[dict[str, Any]]:
    """Return only literal matches; semantic retrieval supplies non-literal matches.

    Sending arbitrary fallback terms makes the SQL prompt grow with the glossary
    and can also distract the model with unrelated business definitions.
    """
    matched = []
    for term in all_terms:
        if term.get("dataset_id") not in dataset_ids and term.get("dataset_id") is not None:
            continue
        texts = [str(term.get("term", "")), *_split_synonyms(str(term.get("synonyms", "")))]
        if any(_contains(question, text) for text in texts):
            matched.append(term)
    return matched[:limit]
