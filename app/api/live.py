"""Live call WebSocket: /ws/calls/{call_id}

Client -> server messages:
    {"type": "turn", "speaker": "customer", "text": "...", "turn_id": 3 (optional), "ts": "..." (optional)}
    {"type": "end"}
Server -> client events:
    {"type": "turn", ...}      instant: redacted turn + sentiment + rule flags (fast path)
    {"type": "actions", ...}   follow-up action deltas + rolling summary (live LLM path)
    {"type": "live_error"}     LLM unavailable; those turns are retried with the next batch
    {"type": "ended"} then {"type": "analysis", ...} when the worker finishes

Sending the same turn_id twice is safe: it is acknowledged as a duplicate and ignored.
Reconnecting to the same call_id resumes: turns, actions and PII placeholders are reloaded.
"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import metrics
from app.api.auth import websocket_key_ok
from app.db import repo
from app.pipeline.live_actions import LiveSession
from app.pipeline.normalize import clean_text, map_speaker, parse_ts
from app.schemas import CallMeta, Turn

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/calls/{call_id}")
async def live_call(ws: WebSocket, call_id: str, agent_id: str = "unknown", team_id: str = "unknown",
                    channel: str = "voice", tenant_id: str = "default", demo: bool = False):
    if not websocket_key_ok(ws):
        await ws.close(code=4401)
        return
    svc = ws.app.state.svc
    try:
        config = svc.config_for(tenant_id)
    except (ValueError, FileNotFoundError):
        await ws.close(code=4404)
        return
    await ws.accept()
    meta = CallMeta(call_id=call_id, agent_id=agent_id, team_id=team_id, channel=channel, tenant_id=tenant_id,
                    is_demo=demo)
    await repo.upsert_call(svc.pool, meta, source="live")
    ctx = await repo.load_redaction_context(svc.pool, call_id)
    send_lock = asyncio.Lock()

    async def send(event: dict) -> None:
        async with send_lock:
            await ws.send_json(event)

    async def on_live_event(event: dict) -> None:
        if event["type"] == "actions":
            await repo.upsert_actions(svc.pool, call_id, list(session.tracker.actions.values()))
        await send(event)

    session = LiveSession(call_id, svc.llm, config, on_event=on_live_event,
                          actions=await repo.get_actions(svc.pool, call_id))
    for t in await repo.get_turns(svc.pool, call_id):
        session.turns[t.turn_id] = t
    next_id = max(session.turns, default=0) + 1
    metrics.ACTIVE_LIVE_CALLS.inc()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "turn":
                turn = Turn(turn_id=int(msg.get("turn_id") or next_id), speaker=map_speaker(msg.get("speaker", "")),
                            text=clean_text(msg.get("text", "")), ts=parse_ts(msg.get("ts")))
                next_id = max(next_id, turn.turn_id + 1)
                redacted, fast, is_new = await svc.ingest_turn(call_id, turn, ctx, tenant_id)
                await send({"type": "turn", "turn": redacted.model_dump(mode="json"), **fast, "duplicate": not is_new})
                if is_new:
                    await session.add_turn(redacted)
            elif msg.get("type") == "end":
                await end_call(svc, ws.app.state.arq, session, call_id, send)
                break
    except WebSocketDisconnect:
        log.info("live call %s disconnected", call_id)
    finally:
        await session.close()
        metrics.ACTIVE_LIVE_CALLS.dec()


async def end_call(svc, arq, session: LiveSession, call_id: str, send) -> None:
    await session.flush()
    await repo.upsert_actions(svc.pool, call_id, list(session.tracker.actions.values()))
    await repo.set_call_status(svc.pool, call_id, "ended")
    job = await arq.enqueue_job("analyze_call_job", call_id, _job_id=f"analyze:{call_id}")
    await send({"type": "ended", "call_id": call_id, "queued": job is not None})
    if job is None:
        return
    try:
        result = await job.result(timeout=240)
        await send({"type": "analysis", **result})
    except Exception as e:
        await send({"type": "analysis_pending", "detail": f"{type(e).__name__}; fetch GET /calls/{call_id} later"})
