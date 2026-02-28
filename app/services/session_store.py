from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4


@dataclass
class ParseSession:
    session_id: str
    file_name: str
    parser_code: str
    rows: list[dict[str, str]]
    created_at: datetime
    last_accessed_at: datetime


class ParseSessionStore:
    def __init__(self, max_sessions: int = 8, ttl_minutes: int = 120) -> None:
        self._max_sessions = max_sessions
        self._ttl = timedelta(minutes=ttl_minutes)
        self._lock = Lock()
        self._sessions: dict[str, ParseSession] = {}

    def create(self, *, file_name: str, parser_code: str, rows: list[dict[str, str]]) -> ParseSession:
        now = datetime.now(timezone.utc)
        session = ParseSession(
            session_id=str(uuid4()),
            file_name=file_name,
            parser_code=parser_code,
            rows=rows,
            created_at=now,
            last_accessed_at=now,
        )
        with self._lock:
            self._prune_locked(now)
            self._sessions[session.session_id] = session
            self._enforce_capacity_locked()
        return session

    def get(self, session_id: str) -> ParseSession | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.last_accessed_at = now
            return session

    def _prune_locked(self, now: datetime) -> None:
        expired_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if (now - session.last_accessed_at) > self._ttl
        ]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)

    def _enforce_capacity_locked(self) -> None:
        overflow = len(self._sessions) - self._max_sessions
        if overflow <= 0:
            return

        # Remove least recently used sessions first.
        oldest = sorted(self._sessions.values(), key=lambda s: s.last_accessed_at)[:overflow]
        for session in oldest:
            self._sessions.pop(session.session_id, None)


SESSION_STORE = ParseSessionStore()

