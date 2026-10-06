"""Assemble docs/eval_report.md from evals/results/*.json (numbers are copied by code, not by hand).

    PYTHONPATH=. uv run python evals/make_report.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "evals" / "results"


def load(name: str) -> dict | None:
    p = RES / name
    return json.loads(p.read_text()) if p.exists() else None


def pct(x) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def headline_table(dev: dict | None, held: dict | None) -> list[str]:
    rows = [("QA item accuracy (mean of 6 items)", "qa_item_accuracy_mean"),
            ("Compliance violations: recall", "violation_recall"),
            ("Compliance violations: precision", "violation_precision"),
            ("Call reasons: micro-F1", "reasons_micro_f1"),
            ("Resolution status: accuracy", "resolution_accuracy"),
            ("Churn flag: recall", "churn_recall"),
            ("Sentiment direction: agreement", "sentiment_direction_agreement"),
            ("Follow-up actions: recall (live replay)", "action_recall"),
            ("PII redaction: recall on planted values", "pii_recall"),
            ("Grounding: evidence quotes verified", "grounding_verified_rate")]
    out = ["| Metric | Dev (tuned on) | **Held-out (never tuned on)** |", "|---|---|---|"]
    for label, key in rows:
        out.append(f"| {label} | {pct(dev['headline'][key]) if dev else '–'} | **{pct(held['headline'][key]) if held else '–'}** |")
    return out


def qa_table(r: dict) -> list[str]:
    out = ["| Item | Accuracy | Fail precision | Fail recall | Planted fails |", "|---|---|---|---|---|"]
    for item, v in r["metrics"]["qa_items"].items():
        fc = v["fail_class"]
        out.append(f"| {item} | {pct(v['accuracy'])} | {pct(fc['precision'])} | {pct(fc['recall'])} | {fc['tp'] + fc['fn']} |")
    return out


def slices_table(r: dict) -> list[str]:
    out = ["| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution | PII recall |", "|---|---|---|---|---|---|---|"]
    for name, s in r["slices"].items():
        out.append(f"| {name} | {s['n']} | {pct(s['qa_item_accuracy_mean'])} | {pct(s['violation_recall'])} | "
                   f"{pct(s['reasons_micro_f1'])} | {pct(s['resolution_accuracy'])} | {pct(s['pii_recall'])} |")
    return out


TIMING_KEYS = ("live_update_ms", "latency_ms_by_task", "cost_per_call_usd", "tokens_per_call", "source_run")


def fresh_ops(split: str) -> dict | None:
    best, best_n = None, 0
    for p in RES.glob(f"run_{split}_*.json"):
        ops = json.loads(p.read_text())["ops"]
        if ops["live_update_ms"]["n"] > best_n:
            best, best_n = ops | {"source_run": p.name}, ops["live_update_ms"]["n"]
    return best


def main() -> None:
    dev, held = load("latest_dev.json"), load("latest_heldout.json")
    for r, split in ((dev, "dev"), (held, "heldout")):
        best = fresh_ops(split) if r else None
        if best:
            r["ops"] = r["ops"] | {k: best[k] for k in TIMING_KEYS if k in best}
    pii, sent, qa_exp, bench = (load("experiment_pii.json"), load("experiment_sentiment.json"),
                                load("experiment_qa_model.json"), load("latency_bench.json"))
    ref = held or dev
    used = {k.split(":", 1)[1] for r in (dev, held) if r for k in r["ops"]["models_used"]}
    configured = {ref["models"]["strong"], ref["models"]["fast"]}
    served = ("every call was answered by the configured models" if used <= configured
              else f"the fallback chain also served traffic ({', '.join(sorted(used - configured))})")
    L = ["# Eval report", "",
         f"Configured models: strong `{ref['models']['strong']}`, fast `{ref['models']['fast']}` ({served}) · config `{ref['config_version']}` · "
         f"PII backend `{ref['pii_backend']}` · data: 69 synthetic calls with planted, code-verified labels "
         "(49 dev / 20 held-out). Reproduce: `make eval`, `make eval-heldout`.", "",
         "## Quality (offline, against the answer key)", ""] + headline_table(dev, held)
    for name, r in (("Held-out", held), ("Dev", dev)):
        if not r:
            continue
        m = r["metrics"]
        pp = m["violations"]["prohibited_promise_recall"]
        L += ["", f"### {name}: QA items (the 'fail' class is the planted violation)", ""] + qa_table(r)
        L += ["", f"Prohibited promises (n={pp['n_planted']} planted): regex alone {pct(pp['regex_only'])}, "
              f"LLM alone {pct(pp['llm_only'])}. Violations by source: {m['violations']['by_source']}.",
              f"Resolution confusion (gold→pred): {m['outcomes']['resolution_confusion']}.",
              f"Churn: {m['outcomes']['churn']}.",
              f"Actions: {m['actions']}.",
              f"PII per type: {m['pii']['per_type_recall']}; extra redactions per call: {m['pii']['extra_redactions_per_call']}.",
              f"Grounding: {m['grounding']}.", "", f"#### {name}: slices", ""] + slices_table(r)
    L += ["", "## Service health and cost", ""]
    if bench:
        L += [f"Latency benchmark (cache off, {bench['calls']} held-out calls, one at a time, model `{bench['model']}`):", "",
              f"- fast path: **{bench['fast_path_ms_per_turn']} ms per turn** (redaction + sentiment)",
              f"- end-of-call analysis wall time: p50 **{bench['end_of_call_wall_s']['p50']} s**, max {bench['end_of_call_wall_s']['max']} s",
              f"- API latency by task (ms): {bench['api_ms_by_task']}", ""]
    if ref:
        o = ref["ops"]
        L += [f"Live action updates during the held-out replay (fresh calls, from `{o.get('source_run', 'latest run')}`): "
              f"p50 **{o['live_update_ms']['p50']} ms**, p95 {o['live_update_ms']['p95']} ms (n={o['live_update_ms']['n']}).",
              f"LLM latency by task (ms): {o['latency_ms_by_task']}.",
              f"Models that actually answered: {o['models_used']}.",
              f"Estimated cost per call (fresh calls): ${o['cost_per_call_usd']}; tokens per call: {o['tokens_per_call']}.", ""]
    L += ["## Additional exploration", ""]
    if pii:
        L += ["### PII: regex vs GLiNER vs hybrid (all 69 calls, 261 planted values)", "",
              "| Backend | Recall | Leaked | False positives / call | ms / turn (p50) |", "|---|---|---|---|---|"]
        for k, v in pii.items():
            L.append(f"| {k} | {pct(v['recall'])} | {v['leaked']} | {v['extra_redactions_per_call']} | {v['ms_per_turn_p50']} |")
        L += ["", "Per-type recall: " + "; ".join(f"**{k}** {v['per_type_recall']}" for k, v in pii.items()), ""]
    if qa_exp:
        n = next(iter(qa_exp.values())).get("n_calls")
        L += [f"### QA scoring: small vs larger (thinking) model ({n} dev calls answered by both)", "",
              "| Model | QA item accuracy | Violation recall | Est. cost / call | Output tokens (p50) |", "|---|---|---|---|---|"]
        for k, v in qa_exp.items():
            L.append(f"| {k} | {pct(v.get('qa_item_accuracy'))} | {pct(v.get('violation_recall'))} | ${v.get('cost_per_call_usd')} | {v.get('output_tokens_p50')} |")
        L.append("")
    judges = [load(f"experiment_judge_judge_v{v}.json") for v in (1, 2, 3)]
    if any(judges):
        L += ["### LLM judge vs the answer key (20 held-out calls)", "",
              "| Judge | Models | Agreement with system | Real system errors | Errors flagged | False alarms |",
              "|---|---|---|---|---|---|"]
        for j in judges:
            if j:
                L.append(f"| {j['judge_prompt']} | {', '.join(j['judge_models'])} | {pct(j['mean_faithfulness'])} | "
                         f"{j['system_errors']} | {j['judge_caught_errors']} ({pct(j['judge_recall_on_errors'])}) | "
                         f"{j['judge_false_alarms']} / {j['correct_claims']} ({pct(j['false_alarm_rate'])}) |")
        L.append("")
    if sent:
        L += ["### Sentiment: lexicon vs multilingual RoBERTa (69 calls)", "",
              "| Scorer | Direction agreement | Hinglish | ms / turn |", "|---|---|---|---|"]
        for k, v in sent.items():
            L.append(f"| {k} | {pct(v['direction_agreement'])} | {pct(v['hinglish_agreement'])} | {v['ms_per_turn_p50']} |")
        L.append("")
    (ROOT / "docs" / "eval_report.md").write_text("\n".join(L) + "\n")
    print("wrote docs/eval_report.md")


if __name__ == "__main__":
    main()
