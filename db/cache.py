"""Postgres-backed Match cache (ADR 0006, ADR 0007).

Plain key-value lookup on ticket_name -- no relational structure, so this is
a cache-aside store, not an ORM layer. No connection pooling: connect per
call, same minimalism as VectorStore/HelpdeskClient. A cached row is served
unconditionally once it exists (ADR 0007 dropped the corpus_version
staleness gate) -- `get` never rejects a row for being old, `computed_at` is
kept only for observability.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import psycopg2
import psycopg2.extras

import config

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class MatchCache:
    def __init__(self, database_url: str = config.DATABASE_URL) -> None:
        self._database_url = database_url

    def _connect(self):
        conn = psycopg2.connect(self._database_url)
        conn.autocommit = True
        return conn

    def ensure_schema(self) -> None:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text())

    def get(self, ticket_name: str) -> list[dict] | None:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT matches FROM ticket_matches_cache WHERE ticket_name = %s", (ticket_name,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def put(self, ticket_name: str, matches: list[dict]) -> None:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ticket_matches_cache (ticket_name, matches, computed_at)
                VALUES (%s, %s, now())
                ON CONFLICT (ticket_name)
                DO UPDATE SET matches = EXCLUDED.matches, computed_at = EXCLUDED.computed_at
                """,
                (ticket_name, psycopg2.extras.Json(matches)),
            )
