# Eval results: `heldout` split (20 calls)

Run 2026-10-05T16:38:34+00:00 · models: strong `gemini-3.5-flash-lite`, fast `gemini-3.5-flash-lite` · config `13873791f1a3` · PII backend `regex`

## Headline

| Metric | Value |
|---|---|
| qa_item_accuracy_mean | 0.992 |
| violation_recall | 0.909 |
| violation_precision | 1.0 |
| reasons_micro_f1 | 0.789 |
| resolution_accuracy | 0.75 |
| churn_recall | 0.714 |
| sentiment_direction_agreement | 0.9 |
| action_recall | 1.0 |
| pii_recall | 1.0 |
| grounding_verified_rate | 1.0 |

## QA items (fail class = the planted violation)

| Item | Accuracy | Fail precision | Fail recall | n |
|---|---|---|---|---|
| greeting | 1.0 | 1.0 | 1.0 | 20 |
| identity_verification | 1.0 | 1.0 | 1.0 | 20 |
| empathy | 1.0 | None | None | 20 |
| correct_disclosure | 0.95 | 1.0 | 0.667 | 20 |
| no_prohibited_promises | 1.0 | 1.0 | 1.0 | 20 |
| proper_closure | 1.0 | 1.0 | 1.0 | 20 |

Prohibited-promise recall: regex alone 0.333, LLM alone 1.0 (n=3); violation sources: {'llm': 9, 'rule+llm': 1}

## Breakdowns

| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution acc | PII recall |
|---|---|---|---|---|---|---|
| language=en | 19 | 0.991 | 0.9 | 0.794 | 0.737 | 1.0 |
| language=hinglish | 1 | 1.0 | 1.0 | 0.667 | 1.0 | 1.0 |
| channel=chat | 5 | 1.0 | 1.0 | 0.875 | 0.6 | 1.0 |
| channel=voice | 15 | 0.989 | 0.875 | 0.764 | 0.8 | 1.0 |

## Operations

- LLM calls: 151 (101 cache hits), statuses {'cache_hit': 101, 'ok': 50}
- Models that actually answered (task:model → calls): {'final_analysis:gemini-3.5-flash-lite': 20, 'qa_scoring:gemini-3.5-flash-lite': 20, 'live_actions:gemini-3.5-flash-lite': 111}
- Live action update latency (fresh calls): p50 1418 ms, p95 2254 ms
- Latency by task: {"live_actions": {"p50": 1418, "p95": 2254, "n": 40}, "qa_scoring": {"p50": 2700, "p95": 3573, "n": 10}}
- Estimated cost per call (fresh calls only): $0.00043, tokens per call: 2663
- Grounding: {'evidence_quotes': 272, 'verified_rate': 1.0, 'regrounding_retries': 0, 'review_items_per_call': 0.0, 'review_reasons': {}}
- PII: {'recall': 1.0, 'planted': 69, 'leaked': 0, 'per_type_recall': {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 1.0}, 'extra_redactions_per_call': 1.6}
- Actions: {'calls_replayed': 15, 'expected': 14, 'recall': 1.0, 'on_time_rate': 1.0, 'avg_actions_per_call': 1.667}
- Outcomes: {"resolution_accuracy": 0.75, "resolution_confusion": {"resolved->resolved": 7, "escalated->escalated": 1, "unresolved->unresolved": 5, "partial->partial": 2, "partial->resolved": 4, "resolved->partial": 1}, "churn_accuracy": 0.9, "churn": {"precision": 1.0, "recall": 0.714, "f1": 0.833, "tp": 5, "fp": 0, "fn": 2}, "sentiment_direction_agreement": 0.9, "sentiment_confusion": {"improved->improved": 10, "worsened->worsened": 7, "worsened->improved": 1, "flat->improved": 1, "flat->flat": 1}}
