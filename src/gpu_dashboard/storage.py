"""Persistance locale SQLite des métriques et des événements.

Choix techno : stdlib `sqlite3` uniquement. Pas de dépendance externe.

Architecture :
- Fichier unique par défaut à `~/.local/share/gpu-dashboard/metrics.db`
- WAL mode activé pour permettre lectures concurrentes pendant les écritures
- 1 seule connexion partagée entre threads (check_same_thread=False) + lock pour
  sérialiser les writes côté Python (SQLite le fait déjà mais on garde explicite)
- Schéma versionné via table `schema_version` pour migrations futures
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import time
from typing import Optional


CURRENT_SCHEMA_VERSION = 4  # bumped from 3: added gpu_index column to samples

# Colonnes du sample, dans l'ordre. Sert pour insert + export CSV.
SAMPLE_COLUMNS = (
    "ts", "temp", "fan_pct", "fan0_rpm", "fan1_rpm",
    "clk_gpu", "clk_mem", "power", "power_limit",
    "util_gpu", "mem_used_mib",
    "tokens_total_snapshot",
    "gpu_index",  # which GPU this sample is from (default 0 for back-compat)
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER NOT NULL,
    temp REAL,
    fan_pct INTEGER,
    fan0_rpm INTEGER,
    fan1_rpm INTEGER,
    clk_gpu INTEGER,
    clk_mem INTEGER,
    power REAL,
    power_limit REAL,
    util_gpu INTEGER,
    mem_used_mib INTEGER,
    tokens_total_snapshot INTEGER,
    gpu_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, gpu_index)
);
-- Index on (gpu_index, ts) is created in _migrate_v3_to_v4 (must run after
-- ALTER TABLE for old DBs that didn't have gpu_index yet).

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
"""


def _migrate_v1_to_v2(conn) -> None:
    """Add the tokens_total_snapshot column to an existing v1 samples table."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(samples)").fetchall()]
    if "tokens_total_snapshot" not in cols:
        conn.execute("ALTER TABLE samples ADD COLUMN tokens_total_snapshot INTEGER")


def _migrate_v2_to_v3(conn) -> None:
    """Idempotent: push_subscriptions table is created by _SCHEMA_SQL via
    CREATE TABLE IF NOT EXISTS, so this is a no-op. Kept for documentation."""
    pass


def _migrate_v3_to_v4(conn) -> None:
    """Add gpu_index column to existing samples table (if missing).

    Always runs the index creation at the end (idempotent) so brand-new DBs
    also get the index — _SCHEMA_SQL can't create it because old DBs hit
    'no such column' before the ALTER runs.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(samples)").fetchall()]
    if "gpu_index" not in cols:
        conn.execute("ALTER TABLE samples ADD COLUMN gpu_index INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_gpu_ts ON samples(gpu_index, ts)")


