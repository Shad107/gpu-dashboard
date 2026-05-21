"""Tests pour gpu_dashboard.storage — persistance SQLite locale.

Le module Storage stocke :
- des échantillons de métriques (samples) — 1 ligne toutes les 5 secondes
- des événements (events) — OcuLink drops/recoveries, changements de config, alerts

API publique :
- record_sample(dict)
- record_event(kind, payload)
- get_samples(from_ts, to_ts, step=None)  — step = resampling bin en secondes
- get_events(from_ts, kind=None)
- purge_older_than(days)
- vacuum()
- export_csv(from_ts, to_ts)
- close()
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from gpu_dashboard.storage import Storage


# ──────────────────────────────── fixtures ─────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """Storage instance temporaire, fermé à la fin du test."""
    path = str(tmp_path / "metrics.db")
    s = Storage(path)
    yield s
    s.close()


def _sample(ts, **overrides):
    """Helper : sample par défaut à un timestamp donné."""
    base = {
        "ts": ts,
        "temp": 50.0,
        "fan_pct": 40,
        "fan0_rpm": 1200,
        "fan1_rpm": 1100,
        "clk_gpu": 1500,
        "clk_mem": 9500,
        "power": 200.0,
        "power_limit": 250.0,
        "util_gpu": 75,
        "mem_used_mib": 8000,
    }
    base.update(overrides)
    return base


# ──────────────────────────────── init / schema ────────────────────────────


class TestInit:
    def test_creates_db_file(self, tmp_path):
        path = str(tmp_path / "metrics.db")
        s = Storage(path)
        assert os.path.isfile(path)
        s.close()

    def test_creates_parent_dir_if_missing(self, tmp_path):
        path = str(tmp_path / "subdir" / "deeper" / "metrics.db")
        s = Storage(path)
        assert os.path.isfile(path)
        s.close()

    def test_idempotent_reopen(self, tmp_path):
        path = str(tmp_path / "metrics.db")
        s1 = Storage(path); s1.close()
        s2 = Storage(path); s2.close()  # ne doit pas planter

    def test_schema_version_set(self, db):
        # Le schéma doit avoir une version trackée (pour migrations futures)
        assert db.schema_version() >= 1


# ──────────────────────────────── samples ──────────────────────────────────


class TestSamples:
    def test_record_then_get(self, db):
        s = _sample(1000)
        db.record_sample(s)
        result = db.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["ts"] == 1000
        assert result[0]["temp"] == 50.0
        assert result[0]["power"] == 200.0

    def test_get_samples_range_filter(self, db):
        for ts in [100, 200, 300, 400]:
            db.record_sample(_sample(ts))
        # Range [150, 350] devrait renvoyer ts=200 et 300
        result = db.get_samples(from_ts=150, to_ts=350)
        assert [r["ts"] for r in result] == [200, 300]

    def test_get_samples_returns_sorted(self, db):
        for ts in [300, 100, 200]:
            db.record_sample(_sample(ts))
        result = db.get_samples(from_ts=0)
        assert [r["ts"] for r in result] == [100, 200, 300]

    def test_duplicate_ts_replaces(self, db):
        """Si on insère 2 fois le même ts, le second remplace (PRIMARY KEY)."""
        db.record_sample(_sample(100, temp=50))
        db.record_sample(_sample(100, temp=80))
        result = db.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["temp"] == 80

    def test_missing_fields_become_null(self, db):
        """Un sample incomplet doit pouvoir être inséré (champs absents → NULL)."""
        db.record_sample({"ts": 100, "temp": 50.0})
        result = db.get_samples(from_ts=0)
        assert result[0]["temp"] == 50.0
        assert result[0]["fan0_rpm"] is None

    def test_get_samples_with_step_resamples(self, db):
        """step=60 → groupe en bins de 60s avec moyennes.

        Bins SQLite : (ts / 60) * 60 → 60 pour ts∈[60,119], 120 pour ts∈[120,179], etc.
        On garde tous les samples à l'intérieur d'UN seul bin pour des chiffres clairs.
        """
        for ts, temp in [
            # bin 60 (ts < 120) : moyenne attendue = 50
            (100, 40), (110, 50), (115, 60),
            # bin 120 (ts ∈ [120, 180)) : moyenne attendue = 80
            (130, 70), (150, 80), (170, 90),
        ]:
            db.record_sample(_sample(ts, temp=temp))
        result = db.get_samples(from_ts=0, to_ts=300, step=60)
        assert len(result) == 2
        temps_by_bin = {r["ts"]: r["temp"] for r in result}
        assert temps_by_bin[60] == pytest.approx(50.0, abs=0.1)
        assert temps_by_bin[120] == pytest.approx(80.0, abs=0.1)


# ──────────────────────────────── events ───────────────────────────────────


class TestEvents:
    def test_record_then_get(self, db):
        db.record_event("drop", {"reason": "OcuLink lost"})
        events = db.get_events(from_ts=0)
        assert len(events) == 1
        assert events[0]["kind"] == "drop"
        assert events[0]["payload"] == {"reason": "OcuLink lost"}

    def test_payload_none_stored(self, db):
        db.record_event("recover")
        events = db.get_events(from_ts=0)
        assert events[0]["kind"] == "recover"
        assert events[0]["payload"] is None

    def test_filter_by_kind(self, db):
        db.record_event("drop")
        db.record_event("recover")
        db.record_event("drop")
        drops = db.get_events(from_ts=0, kind="drop")
        assert len(drops) == 2
        recovers = db.get_events(from_ts=0, kind="recover")
        assert len(recovers) == 1

    def test_filter_by_from_ts(self, db, monkeypatch):
        # On peut pas mocker time.time facilement ici, donc on vérifie
        # qu'avec un from_ts dans le futur, on n'a rien
        db.record_event("drop")
        events = db.get_events(from_ts=int(time.time()) + 1000)
        assert events == []

    def test_get_events_sorted(self, db):
        db.record_event("a")
        time.sleep(0.01)
        db.record_event("b")
        time.sleep(0.01)
        db.record_event("c")
        events = db.get_events(from_ts=0)
        assert [e["kind"] for e in events] == ["a", "b", "c"]


# ──────────────────────────────── purge / vacuum ───────────────────────────


class TestPurge:
    def test_purge_removes_old(self, db):
        now = int(time.time())
        old_ts = now - 40 * 86400  # 40 jours
        recent_ts = now - 5 * 86400  # 5 jours
        db.record_sample(_sample(old_ts))
        db.record_sample(_sample(recent_ts))
        samples_deleted, _ = db.purge_older_than(days=30)
        assert samples_deleted == 1
        result = db.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["ts"] == recent_ts

    def test_purge_removes_old_events(self, db, monkeypatch):
        now = int(time.time())
        # Insère un event « ancien » en bidouillant la table directement
        db.record_event("drop")
        db._conn.execute("UPDATE events SET ts = ? WHERE kind = 'drop'", (now - 40 * 86400,))
        db._conn.commit()
        db.record_event("recover")
        _, events_deleted = db.purge_older_than(days=30)
        assert events_deleted == 1
        events = db.get_events(from_ts=0)
        assert [e["kind"] for e in events] == ["recover"]

    def test_vacuum_no_op_on_empty(self, db):
        db.vacuum()  # ne doit pas planter


# ──────────────────────────────── export CSV ──────────────────────────────


class TestExportCsv:
    def test_csv_header(self, db):
        db.record_sample(_sample(100))
        csv = db.export_csv(from_ts=0)
        header = csv.splitlines()[0]
        # Doit contenir au moins ts, temp, power
        assert "ts" in header
        assert "temp" in header
        assert "power" in header

    def test_csv_data_rows(self, db):
        db.record_sample(_sample(100, temp=42.0))
        db.record_sample(_sample(200, temp=55.0))
        csv = db.export_csv(from_ts=0)
        lines = csv.strip().splitlines()
        assert len(lines) == 3  # header + 2 rows
        # La 2e ligne contient 42.0 quelque part
        assert "42" in lines[1]
        assert "55" in lines[2]

    def test_csv_range_filter(self, db):
        db.record_sample(_sample(100))
        db.record_sample(_sample(200))
        db.record_sample(_sample(300))
        csv = db.export_csv(from_ts=150, to_ts=250)
        lines = csv.strip().splitlines()
        # header + 1 row (ts=200)
        assert len(lines) == 2
        assert "200" in lines[1]


# ──────────────────────────────── concurrence ──────────────────────────────


class TestConcurrency:
    def test_concurrent_writes(self, db):
        """Deux threads écrivent en parallèle sans corrompre la DB."""
        N = 50

        def writer_samples():
            for i in range(N):
                db.record_sample(_sample(10000 + i, temp=40 + i))

        def writer_events():
            for i in range(N):
                db.record_event("tick", {"i": i})

        t1 = threading.Thread(target=writer_samples)
        t2 = threading.Thread(target=writer_events)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert len(db.get_samples(from_ts=0)) == N
        assert len(db.get_events(from_ts=0)) == N


# ──────────────────────────────── persistance ──────────────────────────────


class TestPersistence:
    def test_data_survives_close_reopen(self, tmp_path):
        path = str(tmp_path / "metrics.db")
        s1 = Storage(path)
        s1.record_sample(_sample(100, temp=42.0))
        s1.record_event("test")
        s1.close()

        s2 = Storage(path)
        samples = s2.get_samples(from_ts=0)
        events = s2.get_events(from_ts=0)
        assert len(samples) == 1
        assert samples[0]["temp"] == 42.0
        assert len(events) == 1
        assert events[0]["kind"] == "test"
        s2.close()
