from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from typing import Any

from .db import DB_PATH

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|pragma|reindex|analyze|transaction|commit|rollback)\b", re.I)


def validate_sql(sql: str, allowed_table: str | None = None, *,
                 allowed_tables: Iterable[str] | None = None) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise ValueError("模型未生成只读 SELECT 查询")
    if FORBIDDEN.search(cleaned) or ";" in cleaned:
        raise ValueError("SQL 安全校验未通过：仅允许单条只读查询")
    allowed = {item.lower() for item in (allowed_tables or [])}
    if allowed_table:
        allowed.add(allowed_table.lower())
    if not allowed:
        raise ValueError("SQL 安全校验缺少允许访问的数据表")
    referenced = re.findall(r'\b(?:from|join)\s+(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|([\w\u4e00-\u9fff]+))', cleaned, re.I)
    tables = {next(part for part in match if part) for match in referenced}
    cte_names = {next(part for part in match if part).lower() for match in re.findall(
        r'(?:\bwith|,)\s*(?:"([^"]+)"|`([^`]+)`|\[([^]]+)\]|([\w\u4e00-\u9fff]+))\s+as\s*\(', cleaned, re.I)}
    tables = {table for table in tables if table.lower() not in cte_names}
    if not tables or any(table.lower() not in allowed for table in tables):
        raise ValueError("SQL 引用了当前多表上下文之外的数据表")
    if re.search(r"\bcross\s+join\b", cleaned, re.I):
        raise ValueError("禁止 CROSS JOIN")
    return cleaned


def validate_question_semantics(sql: str, question: str, multi_table_context: dict[str, Any] | None) -> None:
    if not multi_table_context or not multi_table_context.get("enabled"):
        return
    if any(cue in re.sub(r"\s+", "", question.lower()) for cue in ("多少艘", "几艘", "船舶数", "船只数")):
        if re.search(r"\bcount\s*\(", sql, re.I) and not re.search(r"\bcount\s*\(\s*distinct\b", sql, re.I):
            raise ValueError("多表查询询问船舶数量时必须使用 COUNT(DISTINCT ...) 去重")


def execute_readonly(sql: str) -> tuple[list[str], list[dict[str, Any]], bool]:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description or []]
    except sqlite3.Error as exc:
        raise ValueError(f"SQL 执行失败：{exc}") from exc
    finally:
        conn.close()
    return columns, [dict(row) for row in rows], False


def get_data_update_time(table_name: str, column_names: list[str]) -> str | None:
    available = [item for item in ("update_time", "received_at", "event_time", "risk_time", "violation_time") if item in column_names]
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
