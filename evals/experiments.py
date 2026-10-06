"""Additional exploration experiments. Each prints a small table and saves JSON to evals/results/.

    PYTHONPATH=. uv run python evals/experiments.py pii      # regex vs GLiNER vs hybrid (needs `uv sync --extra ml`)
    PYTHONPATH=. uv run python evals/experiments.py qa_model [models...]  # small vs larger model for QA
    PYTHONPATH=. uv run python evals/experiments.py sentiment  # lexicon vs RoBERTa (needs --extra ml)
"""
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_config  # noqa: E402
from app.llm import LLMClient, estimate_cost  # noqa: E402
from app.pipeline.fast_path import RuleEngine  # noqa: E402
from app.pipeline.pii import RedactionContext, Redactor, RegexBackend  # noqa: E402
from app.pipeline.qa_scoring import run_qa_scoring  # noqa: E402
from app.settings import get_settings  # noqa: E402
from data.synth_generate import AGENT_NAMES  # noqa: E402
from evals.run_evals import CALLS, RESULTS, load_split, raw_turns  # noqa: E402


def pii_experiment() -> dict:
    from app.pipeline.pii import GlinerBackend, HybridBackend
    labels = load_split("all")
    backends = {"regex": RegexBackend()}
    t = time.perf_counter()
    gliner = GlinerBackend()
    print(f"GLiNER model loaded in {time.perf_counter() - t:.1f}s")
    raw = GlinerBackend.__new__(GlinerBackend)
    raw.model, raw.threshold, raw.filter = gliner.model, gliner.threshold, False
    backends["gliner_raw"] = raw
    backends["gliner"] = gliner
    hybrid = HybridBackend.__new__(HybridBackend)
    hybrid.backends = [backends["regex"], gliner]
    backends["hybrid"] = hybrid
    out = {}
    for name, backend in backends.items():
        red = Redactor(backend)
        planted = leaked = extra = 0
        per_type: dict[str, list[int]] = {}
        per_split: dict[str, list[int]] = {}
        latencies = []
        for lb in labels:
            call = json.loads((CALLS / f"{lb['call_id']}.json").read_text())
            ctx = RedactionContext()
            texts, entities = [], []
            for turn in raw_turns(call):
                s = time.perf_counter()
                rt, ents = red.redact_turn(turn, ctx)
                latencies.append((time.perf_counter() - s) * 1000)
                texts.append(rt.text)
                entities += ents
            text = " ".join(texts)
            values = [p["value"] for p in lb["pii"]]
            for p in lb["pii"]:
                leak = p["value"] in text or (p["type"] == "PERSON" and any(part in text for part in p["value"].split()))
                planted += 1
                leaked += leak
                per_type.setdefault(p["type"], [0, 0])
                per_type[p["type"]][0] += 1
                per_type[p["type"]][1] += leak
                per_split.setdefault(lb["split"], [0, 0])
                per_split[lb["split"]][0] += 1
                per_split[lb["split"]][1] += leak
            extra += sum(1 for e in entities if not any(e.value in v or v in e.value for v in values)
                         and e.value not in AGENT_NAMES.values())
        out[name] = {"recall": round(1 - leaked / planted, 3), "leaked": leaked, "planted": planted,
                     "extra_redactions_per_call": round(extra / len(labels), 2),
                     "per_type_recall": {k: round(1 - v[1] / v[0], 3) for k, v in sorted(per_type.items())},
                     "per_split_recall": {k: round(1 - v[1] / v[0], 3) for k, v in sorted(per_split.items())},
                     "ms_per_turn_p50": round(statistics.median(latencies), 2),
                     "ms_per_turn_p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2)}
    _table("PII redaction", out, ["recall", "leaked", "extra_redactions_per_call", "ms_per_turn_p50", "ms_per_turn_p95"])
    for name, r in out.items():
        print(f"  {name} per-type recall: {r['per_type_recall']}  per split: {r['per_split_recall']}")
    return out


async def qa_model_experiment(n: int = 15, models: list[str] | None = None) -> dict:
    from app.pipeline.pii import get_redactor
    labels = load_split("dev")[:n]
    config, llm = get_config(), LLMClient()
    rules = RuleEngine(config)
    s = get_settings()
    models = models or [s.gemini_model_fast, s.gemini_model_strong]
    out, per_model = {}, {}
    for model in models:
        async def one_safe(lb, model=model):
            call = json.loads((CALLS / f"{lb['call_id']}.json").read_text())
            turns, _ = get_redactor("regex").redact_turns(raw_turns(call))
            flags = [f for t in turns for f in rules.check(t)]
            return lb, await run_qa_scoring(turns, llm, config, flags, lb["call_id"], model_override=model)
        res = await asyncio.gather(*(one_safe(lb) for lb in labels), return_exceptions=True)
        per_model[model] = {r[0]["call_id"]: r for r in res if not isinstance(r, Exception)}
        print(f"{model}: {len(per_model[model])}/{len(labels)} calls succeeded")
    common = set.intersection(*(set(v) for v in per_model.values())) if per_model else set()
    print(f"comparing on {len(common)} calls answered by every model")
    for model in models:
        correct = total = vtp = vfn = 0
        costs, lats, toks = [], [], []

        ok = [per_model[model][cid] for cid in sorted(common)]
        for lb, (qa, _, metas) in ok:
            for it in qa.items:
                total += 1
                correct += it.verdict == lb["qa_expected"][it.item_id]
            pred = {v.item_id for v in qa.violations}
            gold = set(lb["violations_expected"])
            vtp, vfn = vtp + len(pred & gold), vfn + len(gold - pred)
            costs += [estimate_cost(m.model, m.input_tokens, m.output_tokens) for m in metas]
            toks += [m.output_tokens for m in metas]
            lats += [m.latency_ms for m in metas if m.status != "cache_hit"]
        if not ok:
            out[model] = {"n_calls": 0}
            continue
        out[model] = {"n_calls": len(ok), "qa_item_accuracy": round(correct / total, 3),
                      "violation_recall": round(vtp / (vtp + vfn), 3) if vtp + vfn else None,
                      "cost_per_call_usd": round(sum(costs) / max(1, len(ok)), 5),
                      "output_tokens_p50": statistics.median(toks) if toks else None,
                      "latency_p50_ms": statistics.median(lats) if lats else None}
    _table(f"QA scoring model ({len(common)} dev calls)", out,
           ["n_calls", "qa_item_accuracy", "violation_recall", "cost_per_call_usd", "output_tokens_p50"])
    return out


def sentiment_experiment() -> dict:
    from app.pipeline.fast_path import LexiconSentiment, RobertaSentiment, score_turn, trajectory
    labels = load_split("all")
    out = {}
    for name, scorer in (("lexicon", LexiconSentiment()), ("roberta_xlm", RobertaSentiment())):
        ok = ok_hi = n_hi = 0
        ms = []
        for lb in labels:
            call = json.loads((CALLS / f"{lb['call_id']}.json").read_text())
            pts = []
            for t in raw_turns(call):
                s0 = time.perf_counter()
                p = score_turn(t, scorer)
                if p:
                    ms.append((time.perf_counter() - s0) * 1000)
                    pts.append(p)
            hit = trajectory(pts).direction == lb["sentiment_direction"]
            ok += hit
            if lb["language"] == "hinglish":
                n_hi += 1
                ok_hi += hit
        out[name] = {"direction_agreement": round(ok / len(labels), 3),
                     "hinglish_agreement": round(ok_hi / n_hi, 3) if n_hi else None,
                     "ms_per_turn_p50": round(statistics.median(ms), 2)}
    _table(f"Sentiment direction ({len(labels)} calls)", out, ["direction_agreement", "hinglish_agreement", "ms_per_turn_p50"])
    return out


def _table(title: str, rows: dict, cols: list[str]) -> None:
    print(f"\n{title}\n| variant | " + " | ".join(cols) + " |\n|---|" + "---|" * len(cols))
    for name, r in rows.items():
        print(f"| {name} | " + " | ".join(str(r.get(c)) for c in cols) + " |")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "pii"
    if which == "pii":
        result = pii_experiment()
    elif which == "sentiment":
        result = sentiment_experiment()
    else:
        n = int(os.environ.get("EXP_N", "15"))
        result = asyncio.run(qa_model_experiment(n=n, models=sys.argv[2:] or None))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"experiment_{which}.json").write_text(json.dumps(result, indent=1))
