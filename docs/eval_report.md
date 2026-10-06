# Eval report

Configured models: strong `gemini-3.5-flash-lite`, fast `gemini-3.5-flash-lite` (every call was answered by the configured models) · config `13873791f1a3` · PII backend `regex` · data: 69 synthetic calls with planted, code-verified labels (49 dev / 20 held-out). Reproduce: `make eval`, `make eval-heldout`.

## Quality (offline, against the answer key)

| Metric | Dev (tuned on) | **Held-out (never tuned on)** |
|---|---|---|
| QA item accuracy (mean of 6 items) | 99.3% | **99.2%** |
| Compliance violations: recall | 96.8% | **90.9%** |
| Compliance violations: precision | 96.8% | **100.0%** |
| Call reasons: micro-F1 | 85.7% | **78.9%** |
| Resolution status: accuracy | 65.3% | **75.0%** |
| Churn flag: recall | 76.2% | **71.4%** |
| Sentiment direction: agreement | 79.6% | **90.0%** |
| Follow-up actions: recall (live replay) | 94.1% | **100.0%** |
| PII redaction: recall on planted values | 100.0% | **100.0%** |
| Grounding: evidence quotes verified | 100.0% | **100.0%** |

### Held-out: QA items (the 'fail' class is the planted violation)

| Item | Accuracy | Fail precision | Fail recall | Planted fails |
|---|---|---|---|---|
| greeting | 100.0% | 100.0% | 100.0% | 1 |
| identity_verification | 100.0% | 100.0% | 100.0% | 5 |
| empathy | 100.0% | – | – | 0 |
| correct_disclosure | 95.0% | 100.0% | 66.7% | 3 |
| no_prohibited_promises | 100.0% | 100.0% | 100.0% | 3 |
| proper_closure | 100.0% | 100.0% | 100.0% | 1 |

Prohibited promises (n=3 planted): regex alone 33.3%, LLM alone 100.0%. Violations by source: {'llm': 9, 'rule+llm': 1}.
Resolution confusion (gold→pred): {'resolved->resolved': 7, 'escalated->escalated': 1, 'unresolved->unresolved': 5, 'partial->partial': 2, 'partial->resolved': 4, 'resolved->partial': 1}.
Churn: {'precision': 1.0, 'recall': 0.714, 'f1': 0.833, 'tp': 5, 'fp': 0, 'fn': 2}.
Actions: {'calls_replayed': 15, 'expected': 14, 'recall': 1.0, 'on_time_rate': 1.0, 'avg_actions_per_call': 1.667}.
PII per type: {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 1.0}; extra redactions per call: 1.6.
Grounding: {'evidence_quotes': 272, 'verified_rate': 1.0, 'regrounding_retries': 0, 'review_items_per_call': 0.0, 'review_reasons': {}}.

#### Held-out: slices

| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution | PII recall |
|---|---|---|---|---|---|---|
| language=en | 19 | 99.1% | 90.0% | 79.4% | 73.7% | 100.0% |
| language=hinglish | 1 | 100.0% | 100.0% | 66.7% | 100.0% | 100.0% |
| channel=chat | 5 | 100.0% | 100.0% | 87.5% | 60.0% | 100.0% |
| channel=voice | 15 | 98.9% | 87.5% | 76.4% | 80.0% | 100.0% |

### Dev: QA items (the 'fail' class is the planted violation)

| Item | Accuracy | Fail precision | Fail recall | Planted fails |
|---|---|---|---|---|
| greeting | 100.0% | 100.0% | 100.0% | 2 |
| identity_verification | 98.0% | 100.0% | 85.7% | 7 |
| empathy | 100.0% | 100.0% | 100.0% | 11 |
| correct_disclosure | 98.0% | 88.9% | 100.0% | 8 |
| no_prohibited_promises | 100.0% | 100.0% | 100.0% | 16 |
| proper_closure | 100.0% | 100.0% | 100.0% | 6 |

Prohibited promises (n=16 planted): regex alone 37.5%, LLM alone 100.0%. Violations by source: {'rule+llm': 6, 'llm': 25}.
Resolution confusion (gold→pred): {'resolved->escalated': 3, 'partial->partial': 2, 'unresolved->unresolved': 2, 'escalated->escalated': 9, 'partial->resolved': 6, 'resolved->resolved': 19, 'resolved->partial': 2, 'unresolved->resolved': 1, 'unresolved->partial': 2, 'escalated->partial': 1, 'unresolved->escalated': 2}.
Churn: {'precision': 0.941, 'recall': 0.762, 'f1': 0.842, 'tp': 16, 'fp': 1, 'fn': 5}.
Actions: {'calls_replayed': 15, 'expected': 17, 'recall': 0.941, 'on_time_rate': 1.0, 'avg_actions_per_call': 1.533}.
PII per type: {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 1.0}; extra redactions per call: 1.918.
Grounding: {'evidence_quotes': 589, 'verified_rate': 1.0, 'regrounding_retries': 1, 'review_items_per_call': 0.0, 'review_reasons': {}}.

