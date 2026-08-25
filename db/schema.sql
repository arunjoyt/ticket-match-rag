-- Match cache (ADR 0006, staleness accepted per ADR 0007 -- no corpus_version
-- table; a row is served unconditionally once it exists).
CREATE TABLE IF NOT EXISTS ticket_matches_cache (
    ticket_name TEXT PRIMARY KEY,
    matches JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
