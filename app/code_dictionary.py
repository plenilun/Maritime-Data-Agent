from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from . import db


FM_CODE_PATH = db.DATA_DIR / "fm_code.xlsx"
REQUIRED_COLUMNS = ("dict_type", "dict_code", "name_cn")
FIELD_CODE_TYPES = {
    "ship_type": "AIS_SHIP_TYPE",
    "lloyds_ship_type": "AIS_SHIP_TYPE",
    "navstatus": "NAV_STATUS",
    "risk_level_code": "RISK_LEVEL_CODE",
}
TYPE_CUES = {
    "AIS_SHIP_TYPE": ("船型", "船舶类型", "船种", "ship_type", "lloyds_ship_type"),
    "NAV_STATUS": ("航行状态", "navstatus", "nav_status"),
    "RISK_LEVEL_CODE": ("风险等级", "风险程度", "risk_level_code"),
}
GROUP_CUES = ("各类", "各型", "按", "分别", "分组", "分布", "占比")


def _normalize_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return re.sub(r"\.0$", "", text)


def _split_synonyms(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，;；]", value or "") if part.strip()]


def parse_workbook(content: bytes) -> dict[str, Any]:
    errors: list[str] = []
    try:
        book = pd.ExcelFile(io.BytesIO(content))
    except Exception as exc:
        return {"entries": [], "errors": [f"无法读取 Excel：{exc}"], "duplicates": []}
    frames: list[pd.DataFrame] = []
    for sheet in book.sheet_names:
        frame = pd.read_excel(io.BytesIO(content), sheet_name=sheet, dtype=object)
        if frame.empty and not len(frame.columns):
            continue
        frame.columns = [str(column).strip() for column in frame.columns]
        missing = set(REQUIRED_COLUMNS).difference(frame.columns)
        if missing:
            errors.append(f"工作表“{sheet}”缺少字段：{', '.join(sorted(missing))}")
            continue
        frames.append(frame[list(REQUIRED_COLUMNS)])
    if not frames and not errors:
        errors.append("Excel 中没有可导入的编码数据")

    entries: list[dict[str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    duplicates: list[dict[str, Any]] = []
    row_number = 1
    for frame in frames:
        for _, record in frame.iterrows():
            row_number += 1
            code_type = "" if pd.isna(record["dict_type"]) else str(record["dict_type"]).strip()
            code_value = _normalize_code(record["dict_code"])
            description = "" if pd.isna(record["name_cn"]) else str(record["name_cn"]).strip()
            if not code_type or not code_value or not description:
                errors.append(f"第 {row_number} 行：dict_type、dict_code、name_cn 均不能为空")
                continue
            key = (code_type, code_value)
            if key in seen:
                duplicates.append({"code_type": code_type, "code_value": code_value, "rows": [seen[key], row_number]})
                continue
            seen[key] = row_number
            entries.append(
                {"code_type": code_type, "code_value": code_value, "description": description, "synonyms": ""}
            )
    if duplicates:
        errors.append(f"发现 {len(duplicates)} 组重复的 code_type + code_value")
    return {"entries": entries, "errors": errors, "duplicates": duplicates}


def import_workbook(content: bytes, filename: str, *, activate: bool = True, source_type: str = "excel") -> dict[str, Any]:
    parsed = parse_workbook(content)
    if parsed["errors"]:
        raise ValueError("；".join(parsed["errors"][:20]))
    normalized_content = json.dumps(
        sorted(parsed["entries"], key=lambda item: (item["code_type"], item["code_value"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    with db.connection() as conn:
        same = conn.execute(
            "SELECT * FROM code_dictionary_versions WHERE content_hash = ? ORDER BY version_number DESC LIMIT 1",
            (digest,),
        ).fetchone()
        if same:
            return {"version": dict(same), "imported": 0, "duplicate_file": True}
        version_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM code_dictionary_versions"
        ).fetchone()[0]
        version_id = uuid.uuid4().hex
        now = db.utc_now()
        if activate:
            conn.execute("UPDATE code_dictionary_versions SET enabled = 0 WHERE enabled = 1")
        conn.execute(
            "INSERT INTO code_dictionary_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (version_id, version_number, source_type, filename, digest, now, int(activate)),
        )
        conn.executemany(
            """
            INSERT INTO code_dictionary_entries
                (id, version_id, code_type, code_value, description, synonyms, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    uuid.uuid4().hex,
                    version_id,
                    item["code_type"],
                    item["code_value"],
                    item["description"],
                    item["synonyms"],
                    now,
                    now,
                )
                for item in parsed["entries"]
            ],
        )
    return {
        "version": get_version(version_id),
        "imported": len(parsed["entries"]),
        "duplicate_file": False,
    }


def initialize_default_dictionary() -> None:
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM code_dictionary_versions").fetchone()[0]
    if count == 0 and FM_CODE_PATH.exists():
        import_workbook(FM_CODE_PATH.read_bytes(), FM_CODE_PATH.name, activate=True, source_type="system")
    sync_default_bindings()


def sync_default_bindings() -> None:
    now = db.utc_now()
    with db.connection() as conn:
        for dataset in db.list_datasets():
            for column in dataset.get("columns", []):
                column_name = str(column.get("name", ""))
                code_type = FIELD_CODE_TYPES.get(column_name.lower())
                if not code_type:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO code_field_bindings
                        (id, dataset_id, table_name, column_name, code_type, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (uuid.uuid4().hex, dataset["id"], dataset["table_name"], column_name, code_type, now, now),
                )


def get_version(version_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM code_dictionary_versions WHERE id = ?", (version_id,)).fetchone()
    return dict(row) if row else None


def active_version() -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM code_dictionary_versions WHERE enabled = 1").fetchone()
    return dict(row) if row else None


def list_versions() -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM code_dictionary_versions ORDER BY version_number DESC").fetchall()
    return [dict(row) for row in rows]


def activate_version(version_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM code_dictionary_versions WHERE id = ?", (version_id,)).fetchone()
        if not exists:
            return None
        conn.execute("UPDATE code_dictionary_versions SET enabled = 0 WHERE enabled = 1")
        conn.execute("UPDATE code_dictionary_versions SET enabled = 1 WHERE id = ?", (version_id,))
    return get_version(version_id)


def list_entries(query: str = "", code_type: str = "", version_id: str | None = None) -> list[dict[str, Any]]:
    version = get_version(version_id) if version_id else active_version()
    if not version:
        return []
    sql = "SELECT * FROM code_dictionary_entries WHERE version_id = ?"
    params: list[Any] = [version["id"]]
    if code_type:
        sql += " AND code_type = ?"
        params.append(code_type)
    if query.strip():
        sql += " AND (description LIKE ? OR synonyms LIKE ? OR code_value = ?)"
        params.extend([f"%{query.strip()}%", f"%{query.strip()}%", query.strip()])
    sql += " ORDER BY code_type, code_value"
    with db.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def update_entry(entry_id: str, description: str, synonyms: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        cursor = conn.execute(
            "UPDATE code_dictionary_entries SET description = ?, synonyms = ?, updated_at = ? WHERE id = ?",
            (description.strip(), synonyms.strip(), db.utc_now(), entry_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM code_dictionary_entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def create_entry(code_type: str, code_value: str, description: str, synonyms: str = "") -> dict[str, Any]:
    version = active_version()
    if not version:
        raise ValueError("当前没有启用的编码字典版本")
    normalized = _normalize_code(code_value)
    if not code_type.strip() or not normalized or not description.strip():
        raise ValueError("code_type、code_value 和 description 均不能为空")
    entry_id = uuid.uuid4().hex
    now = db.utc_now()
    try:
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO code_dictionary_entries
                    (id, version_id, code_type, code_value, description, synonyms, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, version["id"], code_type.strip(), normalized, description.strip(), synonyms.strip(), now, now),
            )
            row = conn.execute("SELECT * FROM code_dictionary_entries WHERE id = ?", (entry_id,)).fetchone()
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("当前版本中已存在相同的 code_type + code_value") from exc
        raise
    return dict(row)


def delete_entry(entry_id: str) -> bool:
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM code_dictionary_entries WHERE id = ?", (entry_id,))
    return cursor.rowcount > 0


def list_bindings(dataset_id: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM code_field_bindings WHERE 1=1"
    params: list[Any] = []
    if dataset_id:
        sql += " AND dataset_id = ?"
        params.append(dataset_id)
    sql += " ORDER BY table_name, column_name"
    with db.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def save_binding(dataset_id: str, table_name: str, column_name: str, code_type: str, enabled: bool = True) -> dict[str, Any]:
    dataset = db.get_dataset(dataset_id)
    if not dataset or dataset["table_name"] != table_name:
        raise ValueError("数据集与数据表不匹配")
    if column_name not in {column["name"] for column in dataset["columns"]}:
        raise ValueError("绑定字段不存在于数据表 Schema")
    version = active_version()
    if not version:
        raise ValueError("当前没有启用的编码字典版本")
    with db.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM code_dictionary_entries WHERE version_id = ? AND code_type = ? LIMIT 1",
            (version["id"], code_type),
        ).fetchone()
        if not exists:
            raise ValueError("当前启用版本中不存在该 code_type")
        current = conn.execute(
            "SELECT * FROM code_field_bindings WHERE dataset_id = ? AND table_name = ? AND column_name = ?",
            (dataset_id, table_name, column_name),
        ).fetchone()
        now = db.utc_now()
        if current:
            conn.execute(
                "UPDATE code_field_bindings SET code_type = ?, enabled = ?, updated_at = ? WHERE id = ?",
                (code_type, int(enabled), now, current["id"]),
            )
            binding_id = current["id"]
        else:
            binding_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO code_field_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (binding_id, dataset_id, table_name, column_name, code_type, int(enabled), now, now),
            )
        row = conn.execute("SELECT * FROM code_field_bindings WHERE id = ?", (binding_id,)).fetchone()
    return dict(row)


def delete_binding(binding_id: str) -> bool:
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM code_field_bindings WHERE id = ?", (binding_id,))
    return cursor.rowcount > 0


def resolve_code_context(question: str, dataset: dict[str, Any], route_info: dict[str, Any]) -> dict[str, Any]:
    bindings = [item for item in list_bindings(dataset["id"]) if item["enabled"]]
    if not bindings:
        return {"version": None, "requests": [], "mappings": [], "warnings": []}
    relevant_types = {
        binding["code_type"]
        for binding in bindings
        if any(cue.lower() in question.lower() for cue in TYPE_CUES.get(binding["code_type"], ()))
    }
    if route_info.get("intent_type") == "ship_type_stat":
        relevant_types.add("AIS_SHIP_TYPE")
    if route_info.get("intent_type") == "risk_event_stat" and "风险" in question:
        relevant_types.add("RISK_LEVEL_CODE")
    if not relevant_types:
        return {"version": None, "requests": [], "mappings": [], "warnings": []}

    version = active_version()
    if not version:
        return {"version": None, "requests": [], "mappings": [], "warnings": ["当前没有启用的编码字典版本"]}
    entries = [item for item in list_entries(version_id=version["id"]) if item["code_type"] in relevant_types]
    requests: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    warnings: list[str] = []
    grouped = any(cue in question for cue in GROUP_CUES)
    for binding in bindings:
        if binding["code_type"] not in relevant_types:
            continue
        type_entries = [item for item in entries if item["code_type"] == binding["code_type"]]
        matches = []
        for entry in type_entries:
            labels = [entry["description"], *_split_synonyms(entry.get("synonyms", ""))]
            if any(label and label.lower() in question.lower() for label in labels):
                matches.append(entry)
        purpose = "filter" if matches else ("group_by" if grouped else "display")
        mention = "、".join(item["description"] for item in matches)
        requests.append(
            {
                "table": binding["table_name"],
                "column": binding["column_name"],
                "code_type": binding["code_type"],
                "mention": mention,
                "purpose": purpose,
                "code_values": [item["code_value"] for item in matches],
            }
        )
        mappings.extend(
            {
                "table": binding["table_name"],
                "column": binding["column_name"],
                "code_type": binding["code_type"],
                "code_value": item["code_value"],
                "description": item["description"],
                "synonyms": item.get("synonyms", ""),
            }
            for item in type_entries
        )
    return {"version": version, "requests": requests, "mappings": mappings, "warnings": warnings}


def resolve_code_contexts(question: str, datasets: list[dict[str, Any]], route_info: dict[str, Any]) -> dict[str, Any]:
    contexts = [resolve_code_context(question, dataset, route_info) for dataset in datasets]
    # When a dedicated status-event table participates in the query, phrases
    # such as “当前处于航行状态” describe event_name_code/event_type_code rather
    # than the AIS snapshot navstatus field.  Do not force an unrelated
    # NAV_STATUS lookup from the realtime table before SQL generation.
    has_status_events = any(
        {"event_name_code", "event_type_code"}.issubset(
            {str(column.get("name", "")).lower() for column in dataset.get("columns", [])}
        ) and "status" in str(dataset.get("table_name", "")).lower()
        for dataset in datasets
    )
    if has_status_events and any(cue in question for cue in ("航行", "锚泊", "靠泊", "当前处于")):
        for context in contexts:
            context["requests"] = [item for item in context.get("requests", []) if item["code_type"] != "NAV_STATUS"]
            context["mappings"] = [item for item in context.get("mappings", []) if item["code_type"] != "NAV_STATUS"]
    version = next((context["version"] for context in contexts if context.get("version")), None)
    return {
        "version": version,
        "requests": [item for context in contexts for item in context.get("requests", [])],
        "mappings": [item for context in contexts for item in context.get("mappings", [])],
        "warnings": list(dict.fromkeys(warning for context in contexts for warning in context.get("warnings", []))),
    }


def prompt_block(context: dict[str, Any]) -> str:
    if not context.get("requests"):
        return "无。本次问题不涉及已绑定的编码字段。"
    lines: list[str] = []
    for request in context["requests"]:
        allowed = [
            item for item in context["mappings"]
            if item["table"] == request["table"]
            and item["column"] == request["column"]
            and item["code_type"] == request["code_type"]
        ]
        lines.extend(
            [
                f'- 表：{request["table"]}',
                f'  字段：{request["column"]}',
                f'  编码类型：{request["code_type"]}',
                f'  用途：{request["purpose"]}',
                f'  用户描述：{request["mention"] or "未指定具体标签"}',
                "  允许使用的映射：" + "；".join(f'{item["description"]}={item["code_value"]}' for item in allowed),
            ]
        )
    return "\n".join(lines)


def validate_required_filters(sql: str, context: dict[str, Any]) -> None:
    where_clause = re.split(r"\b(?:GROUP\s+BY|ORDER\s+BY|LIMIT)\b", re.split(r"\bWHERE\b", sql, maxsplit=1, flags=re.I)[-1], maxsplit=1, flags=re.I)[0] if re.search(r"\bWHERE\b", sql, re.I) else ""
    for request in context.get("requests", []):
        column_pattern = rf'["`\[]?{re.escape(request["column"])}["`\]]?'
        table_pattern = rf'["`\[]?{re.escape(request["table"])}["`\]]?'
        alias_matches = re.findall(
            rf'\b(?:FROM|JOIN)\s+{table_pattern}(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?', sql, re.I
        )
        aliases = [alias for alias in alias_matches if alias.lower() not in {"where", "on", "join", "left", "right", "inner", "group", "order"}]
        qualified_patterns = [rf'(?:{table_pattern}|["`\[]?{re.escape(alias)}["`\]]?)\s*\.\s*{column_pattern}' for alias in aliases]
        qualified_patterns.append(rf'{table_pattern}\s*\.\s*{column_pattern}')
        qualified_column = "(?:" + "|".join(qualified_patterns) + ")"
        multiple_tables = len({item["table"] for item in context.get("requests", [])}) > 1
        if request["purpose"] != "filter" or not request["code_values"]:
            if where_clause and re.search(column_pattern, where_clause, re.I):
                raise ValueError(f'用户描述未匹配到 {request["code_type"]} 的明确编码，禁止猜测筛选值')
            continue
        if not re.search(qualified_column if multiple_tables else column_pattern, sql, re.I):
            raise ValueError(f'生成的 SQL 未使用编码字段 {request["column"]}')
        if not any(re.search(rf"(?<![\w.])['\"]?{re.escape(value)}['\"]?(?![\w.])", sql) for value in request["code_values"]):
            raise ValueError(f'生成的 SQL 未使用“{request["mention"]}”对应的真实编码')


def translate_result(
    columns: list[str], rows: list[dict[str, Any]], context: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    by_column: dict[str, dict[str, str]] = {}
    scoped: dict[str, dict[str, dict[str, str]]] = {}
    for item in context.get("mappings", []):
        by_column.setdefault(f'{item["table"]}__{item["column"]}', {})[item["code_value"]] = item["description"]
        scoped.setdefault(item["column"], {}).setdefault(item["table"], {})[item["code_value"]] = item["description"]
    for column, table_mappings in scoped.items():
        unique = {json.dumps(mapping, ensure_ascii=False, sort_keys=True) for mapping in table_mappings.values()}
        if len(unique) == 1:
            by_column[column] = next(iter(table_mappings.values()))
    if not by_column:
        return rows, []
    warnings: set[str] = set()
    translated: list[dict[str, Any]] = []
    for row in rows:
        output = dict(row)
        for column in columns:
            if column not in by_column or output.get(column) is None:
                continue
            raw = _normalize_code(output[column])
            label = by_column[column].get(raw)
            if label is None:
                warnings.add(f'{column} 的编码 {raw} 在当前字典版本中没有映射')
            else:
                output[column] = label
        translated.append(output)
    return translated, sorted(warnings)


def context_digest(context: dict[str, Any]) -> str:
    compact = {
        "version": (context.get("version") or {}).get("id"),
        "requests": context.get("requests", []),
        "mappings": context.get("mappings", []),
    }
    return hashlib.sha256(json.dumps(compact, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
