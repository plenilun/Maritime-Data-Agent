from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "smart_query.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                table_name TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                columns_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terms (
                id TEXT PRIMARY KEY,
                term TEXT NOT NULL,
                definition TEXT NOT NULL,
                synonyms TEXT NOT NULL DEFAULT '',
                dataset_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_terms_dataset ON terms(dataset_id);
            """
        )


def safe_identifier(name: str, prefix: str = "dataset") -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return f"{cleaned[:32]}_{uuid.uuid4().hex[:8]}"


def serialize_dataset(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["columns"] = json.loads(item.pop("columns_json"))
    return item


def list_datasets() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
    return [serialize_dataset(row) for row in rows]


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return serialize_dataset(row) if row else None


def save_dataset(
    dataset_id: str,
    name: str,
    table_name: str,
    source_file: str,
    row_count: int,
    columns: list[dict[str, str]],
) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dataset_id, name, table_name, source_file, row_count, json.dumps(columns, ensure_ascii=False), utc_now()),
        )


def delete_dataset(dataset_id: str) -> bool:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return False
    with connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{dataset["table_name"]}"')
        conn.execute("DELETE FROM terms WHERE dataset_id = ?", (dataset_id,))
        conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    return True


def list_terms(dataset_id: str | None = None, query: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM terms WHERE 1=1"
    params: list[Any] = []
    if dataset_id:
        sql += " AND (dataset_id = ? OR dataset_id IS NULL)"
        params.append(dataset_id)
    if query.strip():
        sql += " AND (term LIKE ? OR definition LIKE ? OR synonyms LIKE ?)"
        keyword = f"%{query.strip()}%"
        params.extend([keyword, keyword, keyword])
    sql += " ORDER BY created_at DESC"
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def add_term(term: str, definition: str, synonyms: str, dataset_id: str | None) -> dict[str, Any]:
    item = {
        "id": uuid.uuid4().hex,
        "term": term.strip(),
        "definition": definition.strip(),
        "synonyms": synonyms.strip(),
        "dataset_id": dataset_id,
        "created_at": utc_now(),
    }
    with connection() as conn:
        conn.execute(
            "INSERT INTO terms VALUES (:id, :term, :definition, :synonyms, :dataset_id, :created_at)", item
        )
    return item


def add_terms(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert a validated batch of terms in a single transaction."""
    now = utc_now()
    rows = [
        {
            "id": uuid.uuid4().hex,
            "term": str(item["term"]).strip(),
            "definition": str(item["definition"]).strip(),
            "synonyms": str(item.get("synonyms", "")).strip(),
            "dataset_id": item.get("dataset_id"),
            "created_at": now,
        }
        for item in items
    ]
    with connection() as conn:
        conn.executemany(
            "INSERT INTO terms VALUES (:id, :term, :definition, :synonyms, :dataset_id, :created_at)", rows
        )
    return rows


def delete_term(term_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM terms WHERE id = ?", (term_id,))
    return cursor.rowcount > 0
