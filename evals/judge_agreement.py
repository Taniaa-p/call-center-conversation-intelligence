"""Exploration: how far can we trust the online LLM judge? Compare its flags with the answer key.

For every judged claim (QA items, resolution, churn) we know from the labels whether the system's
answer was actually right. A useful judge flags wrong answers and leaves right ones alone.

    PYTHONPATH=. uv run python evals/judge_agreement.py      (needs the stack running + judged calls)
"""
import asyncio
import json
import statistics
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.settings import get_settings  # noqa: E402
from evals.run_evals import RESULTS, load_split  # noqa: E402


def system_correct(claim_id: str, analysis: dict, label: dict) -> bool | None:
    if claim_id.startswith("qa:"):
        item = claim_id[3:]
        pred = next((i["verdict"] for i in analysis["qa"]["items"] if i["item_id"] == item), None)
        return pred == label["qa_expected"].get(item)
    if claim_id == "resolution":
        return analysis["final"]["resolution"] == label["resolution"]
    if claim_id == "churn":
        return analysis["final"]["churn_flag"] == label["churn"]
    if claim_id.startswith("reason:"):
        return claim_id[7:] in label["reasons"]
    return None


async def main() -> None:
    labels = {lb["call_id"]: lb for lb in load_split("all")}
    conn = await asyncpg.connect(get_settings().asyncpg_dsn)
    version = sys.argv[1] if len(sys.argv) > 1 else "judge.v2"
    rows = await conn.fetch(
        """SELECT DISTINCT ON (j.call_id) j.call_id, j.model, j.faithfulness, j.items::text AS items, a.result::text AS result
           FROM judge_results j JOIN analyses a ON a.analysis_id = j.analysis_id
           WHERE j.prompt_version = $1 ORDER BY j.call_id, j.id DESC""", version)
    await conn.close()
    flagged_wrong = n_wrong = flagged_right = n_right = 0
    faith, misses, false_alarms = [], [], []
    for r in rows:
        if r["call_id"] not in labels:
            continue
        analysis, label = json.loads(r["result"]), labels[r["call_id"]]
        faith.append(r["faithfulness"])
        for it in json.loads(r["items"]):
            ok = system_correct(it["claim_id"], analysis, label)
            if ok is None:
                continue
            flagged = it["support"] != "supported"
            if ok:
                n_right += 1
                flagged_right += flagged
                if flagged:
                    false_alarms.append(f"{r['call_id']} {it['claim_id']}: {it['reason'][:120]}")
            else:
                n_wrong += 1
                flagged_wrong += flagged
                if not flagged:
                    misses.append(f"{r['call_id']} {it['claim_id']}")
    out = {"judge_prompt": version, "calls_judged": len(faith),
           "judge_models": sorted({r["model"] for r in rows}),
           "mean_faithfulness": round(statistics.mean(faith), 3) if faith else None,
           "system_errors": n_wrong, "judge_caught_errors": flagged_wrong,
           "judge_recall_on_errors": round(flagged_wrong / n_wrong, 3) if n_wrong else None,
           "correct_claims": n_right, "judge_false_alarms": flagged_right,
           "false_alarm_rate": round(flagged_right / n_right, 3) if n_right else None,
           "missed_errors": misses, "false_alarm_examples": false_alarms[:8]}
    print(json.dumps(out, indent=1))
    (RESULTS / f"experiment_judge_{version.replace('.', '_')}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
