"""R&D #11.1 — k8s healthz + readyz probes tests."""
import pytest
from unittest.mock import patch, MagicMock
from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    storage = Storage(str(tmp_path / "test.db"))
    sampler = MagicMock()
    sampler.snapshot.return_value = [{"temp": 50, "util_gpu": 20}]
    return {
        "config": Config(defaults={}),
        "storage": storage,
        "sampler": sampler,
    }


# ── /healthz ──────────────────────────────────────────────────────────────


def test_healthz_always_returns_200():
    """Liveness should never fail (no checks beyond process-alive)."""
    code, body = api.handle_healthz({})
    assert code == 200
    assert body["alive"] is True


def test_healthz_includes_timestamp():
    code, body = api.handle_healthz({})
    assert "ts" in body
    assert isinstance(body["ts"], int)


# ── /readyz ───────────────────────────────────────────────────────────────


def test_readyz_all_ok_returns_200(ctx):
    """sampler + storage + nvidia all reachable → 200."""
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]):
        code, body = api.handle_readyz(ctx)
    assert code == 200
    assert body["ready"] is True


def test_readyz_no_sampler_returns_503():
    """Without sampler → 503 readiness."""
    ctx = {"config": Config(defaults={}), "sampler": None, "storage": None}
    with patch.object(api._monolith, "_gpus_available", return_value=[]):
        code, body = api.handle_readyz(ctx)
    assert code == 503
    assert body["ready"] is False
    assert body["checks"]["sampler"]["ok"] is False


def test_readyz_no_nvidia_returns_503(ctx):
    """nvidia-smi unreachable → 503."""
    with patch.object(api._monolith, "_gpus_available", return_value=[]):
        code, body = api.handle_readyz(ctx)
    assert code == 503
    assert body["checks"]["nvidia"]["ok"] is False


def test_readyz_storage_locked_returns_503(ctx):
    """If storage write probe fails → 503."""
    fake_conn = MagicMock()
    fake_conn.execute.side_effect = RuntimeError("db locked")
    ctx["storage"]._conn = fake_conn
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]):
        code, body = api.handle_readyz(ctx)
    assert code == 503
    assert body["checks"]["storage"]["ok"] is False


def test_readyz_default_skip_ecc_drift_checks(ctx):
    """Without ?strict=1, ECC + drift are NOT in checks."""
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]):
        code, body = api.handle_readyz(ctx)
    assert "ecc" not in body["checks"]
    assert "drift" not in body["checks"]


def test_readyz_strict_includes_ecc_drift(ctx):
    """With ?strict=1, ECC + drift checks added (even if passing)."""
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]):
        code, body = api.handle_readyz(ctx, {"strict": "1"})
    assert "ecc" in body["checks"]
    assert "drift" in body["checks"]
    assert body["strict"] is True


def test_readyz_strict_ecc_failing_returns_503(ctx):
    """Strict mode + ECC verdict='failing' → 503."""
    fake_ecc = {"verdict_kind": "failing", "verdict_msg": "Memory degrading"}
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(api._monolith, "handle_ecc_health", return_value=(200, fake_ecc)), \
         patch.object(api._monolith, "handle_drift_check", return_value=(200, {"last_drift": None})):
        code, body = api.handle_readyz(ctx, {"strict": "1"})
    assert code == 503
    assert body["checks"]["ecc"]["ok"] is False


def test_readyz_strict_recent_drift_returns_503(ctx):
    """Strict mode + driver drift in last 24h → 503."""
    import time
    fake_drift = {"last_drift": {"ts": int(time.time()) - 3600, "diffs": [{"field": "driver"}]}}
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(api._monolith, "handle_ecc_health", return_value=(200, {"verdict_kind": "ok"})), \
         patch.object(api._monolith, "handle_drift_check", return_value=(200, fake_drift)):
        code, body = api.handle_readyz(ctx, {"strict": "1"})
    assert code == 503
    assert "recent driver/kernel drift" in body["checks"]["drift"]["reason"]


def test_readyz_strict_old_drift_passes(ctx):
    """Strict mode + drift > 24h ago → readyz still passes."""
    import time
    fake_drift = {"last_drift": {"ts": int(time.time()) - 48 * 3600, "diffs": [{}]}}
    with patch.object(api._monolith, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(api._monolith, "handle_ecc_health", return_value=(200, {"verdict_kind": "ok"})), \
         patch.object(api._monolith, "handle_drift_check", return_value=(200, fake_drift)):
        code, body = api.handle_readyz(ctx, {"strict": "1"})
    assert code == 200
    assert body["checks"]["drift"]["ok"] is True
