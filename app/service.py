"""Glue between the pure pipeline and the outside world (DB, queue, WebSocket).

`Services` is created once at startup by the API and by the worker.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field

import asyncpg

from app import metrics
from app.config import AppConfig, get_config
from app.db import repo
from app.llm import LLMClient
from app.pipeline.analyze import analyze_call
from app.pipeline.fast_path import RuleEngine, SentimentScorer, get_sentiment_scorer, score_turn
from app.pipeline.pii import RedactionContext, Redactor, get_redactor
from app.schemas import CallAnalysis, CallMeta, Turn
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    config: AppConfig
    pool: asyncpg.Pool
    llm: LLMClient
    redactor: Redactor
    scorer: SentimentScorer
    rules: RuleEngine
    _rules: dict = field(default_factory=dict)

    @classmethod
    async def create(cls) -> "Services":
        settings = get_settings()
        pool = await repo.create_pool(settings.asyncpg_dsn)
        llm = LLMClient(settings, recorder=lambda meta, call_id, err: repo.record_llm_call(pool, meta, call_id, err))
        config = get_config()
        return cls(settings, config, pool, llm, get_redactor(settings.pii_backend),
                   get_sentiment_scorer(settings.sentiment_backend), RuleEngine(config))

    def config_for(self, tenant: str) -> AppConfig:
        return get_config(tenant)

    def rules_for(self, tenant: str) -> RuleEngine:
        if tenant not in self._rules:
            self._rules[tenant] = RuleEngine(self.config_for(tenant))
        return self._rules[tenant]

    @property
    def models(self) -> dict[str, str]:
        return {"strong": self.settings.gemini_model_strong, "fast": self.settings.gemini_model_fast}


    async def ingest_turn(self, call_id: str, turn: Turn, ctx: RedactionContext,
                          tenant: str = "default") -> tuple[Turn, dict, bool]:
        started = time.perf_counter()
        redacted, entities = self.redactor.redact_turn(turn, ctx)
        point = score_turn(redacted, self.scorer)
        flags = [f.model_dump() for f in self.rules_for(tenant).check(redacted)]
        is_new = await repo.insert_turn(self.pool, call_id, redacted, point.score if point else None, flags)
        if is_new and entities:
            await repo.insert_pii(self.pool, call_id, entities, self.settings.pii_ttl_days)
        metrics.TURN_LATENCY.labels("fast").observe(time.perf_counter() - started)
        fast = {"sentiment": point.model_dump() if point else None, "flags": flags,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        return redacted, fast, is_new

    async def ingest_batch(self, meta: CallMeta, turns: list[Turn], source: str = "api") -> int:
        await repo.upsert_call(self.pool, meta, source=source, status="live")
        ctx = await repo.load_redaction_context(self.pool, meta.call_id)
        stored = 0
        for t in turns:
            _, _, is_new = await self.ingest_turn(meta.call_id, t, ctx, meta.tenant_id)
            stored += is_new
        await repo.set_call_status(self.pool, meta.call_id, "ended")
        return stored


    async def analyze(self, call_id: str) -> CallAnalysis:
        meta = await repo.get_call_meta(self.pool, call_id)
        if meta is None:
            raise KeyError(call_id)
        turns = await repo.get_turns(self.pool, call_id)
        actions = await repo.get_actions(self.pool, call_id)
        call = await self.pool.fetchrow("SELECT source FROM calls WHERE call_id = $1", call_id)
        existing = actions if call["source"] == "live" else None
        try:
            analysis = await analyze_call(meta, turns, self.llm, self.config_for(meta.tenant_id), self.scorer,
                                          actions=existing)
        except Exception:
            await repo.set_call_status(self.pool, call_id, "failed")
            raise
        await repo.save_analysis(self.pool, meta, analysis, self.models)
        return analysis


    async def judge(self, call_id: str) -> dict:
        from app.pipeline.judge import judge_analysis
        current = await repo.current_analysis(self.pool, call_id)
        if current is None:
            raise KeyError(call_id)
        analysis_id, analysis = current
        turns = await repo.get_turns(self.pool, call_id)
        call_meta = await repo.get_call_meta(self.pool, call_id)
        items, score, meta = await judge_analysis(analysis, turns, self.llm, self.config_for(call_meta.tenant_id),
                                                  self.settings.gemini_model_judge)
        await repo.save_judge(self.pool, call_id, analysis_id, meta.model, meta.prompt_version, score, items)
        if score is not None:
            metrics.JUDGE_FAITHFULNESS.observe(score)
        return {"call_id": call_id, "faithfulness": score, "n_claims": len(items),
                "unsupported": [i for i in items if i["support"] == "unsupported"]}


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"
