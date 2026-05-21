"""Tests for the storage schema migration : add tokens_total_snapshot column."""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

from gpu_dashboard.storage import Storage


class TestSchemaMigration:
    def test_new_db_has_tokens_column(self, tmp_path):
        s = Storage(str(tmp_path / "metrics.db"))
        # PRAGMA table_info → list of columns
        info = s._conn.execute("PRAGMA table_info(samples)").fetchall()
        col_names = [row[1] for row in info]
        assert "tokens_total_snapshot" in col_names
        s.close()

    def test_old_db_gets_migrated(self, tmp_path):
        """Pre-existing DB without the new column should get migrated on open."""
        path = str(tmp_path / "metrics.db")
        # Manually create an old-schema DB (no tokens_total_snapshot)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE samples (
                ts INTEGER PRIMARY KEY,
                temp REAL, fan_pct INTEGER,
                fan0_rpm INTEGER, fan1_rpm INTEGER,
                clk_gpu INTEGER, clk_mem INTEGER,
                power REAL, power_limit REAL,
                util_gpu INTEGER, mem_used_mib INTEGER
            )
        """)
        conn.execute("INSERT INTO samples (ts, temp, power) VALUES (100, 50, 200)")
        conn.commit()
        conn.close()

        # Open via Storage — should migrate
        s = Storage(path)
        info = s._conn.execute("PRAGMA table_info(samples)").fetchall()
        col_names = [row[1] for row in info]
        assert "tokens_total_snapshot" in col_names

        # Old data should still be there
        result = s.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["temp"] == 50.0
        assert result[0].get("tokens_total_snapshot") is None
        s.close()


class TestRecordWithTokens:
    def test_record_and_retrieve_tokens(self, tmp_path):
        s = Storage(str(tmp_path / "metrics.db"))
        s.record_sample({
            "ts": 100, "temp": 50, "power": 200,
            "tokens_total_snapshot": 12345,
        })
        result = s.get_samples(from_ts=0)
        assert len(result) == 1
        assert result[0]["tokens_total_snapshot"] == 12345
        s.close()

    def test_record_without_tokens_is_null(self, tmp_path):
        s = Storage(str(tmp_path / "metrics.db"))
        s.record_sample({"ts": 100, "temp": 50, "power": 200})
        result = s.get_samples(from_ts=0)
        assert result[0].get("tokens_total_snapshot") is None
        s.close()
