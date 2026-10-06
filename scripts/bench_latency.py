"""Measure real end-of-call and live latency with the LLM cache OFF, one call at a time
(no queueing), so the numbers reflect the model + pipeline only.

    LLM_CACHE_ENABLED=false PYTHONPATH=. uv run python scripts/bench_latency.py 6
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_config  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.pipeline.analyze import analyze_call  # noqa: E402
from app.pipeline.fast_path import get_sentiment_scorer  # noqa: E402
from app.pipeline.pii import get_redactor  # noqa: E402
from app.schemas import CallMeta  # noqa: E402
from evals.run_evals import CALLS, RESULTS, load_split, raw_turns  # noqa: E402


async def main(n: int) -> None:
    llm, cfg, scorer = LLMClient(), get_config(), get_sentiment_scorer()
    walls, per_task, fast_ms = [], {}, []
    for lb in load_split("heldout")[:n]:
        call = json.loads((CALLS / f"{lb['call_id']}.json").read_text())
        raw = raw_turns(call)
        t = time.perf_counter()
        turns, _ = get_redactor("regex").redact_turns(raw)
        [scorer.score(x.text) for x in turns]
        fast_ms.append((time.perf_counter() - t) * 1000 / len(turns))
        t = time.perf_counter()
        a = await analyze_call(CallMeta(call_id=lb["call_id"]), turns, llm, cfg, scorer)
        walls.append(time.perf_counter() - t)
        for m in a.llm_calls:
            per_task.setdefault(m.task, []).append(m.latency_ms)
    out = {"calls": n, "end_of_call_wall_s": {"p50": round(statistics.median(walls), 2), "max": round(max(walls), 2)},
           "fast_path_ms_per_turn": round(statistics.mean(fast_ms), 3),
           "api_ms_by_task": {k: {"p50": statistics.median(v), "max": max(v), "n": len(v)} for k, v in per_task.items()},
           "model": llm.settings.gemini_model_strong}
    print(json.dumps(out, indent=1))
    (RESULTS / "latency_bench.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
