from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from typing import Any

from .db import DB_PATH

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|pragma|reindex|analyze|transaction|commit|rollback)\b",
    re.I,
)


def validate_sql(
    sql: str,
    allowed_table: str | None = None,
    *,
    allowed_tables: Iterable[str] | None = None,
) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise ValueError("模型未生成只读 SELECT 查询")
    if FORBIDDEN.search(cleaned) or ";" in cleaned:
        raise ValueError("SQL 安全校验未通过：仅允许单条只读查询")
    if allowed_tables is None:
        if allowed_table is None:
            raise ValueError("SQL 安全校验缺少允许访问的数据表")
        allowed = {allowed_table.lower()}
    else:
        allowed = {table.lower() for table in allowed_tables}
        if allowed_table:
            allowed.add(allowed_table.lower())
    if not allowed:
        raise ValueError("SQL 安全校验缺少允许访问的数据表")
    referenced = re.findall(r'\b(?:from|join)\s+(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|([\w\u4e00-\u9fff]+))', cleaned, re.I)
    tables = {next(part for part in match if part) for match in referenced}
    cte_names = {
        next(part for part in match if part).lower()
        for match in re.findall(r'(?:\bwith|,)\s*(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|([\w\u4e00-\u9fff]+))\s+as\s*\(', cleaned, re.I)
    }
    tables = {table for table in tables if table.lower() not in cte_names}
    if not tables or any(table.lower() not in allowed for table in tables):
        raise ValueError("SQL 引用了当前数据集之外的表")
    return cleaned


def execute_readonly(sql: str, max_rows: int = 200) -> tuple[list[str], list[dict[str, Any]], bool]:
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        columns = [item[0] for item in cursor.description or []]
    except sqlite3.Error as exc:
        raise ValueError(f"SQL 执行失败：{exc}") from exc
    finally:
        conn.close()
    truncated = len(rows) > max_rows
    return columns, [dict(row) for row in rows[:max_rows]], truncated


def get_data_update_time(table_name: str, column_names: list[str]) -> str | None:
    candidates = ["update_time", "received_at", "event_time", "risk_time", "violation_time"]
    available = [item for item in candidates if item in column_names]
    if not available:
        return None
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        for field in available:
            value = conn.execute(f'SELECT MAX("{field}") FROM "{table_name}"').fetchone()[0]
            if value:
                return str(value)
    finally:
        conn.close()
    return None
