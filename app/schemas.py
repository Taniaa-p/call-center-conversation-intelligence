"""Pydantic models.

Two families:
  * LLM output models (suffix `Out`): exactly what we ask Gemini to return. Field order
    matters: `evidence` comes FIRST so the model commits to quotes before it gives a
    verdict, score or label ("evidence first").
  * Result models: what the pipeline stores and the API returns. They add code-computed
    fields such as `verified` (grounding check) and weighted QA totals.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Speaker = Literal["agent", "customer", "system"]
Verdict = Literal["pass", "partial", "fail", "na", "insufficient_evidence"]
Resolution = Literal["resolved", "partial", "unresolved", "escalated"]


class Turn(BaseModel):
    """One normalized utterance. `text` is always the REDACTED text after the PII step."""
    turn_id: int
    speaker: Speaker
    text: str
    ts: datetime | None = None


class CallMeta(BaseModel):
    call_id: str
    agent_id: str = "unknown"
    team_id: str = "unknown"
    tenant_id: str = "default"
    channel: Literal["voice", "chat"] = "voice"
    started_at: datetime | None = None
    is_demo: bool = False


class Evidence(BaseModel):
    turn_id: int
    quote: str = Field(description="Exact, verbatim substring copied from that turn's text.")


class ReasonOut(BaseModel):
    evidence: list[Evidence]
    label: str
    confidence: float
    rationale: str


class ResolutionOut(BaseModel):
    evidence: list[Evidence]
    status: Resolution
    confidence: float
    rationale: str


class ChurnOut(BaseModel):
    evidence: list[Evidence]
    risk_score: float = Field(description="0.0 (no risk) to 1.0 (certain to leave).")
    flag: bool
    rationale: str


class FinalAnalysisOut(BaseModel):
    summary: str = Field(description="2-3 sentences, max 60 words.")
    reasons: list[ReasonOut]
    resolution: ResolutionOut
    churn: ChurnOut


class QAItemOut(BaseModel):
    item_id: str
    evidence: list[Evidence]
    rationale: str
    verdict: Verdict
    confidence: float


class QAOut(BaseModel):
    items: list[QAItemOut]


class ActionDeltaOut(BaseModel):
    evidence: list[Evidence]
    op: Literal["add", "update", "close"]
    action_id: str | None = Field(None, description="Required for update/close. Null for add.")
    text: str = Field(description="Short imperative follow-up action, e.g. 'Schedule technician visit'.")
    owner: Literal["agent", "customer", "backoffice"]
    rationale: str
    confidence: float


class LiveUpdateOut(BaseModel):
    rolling_summary: str = Field(description="1-2 sentences, max 40 words, summary of the call so far.")
    deltas: list[ActionDeltaOut]


class CheckedEvidence(Evidence):
    verified: bool


class Action(BaseModel):
    action_id: str
    text: str
    owner: str
    status: Literal["open", "done", "cancelled"] = "open"
    created_turn: int
    updated_turn: int
    evidence: list[CheckedEvidence] = []
    verified: bool = False
    rationale: str = ""
    confidence: float = 0.0


class SentimentPoint(BaseModel):
    turn_id: int
    score: float
    label: Literal["negative", "neutral", "positive"]


class SentimentTrajectory(BaseModel):
    points: list[SentimentPoint]
    start: float | None
    end: float | None
    delta: float | None
    direction: Literal["improved", "worsened", "flat", "unknown"]
    shift_turns: list[int]


class RuleFlag(BaseModel):
    """A deterministic regex hit from policy.yaml (fast path)."""
    rule_id: str
    kind: Literal["prohibited_promise", "churn_signal"]
    turn_id: int
    quote: str


class ReasonResult(BaseModel):
    label: str
    confidence: float
    rationale: str
    evidence: list[CheckedEvidence]
    verified: bool


class FinalAnalysis(BaseModel):
    summary: str
    reasons: list[ReasonResult]
    resolution: Resolution
    resolution_confidence: float
    resolution_rationale: str
    resolution_evidence: list[CheckedEvidence]
    churn_flag: bool
    churn_score: float
    churn_rationale: str
    churn_evidence: list[CheckedEvidence]
    verified: bool


class QAItemResult(BaseModel):
    item_id: str
    verdict: Verdict
    points: float | None
    weight: float
    critical: bool
    confidence: float
    rationale: str
    evidence: list[CheckedEvidence]
    verified: bool


class Violation(BaseModel):
    item_id: str
    severity: str
    description: str
    source: Literal["rule", "llm", "rule+llm"]
    evidence: list[CheckedEvidence]
    verified: bool


class QAResult(BaseModel):
    items: list[QAItemResult]
    score_pct: float | None
    violations: list[Violation]


class ReviewItem(BaseModel):
    item_type: str
    item_ref: str
    reason: str
    payload: dict


class LLMMeta(BaseModel):
    task: str
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    status: str = "ok"


class CallAnalysis(BaseModel):
    call_id: str
    final: FinalAnalysis | None
    qa: QAResult | None
    sentiment: SentimentTrajectory
    rule_flags: list[RuleFlag]
    actions: list[Action]
    review_items: list[ReviewItem]
    llm_calls: list[LLMMeta]
    config_version: str
    created_at: datetime
