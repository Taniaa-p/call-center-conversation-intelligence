-- Applied at startup; every statement is idempotent (IF NOT EXISTS / OR REPLACE).
-- Only REDACTED text is stored in the public schema. Raw PII lives in pii_vault only.

CREATE TABLE IF NOT EXISTS calls (
    call_id      TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    team_id      TEXT NOT NULL,
    channel      TEXT NOT NULL DEFAULT 'voice',          -- voice | chat
    source       TEXT NOT NULL DEFAULT 'api',            -- api | live | seed
    status       TEXT NOT NULL DEFAULT 'live',           -- live | ended | analyzed | failed
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    analyzed_at  TIMESTAMPTZ
);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
-- Dashboard demo runs (live replays, pasted transcripts): stored, but kept out of every roll-up.
ALTER TABLE calls ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS calls_agent_idx ON calls (agent_id);
CREATE INDEX IF NOT EXISTS calls_team_idx ON calls (team_id);

CREATE TABLE IF NOT EXISTS turns (
    call_id          TEXT NOT NULL REFERENCES calls ON DELETE CASCADE,
    turn_id          INT  NOT NULL,
    speaker          TEXT NOT NULL,
    text_redacted    TEXT NOT NULL,
    ts               TIMESTAMPTZ,
    sentiment        REAL,
    rule_flags       JSONB NOT NULL DEFAULT '[]',
    PRIMARY KEY (call_id, turn_id)                        -- re-sent turn = no duplicate
);

-- Restricted entity map. Never joined by any view, never returned by the API.
-- Production: separate DB role with REVOKE on this schema for the app/analytics roles,
-- plus encryption at rest. Rows are purged by the worker cron after expires_at.
CREATE SCHEMA IF NOT EXISTS pii_vault;
CREATE TABLE IF NOT EXISTS pii_vault.entities (
    call_id      TEXT NOT NULL,
    placeholder  TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    value        TEXT NOT NULL,
    turn_id      INT  NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (call_id, placeholder, turn_id)
);
CREATE INDEX IF NOT EXISTS pii_expiry_idx ON pii_vault.entities (expires_at);

CREATE TABLE IF NOT EXISTS actions (
    call_id       TEXT NOT NULL REFERENCES calls ON DELETE CASCADE,
    action_id     TEXT NOT NULL,
    text          TEXT NOT NULL,
    owner         TEXT NOT NULL,
    status        TEXT NOT NULL,                          -- open | done | cancelled
    created_turn  INT  NOT NULL,
    updated_turn  INT  NOT NULL,
    evidence      JSONB NOT NULL DEFAULT '[]',
    verified      BOOLEAN NOT NULL,
    rationale     TEXT,
    confidence    REAL,
    PRIMARY KEY (call_id, action_id)
);

