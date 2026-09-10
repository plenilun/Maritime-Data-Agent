from __future__ import annotations

import json
import re
import uuid
from typing import Any

from . import db

MAX_RECALL_CANDIDATES = 200


def init_memory() -> None:
    with db.connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_query_memory (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                answer_status TEXT NOT NULL,
                intent_type TEXT NOT NULL DEFAULT '',
                dataset_ids_json TEXT NOT NULL DEFAULT '[]',
                table_names_json TEXT NOT NULL DEFAULT '[]',
                sql TEXT NOT NULL DEFAULT '',
                reasoning_summary TEXT NOT NULL DEFAULT '',
                answer_preview TEXT NOT NULL DEFAULT '',
                row_count INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0 CHECK(truncated IN (0, 1)),
                sql_repaired INTEGER NOT NULL DEFAULT 0 CHECK(sql_repaired IN (0, 1)),
                trace_id TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_memory_created
                ON agent_query_memory(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_memory_intent
                ON agent_query_memory(intent_type, answer_status, created_at DESC);
            """
        )


def normalize_question(question: str) -> str:
    text = question.casefold().strip()
    text = re.sub(r"[\s,，。.!！?？:：;；、\"'`]+", "", text)
    return text


def _ngrams(text: str, size: int = 2) -> set[str]:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item is not None]


def _route_dataset_ids(route_info: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    dataset_id = route_info.get("dataset_id")
    if dataset_id:
        ids.append(str(dataset_id))
    for table in route_info.get("multi_table_context", {}).get("tables", []):
        if table.get("dataset_id"):
            ids.append(str(table["dataset_id"]))
    return list(dict.fromkeys(ids))


def _route_table_names(route_info: dict[str, Any]) -> list[str]:
    names = [str(item) for item in route_info.get("table_names", []) if item]
    for table in route_info.get("multi_table_context", {}).get("tables", []):
        if table.get("table_name"):
            names.append(str(table["table_name"]))
    if route_info.get("table_name"):
        names.append(str(route_info["table_name"]))
    return list(dict.fromkeys(names))


def _score_memory(question: str, route_info: dict[str, Any] | None, row: dict[str, Any]) -> float:
    query_norm = normalize_question(question)
    memory_norm = str(row.get("normalized_question", ""))
    if not query_norm or not memory_norm:
        return 0.0
    if query_norm == memory_norm:
        return 1.0

    query_grams = _ngrams(query_norm)
    memory_grams = _ngrams(memory_norm)
    lexical_score = len(query_grams & memory_grams) / max(len(query_grams | memory_grams), 1)
    score = lexical_score

    route = route_info or {}
    if route.get("intent_type") and route.get("intent_type") == row.get("intent_type"):
        score += 0.2

    route_tables = set(_route_table_names(route))
    memory_tables = set(_json_list(str(row.get("table_names_json", "[]"))))
    if route_tables and memory_tables:
        score += 0.15 * len(route_tables & memory_tables) / max(len(route_tables | memory_tables), 1)

    route_datasets = set(_route_dataset_ids(route))
    memory_datasets = set(_json_list(str(row.get("dataset_ids_json", "[]"))))
    if route_datasets and memory_datasets:
        score += 0.1 * len(route_datasets & memory_datasets) / max(len(route_datasets | memory_datasets), 1)

    return round(min(score, 1.0), 4)


def score_memory_candidate(question: str, route_info: dict[str, Any] | None, row: dict[str, Any]) -> float:
    return _score_memory(question, route_info, row)


def recall_memories(
    question: str,
    route_info: dict[str, Any] | None = None,
    *,
    limit: int = 3,
    include_unsuccessful: bool = False,
) -> list[dict[str, Any]]:
    init_memory()
    sql = "SELECT * FROM agent_query_memory"
    params: list[Any] = []
    if not include_unsuccessful:
        sql += " WHERE answer_status = ?"
        params.append("success")
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(MAX_RECALL_CANDIDATES)
    with db.connection() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]

    scored: list[dict[str, Any]] = []
    for row in rows:
        score = _score_memory(question, route_info, row)
        if score < 0.12:
            continue
        scored.append(
            {
                "id": row["id"],
                "question": row["question"],
                "answer_status": row["answer_status"],
                "intent_type": row["intent_type"],
                "dataset_ids": _json_list(row["dataset_ids_json"]),
                "table_names": _json_list(row["table_names_json"]),
                "sql": row["sql"],
                "reasoning_summary": row["reasoning_summary"],
                "answer_preview": row["answer_preview"],
                "row_count": row["row_count"],
                "truncated": bool(row["truncated"]),
                "sql_repaired": bool(row["sql_repaired"]),
                "trace_id": row["trace_id"],
                "created_at": row["created_at"],
                "score": score,
            }
        )
    scored.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
    return scored[: max(0, limit)]


def save_memory(
    *,
    question: str,
    answer_status: str,
    route_info: dict[str, Any] | None = None,
    sql: str = "",
    reasoning_summary: str = "",
    answer: str = "",
    rows: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    trace_record: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    error_message: str = "",
) -> str:
    init_memory()
    route = route_info or {}
    trace = trace_record or {}
    values = {
        "id": uuid.uuid4().hex,
        "question": question.strip(),
        "normalized_question": normalize_question(question),
        "answer_status": answer_status,
        "intent_type": str(route.get("intent_type", "")),
        "dataset_ids_json": json.dumps(_route_dataset_ids(route), ensure_ascii=False),
        "table_names_json": json.dumps(_route_table_names(route), ensure_ascii=False),
        "sql": sql,
        "reasoning_summary": reasoning_summary,
        "answer_preview": answer.strip()[:600],
        "row_count": len(rows or []),
        "truncated": 1 if truncated else 0,
        "sql_repaired": 1 if (metrics or {}).get("sql_repaired") else 0,
        "trace_id": str(trace.get("query_id") or trace.get("trace_id") or ""),
        "error_message": error_message.strip()[:1000],
        "created_at": db.utc_now(),
    }
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_query_memory (
                id, question, normalized_question, answer_status, intent_type,
                dataset_ids_json, table_names_json, sql, reasoning_summary,
                answer_preview, row_count, truncated, sql_repaired, trace_id,
                error_message, created_at
            )
            VALUES (
                :id, :question, :normalized_question, :answer_status, :intent_type,
                :dataset_ids_json, :table_names_json, :sql, :reasoning_summary,
                :answer_preview, :row_count, :truncated, :sql_repaired, :trace_id,
                :error_message, :created_at
            )
            """,
            values,
        )
    return values["id"]


def list_memories(limit: int = 20) -> list[dict[str, Any]]:
    init_memory()
    safe_limit = max(1, min(limit, 100))
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_query_memory ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "question": row["question"],
            "answer_status": row["answer_status"],
            "intent_type": row["intent_type"],
            "dataset_ids": _json_list(row["dataset_ids_json"]),
            "table_names": _json_list(row["table_names_json"]),
            "sql": row["sql"],
            "reasoning_summary": row["reasoning_summary"],
            "answer_preview": row["answer_preview"],
            "row_count": row["row_count"],
            "truncated": bool(row["truncated"]),
            "sql_repaired": bool(row["sql_repaired"]),
            "trace_id": row["trace_id"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
