"""REST endpoints."""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app import metrics
from app.db import repo
from app.pipeline.normalize import normalize
from app.schemas import CallMeta
from app.service import Services, new_call_id

router = APIRouter()


def svc(request: Request) -> Services:
    return request.app.state.svc


class CallIn(BaseModel):
    call_id: str | None = None
    agent_id: str = "unknown"
    team_id: str = "unknown"
    tenant_id: str = Field("default", pattern=r"^[a-z0-9_-]{1,64}$")
    channel: Literal["voice", "chat"] = "voice"
    started_at: datetime | None = None
    transcript: str | None = Field(None, description="'Agent: ...' / 'Customer: ...' lines")
    messages: list[dict] | None = Field(None, description="chat log: [{sender, message, timestamp}]")
    analyze: Literal["async", "sync", "none"] = "async"
    demo: bool = False


class ReviewDecision(BaseModel):
    status: Literal["accepted", "overturned"]
    reviewer: str
    note: str | None = None


@router.post("/calls", status_code=202, tags=["calls"])
async def create_call(body: CallIn, request: Request):
    if not body.transcript and not body.messages:
        raise HTTPException(422, "provide `transcript` or `messages`")
    turns = normalize(body.transcript) if body.transcript else normalize(body.messages, "chat")
    if not turns:
        raise HTTPException(422, "no turns could be parsed")
    try:
        svc(request).config_for(body.tenant_id)
    except FileNotFoundError:
        raise HTTPException(422, f"unknown tenant {body.tenant_id}") from None
    meta = CallMeta(call_id=body.call_id or new_call_id(), agent_id=body.agent_id, team_id=body.team_id,
                    tenant_id=body.tenant_id,
                    channel=body.channel, started_at=body.started_at, is_demo=body.demo)
    stored = await svc(request).ingest_batch(meta, turns, source="api")
    out = {"call_id": meta.call_id, "turns": len(turns), "turns_stored": stored}
    return out | await _run_analysis(request, meta.call_id, body.analyze)


@router.post("/calls/{call_id}/analyze", status_code=202, tags=["calls"])
async def analyze_call(call_id: str, request: Request, mode: Literal["async", "sync"] = "async"):
    if not await repo.get_call_meta(svc(request).pool, call_id):
        raise HTTPException(404, "call not found")
    await repo.set_call_status(svc(request).pool, call_id, "ended")
    return await _run_analysis(request, call_id, mode, rerun=True)


async def _run_analysis(request: Request, call_id: str, mode: str, rerun: bool = False) -> dict:
    if mode == "none":
        return {}
    if mode == "sync":
        a = await svc(request).analyze(call_id)
        return {"analysis": a.model_dump(mode="json")}
    job_id = f"analyze:{call_id}" + (f":{int(time.time())}" if rerun else "")
    job = await request.app.state.arq.enqueue_job("analyze_call_job", call_id, _job_id=job_id)
    return {"job_id": job_id, "queued": job is not None}


@router.get("/calls", tags=["calls"])
async def list_calls(request: Request, agent_id: str | None = None, team_id: str | None = None, limit: int = 100,
                     include_demo: bool = False):
    return await repo.list_calls(svc(request).pool, agent_id, team_id, limit, include_demo)


@router.get("/calls/{call_id}", tags=["calls"])
async def get_call(call_id: str, request: Request):
    data = await repo.get_call(svc(request).pool, call_id)
    if not data:
        raise HTTPException(404, "call not found")
    return data


@router.get("/agents", tags=["rollups"])
async def agents(request: Request, team_id: str | None = None):
    where, args = ("WHERE team_id = $1", [team_id]) if team_id else ("", [])
    return await repo.fetch_view(svc(request).pool, "v_agent_rollup", where + " ORDER BY shrunk_qa_pct DESC NULLS LAST", *args)


@router.get("/agents/{agent_id}", tags=["rollups"])
async def agent_detail(agent_id: str, request: Request):
    pool = svc(request).pool
    rollup = await repo.fetch_view(pool, "v_agent_rollup", "WHERE agent_id = $1", agent_id)
    if not rollup:
        raise HTTPException(404, "agent not found")
    return {"rollup": rollup[0],
            "items": await repo.fetch_view(pool, "v_agent_item_scores", "WHERE agent_id = $1 ORDER BY item_id", agent_id),
            "daily": await repo.fetch_view(pool, "v_agent_daily", "WHERE agent_id = $1 ORDER BY day", agent_id),
            "calls": await repo.list_calls(pool, agent_id=agent_id)}


