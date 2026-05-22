"""Module vector_db — discover & query local RAG vector stores (R&D #10.1).

Surfaces the 'hidden GPU consumer' on LLM rigs : local RAG stacks (ChromaDB /
Qdrant / Weaviate / pgvector). The dashboard can then correlate VRAM/util
spikes with retrieval bursts.

stdlib only :
  - Chroma / Qdrant / Weaviate : HTTP GET via urllib
  - pgvector : `psql -At -c '<query>'` subprocess

All probes are best-effort + silent on failure (typical case : DB not
running) so the module costs ~0 when nothing's there.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Optional


NAME = "vector_db"

_HTTP_TIMEOUT = 2.0
_PSQL_TIMEOUT = 3.0


# ─── Chroma (HTTP) ────────────────────────────────────────────────────────


def probe_chroma(host: str = "localhost", port: int = 8000) -> Optional[dict]:
    """Probe a Chroma server on host:port. Returns dict or None on failure."""
    url = f"http://{host}:{port}/api/v1/collections"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            if r.status != 200:
                return None
            collections = json.loads(r.read().decode("utf-8"))
        if not isinstance(collections, list):
            return None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None

    out: list = []
    total_items = 0
    for col in collections:
        if not isinstance(col, dict):
            continue
        name = col.get("name", "?")
        cid = col.get("id", "")
        # Per-collection count : separate endpoint
        count = _chroma_count(host, port, cid)
        if count is not None:
            total_items += count
        out.append({"name": name, "id": cid, "count": count,
                    "metadata": col.get("metadata", {})})
    return {
        "engine": "chroma",
        "url": f"http://{host}:{port}",
        "collections_count": len(out),
        "items_total": total_items,
        "collections": out,
    }


def _chroma_count(host: str, port: int, collection_id: str) -> Optional[int]:
    """Chroma /api/v1/collections/{id}/count → integer."""
    if not collection_id:
        return None
    url = f"http://{host}:{port}/api/v1/collections/{collection_id}/count"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            if r.status != 200:
                return None
            return int(r.read().decode("utf-8").strip())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None


# ─── Qdrant (HTTP) ────────────────────────────────────────────────────────


def probe_qdrant(host: str = "localhost", port: int = 6333) -> Optional[dict]:
    """Probe Qdrant /collections endpoint. Returns dict or None."""
    url = f"http://{host}:{port}/collections"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None
    result = data.get("result", {}) if isinstance(data, dict) else {}
    cols = result.get("collections", []) if isinstance(result, dict) else []
    out: list = []
    total = 0
    for c in cols:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "?")
        info = _qdrant_collection_info(host, port, name)
        count = (info or {}).get("vectors_count") or 0
        total += count
        out.append({"name": name, "count": count,
                    "config": (info or {}).get("config", {})})
    return {
        "engine": "qdrant",
        "url": f"http://{host}:{port}",
        "collections_count": len(out),
        "items_total": total,
        "collections": out,
    }


def _qdrant_collection_info(host: str, port: int, name: str) -> Optional[dict]:
    url = f"http://{host}:{port}/collections/{name}"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read().decode("utf-8"))
            return data.get("result") if isinstance(data, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, TimeoutError):
        return None


# ─── pgvector (psql subprocess) ───────────────────────────────────────────


def probe_pgvector(dsn: str = "") -> Optional[dict]:
    """Probe a Postgres instance for pgvector tables.

    Returns dict listing tables that have a 'vector' column. Best-effort —
    if psql isn't installed or DSN is wrong, returns None.
    """
    if not dsn:
        return None

    sql = (
        "SELECT n.nspname || '.' || c.relname AS tbl, "
        "       a.attname AS col, "
        "       COALESCE(pg_relation_size(c.oid), 0) AS size_bytes "
        "FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_type t ON t.oid = a.atttypid "
        "WHERE t.typname = 'vector' AND a.attnum > 0 AND NOT a.attisdropped"
    )
    try:
        r = subprocess.run(
            ["psql", dsn, "-At", "-F", "|", "-c", sql],
            capture_output=True, text=True, timeout=_PSQL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None

    cols = []
    total_bytes = 0
    for line in r.stdout.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        try:
            sz = int(parts[2])
        except ValueError:
            sz = 0
        cols.append({"table": parts[0], "column": parts[1], "size_mib": int(sz / 1024 / 1024)})
        total_bytes += sz
    return {
        "engine": "pgvector",
        "dsn_redacted": _redact_dsn(dsn),
        "vector_columns_count": len(cols),
        "total_size_mib": int(total_bytes / 1024 / 1024),
        "columns": cols,
    }


def _redact_dsn(dsn: str) -> str:
    """Hide password in DSN string for logging."""
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1<redacted>\2", dsn)


# ─── public entry point ───────────────────────────────────────────────────


def status(cfg) -> dict:
    """Probe all configured/default vector DBs and aggregate."""
    chroma_host = cfg.get("CHROMA_HOST") or "localhost"
    try:
        chroma_port = int(cfg.get("CHROMA_PORT") or "8000")
    except ValueError:
        chroma_port = 8000
    qdrant_host = cfg.get("QDRANT_HOST") or "localhost"
    try:
        qdrant_port = int(cfg.get("QDRANT_PORT") or "6333")
    except ValueError:
        qdrant_port = 6333
    pg_dsn = cfg.get("PGVECTOR_DSN") or ""

    engines: list = []
    c = probe_chroma(chroma_host, chroma_port)
    if c:
        engines.append(c)
    q = probe_qdrant(qdrant_host, qdrant_port)
    if q:
        engines.append(q)
    p = probe_pgvector(pg_dsn) if pg_dsn else None
    if p:
        engines.append(p)
    return {
        "ok": True,
        "available": bool(engines),
        "engines_count": len(engines),
        "engines": engines,
    }
