"""Rate limiting de ventana deslizante sobre SQLite (compartido entre procesos)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .tickets import connect


class RateLimiter:
    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self._clock = clock
        with connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hits (key TEXT NOT NULL, ts REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS hits_key ON hits (key, ts)")

    def _count(self, conn, key: str, window: float, now: float) -> int:
        conn.execute("DELETE FROM hits WHERE key = ? AND ts <= ?", (key, now - window))
        return conn.execute(
            "SELECT COUNT(*) FROM hits WHERE key = ?", (key,)
        ).fetchone()[0]

    def check(self, key: str, limit: int, window: float) -> bool:
        """True si todavía hay cupo. No registra el intento."""

        with connect(self.db_path) as conn:
            return self._count(conn, key, window, self._clock()) < limit

    def record(self, key: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute("INSERT INTO hits VALUES (?, ?)", (key, self._clock()))

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Comprueba y registra. False si se superó el límite."""

        now = self._clock()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            allowed = self._count(conn, key, window, now) < limit
            if allowed:
                conn.execute("INSERT INTO hits VALUES (?, ?)", (key, now))
            conn.execute("COMMIT")
        return allowed
