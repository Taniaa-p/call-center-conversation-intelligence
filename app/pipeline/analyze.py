"""End-of-call orchestration. Pure pipeline: no database, so the worker and the eval
harness run exactly the same code.

Input turns must already be REDACTED.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from app import metrics
from app.config import AppConfig
from app.llm import LLMClient
from app.pipeline.fast_path import RuleEngine, SentimentScorer, score_turn, trajectory
from app.pipeline.final_analysis import run_final_analysis
from app.pipeline.live_actions import LiveSession
from app.pipeline.qa_scoring import run_qa_scoring
from app.schemas import Action, CallAnalysis, CallMeta, ReviewItem, Turn

log = logging.getLogger(__name__)


def fast_path_all(turns: list[Turn], scorer: SentimentScorer, rules: RuleEngine):
    points = [p for t in turns if (p := score_turn(t, scorer))]
    flags = [f for t in turns for f in rules.check(t)]
    return points, flags


async def batch_actions(meta: CallMeta, turns: list[Turn], llm: LLMClient, config: AppConfig) -> tuple[list[Action], list]:
    session = LiveSession(meta.call_id, llm, config)
    for t in turns:
        session.turns[t.turn_id] = t
    triggering = [t for t in turns if session.should_trigger(t)]
    await session.process_batch(triggering, budget=False)
    return list(session.tracker.actions.values()), session.llm_calls


async def analyze_call(meta: CallMeta, turns: list[Turn], llm: LLMClient, config: AppConfig,
                       scorer: SentimentScorer, actions: list[Action] | None = None) -> CallAnalysis:
    started = time.perf_counter()
    rules = RuleEngine(config)
    points, flags = fast_path_all(turns, scorer, rules)
    llm_calls, review = [], []

    tasks = [run_final_analysis(turns, llm, config, meta.call_id),
             run_qa_scoring(turns, llm, config, flags, meta.call_id)]
    if actions is None:
        tasks.append(batch_actions(meta, turns, llm, config))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final = qa = None
    if isinstance(results[0], Exception):
        review.append(_failure("final_analysis", results[0]))
    else:
        final, rv, metas = results[0]
        review += rv
        llm_calls += metas
    if isinstance(results[1], Exception):
        review.append(_failure("qa_scoring", results[1]))
    else:
        qa, rv, metas = results[1]
        review += rv
        llm_calls += metas
    if actions is None:
        if isinstance(results[2], Exception):
            review.append(_failure("live_actions", results[2]))
            actions = []
        else:
            actions, metas = results[2]
            llm_calls += metas
    review += [ReviewItem(item_type="action", item_ref=a.action_id, reason="unverified_evidence",
                          payload=a.model_dump(mode="json")) for a in actions if not a.verified]

    for r in review:
        metrics.REVIEW_ITEMS.labels(r.reason).inc()
    metrics.CALL_ANALYSIS_LATENCY.observe(time.perf_counter() - started)
    return CallAnalysis(call_id=meta.call_id, final=final, qa=qa, sentiment=trajectory(points), rule_flags=flags,
                        actions=actions, review_items=review, llm_calls=llm_calls, config_version=config.version,
                        created_at=datetime.now(timezone.utc))


def _failure(stage: str, err: Exception) -> ReviewItem:
    log.error("stage %s failed: %s", stage, err)
    metrics.PIPELINE_ERRORS.labels(stage).inc()
    return ReviewItem(item_type="analysis", item_ref=stage, reason="analysis_failed", payload={"error": str(err)[:500]})
