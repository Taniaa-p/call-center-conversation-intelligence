"""Database access: plain SQL with asyncpg. Every query is visible here."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

from app.pipeline.pii import PIIEntity, RedactionContext
from app.schemas import Action, CallAnalysis, CallMeta, LLMMeta, ReviewItem, Turn

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_conn)
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(42)")
        try:
            await conn.execute(SCHEMA_SQL.read_text())
        finally:
            await conn.execute("SELECT pg_advisory_unlock(42)")
    return pool


async def upsert_call(pool, meta: CallMeta, source: str, status: str = "live") -> None:
    await pool.execute(
        """INSERT INTO calls (call_id, agent_id, team_id, channel, source, status, started_at, tenant_id, is_demo)
           VALUES ($1, $2, $3, $4, $5, $6, coalesce($7, now()), $8, $9)
           ON CONFLICT (call_id) DO UPDATE SET agent_id = EXCLUDED.agent_id, team_id = EXCLUDED.team_id""",
        meta.call_id, meta.agent_id, meta.team_id, meta.channel, source, status, meta.started_at, meta.tenant_id,
        meta.is_demo)


async def set_call_status(pool, call_id: str, status: str) -> None:
    col = {"ended": ", ended_at = now()", "analyzed": ", analyzed_at = now()"}.get(status, "")
    await pool.execute(f"UPDATE calls SET status = $2{col} WHERE call_id = $1", call_id, status)


async def insert_turn(pool, call_id: str, turn: Turn, sentiment: float | None, flags: list[dict]) -> bool:
    row = await pool.fetchrow(
        """INSERT INTO turns (call_id, turn_id, speaker, text_redacted, ts, sentiment, rule_flags)
           VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT DO NOTHING RETURNING turn_id""",
        call_id, turn.turn_id, turn.speaker, turn.text, turn.ts, sentiment, flags)
    return row is not None


async def get_turns(pool, call_id: str) -> list[Turn]:
    rows = await pool.fetch("SELECT turn_id, speaker, text_redacted, ts FROM turns WHERE call_id = $1 ORDER BY turn_id", call_id)
    return [Turn(turn_id=r["turn_id"], speaker=r["speaker"], text=r["text_redacted"], ts=r["ts"]) for r in rows]


async def get_call_meta(pool, call_id: str) -> CallMeta | None:
    r = await pool.fetchrow("SELECT call_id, agent_id, team_id, tenant_id, channel FROM calls WHERE call_id = $1", call_id)
    return CallMeta(**dict(r)) if r else None


async def insert_pii(pool, call_id: str, entities: list[PIIEntity], ttl_days: int) -> None:
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    await pool.executemany(
        """INSERT INTO pii_vault.entities (call_id, placeholder, entity_type, value, turn_id, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING""",
        [(call_id, e.placeholder, e.entity_type, e.value, e.turn_id, expires) for e in entities])


async def load_redaction_context(pool, call_id: str) -> RedactionContext:
    ctx = RedactionContext()
    rows = await pool.fetch("SELECT DISTINCT placeholder, entity_type, value FROM pii_vault.entities WHERE call_id = $1", call_id)
    for r in rows:
        ctx.restore(r["placeholder"], r["entity_type"], r["value"])
    return ctx


async def purge_expired_pii(pool) -> int:
    result = await pool.execute("DELETE FROM pii_vault.entities WHERE expires_at < now()")
    return int(result.split()[-1])


async def upsert_actions(pool, call_id: str, actions: list[Action]) -> None:
    await pool.executemany(
        """INSERT INTO actions (call_id, action_id, text, owner, status, created_turn, updated_turn,
                               evidence, verified, rationale, confidence)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
           ON CONFLICT (call_id, action_id) DO UPDATE SET text = EXCLUDED.text, owner = EXCLUDED.owner,
             status = EXCLUDED.status, updated_turn = EXCLUDED.updated_turn, evidence = EXCLUDED.evidence,
             verified = EXCLUDED.verified, rationale = EXCLUDED.rationale, confidence = EXCLUDED.confidence""",
        [(call_id, a.action_id, a.text, a.owner, a.status, a.created_turn, a.updated_turn,
          [e.model_dump() for e in a.evidence], a.verified, a.rationale, a.confidence) for a in actions])


async def get_actions(pool, call_id: str) -> list[Action]:
    rows = await pool.fetch("SELECT * FROM actions WHERE call_id = $1 ORDER BY created_turn, action_id", call_id)
    return [Action(**{k: v for k, v in dict(r).items() if k != "call_id"}) for r in rows]


async def save_analysis(pool, meta: CallMeta, a: CallAnalysis, models: dict[str, str]) -> int:
    f, qa, s = a.final, a.qa, a.sentiment
    prompt_versions = {m.task: m.prompt_version for m in a.llm_calls}
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE analyses SET is_current = FALSE WHERE call_id = $1", meta.call_id)
        analysis_id = await conn.fetchval(
            """INSERT INTO analyses (call_id, summary, reasons, resolution, churn_flag, churn_score,
                 sentiment_start, sentiment_end, sentiment_delta, sentiment_direction, qa_score_pct,
                 n_violations, n_review_items, verified, result, model_strong, model_fast,
                 prompt_versions, config_version)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
               RETURNING analysis_id""",
            meta.call_id, f.summary if f else None, [r.label for r in f.reasons] if f else [],
            f.resolution if f else None, f.churn_flag if f else None, f.churn_score if f else None,
            s.start, s.end, s.delta, s.direction, qa.score_pct if qa else None,
            len(qa.violations) if qa else 0, len(a.review_items),
            bool(f and f.verified and qa and all(i.verified for i in qa.items)),
            a.model_dump(mode="json"), models.get("strong"), models.get("fast"),
            prompt_versions, a.config_version)
        if qa:
            await conn.executemany(
                """INSERT INTO qa_scores (analysis_id, call_id, item_id, verdict, points, weight, critical,
                     confidence, verified, rationale, evidence) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                [(analysis_id, meta.call_id, i.item_id, i.verdict, i.points, i.weight, i.critical, i.confidence,
                  i.verified, i.rationale, [e.model_dump() for e in i.evidence]) for i in qa.items])
            await conn.executemany(
                """INSERT INTO violations (analysis_id, call_id, item_id, severity, source, description, evidence, verified)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                [(analysis_id, meta.call_id, v.item_id, v.severity, v.source, v.description,
                  [e.model_dump() for e in v.evidence], v.verified) for v in qa.violations])
        await insert_review_items(conn, meta.call_id, analysis_id, a.review_items)
        await conn.execute("UPDATE calls SET status = 'analyzed', analyzed_at = now() WHERE call_id = $1", meta.call_id)
    await upsert_actions(pool, meta.call_id, a.actions)
    return analysis_id


async def insert_review_items(conn, call_id: str, analysis_id: int | None, items: list[ReviewItem]) -> None:
    await conn.executemany(
        """INSERT INTO review_queue (call_id, analysis_id, item_type, item_ref, reason, payload)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        [(call_id, analysis_id, r.item_type, r.item_ref, r.reason, r.payload) for r in items])


