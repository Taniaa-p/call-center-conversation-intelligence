"""Regression gate: fails if the latest eval run dropped below the committed baseline.

Workflow after any prompt/config/model change:
    make eval && uv run pytest evals/test_regression.py
CI does the same: it re-runs the dev eval from the committed LLM cache (data/cache), so a
code change that alters what the pipeline sends or scores shows up here without an API key.
To accept a new (better) baseline on purpose:  python evals/test_regression.py --update
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LATEST = HERE / "results" / "latest_dev.json"
BASELINE = HERE / "baseline.json"
TOLERANCE = 0.05


def load():
    if not LATEST.exists() or not BASELINE.exists():
        pytest.skip("no eval results yet: run `make eval` first")
    return json.loads(LATEST.read_text())["headline"], json.loads(BASELINE.read_text())


METRICS = ["qa_item_accuracy_mean", "violation_recall", "reasons_micro_f1", "resolution_accuracy",
           "churn_recall", "sentiment_direction_agreement", "action_recall", "pii_recall", "grounding_verified_rate"]


@pytest.mark.parametrize("metric", METRICS)
def test_metric_not_regressed(metric):
    latest, baseline = load()
    if baseline.get(metric) is None or latest.get(metric) is None:
        pytest.skip(f"{metric} not available")
    assert latest[metric] >= baseline[metric] - TOLERANCE, (
        f"{metric} regressed: {latest[metric]} < baseline {baseline[metric]} - {TOLERANCE}")


def test_pii_recall_is_a_hard_floor():
    latest, _ = load()
    assert latest["pii_recall"] >= 0.95


if __name__ == "__main__" and "--update" in sys.argv:
    BASELINE.write_text(json.dumps(json.loads(LATEST.read_text())["headline"], indent=1))
    print("baseline updated from", LATEST)
