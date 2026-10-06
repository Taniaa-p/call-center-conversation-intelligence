"""Prometheus metrics. Exposed at GET /metrics (API) and on port 9101 (worker)."""
from prometheus_client import Counter, Gauge, Histogram

from app.schemas import LLMMeta

LLM_REQUESTS = Counter("llm_requests_total", "LLM calls by outcome", ["task", "model", "status"])
LLM_LATENCY = Histogram("llm_latency_seconds", "LLM call latency", ["task", "model"],
                        buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "kind"])
LLM_COST = Counter("llm_cost_usd_total", "Estimated LLM spend in USD", ["model"])

TURN_LATENCY = Histogram("turn_processing_seconds", "Per-turn latency", ["path"],
                         buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8))
CALL_ANALYSIS_LATENCY = Histogram("call_analysis_seconds", "End-of-call analysis latency",
                                  buckets=(1, 2, 5, 10, 20, 40, 80, 160))
LIVE_BATCH_SIZE = Histogram("live_coalesced_turns", "Turns per live LLM update (coalescing)",
                            buckets=(1, 2, 3, 5, 8, 13))
LIVE_TIMEOUTS = Counter("live_update_timeouts_total", "Live updates that missed the latency budget (carried forward)")
GROUNDING = Counter("grounding_checks_total", "Evidence quotes checked", ["result"])
REVIEW_ITEMS = Counter("review_queue_items_total", "Items sent to human review", ["reason"])
PIPELINE_ERRORS = Counter("pipeline_errors_total", "Pipeline failures", ["stage"])
QUEUE_DEPTH = Gauge("arq_queue_depth", "Jobs waiting in the arq queue")
ACTIVE_LIVE_CALLS = Gauge("live_calls_active", "Open live WebSocket calls")
JUDGE_FAITHFULNESS = Histogram("judge_faithfulness", "Online LLM-judge faithfulness per sampled call",
                               buckets=(0.5, 0.7, 0.8, 0.9, 0.95, 1.0))


def observe_llm(meta: LLMMeta) -> None:
    LLM_REQUESTS.labels(meta.task, meta.model, meta.status).inc()
    if meta.status != "cache_hit" and meta.latency_ms:
        LLM_LATENCY.labels(meta.task, meta.model).observe(meta.latency_ms / 1000)
    if meta.status != "cache_hit":
        LLM_TOKENS.labels(meta.model, "input").inc(meta.input_tokens)
        LLM_TOKENS.labels(meta.model, "output").inc(meta.output_tokens)
    LLM_COST.labels(meta.model).inc(meta.cost_usd)