async def get_call(pool, call_id: str) -> dict | None:
    call = await pool.fetchrow("SELECT * FROM calls WHERE call_id = $1", call_id)
    if not call:
        return None
    turns = await pool.fetch("SELECT turn_id, speaker, text_redacted AS text, ts, sentiment, rule_flags "
                             "FROM turns WHERE call_id = $1 ORDER BY turn_id", call_id)
    analysis = await pool.fetchrow(
        "SELECT analysis_id, result, model_strong, model_fast, prompt_versions, config_version, created_at "
        "FROM analyses WHERE call_id = $1 AND is_current", call_id)
    return {"call": dict(call), "turns": [dict(t) for t in turns],
            "actions": [a.model_dump(mode="json") for a in await get_actions(pool, call_id)],
            "analysis": dict(analysis) if analysis else None}


async def list_calls(pool, agent_id: str | None = None, team_id: str | None = None, limit: int = 100,
                     include_demo: bool = False) -> list[dict]:
    rows = await pool.fetch(
        """SELECT c.call_id, c.agent_id, c.team_id, c.channel, c.status, c.started_at, c.is_demo,
                  a.qa_score_pct, a.resolution, a.churn_flag, a.reasons, a.n_violations, a.sentiment_direction
           FROM calls c LEFT JOIN analyses a ON a.call_id = c.call_id AND a.is_current
           WHERE ($1::text IS NULL OR c.agent_id = $1) AND ($2::text IS NULL OR c.team_id = $2)
             AND ($4 OR NOT c.is_demo)
           ORDER BY c.started_at DESC LIMIT $3""", agent_id, team_id, limit, include_demo)
    return [dict(r) for r in rows]


