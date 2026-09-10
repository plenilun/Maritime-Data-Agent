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
            CREATE TABLE IF NOT EXISTS dataset_relationships (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                left_dataset_id TEXT NOT NULL,
                left_field TEXT NOT NULL,
                right_dataset_id TEXT NOT NULL,
                right_field TEXT NOT NULL,
                meaning TEXT NOT NULL,
                left_grain TEXT NOT NULL,
                right_grain TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(left_dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                FOREIGN KEY(right_dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                CHECK(left_dataset_id <> right_dataset_id)
            );
            CREATE INDEX IF NOT EXISTS idx_relationships_datasets
                ON dataset_relationships(left_dataset_id, right_dataset_id, enabled);
            CREATE TABLE IF NOT EXISTS term_embeddings (
                term_id TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(term_id) REFERENCES terms(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS code_dictionary_versions (
                id TEXT PRIMARY KEY,
                version_number INTEGER NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_code_versions_one_enabled
                ON code_dictionary_versions(enabled) WHERE enabled = 1;

            CREATE TABLE IF NOT EXISTS code_dictionary_entries (
                id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                code_type TEXT NOT NULL,
                code_value TEXT NOT NULL,
                description TEXT NOT NULL,
                synonyms TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(version_id) REFERENCES code_dictionary_versions(id) ON DELETE CASCADE,
                UNIQUE(version_id, code_type, code_value)
            );
            CREATE INDEX IF NOT EXISTS idx_code_entries_lookup
                ON code_dictionary_entries(version_id, code_type, code_value);

            CREATE TABLE IF NOT EXISTS code_field_bindings (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                code_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                UNIQUE(dataset_id, table_name, column_name)
            );
            CREATE INDEX IF NOT EXISTS idx_code_bindings_dataset
                ON code_field_bindings(dataset_id, table_name, enabled);
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


def preview_dataset(dataset_id: str, limit: int = 100) -> dict[str, Any] | None:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    safe_limit = max(1, min(limit, 200))
    table_name = dataset["table_name"].replace('"', '""')
    with connection() as conn:
        rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT ?', (safe_limit,)).fetchall()
    return {
        "dataset": dataset,
        "columns": [column["name"] for column in dataset["columns"]],
        "rows": [dict(row) for row in rows],
        "limit": safe_limit,
        "truncated": dataset["row_count"] > len(rows),
    }


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


def rename_dataset(dataset_id: str, name: str) -> dict[str, Any] | None:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("数据表名称不能为空")
    with connection() as conn:
        row = conn.execute("SELECT id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not row:
            return None
        duplicate = conn.execute(
            "SELECT id FROM datasets WHERE id <> ? AND lower(name) = lower(?)",
            (dataset_id, cleaned_name),
        ).fetchone()
        if duplicate:
            raise ValueError("数据表名称已存在")
        conn.execute("UPDATE datasets SET name = ? WHERE id = ?", (cleaned_name, dataset_id))
    return get_dataset(dataset_id)


def delete_dataset(dataset_id: str) -> bool:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return False
    with connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{dataset["table_name"]}"')
        conn.execute("DELETE FROM dataset_relationships WHERE left_dataset_id = ? OR right_dataset_id = ?", (dataset_id, dataset_id))
        conn.execute("DELETE FROM term_embeddings WHERE term_id IN (SELECT id FROM terms WHERE dataset_id = ?)", (dataset_id,))
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
            """
            INSERT INTO terms (id, term, definition, synonyms, dataset_id, created_at)
            VALUES (:id, :term, :definition, :synonyms, :dataset_id, :created_at)
            """,
            item,
        )
        conn.execute("DELETE FROM term_embeddings WHERE term_id = ?", (item["id"],))
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
            """
            INSERT INTO terms (id, term, definition, synonyms, dataset_id, created_at)
            VALUES (:id, :term, :definition, :synonyms, :dataset_id, :created_at)
            """,
            rows,
        )
    return rows


def update_term(
    term_id: str,
    term: str,
    definition: str,
    synonyms: str,
    dataset_id: str | None,
) -> dict[str, Any] | None:
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE terms
            SET term = ?, definition = ?, synonyms = ?, dataset_id = ?
            WHERE id = ?
            """,
            (term.strip(), definition.strip(), synonyms.strip(), dataset_id, term_id),
        )
        if cursor.rowcount == 0:
            return None
        conn.execute("DELETE FROM term_embeddings WHERE term_id = ?", (term_id,))
        row = conn.execute("SELECT * FROM terms WHERE id = ?", (term_id,)).fetchone()
        return dict(row) if row else None


def delete_term(term_id: str) -> bool:
    with connection() as conn:
        conn.execute("DELETE FROM term_embeddings WHERE term_id = ?", (term_id,))
        cursor = conn.execute("DELETE FROM terms WHERE id = ?", (term_id,))
    return cursor.rowcount > 0
