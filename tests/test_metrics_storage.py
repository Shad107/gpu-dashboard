"""Tests pour l'intégration MetricsSampler ↔ Storage.

On vérifie que :
- Un sampler sans storage continue de marcher (backward compat)
- Avec un storage, chaque _poll réussi est persisté
- Le mapping fields (sample → DB columns) est correct
- Les events posés via record_event() arrivent en DB
- Les exceptions DB n'arrêtent pas le sampler
"""
from __future__ import annotations

import pytest

from gpu_dashboard.metrics import MetricsSampler
from gpu_dashboard.storage import Storage


@pytest.fixture
def db(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield s
    s.close()


class TestSamplerStorageIntegration:
    def test_sampler_without_storage_works(self):
        """Backward compat : sampler sans storage ne crashe pas."""
        s = MetricsSampler()
        # _persist sur un sample arbitraire doit être un no-op silencieux
        s._persist({"temp": 50})
        assert s.snapshot() == []

    def test_persist_writes_to_db(self, db):
        sampler = MetricsSampler(storage=db)
        sample = {
            "ts": "12:00:00",
            "temp": 55,
            "fan": 40,
            "clk_gpu": 1500,
            "clk_mem": 9500,
            "power": 230.5,
            "power_limit": 280.0,
            "util_gpu": 75,
            "mem_used_mib": 12000,
        }
        sampler._persist(sample)
        result = db.get_samples(from_ts=0)
        assert len(result) == 1
        r = result[0]
        # Mapping: sample.fan → DB.fan_pct
        assert r["fan_pct"] == 40
        assert r["temp"] == 55
        assert r["power"] == 230.5
        assert r["power_limit"] == 280.0
        assert r["util_gpu"] == 75
        assert r["mem_used_mib"] == 12000
        # ts doit être un epoch entier (et pas "12:00:00")
        assert isinstance(r["ts"], int)
        assert r["ts"] > 1_700_000_000  # après nov 2023

    def test_persist_handles_missing_fields(self, db):
        """Un sample minimal ne plante pas, les champs absents → NULL."""
        sampler = MetricsSampler(storage=db)
        sampler._persist({"temp": 50})
        result = db.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["temp"] == 50
        assert result[0]["fan0_rpm"] is None
        assert result[0]["power_limit"] is None

    def test_persist_with_fan_rpms(self, db):
        sampler = MetricsSampler(storage=db)
        sampler._persist({
            "temp": 50, "fan": 40, "power": 200.0,
            "fan0_rpm": 1234, "fan1_rpm": 1100,
        })
        r = db.get_samples(from_ts=0)[0]
        assert r["fan0_rpm"] == 1234
        assert r["fan1_rpm"] == 1100

    def test_record_event_writes_to_storage(self, db):
        sampler = MetricsSampler(storage=db)
        sampler.record_event("drop", {"reason": "OcuLink"})
        events = db.get_events(from_ts=0)
        assert len(events) == 1
        assert events[0]["kind"] == "drop"
        assert events[0]["payload"]["reason"] == "OcuLink"

    def test_record_event_without_storage_no_crash(self):
        sampler = MetricsSampler()  # no storage
        sampler.record_event("test")  # ne doit pas planter

    def test_persist_failure_silently_swallowed(self, db):
        """Si storage.record_sample lève, le sampler ne crashe pas."""
        class BrokenStorage:
            def record_sample(self, _):
                raise RuntimeError("disk full")
        sampler = MetricsSampler(storage=BrokenStorage())
        # ne doit pas raise
        sampler._persist({"temp": 50})
