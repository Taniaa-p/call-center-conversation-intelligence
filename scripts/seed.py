"""Load demo data through the public API (exactly like a real client would):
  * all synthetic calls (with agent/team IDs)
  * the HF corpus sample (no agent IDs in the source, so we assign demo IDs on team_corpus)
Analysis runs on the worker; this script waits until every call is analysed.

    make seed              # needs `make up` and data/synthetic + data/corpus_sample.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def synthetic_payloads() -> list[dict]:
    out = []
    for f in sorted((ROOT / "data" / "synthetic" / "calls").glob("*.json")):
        c = json.loads(f.read_text())
        body = {k: c[k] for k in ("call_id", "agent_id", "team_id", "channel", "started_at")}
        body |= {"messages": c["messages"]} if c["channel"] == "chat" else {"transcript": c["transcript"]}
        out.append(body)
    return out


def corpus_payloads(limit: int) -> list[dict]:
    path = ROOT / "data" / "corpus_sample.json"
    if not path.exists():
        return []
    out = []
    for i, conv in enumerate(json.loads(path.read_text())[:limit]):
        out.append({"call_id": f"corpus_{conv['conversation_id'][:10]}", "agent_id": f"corpus_agent_{i % 3 + 1}",
                    "team_id": "team_corpus", "channel": "voice", "messages": conv["messages"],
                    "started_at": conv["messages"][0]["date_time"]})
    return out


async def main(url: str, corpus_limit: int, reanalyze: bool) -> None:
    payloads = synthetic_payloads() + corpus_payloads(corpus_limit)
    if not payloads:
        sys.exit("no data: run `make synth` and `make fetch-corpus` first")
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
    async with httpx.AsyncClient(base_url=url, timeout=60, headers=headers) as client:
        if reanalyze:
            calls = (await client.get("/calls", params={"limit": 1000})).json()
            for c in calls:
                (await client.post(f"/calls/{c['call_id']}/analyze")).raise_for_status()
            ids = {c["call_id"] for c in calls}
            print(f"re-analysis queued for {len(ids)} calls", flush=True)
            await asyncio.sleep(5)
        else:
            for body in payloads:
                r = await client.post("/calls", json=body)
                r.raise_for_status()
            print(f"uploaded {len(payloads)} calls; waiting for the worker...", flush=True)
            ids = {p["call_id"] for p in payloads}
        while True:
            calls = (await client.get("/calls", params={"limit": 1000})).json()
            status = {c["call_id"]: c["status"] for c in calls if c["call_id"] in ids}
            done = sum(s in ("analyzed", "failed") for s in status.values())
            print(f"  {done}/{len(ids)} analysed", flush=True)
            if done == len(ids):
                break
            await asyncio.sleep(10)
        teams = (await client.get("/teams")).json()
        print(json.dumps(teams, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--corpus", type=int, default=15, help="how many corpus conversations to load")
    ap.add_argument("--reanalyze", action="store_true", help="re-score all stored calls (after a config/prompt change)")
    a = ap.parse_args()
    asyncio.run(main(a.url, a.corpus, a.reanalyze))
