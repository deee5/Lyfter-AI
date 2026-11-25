from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Iterator, Optional


class Database:
    """SQLite persistence layer for sessions and multi-turn message history."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        else:
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            yield self._memory_conn
            return

        conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create required tables and indexes if they do not exist."""
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id    TEXT PRIMARY KEY,
                    system_prompt TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    metadata    TEXT,
                    created_at  TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id
                ON messages (session_id, id);
                """
            )
            conn.commit()

    def check_health(self) -> bool:
        """Verify SQLite is reachable."""
        with self._connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
            return row is not None and row[0] == 1

    def get_session_context(
        self, session_id: str
    ) -> tuple[Optional[str], list[dict[str, str]]]:
        """Load stored system prompt and prior user/assistant messages for a session."""
        with self._connection() as conn:
            session_row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            stored_prompt = session_row["system_prompt"] if session_row else None

            rows = conn.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

            history = [{"role": row["role"], "content": row["content"]} for row in rows]
            return stored_prompt, history

    def save_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        metadata: dict[str, Any],
        system_prompt: Optional[str],
    ) -> None:
        """Atomically persist a completed user + assistant turn."""
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata)

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),
                    updated_at = excluded.updated_at
                """,
                (session_id, system_prompt, now, now),
            )
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, 'user', ?, NULL, ?)
                """,
                (session_id, user_content, now),
            )
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, 'assistant', ?, ?, ?)
                """,
                (session_id, assistant_content, metadata_json, now),
            )
            conn.commit()

    def get_session_messages(self, session_id: str) -> Optional[list[dict[str, Any]]]:
        """Return oldest-first stored history for a session, or None if unknown."""
        with self._connection() as conn:
            session_row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT role, content, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

            if session_row is None and not rows:
                return None

            result: list[dict[str, Any]] = []
            for row in rows:
                item: dict[str, Any] = {
                    "role": row["role"],
                    "content": row["content"],
                }
                if row["role"] == "assistant" and row["metadata"]:
                    item["metadata"] = json.loads(row["metadata"])
                result.append(item)
            return result
