"""Postgres-backed Match cache (ADR 0006, ADR 0007, ADR 0011).

Plain key-value lookup on ticket_name -- no relational structure, so this is
a cache-aside store, not an ORM layer. No connection pooling: connect per
call, same minimalism as VectorStore/HelpdeskClient.

A cached row is always served, fresh or stale (ADR 0007 dropped the
corpus_version gate). `stale` (ADR 0011) is advisory only: `get` returns it so
the read path can schedule a single-ticket refresh, but never withholds a row
because of it. `computed_at` is kept for observability; nothing branches on it.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import psycopg2
import psycopg2.extras

import config

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@dataclass
class CachedMatches:
    matches: list[dict]
    stale: bool


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

    def get(self, ticket_name: str) -> CachedMatches | None:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT matches, stale FROM ticket_matches_cache WHERE ticket_name = %s",
                (ticket_name,),
            )
            row = cur.fetchone()
            return CachedMatches(matches=row[0], stale=row[1]) if row else None

    def put(self, ticket_name: str, matches: list[dict]) -> None:
        """Write a freshly computed row -- clears `stale`, whether inserting or replacing."""
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ticket_matches_cache (ticket_name, matches, stale, computed_at)
                VALUES (%s, %s, false, now())
                ON CONFLICT (ticket_name)
                DO UPDATE SET matches = EXCLUDED.matches, stale = false, computed_at = EXCLUDED.computed_at
                """,
                (ticket_name, psycopg2.extras.Json(matches)),
            )

    def mark_all_stale(self) -> None:
        """Flag every row for revalidation after an index mutation (ADR 0011).

        `WHERE stale = false` keeps a burst of mutations (e.g. the /ingest/full
        loop) from rewriting already-stale rows on every call.
        """
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE ticket_matches_cache SET stale = true WHERE stale = false")

    def delete(self, ticket_name: str) -> None:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ticket_matches_cache WHERE ticket_name = %s", (ticket_name,))
