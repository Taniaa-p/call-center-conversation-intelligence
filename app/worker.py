"""arq worker: runs end-of-call analysis jobs and the PII-retention cron.

arq is a small asyncio job queue on Redis (it uses a sorted set + keys, NOT Redis Streams).
Scale-out path: more worker replicas on the same queue; at very high volume move to Redis
Streams consumer groups or Kafka partitioned by call_id (see README).
"""
import logging
import random

from arq import Retry, cron
from arq.connections import RedisSettings
from prometheus_client import start_http_server

from app.db import repo
from app.service import Services
from app.settings import get_settings

log = logging.getLogger("worker")


async def startup(ctx: dict) -> None:
    logging.basicConfig(level=logging.INFO)
    ctx["svc"] = await Services.create()
    try:
        start_http_server(9101)
    except OSError:
        log.warning("metrics port 9101 busy; worker metrics disabled")


async def shutdown(ctx: dict) -> None:
    await ctx["svc"].pool.close()


MAX_TRIES = 3


async def analyze_call_job(ctx: dict, call_id: str) -> dict:
    analysis = await ctx["svc"].analyze(call_id)
    failed = [r.item_ref for r in analysis.review_items if r.reason == "analysis_failed"]
    if failed and ctx["job_try"] < MAX_TRIES:
        log.warning("call %s: stages %s failed, retry %d", call_id, failed, ctx["job_try"])
        raise Retry(defer=30 * ctx["job_try"])
    if random.random() < ctx["svc"].settings.judge_sample_rate:
        await ctx["redis"].enqueue_job("judge_call_job", call_id, _job_id=f"judge:{call_id}:{ctx['job_id']}")
    return {"call_id": call_id,
            "summary": analysis.final.summary if analysis.final else None,
            "qa_score_pct": analysis.qa.score_pct if analysis.qa else None,
            "n_violations": len(analysis.qa.violations) if analysis.qa else 0,
            "n_review_items": len(analysis.review_items)}


async def judge_call_job(ctx: dict, call_id: str) -> dict:
    return await ctx["svc"].judge(call_id)


async def purge_pii_job(ctx: dict) -> int:
    n = await repo.purge_expired_pii(ctx["svc"].pool)
    log.info("purged %d expired PII rows", n)
    return n


class WorkerSettings:
    functions = [analyze_call_job, judge_call_job]
    cron_jobs = [cron(purge_pii_job, minute=0)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 4
    job_timeout = 300
    keep_result = 3600
    max_tries = MAX_TRIES