class Storage:
    """Wrapper SQLite thread-safe pour les métriques + événements du dashboard."""

    def __init__(self, db_path: str):
        # Crée le répertoire parent si absent
        parent = os.path.dirname(db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        # check_same_thread=False : on partage la conn entre threads, sérialisé par _lock
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        # Pragmas pour perf + concurrence
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")

        # Schéma + migrations idempotentes
        self._conn.executescript(_SCHEMA_SQL)
        _migrate_v1_to_v2(self._conn)
        _migrate_v2_to_v3(self._conn)
        _migrate_v3_to_v4(self._conn)
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )

    # ── push subscriptions ──────────────────────────────────────────────────

    def add_push_subscription(self, endpoint: str, p256dh: str, auth: str) -> None:
        """Idempotent : UPSERT by endpoint."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO push_subscriptions "
                "(endpoint, p256dh, auth, created_ts) VALUES (?, ?, ?, ?)",
                (endpoint, p256dh, auth, int(time.time())),
            )

    def list_push_subscriptions(self) -> list:
        cur = self._conn.execute(
            "SELECT endpoint, p256dh, auth, created_ts FROM push_subscriptions ORDER BY id"
        )
        return [dict(row) for row in cur.fetchall()]

    def remove_push_subscription(self, endpoint: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            )
            return cur.rowcount

    # ── introspection ───────────────────────────────────────────────────────

    def schema_version(self) -> int:
        cur = self._conn.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ── samples ─────────────────────────────────────────────────────────────

    def record_sample(self, sample: dict) -> None:
        """Insère ou remplace un sample (PK = ts). Les colonnes absentes deviennent NULL."""
        values = tuple(sample.get(col) for col in SAMPLE_COLUMNS)
        placeholders = ",".join("?" * len(SAMPLE_COLUMNS))
        cols = ",".join(SAMPLE_COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO samples ({cols}) VALUES ({placeholders})",
                values,
            )

    def get_samples(
        self,
        from_ts: int = 0,
        to_ts: Optional[int] = None,
        step: Optional[int] = None,
        gpu_index: int = 0,
    ) -> list:
        """Renvoie les samples du GPU `gpu_index` dans la plage [from_ts, to_ts].

        Default gpu_index=0 preserves single-GPU behaviour.
        Pass gpu_index=-1 to get samples for ALL GPUs (used by alert_monitor).
        """
        if to_ts is None:
            to_ts = int(time.time()) + 1

        gpu_filter = "" if gpu_index < 0 else " AND gpu_index = :gpu"
        params: dict = {"from": from_ts, "to": to_ts, "gpu": gpu_index}

        if not step or step <= 0:
            cur = self._conn.execute(
                f"SELECT {','.join(SAMPLE_COLUMNS)} FROM samples "
                f"WHERE ts BETWEEN :from AND :to{gpu_filter} ORDER BY ts",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

        params["step"] = step
        cur = self._conn.execute(
            f"""
            SELECT
                (ts / :step) * :step AS bin_ts,
                AVG(temp)         AS temp,
                AVG(fan_pct)      AS fan_pct,
                AVG(fan0_rpm)     AS fan0_rpm,
                AVG(fan1_rpm)     AS fan1_rpm,
                AVG(clk_gpu)      AS clk_gpu,
                AVG(clk_mem)      AS clk_mem,
                AVG(power)        AS power,
                AVG(power_limit)  AS power_limit,
                AVG(util_gpu)     AS util_gpu,
                AVG(mem_used_mib) AS mem_used_mib,
                MAX(tokens_total_snapshot) AS tokens_total_snapshot,
                :gpu              AS gpu_index
            FROM samples
            WHERE ts BETWEEN :from AND :to{gpu_filter}
            GROUP BY bin_ts
            ORDER BY bin_ts
            """,
            params,
        )
        result = []
        for row in cur.fetchall():
            d = dict(row)
            d["ts"] = int(d.pop("bin_ts"))
            result.append(d)
        return result

    # ── events ──────────────────────────────────────────────────────────────

    def record_event(self, kind: str, payload: Optional[dict] = None) -> None:
        """Insère un événement horodaté."""
        ts = int(time.time())
        json_payload = json.dumps(payload) if payload is not None else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                (ts, kind, json_payload),
            )

    def get_events(self, from_ts: int = 0, kind: Optional[str] = None) -> list:
        if kind is not None:
            cur = self._conn.execute(
                "SELECT ts, kind, payload FROM events WHERE ts >= ? AND kind = ? ORDER BY id",
                (from_ts, kind),
            )
        else:
            cur = self._conn.execute(
                "SELECT ts, kind, payload FROM events WHERE ts >= ? ORDER BY id",
                (from_ts,),
            )
        result = []
        for row in cur.fetchall():
            ts, k, payload_str = row[0], row[1], row[2]
            result.append({
                "ts": int(ts),
                "kind": k,
                "payload": json.loads(payload_str) if payload_str else None,
            })
        return result

    # ── purge / vacuum ──────────────────────────────────────────────────────

    def purge_older_than(self, days: int) -> tuple:
        """Supprime samples et events plus vieux que `days` jours.

        Retourne (samples_supprimés, events_supprimés).
        """
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            cur_s = self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            samples_deleted = cur_s.rowcount
            cur_e = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            events_deleted = cur_e.rowcount
        return samples_deleted, events_deleted

    def vacuum(self) -> None:
        """Compacte le fichier SQLite (à appeler hebdo après les purges)."""
        with self._lock:
            self._conn.execute("VACUUM")

    # ── export CSV ──────────────────────────────────────────────────────────

    def export_csv(self, from_ts: int = 0, to_ts: Optional[int] = None) -> str:
        """Exporte les samples au format CSV (avec header)."""
        rows = self.get_samples(from_ts=from_ts, to_ts=to_ts)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(SAMPLE_COLUMNS)
        for row in rows:
            writer.writerow([row.get(col) for col in SAMPLE_COLUMNS])
        return out.getvalue()

    # ── lifecycle ───────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._conn.close()
