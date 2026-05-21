"""Tests pour gpu_dashboard.retention — purge auto + VACUUM."""
from __future__ import annotations

import time

import pytest

from gpu_dashboard.retention import RetentionTask
from gpu_dashboard.storage import Storage


@pytest.fixture
def db(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield s
    s.close()


def _add_old_sample(db, days_ago, **fields):
    """Insère un sample horodaté `days_ago` jours dans le passé."""
    ts = int(time.time()) - days_ago * 86400
    sample = {"ts": ts, "temp": 50.0, "power": 200.0}
    sample.update(fields)
    db.record_sample(sample)


class TestTickNow:
    def test_purges_old_samples(self, db):
        _add_old_sample(db, days_ago=45)  # > 30 jours
        _add_old_sample(db, days_ago=5)   # récent
        task = RetentionTask(db, retention_days=30)
        s, e = task.tick_now()
        assert s == 1
        assert e == 0
        remaining = db.get_samples(from_ts=0)
        assert len(remaining) == 1

    def test_custom_retention(self, db):
        _add_old_sample(db, days_ago=10)
        task = RetentionTask(db, retention_days=7)
        s, _ = task.tick_now()
        assert s == 1

    def test_minimum_retention_1_day(self, db):
        """retention_days <= 0 → forcé à 1 (sécurité, évite tout purge)."""
        task = RetentionTask(db, retention_days=0)
        assert task._retention_days == 1

    def test_handles_storage_failure_gracefully(self):
        """Si storage.purge_older_than lève, tick_now retourne (0,0)."""
        class BrokenStorage:
            def purge_older_than(self, days):
                raise RuntimeError("disk full")
        task = RetentionTask(BrokenStorage(), retention_days=30)
        result = task.tick_now()
        assert result == (0, 0)


class TestVacuumNow:
    def test_vacuum_no_op(self, db):
        task = RetentionTask(db)
        task.vacuum_now()  # ne plante pas

    def test_vacuum_updates_last_ts(self, db):
        task = RetentionTask(db)
        before = task._last_vacuum_ts
        task.vacuum_now()
        assert task._last_vacuum_ts > before

    def test_vacuum_failure_handled(self):
        class BrokenStorage:
            def vacuum(self):
                raise RuntimeError("locked")
        task = RetentionTask(BrokenStorage())
        task.vacuum_now()  # ne plante pas
        # _last_vacuum_ts ne doit pas être mis à jour
        assert task._last_vacuum_ts == 0.0


class TestStartStop:
    def test_start_stop_no_crash(self, db):
        task = RetentionTask(db, retention_days=30)
        task.start()
        task.stop()  # ne plante pas

    def test_double_start_is_idempotent(self, db):
        task = RetentionTask(db)
        task.start()
        task.start()  # ne lance pas un 2e thread
        task.stop()
