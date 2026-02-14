"""SQLite storage for activity sessions - enables analytics and EEG correlation."""

import sqlite3
import time
from pathlib import Path
from typing import Optional

from activity_tracker import ActivityContext


def _ensure_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


class Storage:
    """Persistent storage for activity sessions."""

    def __init__(self, db_path: Path):
        _ensure_dir(db_path)
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT NOT NULL,
                window_title TEXT,
                context_type TEXT,
                context_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                duration_seconds REAL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS eeg_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                event_type TEXT NOT NULL,
                duration_at_trigger REAL,
                mental_state TEXT,
                eeg_data_path TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_context ON sessions(context_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
        """)
        conn.commit()

    def start_session(self, context: ActivityContext) -> int:
        """Record start of a new session. Returns session ID."""
        conn = self._get_conn()
        cur = conn.execute("""
            INSERT INTO sessions (app_name, window_title, context_type, context_id, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (context.app_name, context.window_title, context.context_type, context.context_id, context.detected_at))
        conn.commit()
        return cur.lastrowid

    def end_session(self, session_id: int, duration_seconds: float):
        """Record end of session."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE sessions SET ended_at = ?, duration_seconds = ? WHERE id = ?",
            (time.time(), duration_seconds, session_id),
        )
        conn.commit()

    def record_eeg_trigger(
        self,
        session_id: Optional[int],
        event_type: str,
        duration_at_trigger: float,
        mental_state: Optional[str] = None,
        eeg_data_path: Optional[str] = None,
    ):
        """Record EEG trigger for analytics."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO eeg_events (session_id, event_type, duration_at_trigger, mental_state, eeg_data_path)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, event_type, duration_at_trigger, mental_state, eeg_data_path))
        conn.commit()

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        """Get recent sessions for agent context."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT app_name, window_title, context_type, duration_seconds, started_at
            FROM sessions WHERE ended_at IS NOT NULL
            ORDER BY ended_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
