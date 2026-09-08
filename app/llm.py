from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from .answer import answer_generation_prompt

SQL_CACHE_MAX = 128
_SQL_CACHE: OrderedDict[str, tuple[str, str, str]] = OrderedDict()


@dataclass
class ModelConfig:
    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ModelConfig":
        api_key = str(payload.get("api_key", "")).strip()
        base_url = str(payload.get("base_url", "")).strip().rstrip("/")
        model = str(payload.get("model", "")).strip()
        if not api_key or not base_url or not model:
            raise ValueError("请先完整配置 API Key、Base URL 和模型名称")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
        return cls(api_key=api_key, base_url=base_url, model=model)


@dataclass
class ChatResult:
    content: str
    elapsed_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached: bool = False


def _chat_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


async def chat(
    config: ModelConfig,
    messages: list[dict[str, str]],
    temperature: float = 0,
    max_tokens: int | None = None,
) -> ChatResult:
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    payload = {"model": config.model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(_chat_url(config.base_url), headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise RuntimeError(f"模型接口返回 {exc.response.status_code}：{detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"无法连接模型接口：{exc}") from exc
    try:
        body = response.json()
        usage = body.get("usage") or {}
        return ChatResult(
            content=body["choices"][0]["message"]["content"],
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("模型接口返回格式不兼容 OpenAI Chat Completions") from exc


def extract_response_json(text: str) -> dict[str, Any] | None:
    value = text.strip()
    fenced = re.search(r"```\s*(?:json|sql)?\s*\r?\n?(.*?)```", value, flags=re.I | re.S)
    if fenced:
        value = fenced.group(1).strip()

    if value.startswith("{"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Some OpenAI-compatible providers add a short sentence before the JSON.
    json_object = re.search(r'\{.*?"sql"\s*:\s*"(?:\\.|[^"\\])*".*?\}', value, flags=re.I | re.S)
    if json_object:
        try:
            parsed = json.loads(json_object.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def extract_sql(text: str) -> str:
    value = text.strip()
    parsed = extract_response_json(value)
    if parsed is not None:
        sql: Any = parsed.get("sql", value)
    else:
        fenced = re.search(r"```\s*(?:sql)?\s*\r?\n?(.*?)```", value, flags=re.I | re.S)
        sql = fenced.group(1).strip() if fenced else value
    return str(sql).strip().rstrip(";")


def extract_reasoning_summary(text: str, sql: str) -> str:
    """Return a concise rationale without relying on one provider-specific key."""
    parsed = extract_response_json(text)
    if parsed is not None:
        normalized = {str(key).strip().lower(): value for key, value in parsed.items()}
        for key in ("reasoning_summary", "reasoning", "rationale", "explanation", "summary", "依据摘要", "生成依据"):
            value = normalized.get(key.lower())
            if value is not None and str(value).strip():
                return str(value).strip()

    # Some compatible models only return SQL. Build a factual summary from the
    # validated query shape so the UI still has useful, auditable information.
    upper_sql = sql.upper()
    actions: list[str] = []
    if re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", upper_sql):
        actions.append("进行聚合统计")
    if re.search(r"\bWHERE\b", upper_sql):
        actions.append("按查询条件筛选数据")
    if re.search(r"\bGROUP\s+BY\b", upper_sql):
        actions.append("按指定字段分组")
    if re.search(r"\bORDER\s+BY\b", upper_sql):
        actions.append("按指定字段排序")
    limit = re.search(r"\bLIMIT\s+(\d+)\b", upper_sql)
    if limit:
        actions.append(f"最多返回 {limit.group(1)} 条记录")
    if not actions:
        actions.append("查询与问题相关的字段和记录")
    return "模型返回了可执行 SQL；该 SQL 将" + "、".join(actions) + "。"


def _column_names(columns: list[dict[str, str]]) -> set[str]:
    return {str(col.get("name", "")).strip() for col in columns if str(col.get("name", "")).strip()}


def _canonical_sql_rules(route_info: dict[str, Any] | None, columns: list[dict[str, str]], table_name: str) -> str:
    names = _column_names(columns)
    route = route_info or {}
    intent_type = str(route.get("intent_type", "generic_sql_query"))
    time_fields = [field for field in route.get("time_fields", []) if field in names]
    rules = [
        "同一 intent_type 的同类问题必须使用稳定 SQL 形态：除日期、数值、名称等用户条件外，不要随意更换 SELECT、聚合函数、GROUP BY、ORDER BY、LIMIT 的写法。",
        "问事件数、记录数、次数时统一使用 COUNT(*)；只有用户明确问船舶数、去重船舶数、多少船时才使用去重统计。",
    ]
    if "target_id" in names:
        rules.append('问船舶数时统一使用 COUNT(DISTINCT "target_id")，不要改用 COUNT(*)。')
    elif "mmsi" in names:
        rules.append('问船舶数时统一使用 COUNT(DISTINCT "mmsi")，不要改用 COUNT(*)。')
    if time_fields:
        primary_time = time_fields[0]
        rules.append(f'时间筛选优先使用 "{primary_time}"。用户说“今天/当前/最新”且未给具体日期时，统一使用该字段在当前表内的最大日期/最新记录口径。')
        rules.append(
            f'“今天/今日”这类问题固定写成 substr("{primary_time}",1,10) = '
            f'(SELECT MAX(substr("{primary_time}",1,10)) FROM "{table_name}")；'
            '不要使用 date() 解析带 +08 时区后缀的时间字符串。'
        )
    if intent_type == "gate_crossing_stat":
        if {"event_name_code", "event_type_code"}.issubset(names):
            rules.append('VTS 进入统计固定筛选 "event_name_code" = \'VTS_CROSSING_REPORT_LINE\' AND "event_type_code" = \'CROSSING_IN\'。')
    elif intent_type == "traffic_section_flow":
        if "event_type_code" in names:
            rules.append('截面流量方向固定映射：上行用 "event_type_code" = \'FLOW_UP\'，下行用 "event_type_code" = \'FLOW_DOWN\'。')
    elif intent_type == "vessel_list_by_condition":
        if "draught" in names:
            rules.append('吃水条件固定使用 "draught" 字段。')
        if "update_time" in names:
            rules.append('实时快照排序优先使用 "update_time" DESC。')
    elif intent_type == "violation_event_stat":
        if "violation_time" in names:
            rules.append('违规统计固定使用 "violation_time" 作为发生时间字段。')
    elif intent_type == "risk_event_stat":
        if "risk_time" in names:
            rules.append('风险统计固定使用 "risk_time" 作为发生时间字段。')
        if "risk_type_code" in names:
            rules.append('当前有效风险优先排除 "risk_type_code" = \'CLOSE\'。')
    elif intent_type == "vessel_status_count":
        if {"event_name_code", "event_type_code"}.issubset(names):
            rules.append('当前状态统计固定筛选 "event_type_code" = \'OPEN\'；航行/锚泊/靠泊分别使用 NAVIGATION/ANCHOR/BERTHING。')
    elif intent_type == "anchorage_anchor_stat":
        if {"event_name_code", "event_type_code", "region_uuid"}.issubset(names):
            rules.append('锚地锚泊统计固定筛选 "event_name_code" = \'ANCHOR\' AND "event_type_code" = \'OPEN\'，并按 "region_uuid" 分组。')
    return "\n".join(f"{index + 1}. {rule}" for index, rule in enumerate(rules))


def _multi_table_schema_text(multi_table_context: dict[str, Any]) -> str:
    sections = []
    for table in multi_table_context.get("tables", []):
        columns = "\n".join(
            f'  - "{column["name"]}" ({column["type"]})'
            for column in table.get("columns", [])
        )
        sections.append(
            "\n".join(
                [
                    f'- 表名："{table["table_name"]}"',
                    f'  业务角色：{table.get("role", "关联数据表")}',
                    f'  数据粒度：{table.get("grain", "unknown")}',
                    "  字段：",
                    columns or "  - 无字段信息",
                ]
            )
        )
    return "\n\n".join(sections)


def _multi_table_relationship_text(multi_table_context: dict[str, Any]) -> str:
    items = []
    for relation in multi_table_context.get("join_plan", []):
        items.append(
            "\n".join(
                [
                    f'- {relation["business_meaning"]}',
                    f'  JOIN 条件：{relation["join_condition"]}',
                    f'  基数关系：{relation["cardinality"]}',
                ]
            )
        )
    return "\n".join(items) or "无"


def _multi_table_rules_text(multi_table_context: dict[str, Any]) -> str:
    rules = multi_table_context.get("sql_rules", [])
    return "\n".join(f"{index + 1}. {rule}" for index, rule in enumerate(rules)) or "无"


def _sql_cache_key(
    config: ModelConfig,
    question: str,
    table_name: str,
    columns: list[dict[str, str]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any] | None,
    multi_table_context: dict[str, Any] | None = None,
) -> str:
    route = {key: value for key, value in (route_info or {}).items() if key != "candidates"}
    term_digest = [
        {
            "term": item.get("term"),
            "definition": item.get("definition"),
            "synonyms": item.get("synonyms", ""),
            "dataset_id": item.get("dataset_id"),
        }
        for item in terms
    ]
    payload = {
        "model": config.model,
        "base_url": config.base_url,
        "question": question.strip(),
        "table_name": table_name,
        "columns": columns,
        "terms": term_digest,
        "route_info": route,
        "multi_table_context": multi_table_context or {},
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(text.encode("utf-8")).hexdigest()


async def generate_sql(
    config: ModelConfig,
    question: str,
    table_name: str,
    columns: list[dict[str, str]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any] | None = None,
    multi_table_context: dict[str, Any] | None = None,
) -> tuple[str, str, ChatResult]:
    is_multi_table = bool(multi_table_context and multi_table_context.get("enabled"))
    cache_key = _sql_cache_key(config, question, table_name, columns, terms, route_info, multi_table_context)
    if cache_key in _SQL_CACHE:
        sql, reasoning_summary, content = _SQL_CACHE.pop(cache_key)
        _SQL_CACHE[cache_key] = (sql, reasoning_summary, content)
        return sql, reasoning_summary, ChatResult(content=content, elapsed_ms=0, cached=True)

    if is_multi_table:
        schema = _multi_table_schema_text(multi_table_context or {})
    else:
        schema = "\n".join(f'- "{col["name"]}" ({col["type"]})' for col in columns)
    glossary = "\n".join(
        f'- {item["term"]}（同义词：{item["synonyms"] or "无"}）：{item["definition"]}' for item in terms
    ) or "无相关业务术语"
    prompt_route_info = {key: value for key, value in (route_info or {}).items() if key != "candidates"}
    intent_context = json.dumps(prompt_route_info, ensure_ascii=False, default=str, indent=2)
    if is_multi_table:
        allowed_tables = ", ".join(f'"{table}"' for table in (multi_table_context or {}).get("allowed_tables", []))
        relationship_rules = _multi_table_relationship_text(multi_table_context or {})
        canonical_rules = _multi_table_rules_text(multi_table_context or {})
        prompt = f"""你是 SQLite 数据分析专家。请把问题转换成一条可执行的只读多表 SQL。

系统已识别出的意图与数据表：
{intent_context}

允许访问的数据表：
{allowed_tables}

表结构：
{schema}

关系注册表：
{relationship_rules}

业务术语：
{glossary}

稳定 SQL 写法：
{canonical_rules}

用户问题：{question}

规则：
1. 只能使用允许访问的数据表与表结构中列出的字段。
2. 只能生成 SELECT 或 WITH...SELECT，禁止任何写操作和 PRAGMA。
3. SQLite 方言；所有表名和字段名必须用双引号。
4. 多表关联必须使用关系注册表中的 JOIN 条件；没有注册关系的表不能强行关联。
5. 关联表仅用于筛选时优先使用 EXISTS，避免一对多或多对多 JOIN 放大主表记录。
6. 明细查询必须限制返回数量，最多 200 行；聚合查询可以不加 LIMIT。
7. 对“今天/当前”这类未给具体日期的问题，优先使用相关主事实表时间字段的最大日期或最新记录口径；日期比较固定使用 substr(时间字段,1,10)，不要使用真实系统日期，也不要使用 date() 解析带 +08 时区后缀的时间字符串。
8. 返回 JSON，格式必须是：{{"reasoning_summary":"简要说明使用的表、字段、关联、筛选、聚合、分组和排序逻辑","sql":"可执行 SQL"}}。
9. reasoning_summary 只提供简洁、可核验的 SQL 生成依据，不输出隐藏推理或冗长思维链。
10. 不要输出 Markdown 或 JSON 之外的内容。
11. 不要为了同类问题创造新的等价 SQL 写法，优先复用上面的稳定 SQL 写法。
"""
    else:
        canonical_rules = _canonical_sql_rules(route_info, columns, table_name)
        prompt = f"""你是 SQLite 数据分析专家。请把问题转换成一条可执行的只读 SQL。

系统已识别出的意图与数据表：
{intent_context}

表名："{table_name}"
字段：
{schema}

业务术语：
{glossary}

稳定 SQL 写法：
{canonical_rules}

用户问题：{question}

规则：
1. 只能使用上述唯一一张表与上述字段。
2. 只能生成 SELECT 或 WITH...SELECT，禁止任何写操作和 PRAGMA。
3. SQLite 方言；中文字段和表名必须用双引号。
4. 明细查询必须限制返回数量，最多 200 行；聚合查询可以不加 LIMIT。
5. 对“今天/当前”这类未给具体日期的问题，优先使用该表对应时间字段的最大日期或最新记录口径；日期比较固定使用 substr(时间字段,1,10)，不要使用真实系统日期，也不要使用 date() 解析带 +08 时区后缀的时间字符串。
6. 返回 JSON，格式必须是：{{"reasoning_summary":"简要说明使用的字段、筛选、聚合、分组和排序逻辑","sql":"可执行 SQL"}}。
7. reasoning_summary 只提供简洁、可核验的 SQL 生成依据，不输出隐藏推理或冗长思维链。
8. 不要输出 Markdown 或 JSON 之外的内容。
9. 不要为了同类问题创造新的等价 SQL 写法，优先复用上面的稳定 SQL 写法。
"""
    result = await chat(
        config,
        [
            {
                "role": "system",
                "content": "你只负责生成安全、准确、稳定的 SQLite SQL。temperature 已设为 0，同类问题必须保持一致写法。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=500,
    )
    parsed = extract_response_json(result.content)
    if parsed is not None:
        sql = extract_sql(str(parsed.get("sql", "")))
    else:
        sql = extract_sql(result.content)
    reasoning_summary = extract_reasoning_summary(result.content, sql)
    try:
        from .query import validate_sql

        if is_multi_table:
            validate_sql(sql, allowed_tables=(multi_table_context or {}).get("allowed_tables", []))
        else:
            validate_sql(sql, table_name)
    except ValueError:
        pass
    else:
        _SQL_CACHE[cache_key] = (sql, reasoning_summary, result.content)
        if len(_SQL_CACHE) > SQL_CACHE_MAX:
            _SQL_CACHE.popitem(last=False)
    return sql, reasoning_summary, result


async def summarize(
    config: ModelConfig,
    question: str,
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    route_info: dict[str, Any] | None = None,
    trace_record: dict[str, Any] | None = None,
    answer_payload: dict[str, Any] | None = None,
) -> ChatResult:
    if route_info and trace_record and answer_payload:
        prompt = answer_generation_prompt(
            question=question,
            answer_payload=answer_payload,
            trace_record=trace_record,
            route_info=route_info,
        )
        result = await chat(
            config,
            [
                {
                    "role": "system",
                    "content": "你是海事智能问数系统的答案生成模块，只能基于输入的结构化事实生成中文回答。",
                },
                {"role": "user", "content": prompt},
            ],
            0.1,
        )
        result.content = result.content.strip()
        return result

    data = json.dumps(rows[:100], ensure_ascii=False, default=str)
    prompt = f"""请根据 SQL 查询结果，用简洁、准确、自然的中文直接回答用户问题。

用户问题：{question}
执行的 SQL：{sql}
结果字段：{columns}
查询结果：{data}
结果是否因行数上限截断：{truncated}

要求：
- 开门见山给出结论，再补充必要的关键数字或明细。
- 不要声称结果中没有的信息，不要介绍生成 SQL 的过程。
- 空结果要明确说明未查到符合条件的数据。
- 只输出自然语言答案，不输出 JSON、图表或 Markdown 表格。
"""
    result = await chat(
        config,
        [{"role": "system", "content": "你是严谨的数据分析助手。"}, {"role": "user", "content": prompt}],
        0.1,
    )
    result.content = result.content.strip()
    return result
