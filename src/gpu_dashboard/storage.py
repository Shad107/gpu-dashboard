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


CURRENT_SCHEMA_VERSION = 1

# Colonnes du sample, dans l'ordre. Sert pour insert + export CSV.
SAMPLE_COLUMNS = (
    "ts", "temp", "fan_pct", "fan0_rpm", "fan1_rpm",
    "clk_gpu", "clk_mem", "power", "power_limit",
    "util_gpu", "mem_used_mib",
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    temp REAL,
    fan_pct INTEGER,
    fan0_rpm INTEGER,
    fan1_rpm INTEGER,
    clk_gpu INTEGER,
    clk_mem INTEGER,
    power REAL,
    power_limit REAL,
    util_gpu INTEGER,
    mem_used_mib INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


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

        # Schéma
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )

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
    ) -> list:
        """Renvoie les samples dans la plage [from_ts, to_ts], triés par ts.

        Si `step` est fourni (en secondes), les samples sont resamplés en bins
        de `step` secondes avec moyennes (utile pour les vues 24h/30j).
        """
        if to_ts is None:
            to_ts = int(time.time()) + 1

        if not step or step <= 0:
            cur = self._conn.execute(
                f"SELECT {','.join(SAMPLE_COLUMNS)} FROM samples "
                f"WHERE ts BETWEEN ? AND ? ORDER BY ts",
                (from_ts, to_ts),
            )
            return [dict(row) for row in cur.fetchall()]

        # Resampling : groupe par bin de `step` secondes, moyennes.
        # NB: on alias le bin en `bin_ts` plutôt que `ts` pour ne pas que SQLite
        # confonde l'alias avec la colonne originale dans le GROUP BY.
        cur = self._conn.execute(
            """
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
                AVG(mem_used_mib) AS mem_used_mib
            FROM samples
            WHERE ts BETWEEN :from AND :to
            GROUP BY bin_ts
            ORDER BY bin_ts
            """,
            {"step": step, "from": from_ts, "to": to_ts},
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
