from __future__ import annotations

import hashlib
import json
import math
import os
import re
from functools import lru_cache
from typing import Any, Iterable

from . import db

MODEL_NAME = os.getenv("TERM_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
TOP_K = max(1, int(os.getenv("TERM_RETRIEVAL_TOP_K", "8")))
SEMANTIC_THRESHOLD = float(os.getenv("TERM_SEMANTIC_THRESHOLD", "0.45"))
SEMANTIC_ENABLED = os.getenv("TERM_SEMANTIC_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _split_synonyms(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，、;；|\n]+", value) if part.strip()]


def _document(item: dict[str, Any]) -> str:
    return f"术语：{item['term']}；同义词：{item.get('synonyms') or '无'}；业务定义：{item['definition']}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else -1.0


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    cache_dir = os.getenv("TERM_EMBEDDING_CACHE_DIR", str(db.DATA_DIR / "models"))
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=cache_dir)


def _embed(texts: Iterable[str]) -> list[list[float]]:
    return [vector.tolist() for vector in _model().embed(list(texts))]


def _scope_terms(dataset_id: str) -> list[dict[str, Any]]:
    # list_terms(dataset_id) already limits the candidates to this dataset and globals.
    # When both scopes define the same term, the dataset-specific definition wins.
    selected: dict[str, dict[str, Any]] = {}
    for item in db.list_terms(dataset_id):
        key = str(item["term"]).strip().casefold()
        current = selected.get(key)
        if current is None or (item.get("dataset_id") == dataset_id and current.get("dataset_id") is None):
            selected[key] = item
    return list(selected.values())


def _load_or_build_vectors(items: list[dict[str, Any]]) -> dict[str, list[float]]:
    documents = {item["id"]: _document(item) for item in items}
    hashes = {term_id: _content_hash(text) for term_id, text in documents.items()}
    vectors: dict[str, list[float]] = {}
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT term_id, content_hash, vector_json FROM term_embeddings WHERE model_name = ?",
            (MODEL_NAME,),
        ).fetchall()
    for row in rows:
        if row["term_id"] in hashes and row["content_hash"] == hashes[row["term_id"]]:
            vectors[row["term_id"]] = json.loads(row["vector_json"])

    missing = [item for item in items if item["id"] not in vectors]
    if missing:
        embedded = _embed(documents[item["id"]] for item in missing)
        with db.connection() as conn:
            for item, vector in zip(missing, embedded):
                vectors[item["id"]] = vector
                conn.execute(
                    "INSERT OR REPLACE INTO term_embeddings VALUES (?, ?, ?, ?, ?)",
                    (item["id"], MODEL_NAME, hashes[item["id"]], json.dumps(vector), db.utc_now()),
                )
    return vectors


def retrieve_terms(question: str, dataset_id: str, top_k: int = TOP_K) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = _scope_terms(dataset_id)
    matched: dict[str, dict[str, Any]] = {}
    normalized_question = question.casefold()

    for item in candidates:
        names = [str(item["term"]).strip(), *_split_synonyms(str(item.get("synonyms", "")))]
        keyword = next((name for name in names if name and name.casefold() in normalized_question), None)
        if keyword:
            matched[item["id"]] = dict(item, match_sources=["keyword"], matched_keyword=keyword, relevance=1.0)

    method = "keyword"
    warning = None
    if SEMANTIC_ENABLED and candidates:
        try:
            term_vectors = _load_or_build_vectors(candidates)
            question_vector = _embed([question])[0]
            for item in candidates:
                score = _cosine(question_vector, term_vectors[item["id"]])
                if score < SEMANTIC_THRESHOLD:
                    continue
                result = matched.get(item["id"], dict(item, match_sources=[], relevance=score))
                result["match_sources"] = list(dict.fromkeys([*result["match_sources"], "semantic"]))
                result["semantic_score"] = round(score, 4)
                result["relevance"] = max(float(result["relevance"]), score)
                matched[item["id"]] = result
            method = "hybrid"
        except Exception as exc:
            method = "keyword_fallback"
            warning = f"向量语义检索不可用，已回退关键词检索：{type(exc).__name__}"

    results = sorted(
        matched.values(),
        key=lambda item: (float(item.get("relevance", 0)), item.get("dataset_id") == dataset_id),
        reverse=True,
    )[:top_k]
    trace = {
        "method": method,
        "embedding_model": MODEL_NAME if SEMANTIC_ENABLED else None,
        "semantic_threshold": SEMANTIC_THRESHOLD if SEMANTIC_ENABLED else None,
        "candidate_count": len(candidates),
        "matched_count": len(results),
        "top_k": top_k,
    }
    if warning:
        trace["warning"] = warning
    return results, trace
