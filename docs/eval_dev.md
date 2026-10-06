# Eval results: `dev` split (49 calls)

Run 2026-10-05T16:33:29+00:00 · models: strong `gemini-3.5-flash-lite`, fast `gemini-3.5-flash-lite` · config `13873791f1a3` · PII backend `regex`

## Headline

| Metric | Value |
|---|---|
| qa_item_accuracy_mean | 0.993 |
| violation_recall | 0.968 |
| violation_precision | 0.968 |
| reasons_micro_f1 | 0.857 |
| resolution_accuracy | 0.653 |
| churn_recall | 0.762 |
| sentiment_direction_agreement | 0.796 |
| action_recall | 0.941 |
| pii_recall | 1.0 |
| grounding_verified_rate | 1.0 |

## QA items (fail class = the planted violation)

| Item | Accuracy | Fail precision | Fail recall | n |
|---|---|---|---|---|
| greeting | 1.0 | 1.0 | 1.0 | 49 |
| identity_verification | 0.98 | 1.0 | 0.857 | 49 |
| empathy | 1.0 | 1.0 | 1.0 | 49 |
| correct_disclosure | 0.98 | 0.889 | 1.0 | 49 |
| no_prohibited_promises | 1.0 | 1.0 | 1.0 | 49 |
| proper_closure | 1.0 | 1.0 | 1.0 | 49 |

Prohibited-promise recall: regex alone 0.375, LLM alone 1.0 (n=16); violation sources: {'rule+llm': 6, 'llm': 25}

## Breakdowns

| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution acc | PII recall |
|---|---|---|---|---|---|---|
| language=en | 41 | 0.992 | 0.962 | 0.85 | 0.634 | 1.0 |
| language=hinglish | 8 | 1.0 | 1.0 | 0.9 | 0.75 | 1.0 |
| channel=chat | 15 | 1.0 | 1.0 | 0.818 | 0.667 | 1.0 |
| channel=voice | 34 | 0.99 | 0.957 | 0.875 | 0.647 | 1.0 |

## Operations

- LLM calls: 230 (171 cache hits), statuses {'cache_hit': 171, 'ok': 59}
- Models that actually answered (task:model → calls): {'final_analysis:gemini-3.5-flash-lite': 49, 'qa_scoring:gemini-3.5-flash-lite': 49, 'live_actions:gemini-3.5-flash-lite': 131, 'final_analysis_regrounding:gemini-3.5-flash-lite': 1}
- Live action update latency (fresh calls): p50 1400 ms, p95 1820 ms
- Latency by task: {"live_actions": {"p50": 1400, "p95": 1820, "n": 37}, "qa_scoring": {"p50": 2624, "p95": 3256, "n": 22}}
- Estimated cost per call (fresh calls only): $0.00026, tokens per call: 1512
- Grounding: {'evidence_quotes': 589, 'verified_rate': 1.0, 'regrounding_retries': 1, 'review_items_per_call': 0.0, 'review_reasons': {}}
- PII: {'recall': 1.0, 'planted': 192, 'leaked': 0, 'per_type_recall': {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 1.0}, 'extra_redactions_per_call': 1.918}
- Actions: {'calls_replayed': 15, 'expected': 17, 'recall': 0.941, 'on_time_rate': 1.0, 'avg_actions_per_call': 1.533}
- Outcomes: {"resolution_accuracy": 0.653, "resolution_confusion": {"resolved->escalated": 3, "partial->partial": 2, "unresolved->unresolved": 2, "escalated->escalated": 9, "partial->resolved": 6, "resolved->resolved": 19, "resolved->partial": 2, "unresolved->resolved": 1, "unresolved->partial": 2, "escalated->partial": 1, "unresolved->escalated": 2}, "churn_accuracy": 0.878, "churn": {"precision": 0.941, "recall": 0.762, "f1": 0.842, "tp": 16, "fp": 1, "fn": 5}, "sentiment_direction_agreement": 0.796, "sentiment_confusion": {"improved->improved": 29, "worsened->worsened": 10, "flat->improved": 4, "worsened->improved": 2, "worsened->flat": 4}}
