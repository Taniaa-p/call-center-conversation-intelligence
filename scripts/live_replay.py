"""Replay a transcript over the live WebSocket, turn by turn, and print every event.

    uv run python scripts/live_replay.py data/synthetic/calls/syn_001.json --delay 1.5
    uv run python scripts/live_replay.py --text "Agent: hi\nCustomer: my bill is wrong"
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pipeline.normalize import normalize  # noqa: E402


def load_turns(args) -> tuple[list[dict], dict]:
    if args.text:
        return [t.model_dump(mode="json") for t in normalize(args.text.replace("\\n", "\n"))], {}
    call = json.loads(Path(args.file).read_text())
    return call["turns"], {"agent_id": call.get("agent_id", "unknown"), "team_id": call.get("team_id", "unknown"),
                           "channel": call.get("channel", "voice")}


async def main(args) -> None:
    turns, meta = load_turns(args)
    call_id = args.call_id or f"live_{uuid.uuid4().hex[:8]}"
    if os.environ.get("API_KEY"):
        meta["api_key"] = os.environ["API_KEY"]
    query = "&".join(f"{k}={v}" for k, v in meta.items())
    url = f"{args.url}/ws/calls/{call_id}?{query}"
    print(f"connecting {url}")
    async with websockets.connect(url) as ws:
        async def reader():
            async for raw in ws:
                ev = json.loads(raw)
                stamp = time.strftime("%H:%M:%S")
                if ev["type"] == "turn":
                    s = ev["sentiment"]
                    print(f"{stamp} [fast {ev['latency_ms']}ms] #{ev['turn']['turn_id']} {ev['turn']['speaker']}: "
                          f"{ev['turn']['text'][:90]}" + (f"  sentiment={s['score']}" if s else "")
                          + (f"  FLAGS={[f['rule_id'] for f in ev['flags']]}" if ev["flags"] else "")
                          + ("  (duplicate)" if ev["duplicate"] else ""))
                elif ev["type"] == "actions":
                    print(f"{stamp} [live {ev['latency_ms']}ms turns={ev['turn_ids']}] summary: {ev['summary']}")
                    for c in ev["changes"]:
                        print(f"           {c['op'].upper():8} {c.get('action_id', '')} {c.get('text', '')} ({c.get('status', '')})")
                else:
                    print(f"{stamp} {json.dumps(ev)[:400]}")
                if ev["type"] in ("analysis", "analysis_pending"):
                    return

        task = asyncio.create_task(reader())
        for t in turns:
            await ws.send(json.dumps({"type": "turn", "turn_id": t["turn_id"], "speaker": t["speaker"], "text": t["text"]}))
            await asyncio.sleep(args.delay)
        if args.resend:
            await ws.send(json.dumps({"type": "turn", **{k: turns[0][k] for k in ("turn_id", "speaker", "text")}}))
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(task, timeout=300)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file", nargs="?")
    p.add_argument("--text")
    p.add_argument("--url", default="ws://localhost:8000")
    p.add_argument("--call-id")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between turns")
    p.add_argument("--resend", action="store_true", help="re-send turn 1 at the end (idempotency demo)")
    asyncio.run(main(p.parse_args()))
