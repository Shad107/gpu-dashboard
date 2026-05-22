"""R&D #10.1 — Vector DB watchdog tests."""
import json
import subprocess
from unittest.mock import patch, MagicMock
from gpu_dashboard.modules import vector_db as vd


class FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._body


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_redact_dsn_hides_password():
    dsn = "postgres://user:secret@localhost:5432/db"
    redacted = vd._redact_dsn(dsn)
    assert "secret" not in redacted
    assert "<redacted>" in redacted
    assert "user" in redacted


# ── Chroma ────────────────────────────────────────────────────────────────


def test_probe_chroma_returns_none_on_connection_error():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert vd.probe_chroma() is None


def test_probe_chroma_parses_collections_and_counts():
    """Mock both /collections and /count endpoints."""
    cols = [{"id": "col-abc", "name": "docs", "metadata": {}}]
    call_count = {"n": 0}
    def fake_open(url, timeout=2.0):
        call_count["n"] += 1
        if "/count" in url:
            return FakeResp(200, "42")
        return FakeResp(200, json.dumps(cols))
    with patch("urllib.request.urlopen", side_effect=fake_open):
        result = vd.probe_chroma()
    assert result is not None
    assert result["engine"] == "chroma"
    assert result["collections_count"] == 1
    assert result["items_total"] == 42
    assert result["collections"][0]["name"] == "docs"


# ── Qdrant ────────────────────────────────────────────────────────────────


def test_probe_qdrant_returns_none_on_connection_error():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert vd.probe_qdrant() is None


def test_probe_qdrant_parses_collections():
    list_body = json.dumps({"result": {"collections": [{"name": "papers"}]}})
    info_body = json.dumps({"result": {"vectors_count": 1234, "config": {}}})
    def fake_open(url, timeout=2.0):
        if url.endswith("/collections"):
            return FakeResp(200, list_body)
        return FakeResp(200, info_body)
    with patch("urllib.request.urlopen", side_effect=fake_open):
        result = vd.probe_qdrant()
    assert result["engine"] == "qdrant"
    assert result["items_total"] == 1234


# ── pgvector ──────────────────────────────────────────────────────────────


def test_probe_pgvector_empty_dsn_returns_none():
    assert vd.probe_pgvector("") is None


def test_probe_pgvector_no_psql_returns_none():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert vd.probe_pgvector("postgres://x") is None


def test_probe_pgvector_parses_vector_columns():
    """Mock psql output : 2 tables with vector columns."""
    out = "\n".join([
        "public.documents|embedding|10485760",  # 10 MiB
        "public.papers|vec|5242880",            # 5 MiB
    ])
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        result = vd.probe_pgvector("postgres://localhost/db")
    assert result["engine"] == "pgvector"
    assert result["vector_columns_count"] == 2
    # Total size = 15 MiB
    assert result["total_size_mib"] == 15
    assert result["columns"][0]["table"] == "public.documents"


def test_probe_pgvector_handles_zero_byte_tables():
    out = "public.empty|vec|0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        result = vd.probe_pgvector("postgres://localhost/db")
    assert result["vector_columns_count"] == 1
    assert result["total_size_mib"] == 0


# ── status() top-level ────────────────────────────────────────────────────


def test_status_no_engines_reachable():
    """All probes fail → available=false, engines empty."""
    from gpu_dashboard.config import Config
    cfg = Config(defaults={})
    with patch.object(vd, "probe_chroma", return_value=None), \
         patch.object(vd, "probe_qdrant", return_value=None), \
         patch.object(vd, "probe_pgvector", return_value=None):
        result = vd.status(cfg)
    assert result["available"] is False
    assert result["engines_count"] == 0


def test_status_aggregates_multiple_engines():
    from gpu_dashboard.config import Config
    cfg = Config(defaults={"PGVECTOR_DSN": "postgres://x"})
    with patch.object(vd, "probe_chroma",
                      return_value={"engine": "chroma", "items_total": 100, "collections_count": 1, "url": "...", "collections": []}), \
         patch.object(vd, "probe_qdrant",
                      return_value={"engine": "qdrant", "items_total": 200, "collections_count": 1, "url": "...", "collections": []}), \
         patch.object(vd, "probe_pgvector",
                      return_value={"engine": "pgvector", "total_size_mib": 50,
                                    "vector_columns_count": 1, "dsn_redacted": "", "columns": []}):
        result = vd.status(cfg)
    assert result["available"] is True
    assert result["engines_count"] == 3
    names = {e["engine"] for e in result["engines"]}
    assert names == {"chroma", "qdrant", "pgvector"}


def test_status_respects_cfg_ports():
    """If user sets CHROMA_PORT in config, the probe uses it."""
    from gpu_dashboard.config import Config
    cfg = Config(defaults={"CHROMA_PORT": "9001"})
    called_with: dict = {}
    def fake_chroma(host, port):
        called_with["host"] = host
        called_with["port"] = port
        return None
    with patch.object(vd, "probe_chroma", side_effect=fake_chroma), \
         patch.object(vd, "probe_qdrant", return_value=None), \
         patch.object(vd, "probe_pgvector", return_value=None):
        vd.status(cfg)
    assert called_with["port"] == 9001
