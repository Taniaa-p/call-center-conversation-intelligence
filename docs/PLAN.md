# Build plan (revised)

The original build plan with its risks and gaps fixed before and during the build.

## 1. Scope tiers

| Tier | Contents |
|---|---|
| **Must** | normalizer, PII redaction (regex), final analysis, QA scoring with evidence, grounding validator, Postgres + roll-up views, REST API, synthetic data + labels, eval script |
| **Should** | live WebSocket path, per-turn action deltas, fast path (rules + sentiment), review queue, Prometheus `/metrics`, worker, dashboard |
| **Stretch** | GLiNER backend + PII experiment, model-size experiment, Hinglish breakdown, LLM-judge sampling, Langfuse |

## 2. Risks and gaps found in the original plan, and the fix

| # | Risk / gap | Fix |
|---|---|---|
| 1 | **"Redis Streams + arq" is inconsistent.** arq is a job queue built on Redis *sorted sets + keys*, not Streams. | Use **arq** for end-of-call jobs and the PII TTL cron. README says consumer-group Streams (or Kafka) is the scale-out step. One clear answer for interview Q9. |
| 2 | **Per-turn action deltas need ordering.** A generic job queue can run turn 7 before turn 6, and each delta depends on the previous action list. | The live path runs as **one serial consumer per call** inside the API process (an `asyncio.Queue`). Under load it **coalesces**: it sends *all* unprocessed customer turns in one LLM call. Every customer turn is still covered (the brief says every interaction) but cost and latency stay bounded. This is the backpressure story. |
| 3 | **Langfuse v3 self-hosting is heavy** (ClickHouse + Redis + S3). | Langfuse is optional/stretch. An `llm_calls` table in Postgres (task, model, prompt version, tokens, latency, cost, status) + Prometheus gives the same audit/cost story. |
| 4 | **GLiNER and RoBERTa pull in PyTorch** (huge image, slow build). | `PIIRedactor` and `SentimentScorer` are interfaces. Default backends are **regex** and a **lexicon** scorer (zero deps). GLiNER/RoBERTa are an optional `ml` extra. The GLiNER-vs-regex comparison is an exploration experiment. |
| 5 | **Hardcoded model names** (Gemini 2.5 may be retired soon). | `GEMINI_MODEL_FAST / _STRONG / _FALLBACK` from env. Checked 4 Oct 2026: Pro models return 429 on the free tier, some flash models return 503. Chosen: fast = `gemini-3.5-flash-lite`, strong = `gemini-3.5-flash`, fallback = `gemini-3.1-flash-lite`. **Confirmed during the build:** `gemini-2.5-flash` now returns 404 "no longer available to new users" and `3.5-flash` returned bursts of 503, so the fallback must come from a different, live model family. |
| 6 | **Free-tier rate limits** vs per-turn LLM calls + repeated evals. | Concurrency semaphore, retry with exponential backoff + jitter, fallback model, and an **on-disk cache** keyed by (task, prompt version, model, input hash) so re-running evals costs nothing. |
| 7 | **"No evidence = no score" breaks for absence failures.** You can't quote a greeting that never happened. | Rule: for an absence failure the model cites the turn **where the behaviour was required** (e.g. the agent's first turn for greeting, the last agent turn for closure). The validator still checks that quote. |
| 8 | **LLM arithmetic.** If the LLM computes the weighted QA total or the sentiment trajectory, it can be inconsistent with its own item scores. | The LLM gives **per-item** verdicts only. Code computes the weighted total, the violations (from `critical` items) and the sentiment start/end/delta/shift turns (from fast-path per-turn scores). |
| 9 | **Prohibited promises detected by only one method.** | Two detectors: instant regex rules from `policy.yaml` (fast path, exact evidence by construction) **and** the LLM QA item. Violations are the union, tagged with `source`. |
| 10 | **Averaging agent scores is unfair** (small samples, harder call mix). | Roll-up views show `n_calls` and a **shrunk score** (Bayesian average toward the team mean), and flag agents with < 5 calls. |
| 11 | **`.env` pointed at port 5433**, which clashed with another local Postgres. Compose maps this project's DB to **5434**. Also `postgresql+asyncpg://` is a SQLAlchemy URL; we use asyncpg directly. | Fixed `.env` / `.env.example` to `postgresql://...:5434`. |
| 12 | **Synthetic labels from an LLM can be wrong** (asked to plant a violation, it might not). | The generator gives a Python-made scenario spec + fake PII values. After generation, **code verifies** the planted items (PII values present verbatim, prohibited phrase present, no unplanted prohibited phrase). Failed calls are regenerated. |
| 13 | **Held-out leakage.** | Split is fixed by a seed in the generator: `dev` (tune on this) and `heldout` (report only). |
| 14 | **Entity map "restricted access"** is vague. | Separate Postgres schema `pii_vault`, never joined by any view or API route, `expires_at` column, and an arq cron that purges expired rows hourly. In production: separate DB role + encryption at rest (README). |
| 15 | **Timeline.** Fixed deadline. | Build in the order below, each phase tested and committed before the next. |

## 3. Final architecture

```
client (WS turns / REST batch)
   │
   ▼
normalize ──► PII redact (regex|gliner) ──► entity map → pii_vault (TTL)
   │ redacted turns only from here on
   ├─► fast path (every turn): lexicon sentiment + policy regex flags ─► WS push
   ├─► live LLM path (every customer turn, serial per call, coalescing):
   │      previous actions + new turns → {action deltas, rolling summary} ─► WS push
   └─► call ends → arq job → final analysis (strong model) + QA scoring
                         → grounding validator → retry once → unverified → review_queue
                         → Postgres (calls, turns, analyses, qa_scores, violations, actions)
                         → SQL roll-up views (agent, team, daily trends, reason drift)
Every LLM call → llm_calls table + Prometheus metrics.
```

## 4. Build order

0. Plan fixes, scaffolding, config YAMLs
1. Core pipeline (normalize, pii, fast path, llm client, grounding, final analysis, QA) + unit tests
2. DB schema + views, REST + WebSocket API, worker, review queue, metrics, Docker
3. Data: corpus sample fetch, synthetic generator with verified labels, `make seed`
4. Evals: `make eval`, regression test, eval report with real numbers
5. Dashboard (thin React)
6. Experiments (regex vs GLiNER, flash-lite vs flash for QA, Hinglish) + README + diagrams