@router.get("/teams", tags=["rollups"])
async def teams(request: Request):
    return await repo.fetch_view(svc(request).pool, "v_team_rollup", "ORDER BY team_id")


@router.get("/teams/{team_id}", tags=["rollups"])
async def team_detail(team_id: str, request: Request):
    pool = svc(request).pool
    rollup = await repo.fetch_view(pool, "v_team_rollup", "WHERE team_id = $1", team_id)
    if not rollup:
        raise HTTPException(404, "team not found")
    agents_ = await repo.fetch_view(pool, "v_agent_rollup", "WHERE team_id = $1 ORDER BY shrunk_qa_pct DESC NULLS LAST", team_id)
    return {"rollup": rollup[0], "agents": agents_}


@router.get("/review", tags=["review"])
async def review_queue(request: Request, status: str = "pending", limit: int = 100):
    return await repo.list_review(svc(request).pool, status, limit)


@router.post("/review/{review_id}", tags=["review"])
async def resolve_review(review_id: int, body: ReviewDecision, request: Request):
    if not await repo.resolve_review(svc(request).pool, review_id, body.status, body.reviewer, body.note):
        raise HTTPException(404, "review item not found or already resolved")
    return {"review_id": review_id, "status": body.status}


DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "calls"


@router.get("/demo/calls", tags=["demo"])
async def demo_calls():
    out = []
    for f in sorted(DEMO_DIR.glob("*.json"))[:40]:
        c = json.loads(f.read_text())
        out.append({"call_id": c["call_id"], "agent_id": c["agent_id"], "team_id": c["team_id"],
                    "channel": c["channel"], "language": c["language"], "n_turns": len(c["turns"])})
    return out


@router.get("/demo/calls/{call_id}", tags=["demo"])
async def demo_call(call_id: str):
    path = DEMO_DIR / f"{Path(call_id).name}.json"
    if not path.exists():
        raise HTTPException(404, "demo call not found")
    return json.loads(path.read_text())


@router.get("/monitoring/drift", tags=["monitoring"])
async def drift(request: Request, recent_days: int = 1, baseline_days: int = 7, alert_at: float = 0.15):
    rows = await repo.reason_drift(svc(request).pool, recent_days, baseline_days)
    for r in rows:
        shift = abs(float(r["recent_share"] or 0) - float(r["baseline_share"] or 0))
        r["shift"], r["alert"] = round(shift, 3), shift > alert_at
    return rows


@router.get("/monitoring/llm", tags=["monitoring"])
async def llm_usage(request: Request):
    pool = svc(request).pool
    return {"summary": await repo.cost_summary(pool),
            "daily": await repo.fetch_view(pool, "v_llm_cost_daily", "ORDER BY day DESC, model, task")}


@router.post("/calls/{call_id}/judge", tags=["monitoring"])
async def judge_call(call_id: str, request: Request):
    try:
        return await svc(request).judge(call_id)
    except KeyError:
        raise HTTPException(404, "call has no analysis yet") from None


@router.get("/monitoring/judge", tags=["monitoring"])
async def judge_stats(request: Request):
    return await repo.fetch_view(svc(request).pool, "v_judge_daily", "ORDER BY day DESC")


@router.get("/monitoring/review", tags=["monitoring"])
async def review_stats(request: Request):
    return await repo.fetch_view(svc(request).pool, "v_review_stats", "ORDER BY n_items DESC")


@router.get("/config", tags=["ops"])
async def config(request: Request, tenant_id: str = "default"):
    try:
        c = svc(request).config_for(tenant_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "unknown tenant") from None
    return {"tenant_id": tenant_id, "version": c.version, "checklist": c.checklist, "taxonomy": c.taxonomy, "policy": c.policy}


@router.get("/health", tags=["ops"])
async def health(request: Request, response: Response, deep: bool = False):
    s, checks = svc(request), {}
    try:
        await s.pool.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {type(e).__name__}"
    try:
        await request.app.state.arq.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
    checks["llm"] = "configured" if s.settings.gemini_api_key else "missing GEMINI_API_KEY"
    if deep and s.llm._client:
        try:
            await s.llm._client.aio.models.get(model=s.settings.gemini_model_fast)
            checks["llm"] = "ok"
        except Exception as e:
            checks["llm"] = f"error: {type(e).__name__}"
    healthy = checks["db"] == "ok" and checks["redis"] == "ok"
    response.status_code = 200 if healthy else 503
    return {"status": "ok" if healthy else "degraded", **checks, "config_version": s.config.version}


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    try:
        metrics.QUEUE_DEPTH.set(await request.app.state.arq.zcard("arq:queue"))
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
