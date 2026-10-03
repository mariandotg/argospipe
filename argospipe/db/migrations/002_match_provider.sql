CREATE TABLE matches_new (
    fingerprint     TEXT NOT NULL REFERENCES jobs (fingerprint) ON DELETE CASCADE,
    text_hash       TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'anthropic',
    score           INTEGER NOT NULL,
    result          TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE (fingerprint, text_hash, profile_version, prompt_version, model, provider)
);

INSERT INTO matches_new (
    fingerprint, text_hash, profile_version, prompt_version, model, provider,
    score, result, tokens_in, tokens_out, created_at
)
SELECT
    fingerprint, text_hash, profile_version, prompt_version, model, 'anthropic',
    score, result, tokens_in, tokens_out, created_at
FROM matches;

DROP TABLE matches;

ALTER TABLE matches_new RENAME TO matches;