-- One row per analysis run (a call can be re-analysed after a prompt/config change).
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id        BIGSERIAL PRIMARY KEY,
    call_id            TEXT NOT NULL REFERENCES calls ON DELETE CASCADE,
    is_current         BOOLEAN NOT NULL DEFAULT TRUE,
    summary            TEXT,
    reasons            TEXT[] NOT NULL DEFAULT '{}',
    resolution         TEXT,
    churn_flag         BOOLEAN,
    churn_score        REAL,
    sentiment_start    REAL,
    sentiment_end      REAL,
    sentiment_delta    REAL,
    sentiment_direction TEXT,
    qa_score_pct       REAL,
    n_violations       INT NOT NULL DEFAULT 0,
    n_review_items     INT NOT NULL DEFAULT 0,
    verified           BOOLEAN NOT NULL DEFAULT FALSE,
    result             JSONB NOT NULL,                    -- full CallAnalysis document
    model_strong       TEXT,
    model_fast         TEXT,
    prompt_versions    JSONB NOT NULL DEFAULT '{}',       -- {"qa_scoring": "qa_scoring.v1", ...}
    config_version     TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analyses_call_idx ON analyses (call_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS qa_scores (
    analysis_id  BIGINT NOT NULL REFERENCES analyses ON DELETE CASCADE,
    call_id      TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    points       REAL,
    weight       REAL NOT NULL,
    critical     BOOLEAN NOT NULL,
    confidence   REAL,
    verified     BOOLEAN NOT NULL,
    rationale    TEXT,
    evidence     JSONB NOT NULL DEFAULT '[]',
    PRIMARY KEY (analysis_id, item_id)
);

CREATE TABLE IF NOT EXISTS violations (
    violation_id BIGSERIAL PRIMARY KEY,
    analysis_id  BIGINT NOT NULL REFERENCES analyses ON DELETE CASCADE,
    call_id      TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    severity     TEXT NOT NULL,
    source       TEXT NOT NULL,                           -- rule | llm | rule+llm
    description  TEXT,
    evidence     JSONB NOT NULL DEFAULT '[]',
    verified     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    review_id    BIGSERIAL PRIMARY KEY,
    call_id      TEXT NOT NULL,
    analysis_id  BIGINT REFERENCES analyses ON DELETE CASCADE,
    item_type    TEXT NOT NULL,
    item_ref     TEXT NOT NULL,
    reason       TEXT NOT NULL,
    payload      JSONB NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',         -- pending | accepted | overturned
    reviewer     TEXT,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS review_pending_idx ON review_queue (status, created_at);

-- Online LLM-judge results on a sample of analyses (faithfulness monitoring).
CREATE TABLE IF NOT EXISTS judge_results (
    id              BIGSERIAL PRIMARY KEY,
    call_id         TEXT NOT NULL,
    analysis_id     BIGINT REFERENCES analyses ON DELETE CASCADE,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    faithfulness    REAL,
    n_claims        INT NOT NULL,
    n_unsupported   INT NOT NULL,
    items           JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit + cost trail for every LLM call (the light-weight alternative to Langfuse).
CREATE TABLE IF NOT EXISTS llm_calls (
    id              BIGSERIAL PRIMARY KEY,
    call_id         TEXT,
    task            TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    input_tokens    INT NOT NULL DEFAULT 0,
    output_tokens   INT NOT NULL DEFAULT 0,
    latency_ms      INT NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_calls_created_idx ON llm_calls (created_at);

-- ------------------------------------------------------------------ roll-up views

CREATE OR REPLACE VIEW v_call_facts AS
SELECT c.call_id, c.agent_id, c.team_id, c.channel, c.started_at::date AS day,
       a.qa_score_pct, a.resolution, a.churn_flag, a.sentiment_delta, a.n_violations,
       a.reasons, a.verified
FROM calls c JOIN analyses a ON a.call_id = c.call_id AND a.is_current
WHERE NOT c.is_demo;

-- Agent roll-up. Raw averages are unfair for agents with few calls, so we also show a
-- shrunk score: (sum + k * team_mean) / (n + k), k = 5 pseudo-calls at the team mean.
CREATE OR REPLACE VIEW v_agent_rollup AS
WITH team AS (
    SELECT team_id, avg(qa_score_pct) AS team_mean FROM v_call_facts GROUP BY team_id
)
SELECT f.agent_id, f.team_id,
       count(*)                                                     AS n_calls,
       round(avg(f.qa_score_pct)::numeric, 1)                       AS avg_qa_pct,
       round(((sum(f.qa_score_pct) + 5 * t.team_mean) / (count(f.qa_score_pct) + 5))::numeric, 1) AS shrunk_qa_pct,
       round(avg((f.n_violations > 0)::int)::numeric, 3)            AS violation_rate,
       sum(f.n_violations)                                          AS n_violations,
       round(avg((f.resolution = 'resolved')::int)::numeric, 3)     AS resolution_rate,
       round(avg(f.churn_flag::int)::numeric, 3)                    AS churn_rate,
       round(avg(f.sentiment_delta)::numeric, 3)                    AS avg_sentiment_delta,
       count(*) < 5                                                 AS low_sample
FROM v_call_facts f JOIN team t USING (team_id)
GROUP BY f.agent_id, f.team_id, t.team_mean;

CREATE OR REPLACE VIEW v_team_rollup AS
SELECT team_id,
       count(DISTINCT agent_id)                                     AS n_agents,
       count(*)                                                     AS n_calls,
       round(avg(qa_score_pct)::numeric, 1)                         AS avg_qa_pct,
       round(avg((n_violations > 0)::int)::numeric, 3)              AS violation_rate,
       round(avg((resolution = 'resolved')::int)::numeric, 3)       AS resolution_rate,
       round(avg(churn_flag::int)::numeric, 3)                      AS churn_rate,
       round(avg(sentiment_delta)::numeric, 3)                      AS avg_sentiment_delta
FROM v_call_facts GROUP BY team_id;

-- Per-agent, per-checklist-item pass rate: shows WHAT to coach, not just who.
CREATE OR REPLACE VIEW v_agent_item_scores AS
SELECT c.agent_id, q.item_id,
       count(*) FILTER (WHERE q.points IS NOT NULL)                 AS n_scored,
       round(avg(q.points)::numeric, 3)                             AS avg_points,
       count(*) FILTER (WHERE q.verdict = 'fail')                   AS n_fail
FROM qa_scores q
JOIN analyses a ON a.analysis_id = q.analysis_id AND a.is_current
JOIN calls c ON c.call_id = q.call_id
WHERE NOT c.is_demo
GROUP BY c.agent_id, q.item_id;

CREATE OR REPLACE VIEW v_agent_daily AS
SELECT agent_id, team_id, day, count(*) AS n_calls,
       round(avg(qa_score_pct)::numeric, 1) AS avg_qa_pct,
       sum(n_violations) AS n_violations
FROM v_call_facts GROUP BY agent_id, team_id, day;

-- Drift monitoring: call-reason mix per day.
CREATE OR REPLACE VIEW v_reason_daily AS
SELECT day, reason, count(*) AS n_calls
FROM v_call_facts, unnest(reasons) AS reason
GROUP BY day, reason;

-- Human-review agreement: how often reviewers overturn the model.
CREATE OR REPLACE VIEW v_review_stats AS
SELECT item_type, reason,
       count(*)                                         AS n_items,
       count(*) FILTER (WHERE status = 'pending')       AS n_pending,
       count(*) FILTER (WHERE status = 'overturned')    AS n_overturned,
       round(count(*) FILTER (WHERE status = 'overturned')::numeric
             / nullif(count(*) FILTER (WHERE status <> 'pending'), 0), 3) AS overturn_rate
FROM review_queue GROUP BY item_type, reason;

CREATE OR REPLACE VIEW v_llm_cost_daily AS
SELECT created_at::date AS day, model, task, count(*) AS n_calls,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       round(sum(cost_usd), 4) AS cost_usd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status <> 'cache_hit') AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status <> 'cache_hit') AS p95_ms
FROM llm_calls GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW v_judge_daily AS
SELECT created_at::date AS day, model, count(*) AS n_judged,
       round(avg(faithfulness)::numeric, 3) AS avg_faithfulness,
       round(sum(n_unsupported)::numeric / nullif(sum(n_claims), 0), 3) AS unsupported_rate
FROM judge_results GROUP BY 1, 2;
