"""R&D #14.4 — inference cost calculator tests."""
import pytest
from gpu_dashboard.modules import inference_cost as ic


def _s(ts, power=100, tokens=0):
    return {"ts": ts, "power": power, "tokens_total_snapshot": tokens}


# ── _integrate_kwh ───────────────────────────────────────────────────────


def test_integrate_no_samples():
    r = ic._integrate_kwh([])
    assert r["window_s"] == 0
    assert r["kwh"] == 0


def test_integrate_single_sample_returns_zero():
    """Can't integrate from one point."""
    r = ic._integrate_kwh([_s(0, power=100)])
    assert r["kwh"] == 0


def test_integrate_steady_100w_over_1h():
    """100 W steady for 3600s → 0.1 kWh."""
    samples = [_s(t * 60, power=100) for t in range(61)]  # every 60s for 1h
    r = ic._integrate_kwh(samples)
    assert r["window_s"] == 3600
    assert abs(r["kwh"] - 0.1) < 0.001
    assert r["avg_watts"] == 100.0


def test_integrate_counts_token_deltas():
    """Tokens go 0 → 100 → 300 → 500. Positive deltas = 500 total."""
    samples = [_s(0, 100, 0), _s(60, 100, 100), _s(120, 100, 300), _s(180, 100, 500)]
    r = ic._integrate_kwh(samples)
    assert r["tokens_delta"] == 500


def test_integrate_ignores_negative_token_deltas():
    """Restart drops counter from 500 to 0 → not counted as -500."""
    samples = [_s(0, 100, 0), _s(60, 100, 500), _s(120, 100, 0), _s(180, 100, 200)]
    r = ic._integrate_kwh(samples)
    # +500 then +200 (after restart) = 700 ; the -500 dip is just counted as 1 restart
    assert r["tokens_delta"] == 700
    assert r["restart_count"] == 1


def test_integrate_handles_missing_power_field():
    samples = [_s(0), {"ts": 60, "tokens_total_snapshot": 100},  # no power
               _s(120, power=100, tokens=200)]
    r = ic._integrate_kwh(samples)
    # Only one valid power pair → small kwh
    assert r["kwh"] >= 0


def test_integrate_protects_against_bad_timestamps():
    """A 1-hour gap between two samples should not balloon the integration."""
    samples = [_s(0, 100, 0), _s(99999, 100, 100)]  # huge dt
    r = ic._integrate_kwh(samples)
    # Should be capped (the helper substitutes default_dt_s)
    assert r["kwh"] < 1.0  # not crazy-high


# ── compute_costs ────────────────────────────────────────────────────────


def test_compute_costs_basic():
    integrated = {"window_s": 3600, "tokens_delta": 36000,
                  "kwh": 0.1, "avg_watts": 100, "restart_count": 0,
                  "sample_count": 61}
    r = ic.compute_costs(integrated, price_eur_per_kwh=0.25)
    assert r["cost_gpu_eur"] == 0.025
    # 36000 tokens / (0.1 kWh × 1000 Wh) = 360 tok/Wh
    assert r["tok_per_wh_gpu"] == 360.0
    # 0.025 € / 36 (thousand) = 0.000694 €/1k-tokens
    assert abs(r["cost_per_1k_tokens_eur"] - 0.000694) < 1e-4


def test_compute_costs_zero_tokens_nullifies_ratios():
    integrated = {"window_s": 3600, "tokens_delta": 0, "kwh": 0.1,
                  "avg_watts": 100, "restart_count": 0, "sample_count": 61}
    r = ic.compute_costs(integrated, price_eur_per_kwh=0.25)
    assert r["tok_per_wh_gpu"] is None
    assert r["cost_per_1k_tokens_eur"] is None


def test_compute_costs_wall_overhead_pct():
    """Wall draws more than GPU → overhead_pct surfaces."""
    integrated = {"window_s": 3600, "tokens_delta": 1000,
                  "kwh": 0.1, "avg_watts": 100, "restart_count": 0,
                  "sample_count": 61}
    r = ic.compute_costs(integrated, price_eur_per_kwh=0.25, wall_kwh=0.15)
    assert r["kwh_wall"] == 0.15
    assert "overhead_pct" in r
    # (0.15 - 0.10) / 0.15 × 100 ≈ 33.3 %
    assert 33 < r["overhead_pct"] < 34


def test_compute_costs_wall_not_provided():
    integrated = {"window_s": 3600, "tokens_delta": 1000,
                  "kwh": 0.1, "avg_watts": 100, "restart_count": 0,
                  "sample_count": 61}
    r = ic.compute_costs(integrated, price_eur_per_kwh=0.25)
    assert "kwh_wall" not in r


# ── status (top-level) ──────────────────────────────────────────────────


def test_status_no_storage():
    from gpu_dashboard.config import Config
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.25"})
    r = ic.status(storage=None, cfg=cfg)
    assert r["available"] is False


class _FakeStorage:
    def __init__(self, samples):
        self._samples = samples
    def get_samples(self, from_ts=None, to_ts=None, gpu_index=0):
        return [s for s in self._samples
                if (from_ts is None or s.get("ts", 0) >= from_ts)
                and (to_ts is None or s.get("ts", 0) <= to_ts)]


def test_status_with_storage_per_window():
    import time
    from gpu_dashboard.config import Config
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "0.20"})
    now = int(time.time())
    # 61 samples spread over the last 3600 s
    samples = [_s(now - 3600 + i * 60, power=100, tokens=i * 200) for i in range(61)]
    storage = _FakeStorage(samples)
    r = ic.status(storage, cfg)
    assert r["available"] is True
    assert r["price_eur_per_kwh"] == 0.20
    assert "3600" in r["windows"]
    w_1h = r["windows"]["3600"]
    # 60 deltas of 200 tokens each = 12000
    assert w_1h["tokens_delta"] == 12000
    # 100W × 1h = 0.1 kWh
    assert abs(w_1h["kwh"] - 0.1) < 0.01
    # tok/Wh = 12000 / 100Wh = 120
    assert w_1h["tok_per_wh_gpu"] == 120


def test_status_invalid_price_falls_back_to_default():
    from gpu_dashboard.config import Config
    cfg = Config(defaults={"ELECTRICITY_PRICE_EUR_PER_KWH": "not-a-number"})
    r = ic.status(_FakeStorage([]), cfg)
    assert r["price_eur_per_kwh"] == 0.25
