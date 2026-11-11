"""Tickets de subida: aleatorios, de un solo uso, con vigencia corta.

Solo se guarda el sha256 del token. SQLite (WAL) permite compartir el estado
entre los procesos de Passenger.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 bytes en base64url
DEFAULT_TTL_SECONDS = 300


class TicketError(Exception):
    """El ticket no se puede canjear. ``kind``: invalid, expired o used."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Ticket:
    account: str
    path: str
    max_bytes: int
    overwrite: bool
    expected_sha256: str | None
    expires_at: float


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=10, isolation_level=None)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn


class TicketStore:
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
                "CREATE TABLE IF NOT EXISTS tickets ("
                "token_hash TEXT PRIMARY KEY, account TEXT NOT NULL, "
                "path TEXT NOT NULL, max_bytes INTEGER NOT NULL, "
                "overwrite INTEGER NOT NULL, expected_sha256 TEXT, "
                "created_at REAL NOT NULL, expires_at REAL NOT NULL, "
                "used_at REAL)"
            )

    def create(
        self,
        *,
        account: str,
        path: str,
        max_bytes: int,
        overwrite: bool,
        expected_sha256: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> tuple[str, Ticket]:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        ticket = Ticket(
            account=account,
            path=path,
            max_bytes=max_bytes,
            overwrite=overwrite,
            expected_sha256=expected_sha256,
            expires_at=now + ttl_seconds,
        )
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM tickets WHERE expires_at < ?", (now - 86400,))
            conn.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,NULL)",
                (
                    hash_token(token),
                    account,
                    path,
                    max_bytes,
                    int(overwrite),
                    expected_sha256,
                    now,
                    ticket.expires_at,
                ),
            )
        return token, ticket

    def redeem(self, token: str) -> Ticket:
        """Canjea el ticket de forma atómica; solo funciona una vez."""

        if not TOKEN_PATTERN.fullmatch(token or ""):
            raise TicketError("invalid")
        wanted = hash_token(token)
        now = self._clock()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT token_hash, account, path, max_bytes, overwrite, "
                    "expected_sha256, expires_at, used_at FROM tickets "
                    "WHERE token_hash = ?",
                    (wanted,),
                ).fetchone()
                if row is None or not hmac.compare_digest(row[0], wanted):
                    raise TicketError("invalid")
                if row[7] is not None:
                    raise TicketError("used")
                if row[6] <= now:
                    raise TicketError("expired")
                conn.execute(
                    "UPDATE tickets SET used_at = ? WHERE token_hash = ?",
                    (now, wanted),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return Ticket(
            account=row[1],
            path=row[2],
            max_bytes=row[3],
            overwrite=bool(row[4]),
            expected_sha256=row[5],
            expires_at=row[6],
        )
