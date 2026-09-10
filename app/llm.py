from __future__ import annotations

import json
import re
import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from .answer import answer_generation_prompt

SQL_CACHE_MAX = 128
_SQL_CACHE: OrderedDict[str, tuple[str, str, str]] = OrderedDict()
MODEL_REQUEST_ATTEMPTS = 4
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def clear_sql_cache() -> None:
    _SQL_CACHE.clear()


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
        for attempt in range(MODEL_REQUEST_ATTEMPTS):
            try:
                # Recreate the client after every transport failure. Some
                # OpenAI-compatible gateways close the connection while
                # processing a larger multi-table prompt; reusing that pool can
                # immediately hit the same broken connection again.
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(120, connect=20),
                    limits=httpx.Limits(max_keepalive_connections=0),
                ) as client:
                    response = await client.post(_chat_url(config.base_url), headers=headers, json=payload)
                    response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_STATUS_CODES or attempt == MODEL_REQUEST_ATTEMPTS - 1:
                    raise
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout):
                if attempt == MODEL_REQUEST_ATTEMPTS - 1:
                    raise
            await asyncio.sleep(0.5 * (2 ** attempt))
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise RuntimeError(f"模型接口返回 {exc.response.status_code}：{detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"无法连接模型接口：{exc}") from exc
    try:
        body = response.json()
        usage = body.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        return ChatResult(
            content=body["choices"][0]["message"]["content"],
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
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


def _normalize_exact_date_filters(sql: str, time_fields: set[str]) -> str:
    """Use the project's canonical prefix match for exact dates on TEXT timestamps."""
    if not time_fields:
        return sql

    identifier = r'(?:[A-Za-z_]\w*\s*\.\s*)?(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_]\w*)'
    pattern = re.compile(
        rf'\bdate\s*\(\s*(?P<column>{identifier})\s*\)\s*=\s*'
        r"(?P<quote>['\"])(?P<date>\d{4}-\d{2}-\d{2})(?P=quote)",
        flags=re.I,
    )

    def replace(match: re.Match[str]) -> str:
        column = match.group("column")
        bare_name = re.split(r"\s*\.\s*", column)[-1].strip('"`[]')
        if bare_name not in time_fields:
            return match.group(0)
        return f"{column} LIKE '{match.group('date')}%'"

    return pattern.sub(replace, sql)


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
        rules.append(f'“今天/今日”这类问题优先写成 date("{primary_time}") = (SELECT MAX(date("{primary_time}")) FROM "{table_name}")。')
        rules.append(
            f'用户给出具体日期 YYYY-MM-DD 时，固定使用 "{primary_time}" LIKE \'YYYY-MM-DD%\'，'
            f'例如 "{primary_time}" LIKE \'2026-07-24%\'；禁止写 date("{primary_time}") = \'YYYY-MM-DD\'。'
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


def _sql_cache_key(
    config: ModelConfig,
    question: str,
    table_name: str,
    columns: list[dict[str, str]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any] | None,
    code_context: dict[str, Any] | None = None,
    selected_datasets: list[dict[str, Any]] | None = None,
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
        "code_context": code_context or {},
        "selected_datasets": selected_datasets or [],
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
    code_context: dict[str, Any] | None = None,
    selected_datasets: list[dict[str, Any]] | None = None,
    multi_table_context: dict[str, Any] | None = None,
) -> tuple[str, str, ChatResult]:
    from .code_dictionary import prompt_block

    datasets = selected_datasets or [{"table_name": table_name, "columns": columns, "name": table_name}]
    cache_key = _sql_cache_key(
        config, question, table_name, columns, terms, route_info, code_context, datasets, multi_table_context
    )
    if cache_key in _SQL_CACHE:
        sql, reasoning_summary, content = _SQL_CACHE.pop(cache_key)
        _SQL_CACHE[cache_key] = (sql, reasoning_summary, content)
        return sql, reasoning_summary, ChatResult(
            content=content,
            elapsed_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cached=True,
        )

    schema_lines: list[str] = []
    context_tables = {item.get("table_name"): item for item in (multi_table_context or {}).get("tables", [])}
    for index, dataset in enumerate(datasets):
        alias = f"t{index + 1}"
        table_context = context_tables.get(dataset["table_name"], {})
        schema_lines.append(
            f'表 {index + 1}：业务名“{dataset["name"]}”，物理表名“{dataset["table_name"]}”，建议别名 {alias}；'
            f'角色：{table_context.get("role", "数据表")}；粒度：{table_context.get("grain", "unknown")}'
        )
        schema_lines.extend(f'  - "{col["name"]}" ({col["type"]})' for col in dataset["columns"])
    schema = "\n".join(schema_lines)
    # Keep retrieved evidence concise. Long definitions and synonym dumps can
    # make compatible gateways disconnect before returning an HTTP response.
    prompt_terms = sorted(terms, key=lambda item: float(item.get("relevance", 0)), reverse=True)[:12]
    glossary = "\n".join(
        f'- {str(item["term"])[:100]}（适用：{item.get("applicable_dataset_name") or "全局"}）：{str(item["definition"])[:500]}'
        for item in prompt_terms
    ) or "无相关业务术语"
    # Schemas and relationships are rendered below in a compact, dedicated
    # section. Keeping their full copies inside route_info made multi-table
    # prompts grow roughly twice as fast and caused some compatible gateways to
    # close the connection before returning a response.
    prompt_route_info = {
        key: value for key, value in (route_info or {}).items()
        if key not in {"candidates", "selected_datasets", "relationships", "relationship", "multi_table_context", "question"}
    }
    intent_context = json.dumps(prompt_route_info, ensure_ascii=False, default=str, indent=2)
    context_rules = (multi_table_context or {}).get("sql_rules", [])
    canonical_rules = _canonical_sql_rules(route_info, columns, table_name)
    if context_rules:
        canonical_rules += "\n" + "\n".join(f"M{index + 1}. {rule}" for index, rule in enumerate(context_rules))
    code_mapping_block = prompt_block(code_context or {})
    join_plan = (multi_table_context or {}).get("join_plan", [])
    relationship_block = "\n".join(
        f'- {item["business_meaning"]}；JOIN：{item["join_condition"]}；基数：{item["cardinality"]}'
        for item in join_plan
    ) or "无；本次为单表查询。"
    table_rule = (
        f"只能使用允许访问的 {len(datasets)} 张业务表，并且 JOIN 必须逐字遵守关系注册表中的字段条件。"
        if len(datasets) > 1 else "只能使用上述唯一一张业务表。"
    )
    prompt = f"""你是 SQLite 数据分析专家。请把问题转换成一条可执行的只读 SQL。

系统已识别出的意图与数据表：
{intent_context}

本次可用表及字段：
{schema}

【已确认的表关联关系】
{relationship_block}

【普通业务术语】
{glossary}

【字段编码映射】
{code_mapping_block}

稳定 SQL 写法：
{canonical_rules}

用户问题：{question}

规则：
1. {table_rule}
2. 只能生成 SELECT 或 WITH...SELECT，禁止任何写操作和 PRAGMA。
3. SQLite 方言；中文字段和表名必须用双引号。
4. 不要因为系统默认规则给查询添加 LIMIT；只有用户明确要求返回前 N 条、前 N 名等固定数量时，才按用户要求添加 LIMIT N。
5. 对“今天/当前”这类未给具体日期的问题，优先使用该表对应时间字段的最大日期或最新记录口径，不要使用真实系统日期。
6. 返回 JSON，格式必须是：{{"reasoning_summary":"简要说明使用的字段、筛选、聚合、分组和排序逻辑","sql":"可执行 SQL"}}。
7. reasoning_summary 只提供简洁、可核验的 SQL 生成依据，不输出隐藏推理或冗长思维链。
8. 不要输出 Markdown 或 JSON 之外的内容。
9. 不要为了同类问题创造新的等价 SQL 写法，优先复用上面的稳定 SQL 写法。
10. 编码字段的 WHERE 条件必须使用“字段编码映射”中提供的数据库真实编码，不能直接使用业务名称。
11. 只能使用“字段编码映射”中提供的映射，禁止猜测不存在的代码。
12. display 或 group_by 涉及编码字段时，SELECT 中保留该字段的原始字段名，系统将在查询后翻译为业务名称。
    多表存在同名编码字段时，使用“物理表名__字段名”作为 SELECT 输出别名，确保映射不会串表。
13. 多表查询必须使用表别名限定同名字段；不得 CROSS JOIN、逗号连接或猜测其他关联字段。
14. “有匹配事件”优先使用 EXISTS 或不会放大主体记录的 JOIN；问船舶数时按关系中已确认的船舶标识去重。
15. “每艘船事件次数且无事件显示 0”时，先在事件表按关联键及事件时间条件汇总，再 LEFT JOIN，并用 COALESCE 补 0。
16. “没有匹配事件”使用 NOT EXISTS，或使用 LEFT JOIN 后仅判断事件侧关联键 IS NULL；事件表空关联键不得影响结果。
17. 多类事件必须分别汇总后再关联，禁止直接连接多份事件明细造成记录相乘；表数量不设固定上限，但所有表必须位于已确认关系图中。
18. LEFT JOIN 中事件侧的时间条件必须放在事件子查询或 ON 条件内，不能放在外层 WHERE 中过滤掉零事件主体。
19. 当前属性只能描述当前快照，不能当作历史事件发生时的属性。若主体表可能一船多条且关系粒度未确认快照规则，不得随意选一条，应返回可核验的错误说明。
20. 对任一 TEXT 时间字段按用户给出的具体日期 YYYY-MM-DD 筛选时，固定写成 时间字段 LIKE 'YYYY-MM-DD%'，禁止用 date(时间字段) = 'YYYY-MM-DD'。
21. 对 vessel_new_status_record 一类状态事件表，“当前处于航行/锚泊/靠泊”分别使用 event_name_code='NAVIGATION'/'ANCHOR'/'BERTHING'，并筛选 event_type_code='OPEN'；这不是 AIS navstatus 数值编码。
22. “当前区域内、过去 N 天没有某事件的船舶”是反向集合查询：必须以当前实时船舶表为主表，用 NOT EXISTS 排除事件表中时间落在窗口内的记录；不能从事件表出发，也不能把 LEFT JOIN 后的时间条件写在外层 WHERE。
23. “过去 N 天”未给定日历截止日时，以对应事件表时间字段的 MAX(date(...)) 为数据截止日，窗口包含截止日共 N 个自然日；例如 7 天使用 date(MAX日期, '-6 days') 到 MAX日期，禁止使用系统当前日期。
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
    all_time_fields = {
        str(column.get("name", "")).strip()
        for dataset in datasets
        for column in dataset.get("columns", [])
        if any(cue in str(column.get("name", "")).lower() for cue in ("time", "date", "时间", "日期"))
    }
    sql = _normalize_exact_date_filters(sql, all_time_fields)
    reasoning_summary = extract_reasoning_summary(result.content, sql)
    try:
        from .query import validate_sql

        validate_sql(sql, allowed_tables=(multi_table_context or {}).get("allowed_tables", [item["table_name"] for item in datasets]))
    except ValueError:
        pass
    else:
        _SQL_CACHE[cache_key] = (sql, reasoning_summary, result.content)
        if len(_SQL_CACHE) > SQL_CACHE_MAX:
            _SQL_CACHE.popitem(last=False)
    return sql, reasoning_summary, result


async def repair_sql(
    *,
    config: ModelConfig,
    question: str,
    failed_sql: str,
    error_message: str,
    table_name: str,
    columns: list[dict[str, str]],
    terms: list[dict[str, Any]],
    route_info: dict[str, Any] | None = None,
    code_context: dict[str, Any] | None = None,
    selected_datasets: list[dict[str, Any]] | None = None,
    multi_table_context: dict[str, Any] | None = None,
) -> tuple[str, str, ChatResult]:
    from .code_dictionary import prompt_block

    datasets = selected_datasets or [{"table_name": table_name, "columns": columns, "name": table_name}]
    schema_lines: list[str] = []
    context_tables = {item.get("table_name"): item for item in (multi_table_context or {}).get("tables", [])}
    for index, dataset in enumerate(datasets):
        alias = f"t{index + 1}"
        table_context = context_tables.get(dataset["table_name"], {})
        schema_lines.append(
            f'表 {index + 1}：业务名“{dataset["name"]}”，物理表名“{dataset["table_name"]}”，建议别名 {alias}；'
            f'角色：{table_context.get("role", "数据表")}；粒度：{table_context.get("grain", "unknown")}'
        )
        schema_lines.extend(f'  - "{col["name"]}" ({col["type"]})' for col in dataset["columns"])
    schema = "\n".join(schema_lines)

    prompt_terms = sorted(terms, key=lambda item: float(item.get("relevance", 0)), reverse=True)[:12]
    glossary = "\n".join(
        f'- {str(item["term"])[:100]}（适用：{item.get("applicable_dataset_name") or "全局"}）：{str(item["definition"])[:500]}'
        for item in prompt_terms
    ) or "无相关业务术语"
    prompt_route_info = {
        key: value for key, value in (route_info or {}).items()
        if key not in {"candidates", "selected_datasets", "relationships", "relationship", "multi_table_context", "question"}
    }
    intent_context = json.dumps(prompt_route_info, ensure_ascii=False, default=str, indent=2)
    join_plan = (multi_table_context or {}).get("join_plan", [])
    relationship_block = "\n".join(
        f'- {item["business_meaning"]}；JOIN：{item["join_condition"]}；基数：{item["cardinality"]}'
        for item in join_plan
    ) or "无；本次为单表查询。"
    context_rules = (multi_table_context or {}).get("sql_rules", [])
    canonical_rules = _canonical_sql_rules(route_info, columns, table_name)
    if context_rules:
        canonical_rules += "\n" + "\n".join(f"M{index + 1}. {rule}" for index, rule in enumerate(context_rules))
    code_mapping_block = prompt_block(code_context or {})
    table_rule = (
        f"只能使用允许访问的 {len(datasets)} 张业务表，并且 JOIN 必须逐字遵守关系注册表中的字段条件。"
        if len(datasets) > 1 else "只能使用上述唯一一张业务表。"
    )

    prompt = f"""你是 SQLite SQL 修复工具。请根据错误信息修复上一条 SQL，只做必要修改。

用户问题：{question}

系统已识别出的意图与数据表：
{intent_context}

本次可用表及字段：
{schema}

【已确认的表关联关系】
{relationship_block}

【普通业务术语】
{glossary}

【字段编码映射】
{code_mapping_block}

稳定 SQL 写法：
{canonical_rules}

失败 SQL：
{failed_sql}

错误信息：
{error_message}

修复规则：
1. {table_rule}
2. 只能输出一条 SQLite SELECT 或 WITH...SELECT，禁止写操作、PRAGMA 和多语句。
3. 中文字段和表名必须用双引号；字段必须来自上方 schema。
4. 保持原查询意图，只修复导致校验或执行失败的问题。
5. 多表查询必须使用已确认 JOIN 计划，禁止 CROSS JOIN、逗号连接或猜测关联字段。
6. 编码字段筛选必须使用“字段编码映射”里的真实编码，禁止使用业务名称或猜测未提供的编码。
7. TEXT 时间字段按具体日期 YYYY-MM-DD 筛选时，固定写成 时间字段 LIKE 'YYYY-MM-DD%'。
8. 不要因为修复而添加默认 LIMIT；只有用户明确要求前 N 条、前 N 名时才允许 LIMIT。
9. 返回 JSON，格式必须是：{{"reasoning_summary":"简要说明修复了什么","sql":"修复后的可执行 SQL"}}。
10. 不要输出 Markdown 或 JSON 之外的内容。
"""
    result = await chat(
        config,
        [
            {
                "role": "system",
                "content": "你只负责在严格安全边界内修复 SQLite SQL。保持最小修改，并只返回 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=600,
    )
    parsed = extract_response_json(result.content)
    if parsed is not None:
        sql = extract_sql(str(parsed.get("sql", "")))
    else:
        sql = extract_sql(result.content)
    all_time_fields = {
        str(column.get("name", "")).strip()
        for dataset in datasets
        for column in dataset.get("columns", [])
        if any(cue in str(column.get("name", "")).lower() for cue in ("time", "date", "时间", "日期"))
    }
    sql = _normalize_exact_date_filters(sql, all_time_fields)
    reasoning_summary = extract_reasoning_summary(result.content, sql)
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
        try:
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
        except RuntimeError as exc:
            # SQL has already passed validation and executed successfully. A
            # disconnected answer-polishing request must not turn a successful
            # database query into a failed user request.
            result = ChatResult(
                content=_fallback_answer(question, columns, rows, truncated, trace_record, str(exc)),
                elapsed_ms=0,
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


def _fallback_answer(question: str, columns: list[str], rows: list[dict[str, Any]], truncated: bool,
                     trace_record: dict[str, Any], warning: str) -> str:
    if not rows:
        conclusion = "未查到符合条件的数据。"
        detail = "查询结果为空。"
    elif len(rows) == 1 and len(rows[0]) == 1:
        name, value = next(iter(rows[0].items()))
        conclusion = f"查询结果为 {value}。"
        detail = f"结果字段 {name}：{value}。"
    else:
        conclusion = f"共查询到 {len(rows)} 条结果" + ("（结果已截断）" if truncated else "") + "。"
        preview = "；".join(", ".join(f"{key}={value}" for key, value in row.items()) for row in rows[:5])
        detail = f"前 {min(5, len(rows))} 条：{preview}"
    tables = "、".join(trace_record.get("tables_used", [])) or "已识别数据表"
    return "\n".join([
        f"直接结论：\n{conclusion}",
        f"\n统计范围：\n- {trace_record.get('time_range', {}).get('description', '由查询条件限定')}\n- 统计对象：{trace_record.get('subject', {}).get('type', '数据记录')}",
        f"\n结果明细：\n{detail}",
        f"\n统计口径：\n基于已校验 SQL 的实际查询结果，使用数据表：{tables}。",
        f"\n可追溯信息：\n- 追溯编号：{trace_record.get('query_id', '--')}\n- 注意事项：答案润色接口暂时断开，以上内容由查询结果直接生成；数据库查询本身已成功。",
    ])
