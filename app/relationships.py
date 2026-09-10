from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from . import db


def _invalidate_sql_cache() -> None:
    from .llm import clear_sql_cache

    clear_sql_cache()


def _dataset_field(dataset_id: str, field: str) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset = db.get_dataset(dataset_id)
    if not dataset:
        raise ValueError("数据集不存在")
    column = next((item for item in dataset["columns"] if item["name"] == field), None)
    if not column:
        raise ValueError(f"字段“{field}”不存在于数据集“{dataset['name']}”")
    return dataset, column


def _validate(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if payload["left_dataset_id"] == payload["right_dataset_id"]:
        raise ValueError("关系两侧必须是不同数据集")
    left, _ = _dataset_field(payload["left_dataset_id"], payload["left_field"])
    right, _ = _dataset_field(payload["right_dataset_id"], payload["right_field"])
    return left, right


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    left = db.get_dataset(item["left_dataset_id"])
    right = db.get_dataset(item["right_dataset_id"])
    item["left_dataset_name"] = left["name"] if left else "已删除数据集"
    item["right_dataset_name"] = right["name"] if right else "已删除数据集"
    item["left_table_name"] = left["table_name"] if left else None
    item["right_table_name"] = right["table_name"] if right else None
    return item


def list_relationships(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM dataset_relationships"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY updated_at DESC"
    with db.connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [_serialize(row) for row in rows]


def get_relationship(relationship_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM dataset_relationships WHERE id = ?", (relationship_id,)).fetchone()
    return _serialize(row) if row else None


# 配置关系功能暂时注释掉，待multitable.py实现后启用
# def save_relationship(payload: dict[str, Any], relationship_id: str | None = None) -> dict[str, Any]:
#     _validate(payload)
#     now = db.utc_now()
#     with db.connection() as conn:
#         if relationship_id:
#             cursor = conn.execute(
#                 """UPDATE dataset_relationships SET name=?, left_dataset_id=?, left_field=?,
#                 right_dataset_id=?, right_field=?, meaning=?, left_grain=?, right_grain=?, enabled=?, updated_at=?
#                 WHERE id=?""",
#                 (payload["name"].strip(), payload["left_dataset_id"], payload["left_field"],
#                  payload["right_dataset_id"], payload["right_field"], payload["meaning"].strip(),
#                  payload["left_grain"].strip(), payload["right_grain"].strip(), int(payload.get("enabled", True)),
#                  now, relationship_id),
#             )
#             if cursor.rowcount == 0:
#                 raise LookupError("关系不存在")
#         else:
#             relationship_id = uuid.uuid4().hex
#             conn.execute(
#                 "INSERT INTO dataset_relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
#                 (relationship_id, payload["name"].strip(), payload["left_dataset_id"], payload["left_field"],
#                  payload["right_dataset_id"], payload["right_field"], payload["meaning"].strip(),
#                  payload["left_grain"].strip(), payload["right_grain"].strip(), int(payload.get("enabled", True)),
#                  now, now),
#             )
#     _invalidate_sql_cache()
#     return get_relationship(relationship_id) or {}


# 配置关系功能暂时注释掉，待multitable.py实现后启用
# def set_enabled(relationship_id: str, enabled: bool) -> dict[str, Any] | None:
#     relationship = get_relationship(relationship_id)
#     if not relationship:
#         return None
#     if enabled:
#         _validate(relationship)
#     with db.connection() as conn:
#         conn.execute("UPDATE dataset_relationships SET enabled=?, updated_at=? WHERE id=?",
#                      (int(enabled), db.utc_now(), relationship_id))
#     _invalidate_sql_cache()
#     return get_relationship(relationship_id)


# 配置关系功能暂时注释掉，待multitable.py实现后启用
# def delete_relationship(relationship_id: str) -> bool:
#     with db.connection() as conn:
#         cursor = conn.execute("DELETE FROM dataset_relationships WHERE id = ?", (relationship_id,))
#     if cursor.rowcount:
#         _invalidate_sql_cache()
#     return cursor.rowcount > 0


def find_enabled(left_dataset_id: str, right_dataset_id: str) -> dict[str, Any] | None:
    for item in list_relationships(enabled_only=True):
        if {item["left_dataset_id"], item["right_dataset_id"]} == {left_dataset_id, right_dataset_id}:
            try:
                _validate(item)
            except ValueError:
                continue
            return item
    return None


def inspect_relationship(relationship_id: str) -> dict[str, Any]:
    item = get_relationship(relationship_id)
    if not item:
        raise LookupError("关系不存在")
    left, left_column = _dataset_field(item["left_dataset_id"], item["left_field"])
    right, right_column = _dataset_field(item["right_dataset_id"], item["right_field"])
    lt = left["table_name"].replace('"', '""'); rt = right["table_name"].replace('"', '""')
    lf = item["left_field"].replace('"', '""'); rf = item["right_field"].replace('"', '""')
    with db.connection() as conn:
        def stats(table: str, field: str) -> dict[str, int]:
            row = conn.execute(f'''SELECT COUNT(*) total_rows,
                COALESCE(SUM(CASE WHEN "{field}" IS NULL THEN 1 ELSE 0 END), 0) null_rows,
                COUNT(DISTINCT "{field}") distinct_non_null
                FROM "{table}"''').fetchone()
            duplicates = conn.execute(f'''SELECT COUNT(*) FROM (
                SELECT "{field}" FROM "{table}" WHERE "{field}" IS NOT NULL
                GROUP BY "{field}" HAVING COUNT(*) > 1)''').fetchone()[0]
            return {**dict(row), "duplicate_keys": duplicates}
        left_stats = stats(lt, lf); right_stats = stats(rt, rf)
        matched = conn.execute(f'''SELECT COUNT(*) FROM (
            SELECT DISTINCT l."{lf}" FROM "{lt}" l
            JOIN "{rt}" r ON l."{lf}" = r."{rf}" WHERE l."{lf}" IS NOT NULL)''').fetchone()[0]
    return {
        "relationship": item,
        "left": {"field_type": left_column["type"], **left_stats},
        "right": {"field_type": right_column["type"], **right_stats},
        "matched_distinct_keys": matched,
        "type_compatible": left_column["type"].upper() == right_column["type"].upper(),
        "advisory": "检查结果仅反映数据特征，不代表业务关系已经确认。",
    }
