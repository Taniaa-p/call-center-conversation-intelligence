"""Offline quality evals against the synthetic answer key.

Runs the SAME pipeline code as production (normalize -> redact -> analyze_call, plus a
turn-by-turn live replay for follow-up actions) and compares with data/labels/labels.jsonl.

    make eval            # dev split (tune on this)
    make eval-heldout    # held-out split (report only; never tune on it)

Writes evals/results/latest_<split>.json and docs/eval_<split>.md.
LLM responses are cached on disk, so re-running costs nothing unless prompts/models change.
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_config  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.pipeline.analyze import analyze_call  # noqa: E402
from app.pipeline.fast_path import get_sentiment_scorer  # noqa: E402
from app.pipeline.live_actions import LiveSession  # noqa: E402
from app.pipeline.normalize import normalize  # noqa: E402
from app.pipeline.pii import get_redactor  # noqa: E402
from app.schemas import CallMeta, LLMMeta  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "data" / "labels" / "labels.jsonl"
CALLS = ROOT / "data" / "synthetic" / "calls"
RESULTS = ROOT / "evals" / "results"


def load_split(split: str) -> list[dict]:
    labels = [json.loads(line) for line in LABELS.read_text().splitlines()]
    return [lb for lb in labels if split == "all" or lb["split"] == split]


def raw_turns(call: dict):
    return normalize(call["messages"], "chat") if call["channel"] == "chat" else normalize(call["transcript"])


async def run_call(label: dict, llm: LLMClient, pii_backend: str, live: bool) -> dict:
    call = json.loads((CALLS / f"{label['call_id']}.json").read_text())
    config = get_config()
    turns, entities = get_redactor(pii_backend).redact_turns(raw_turns(call))
    meta = CallMeta(call_id=label["call_id"], agent_id=label["agent_id"], team_id=label["team_id"])
    started = time.perf_counter()
    analysis = await analyze_call(meta, turns, llm, config, get_sentiment_scorer(), actions=[])
    analysis_s = time.perf_counter() - started

    live_actions, live_calls, live_latencies = [], [], []
    if live:
        session = LiveSession(label["call_id"], llm, config)
        for t in turns:
            await session.add_turn_sync(t)
        live_actions = [a.model_dump() for a in session.tracker.actions.values()]
        live_calls = session.llm_calls
        live_latencies = [m.latency_ms for m in live_calls if m.status != "cache_hit"]
    return {"label": label, "analysis": analysis.model_dump(mode="json"), "actions": live_actions, "live": live,
            "redacted_text": " ".join(t.text for t in turns), "entities": [e.__dict__ for e in entities],
            "analysis_s": analysis_s, "live_latency_ms": live_latencies,
            "llm_calls": [m.model_dump() for m in analysis.llm_calls + live_calls]}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": _r(p), "recall": _r(r), "f1": _r(f), "tp": tp, "fp": fp, "fn": fn}


def _r(x):
    return None if x is None else round(x, 3)


def qa_metrics(runs: list[dict]) -> dict:
    out = {}
    items = [i.id for i in get_config().checklist.items]
    for item in items:
        tp = fp = fn = correct = n = 0
        confusion = Counter()
        for r in runs:
            qa = r["analysis"]["qa"]
            if not qa:
                continue
            pred = next(i["verdict"] for i in qa["items"] if i["item_id"] == item)
            gold = r["label"]["qa_expected"][item]
            confusion[f"{gold}->{pred}"] += 1
            n += 1
            correct += pred == gold or (gold == "pass" and pred == "partial" and item == "empathy")
            tp += gold == "fail" and pred == "fail"
            fp += gold != "fail" and pred == "fail"
            fn += gold == "fail" and pred != "fail"
        out[item] = {"accuracy": _r(correct / n if n else None), "fail_class": prf(tp, fp, fn), "n": n,
                     "confusion": dict(confusion)}
    return out


def violation_metrics(runs: list[dict]) -> dict:
    tp = fp = fn = 0
    by_source = Counter()
    pp_rule = pp_llm = pp_total = 0
    for r in runs:
        qa = r["analysis"]["qa"]
        pred = {v["item_id"] for v in qa["violations"]} if qa else set()
        gold = set(r["label"]["violations_expected"])
        tp, fp, fn = tp + len(pred & gold), fp + len(pred - gold), fn + len(gold - pred)
        for v in (qa["violations"] if qa else []):
            by_source[v["source"]] += 1
        if "no_prohibited_promises" in gold:
            pp_total += 1
            rule_hit = any(f["kind"] == "prohibited_promise" for f in r["analysis"]["rule_flags"])
            llm_hit = qa and any(i["item_id"] == "no_prohibited_promises" and i["verdict"] == "fail" for i in qa["items"])
            pp_rule += rule_hit
            pp_llm += bool(llm_hit)
    return {"overall": prf(tp, fp, fn), "by_source": dict(by_source),
            "prohibited_promise_recall": {"regex_only": _r(pp_rule / pp_total if pp_total else None),
                                          "llm_only": _r(pp_llm / pp_total if pp_total else None),
                                          "n_planted": pp_total}}


def reason_metrics(runs: list[dict]) -> dict:
    labels = get_config().taxonomy.ids
    per = {lb: [0, 0, 0] for lb in labels}
    exact = 0
    for r in runs:
        pred = set(r["analysis"]["final"]["reasons_labels"]) if r["analysis"]["final"] else set()
        gold = set(r["label"]["reasons"])
        exact += pred == gold
        for lb in labels:
            per[lb][0] += lb in pred and lb in gold
            per[lb][1] += lb in pred and lb not in gold
            per[lb][2] += lb in gold and lb not in pred
    micro = prf(*(sum(v[i] for v in per.values()) for i in range(3)))
    f1s = [prf(*v)["f1"] for lb, v in per.items() if v[0] + v[2] > 0]
    return {"micro": micro, "macro_f1": _r(statistics.mean(f1s) if f1s else None),
            "exact_match": _r(exact / len(runs)), "per_label": {lb: prf(*v) for lb, v in per.items() if sum(v)}}


def outcome_metrics(runs: list[dict]) -> dict:
    res_ok = churn_tp = churn_fp = churn_fn = churn_ok = sent_ok = 0
    res_conf, sent_conf = Counter(), Counter()
    for r in runs:
        f, lb = r["analysis"]["final"], r["label"]
        if f:
            res_ok += f["resolution"] == lb["resolution"]
            res_conf[f"{lb['resolution']}->{f['resolution']}"] += 1
            churn_ok += f["churn_flag"] == lb["churn"]
            churn_tp += f["churn_flag"] and lb["churn"]
            churn_fp += f["churn_flag"] and not lb["churn"]
            churn_fn += not f["churn_flag"] and lb["churn"]
        d = r["analysis"]["sentiment"]["direction"]
        sent_ok += d == lb["sentiment_direction"]
        sent_conf[f"{lb['sentiment_direction']}->{d}"] += 1
    n = len(runs)
    return {"resolution_accuracy": _r(res_ok / n), "resolution_confusion": dict(res_conf),
            "churn_accuracy": _r(churn_ok / n), "churn": prf(churn_tp, churn_fp, churn_fn),
            "sentiment_direction_agreement": _r(sent_ok / n), "sentiment_confusion": dict(sent_conf)}


def action_metrics(runs: list[dict]) -> dict:
    expected = found = on_time = n_pred = 0
    runs = [r for r in runs if r["live"]]
    for r in runs:
        n_pred += len(r["actions"])
        for exp in r["label"]["expected_actions"]:
            expected += 1
            match = [a for a in r["actions"] if any(k in a["text"].lower() for k in exp["keywords"])]
            if match:
                found += 1
                t = exp["turn_id"]
                on_time += t is None or any(a["created_turn"] <= t + 2 for a in match)
    return {"calls_replayed": len(runs), "expected": expected, "recall": _r(found / expected if expected else None),
            "on_time_rate": _r(on_time / found if found else None),
            "avg_actions_per_call": _r(n_pred / len(runs)) if runs else None}


def pii_metrics(runs: list[dict]) -> dict:
    per_type = defaultdict(lambda: [0, 0])
    extra = 0
    for r in runs:
        text = r["redacted_text"]
        planted = r["label"]["pii"]
        for p in planted:
            per_type[p["type"]][0] += 1
            per_type[p["type"]][1] += p["value"] in text or any(part in text for part in p["value"].split() if p["type"] == "PERSON" and len(part) > 2)
        values = " ".join(p["value"] for p in planted)
        extra += sum(1 for e in r["entities"] if e["value"] not in values and not any(e["value"] in p["value"] or p["value"] in e["value"] for p in planted))
    total = sum(v[0] for v in per_type.values())
    leaked = sum(v[1] for v in per_type.values())
    return {"recall": _r(1 - leaked / total if total else None), "planted": total, "leaked": leaked,
            "per_type_recall": {t: _r(1 - v[1] / v[0]) for t, v in sorted(per_type.items())},
            "extra_redactions_per_call": _r(extra / len(runs))}


def grounding_metrics(runs: list[dict]) -> dict:
    total = ok = 0
    for r in runs:
        a = r["analysis"]
        evs = []
        if a["final"]:
            evs += a["final"]["resolution_evidence"] + a["final"]["churn_evidence"]
            evs += [e for rs in a["final"]["reasons"] for e in rs["evidence"]]
        if a["qa"]:
            evs += [e for i in a["qa"]["items"] for e in i["evidence"]]
        evs += [e for act in r["actions"] for e in act["evidence"]]
        total += len(evs)
        ok += sum(e["verified"] for e in evs)
    retries = sum(1 for r in runs for m in r["llm_calls"] if m["task"].endswith("_regrounding"))
    review = Counter(i["reason"] for r in runs for i in r["analysis"]["review_items"])
    return {"evidence_quotes": total, "verified_rate": _r(ok / total if total else None),
            "regrounding_retries": retries, "review_items_per_call": _r(sum(review.values()) / len(runs)),
            "review_reasons": dict(review)}


def ops_metrics(runs: list[dict]) -> dict:
    calls = [LLMMeta(**m) for r in runs for m in r["llm_calls"]]
    fresh = [m for m in calls if m.status != "cache_hit"]
    by_task = defaultdict(list)
    for m in fresh:
        by_task[m.task].append(m.latency_ms)
    live = sorted(x for r in runs for x in r["live_latency_ms"] if x > 0)
    cost = sum(m.cost_usd for m in fresh)
    return {"llm_calls": len(calls), "cache_hits": len(calls) - len(fresh),
            "models_used": {f"{m.task}:{m.model}": n for (m_task, m_model), n in
                            Counter((m.task, m.model) for m in calls).items()
                            for m in [LLMMeta(task=m_task, model=m_model, prompt_version="")]},
            "status": dict(Counter(m.status for m in calls)),
            "latency_ms_by_task": {t: {"p50": _pct(v, 50), "p95": _pct(v, 95), "n": len(v)} for t, v in sorted(by_task.items())},
            "live_update_ms": {"p50": _pct(live, 50), "p95": _pct(live, 95), "n": len(live)},
            "cost_usd_fresh_calls": round(cost, 4),
            "cost_per_call_usd": round(cost / len(runs), 5) if fresh else None,
            "tokens_per_call": round(sum(m.input_tokens + m.output_tokens for m in fresh) / len(runs)) if fresh else None}


def _pct(values: list, p: int):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def compute(runs: list[dict]) -> dict:
    return {"n_calls": len(runs), "qa_items": qa_metrics(runs), "violations": violation_metrics(runs),
            "reasons": reason_metrics(runs), "outcomes": outcome_metrics(runs), "actions": action_metrics(runs),
            "pii": pii_metrics(runs), "grounding": grounding_metrics(runs)}


def headline(m: dict) -> dict:
    qa_acc = [v["accuracy"] for v in m["qa_items"].values() if v["accuracy"] is not None]
    return {"qa_item_accuracy_mean": _r(statistics.mean(qa_acc)) if qa_acc else None,
            "violation_recall": m["violations"]["overall"]["recall"],
            "violation_precision": m["violations"]["overall"]["precision"],
            "reasons_micro_f1": m["reasons"]["micro"]["f1"],
            "resolution_accuracy": m["outcomes"]["resolution_accuracy"],
            "churn_recall": m["outcomes"]["churn"]["recall"],
            "sentiment_direction_agreement": m["outcomes"]["sentiment_direction_agreement"],
            "action_recall": m["actions"]["recall"],
            "pii_recall": m["pii"]["recall"],
            "grounding_verified_rate": m["grounding"]["verified_rate"]}


def markdown(split: str, res: dict) -> str:
    h, m = res["headline"], res["metrics"]
    lines = [f"# Eval results: `{split}` split ({res['metrics']['n_calls']} calls)", "",
             f"Run {res['run_at']} · models: strong `{res['models']['strong']}`, fast `{res['models']['fast']}` · "
             f"config `{res['config_version']}` · PII backend `{res['pii_backend']}`", "",
             "## Headline", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in h.items()]
    lines += ["", "## QA items (fail class = the planted violation)", "",
              "| Item | Accuracy | Fail precision | Fail recall | n |", "|---|---|---|---|---|"]
    for item, v in m["qa_items"].items():
        lines.append(f"| {item} | {v['accuracy']} | {v['fail_class']['precision']} | {v['fail_class']['recall']} | {v['n']} |")
    pp = m["violations"]["prohibited_promise_recall"]
    lines += ["", f"Prohibited-promise recall: regex alone {pp['regex_only']}, LLM alone {pp['llm_only']} "
              f"(n={pp['n_planted']}); violation sources: {m['violations']['by_source']}", "",
              "## Breakdowns", "", "| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution acc | PII recall |",
              "|---|---|---|---|---|---|---|"]
    for name, sl in res["slices"].items():
        lines.append(f"| {name} | {sl['n']} | {sl['qa_item_accuracy_mean']} | {sl['violation_recall']} | "
                     f"{sl['reasons_micro_f1']} | {sl['resolution_accuracy']} | {sl['pii_recall']} |")
    o = res["ops"]
    lines += ["", "## Operations", "",
              f"- LLM calls: {o['llm_calls']} ({o['cache_hits']} cache hits), statuses {o['status']}",
              f"- Models that actually answered (task:model → calls): {o['models_used']}"]
    if o["live_update_ms"]["n"]:
        lines += [f"- Live action update latency (fresh calls): p50 {o['live_update_ms']['p50']} ms, p95 {o['live_update_ms']['p95']} ms",
                  f"- Latency by task: {json.dumps(o['latency_ms_by_task'])}",
                  f"- Estimated cost per call (fresh calls only): ${o['cost_per_call_usd']}, tokens per call: {o['tokens_per_call']}"]
    else:
        lines.append("- Latency and cost: not measured in this run (every answer came from the cache); "
                     "see docs/eval_report.md for the measured numbers")
    lines += [f"- Grounding: {m['grounding']}", f"- PII: {m['pii']}", f"- Actions: {m['actions']}",
              f"- Outcomes: {json.dumps(m['outcomes'])}"]
    return "\n".join(lines) + "\n"


async def main(split: str, pii_backend: str, live_limit: int, concurrency: int) -> None:
    labels = load_split(split)
    llm = LLMClient()
    sem = asyncio.Semaphore(concurrency)

    async def guarded(i, lb):
        async with sem:
            return await run_call(lb, llm, pii_backend, live=i < live_limit)

    started = time.perf_counter()
    runs = await asyncio.gather(*(guarded(i, lb) for i, lb in enumerate(labels)))
    for r in runs:
        if r["analysis"]["final"]:
            r["analysis"]["final"]["reasons_labels"] = [x["label"] for x in r["analysis"]["final"]["reasons"]]
    metrics = compute(runs)
    slices = {}
    for key in ("language", "channel"):
        for val in sorted({r["label"][key] for r in runs}):
            sub = [r for r in runs if r["label"][key] == val]
            slices[f"{key}={val}"] = {"n": len(sub), **headline(compute(sub))}
    from app.settings import get_settings
    s = get_settings()
    result = {"split": split, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "models": {"strong": s.gemini_model_strong, "fast": s.gemini_model_fast},
              "config_version": get_config().version, "pii_backend": pii_backend,
              "headline": headline(metrics), "metrics": metrics, "slices": slices, "ops": ops_metrics(runs),
              "wall_seconds": round(time.perf_counter() - started, 1)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"latest_{split}.json").write_text(json.dumps(result, indent=1))
    (RESULTS / f"run_{split}_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(json.dumps(result, indent=1))
    (ROOT / "docs" / f"eval_{split}.md").write_text(markdown(split, result))
    (RESULTS / f"predictions_{split}.json").write_text(json.dumps(
        [{"call_id": r["label"]["call_id"], "analysis": r["analysis"], "actions": r["actions"]} for r in runs], indent=1))
    print(json.dumps(result["headline"], indent=1))
    print(f"wrote docs/eval_{split}.md ({result['wall_seconds']} s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])
    ap.add_argument("--pii-backend", default="regex")
    ap.add_argument("--live-limit", type=int, default=1000,
                    help="replay only the first N calls turn by turn (saves quota); 0 = skip live replay")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    asyncio.run(main(a.split, a.pii_backend, a.live_limit, a.concurrency))
