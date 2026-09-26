CREATE TABLE jobs (
    fingerprint TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    location    TEXT,
    modality    TEXT,
    seniority   TEXT,
    stack       TEXT NOT NULL DEFAULT '[]',
    lang        TEXT,
    description TEXT,
    text_hash   TEXT,
    url         TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
);

CREATE TABLE job_sources (
    fingerprint TEXT NOT NULL REFERENCES jobs (fingerprint) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url         TEXT,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX idx_job_sources_fingerprint ON job_sources (fingerprint);

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stats       TEXT NOT NULL DEFAULT '{}',
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0
);

CREATE TABLE run_jobs (
    run_id      INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL REFERENCES jobs (fingerprint) ON DELETE CASCADE,
    stage       TEXT NOT NULL CHECK (stage IN ('prefiltered_out', 'ranked_out', 'matched')),
    reasons     TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, fingerprint)
);

CREATE TABLE matches (
    fingerprint     TEXT NOT NULL REFERENCES jobs (fingerprint) ON DELETE CASCADE,
    text_hash       TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    score           INTEGER NOT NULL,
    result          TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE (fingerprint, text_hash, profile_version, prompt_version, model)
);
