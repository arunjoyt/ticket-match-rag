-- Match cache (ADR 0006, staleness accepted per ADR 0007 -- no corpus_version
-- table; a row is served unconditionally once it exists).
--
-- ADR 0011: `stale` replaces the RQ full-sweep refresh. An index mutation flips
-- every row to stale (one UPDATE, via retrieval/indexing.py); a stale row is
-- still served as-is, and the read that saw it schedules a single-ticket
-- refresh that clears the flag.
CREATE TABLE IF NOT EXISTS ticket_matches_cache (
    ticket_name TEXT PRIMARY KEY,
    matches JSONB NOT NULL,
    stale BOOLEAN NOT NULL DEFAULT false,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Migrate a pre-ADR-0011 table in place; no-op once the column exists.
ALTER TABLE ticket_matches_cache ADD COLUMN IF NOT EXISTS stale BOOLEAN NOT NULL DEFAULT false;