#### Dev: slices

| Slice | n | QA acc | Violation recall | Reasons F1 | Resolution | PII recall |
|---|---|---|---|---|---|---|
| language=en | 41 | 99.2% | 96.2% | 85.0% | 63.4% | 100.0% |
| language=hinglish | 8 | 100.0% | 100.0% | 90.0% | 75.0% | 100.0% |
| channel=chat | 15 | 100.0% | 100.0% | 81.8% | 66.7% | 100.0% |
| channel=voice | 34 | 99.0% | 95.7% | 87.5% | 64.7% | 100.0% |

## Service health and cost

Latency benchmark (cache off, 5 held-out calls, one at a time, model `gemini-3.1-flash-lite`):

- fast path: **0.089 ms per turn** (redaction + sentiment)
- end-of-call analysis wall time: p50 **8.96 s**, max 49.02 s
- API latency by task (ms): {'final_analysis': {'p50': 7580, 'max': 9221, 'n': 5}, 'qa_scoring': {'p50': 7524, 'max': 8949, 'n': 5}, 'live_actions': {'p50': 6615, 'max': 11744, 'n': 5}}

Live action updates during the held-out replay (fresh calls, from `run_heldout_20261004_133101.json`): p50 **1575 ms**, p95 2244 ms (n=111).
LLM latency by task (ms): {'final_analysis': {'p50': 2993, 'p95': 3615, 'n': 16}, 'live_actions': {'p50': 1575, 'p95': 2244, 'n': 111}, 'qa_scoring': {'p50': 2858, 'p95': 4489, 'n': 20}}.
Models that actually answered: {'final_analysis:gemini-3.5-flash-lite': 20, 'qa_scoring:gemini-3.5-flash-lite': 20, 'live_actions:gemini-3.5-flash-lite': 111}.
Estimated cost per call (fresh calls): $0.00118; tokens per call: 7343.

## Additional exploration

### PII: regex vs GLiNER vs hybrid (all 69 calls, 261 planted values)

| Backend | Recall | Leaked | False positives / call | ms / turn (p50) |
|---|---|---|---|---|
| regex | 98.9% | 3 | 0.57 | 0.04 |
| gliner_raw | 89.7% | 27 | 14.72 | 62.26 |
| gliner | 89.7% | 27 | 0.78 | 70.72 |
| hybrid | 100.0% | 0 | 0.83 | 77.96 |

Per-type recall: **regex** {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 0.957, 'PHONE': 1.0}; **gliner_raw** {'ACCOUNT_NO': 0.772, 'ADDRESS': 1.0, 'CARD_NUMBER': 0.0, 'DATE': 0.944, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 0.957}; **gliner** {'ACCOUNT_NO': 0.772, 'ADDRESS': 1.0, 'CARD_NUMBER': 0.0, 'DATE': 0.944, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 0.957}; **hybrid** {'ACCOUNT_NO': 1.0, 'ADDRESS': 1.0, 'CARD_NUMBER': 1.0, 'DATE': 1.0, 'EMAIL': 1.0, 'PERSON': 1.0, 'PHONE': 1.0}

### QA scoring: small vs larger (thinking) model (14 dev calls answered by both)

| Model | QA item accuracy | Violation recall | Est. cost / call | Output tokens (p50) |
|---|---|---|---|---|
| gemini-3.1-flash-lite | 98.8% | 100.0% | $0.00041 | 683.5 |
| gemini-3-flash-preview | 98.8% | 100.0% | $0.01328 | 3967.5 |

### LLM judge vs the answer key (20 held-out calls)

| Judge | Models | Agreement with system | Real system errors | Errors flagged | False alarms |
|---|---|---|---|---|---|
| judge.v1 | gemini-3.1-flash-lite, gemma-4-31b-it | 100.0% | 22 | 0 (0.0%) | 0 / 174 (0.0%) |
| judge.v2 | gemini-3.1-flash-lite, gemma-4-31b-it | 91.2% | 8 | 6 (75.0%) | 8 / 152 (5.3%) |
| judge.v3 | gemini-3.1-flash-lite, gemma-4-31b-it | 93.1% | 10 | 7 (70.0%) | 4 / 150 (2.7%) |

### Sentiment: lexicon vs multilingual RoBERTa (69 calls)

| Scorer | Direction agreement | Hinglish | ms / turn |
|---|---|---|---|
| lexicon | 82.6% | 100.0% | 0.01 |
| roberta_xlm | 79.7% | 100.0% | 11.79 |

