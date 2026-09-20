from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
from typing import Any


class HistoryStore:
    """Local, append-only blackbox stored in SQLite."""

    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "robomap.db"
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'system',
                    state TEXT,
                    battery INTEGER,
                    detail TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_category_action ON events(category, action)")
            conn.commit()

    def log(
        self,
        category: str,
        action: str,
        *,
        source: str = "system",
        state: str | None = None,
        battery: int | None = None,
        detail: str | None = None,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events(ts, category, action, source, state, battery, detail) VALUES(?,?,?,?,?,?,?)",
                (ts, category[:40], action[:80], source[:40], state, battery, detail[:1000] if detail else None),
            )
            conn.commit()

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, category, action, source, state, battery, detail FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _state_events(self, since: datetime) -> list[sqlite3.Row]:
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT ts, state, battery FROM events WHERE category='status' AND action='state' AND ts>=? ORDER BY ts ASC",
                (since.astimezone(timezone.utc).isoformat(),),
            ).fetchall()

    @staticmethod
    def _parse_ts(value: str) -> datetime:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.now(timezone.utc)

    def stats(self, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 3650))
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        rows = self._state_events(since)

        cleaning_runs = sum(1 for r in rows if r["state"] == "cleaning")
        cleaning_seconds = 0.0
        daily_seconds: dict[str, float] = {}
        battery_samples: list[dict[str, Any]] = []

        for idx, row in enumerate(rows):
            ts = self._parse_ts(row["ts"])
            if row["battery"] is not None:
                battery_samples.append({"ts": row["ts"], "battery": int(row["battery"])})
            if row["state"] != "cleaning":
                continue
            end = now
            if idx + 1 < len(rows):
                end = self._parse_ts(rows[idx + 1]["ts"])
            duration = max(0.0, min((end - ts).total_seconds(), 24 * 3600.0))
            cleaning_seconds += duration
            # Split only by start day for a compact local dashboard. Cleaning sessions are short.
            key = ts.astimezone().date().isoformat()
            daily_seconds[key] = daily_seconds.get(key, 0.0) + duration

        local_today = datetime.now().astimezone().date()
        last7 = []
        for offset in range(6, -1, -1):
            day = local_today - timedelta(days=offset)
            sec = daily_seconds.get(day.isoformat(), 0.0)
            last7.append({"date": day.isoformat(), "minutes": round(sec / 60.0, 1)})

        with self._lock, self._connect() as conn:
            total_events = conn.execute("SELECT COUNT(*) FROM events WHERE ts>=?", (since.isoformat(),)).fetchone()[0]
            command_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE category='command' AND ts>=?", (since.isoformat(),)
            ).fetchone()[0]
            error_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE (state='error' OR category='error') AND ts>=?", (since.isoformat(),)
            ).fetchone()[0]

        return {
            "days": days,
            "cleaning_runs": cleaning_runs,
            "cleaning_minutes": round(cleaning_seconds / 60.0, 1),
            "command_count": int(command_count),
            "event_count": int(total_events),
            "error_count": int(error_count),
            "last7": last7,
            "battery_samples": battery_samples[-100:],
        }
