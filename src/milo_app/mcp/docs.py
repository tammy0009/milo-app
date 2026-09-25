"""Full-text index over the BIOVIA documentation dumps (Materials Studio + Discovery Studio)."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from milo_app.config import get_settings

SOURCES = {
    "materials_studio": "MATERIALS_STUDIO_MASTER_AGENT_KNOWLEDGE.md",
    "discovery_studio": "BIOVIA_DISCOVERY_STUDIO_MASTER_AGENT_KNOWLEDGE.md",
}
DOC_SPLIT = re.compile(r"^=+\s*\nDOCUMENT \d+\s*\n=+\s*$", re.MULTILINE)
WORD = re.compile(r"[A-Za-z0-9_]+")


def _db_path() -> Path:
    return get_settings().data_dir / "docs.sqlite"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _meta(text: str, label: str) -> str:
    match = re.search(rf"^{label}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def build_index() -> dict[str, int]:
    docs_dir = get_settings().docs_dir
    counts: dict[str, int] = {}
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS docs")
        conn.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "product UNINDEXED, title, source_file UNINDEXED, section UNINDEXED, body, tokenize='porter unicode61')"
        )
        for product, filename in SOURCES.items():
            path = docs_dir / filename
            if not path.is_file():
                counts[product] = 0
                continue
            rows = []
            for chunk in DOC_SPLIT.split(path.read_text(encoding="utf-8", errors="replace")):
                chunk = chunk.strip()
                if not chunk:
                    continue
                title = next((line[2:].strip() for line in chunk.splitlines() if line.startswith("# ")), chunk[:80])
                rows.append((product, title, _meta(chunk, "Source file"), _meta(chunk, "Source section"), chunk))
            conn.executemany("INSERT INTO docs VALUES (?, ?, ?, ?, ?)", rows)
            counts[product] = len(rows)
    return counts


def _ensure_index() -> None:
    if not _db_path().is_file():
        build_index()


def search(query: str, product: str = "all", limit: int = 8) -> list[dict[str, Any]]:
    _ensure_index()
    words = WORD.findall(query)
    if not words:
        return []
    where = "docs MATCH ?"
    params: list[Any] = []
    if product in SOURCES:
        where += " AND product = ?"
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        for joiner in (" AND ", " OR "):  # precise first, then broad
            params = [joiner.join(f'"{w}"' for w in words)]
            if product in SOURCES:
                params.append(product)
            rows = conn.execute(
                f"SELECT rowid, product, title, source_file, snippet(docs, 4, '[', ']', ' … ', 24) "
                f"FROM docs WHERE {where} ORDER BY bm25(docs, 0, 5.0, 0, 0, 1.0) LIMIT ?",
                [*params, limit],
            ).fetchall()
            if rows:
                results = [
                    {"doc_id": r[0], "product": r[1], "title": r[2], "source_file": r[3], "snippet": r[4]}
                    for r in rows
                ]
                break
    return results


def get_doc(doc_id: int, offset: int = 0, max_chars: int = 12000) -> dict[str, Any] | None:
    _ensure_index()
    with _connect() as conn:
        row = conn.execute("SELECT product, title, source_file, body FROM docs WHERE rowid = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    body = row[3]
    return {
        "doc_id": doc_id,
        "product": row[0],
        "title": row[1],
        "source_file": row[2],
        "text": body[offset : offset + max_chars],
        "offset": offset,
        "total_chars": len(body),
        "truncated": offset + max_chars < len(body),
    }