async def fetch_view(pool, view: str, where: str = "", *args) -> list[dict]:
    allowed = {"v_agent_rollup", "v_team_rollup", "v_agent_item_scores", "v_agent_daily",
               "v_reason_daily", "v_review_stats", "v_llm_cost_daily", "v_judge_daily"}
    if view not in allowed:
        raise ValueError(view)
    rows = await pool.fetch(f"SELECT * FROM {view} {where}", *args)
    return [dict(r) for r in rows]


async def reason_drift(pool, recent_days: int = 1, baseline_days: int = 7) -> list[dict]:
    rows = await pool.fetch(
        """WITH w AS (
             SELECT reason, (day > current_date - $1::int) AS recent, sum(n_calls) AS n
             FROM v_reason_daily WHERE day > current_date - ($1::int + $2::int) GROUP BY 1, 2),
           tot AS (SELECT recent, sum(n) AS total FROM w GROUP BY recent)
           SELECT w.reason,
                  round(coalesce(sum(n) FILTER (WHERE w.recent), 0) / nullif(max(t1.total), 0), 3) AS recent_share,
                  round(coalesce(sum(n) FILTER (WHERE NOT w.recent), 0) / nullif(max(t0.total), 0), 3) AS baseline_share
           FROM w LEFT JOIN tot t1 ON t1.recent LEFT JOIN tot t0 ON NOT t0.recent
           GROUP BY w.reason ORDER BY w.reason""", recent_days, baseline_days)
    return [dict(r) for r in rows]


async def list_review(pool, status: str = "pending", limit: int = 100) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM review_queue WHERE status = $1 ORDER BY created_at LIMIT $2", status, limit)
    return [dict(r) for r in rows]


async def resolve_review(pool, review_id: int, status: str, reviewer: str, note: str | None) -> bool:
    res = await pool.execute(
        "UPDATE review_queue SET status = $2, reviewer = $3, note = $4, resolved_at = now() "
        "WHERE review_id = $1 AND status = 'pending'", review_id, status, reviewer, note)
    return res.endswith("1")


async def record_llm_call(pool, meta: LLMMeta, call_id: str | None, error: str | None) -> None:
    await pool.execute(
        """INSERT INTO llm_calls (call_id, task, model, prompt_version, input_tokens, output_tokens,
             latency_ms, cost_usd, status, error) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        call_id, meta.task, meta.model, meta.prompt_version, meta.input_tokens, meta.output_tokens,
        meta.latency_ms, meta.cost_usd, meta.status, error)


async def cost_summary(pool) -> dict:
    r = await pool.fetchrow(
        """SELECT count(DISTINCT call_id) AS calls, coalesce(sum(cost_usd), 0) AS cost_usd,
                  coalesce(sum(input_tokens + output_tokens), 0) AS tokens
           FROM llm_calls WHERE call_id IS NOT NULL""")
    calls = r["calls"] or 0
    return {"calls_with_llm": calls, "total_cost_usd": float(r["cost_usd"]), "total_tokens": r["tokens"],
            "avg_cost_per_call_usd": round(float(r["cost_usd"]) / calls, 6) if calls else None}


async def current_analysis(pool, call_id: str) -> tuple[int, CallAnalysis] | None:
    r = await pool.fetchrow("SELECT analysis_id, result FROM analyses WHERE call_id = $1 AND is_current", call_id)
    return (r["analysis_id"], CallAnalysis(**r["result"])) if r else None


async def save_judge(pool, call_id: str, analysis_id: int, model: str, prompt_version: str,
                     faithfulness: float | None, items: list[dict]) -> None:
    await pool.execute(
        """INSERT INTO judge_results (call_id, analysis_id, model, prompt_version, faithfulness, n_claims, n_unsupported, items)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        call_id, analysis_id, model, prompt_version, faithfulness, len(items),
        sum(1 for i in items if i["support"] == "unsupported"), items)
