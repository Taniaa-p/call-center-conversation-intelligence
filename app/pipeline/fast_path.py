"""Fast path: runs on EVERY turn, locally, in microseconds, at zero LLM cost.

  * per-turn customer sentiment (lexicon scorer by default; RoBERTa optional)
  * deterministic policy rule flags (prohibited promises, churn signals) from policy.yaml
  * the sentiment trajectory (start -> end, delta, shift turns), computed in code, not by the LLM
"""
import re
from functools import lru_cache
from statistics import mean
from typing import Protocol

from app.config import AppConfig
from app.schemas import RuleFlag, SentimentPoint, SentimentTrajectory, Turn


class SentimentScorer(Protocol):
    name: str

    def score(self, text: str) -> float:
        ...


POSITIVE = {
    "thanks", "thank", "great", "good", "perfect", "awesome", "appreciate", "helpful", "happy",
    "excellent", "resolved", "works", "working", "fixed", "glad", "wonderful", "amazing", "nice",
    "sorted", "fine", "love", "relieved", "brilliant", "cool", "okay", "ok",
    "accha", "achha", "badhiya", "shukriya", "dhanyavaad", "dhanyawad", "theek", "mast",
}
NEGATIVE = {
    "angry", "frustrated", "frustrating", "annoyed", "terrible", "worst", "bad", "horrible",
    "useless", "ridiculous", "unacceptable", "disappointed", "upset", "problem", "issue", "wrong",
    "again", "never", "cancel", "switch", "complaint", "overcharged", "waste", "slow", "dropped",
    "fed", "sick", "tired", "pathetic", "disgusting", "hate", "furious", "broken",
    "poor", "awful", "scam", "fraud", "leave", "nonsense", "joke", "unhappy",
    "bekaar", "bakwas", "pareshan", "pareshaan", "ghatiya", "gussa", "bekar",
}
NEGATORS = {"not", "no", "never", "don't", "dont", "isn't", "wasn't", "didn't", "can't", "nahi", "nahin"}
INTENSIFIERS = {"very", "really", "so", "extremely", "totally", "absolutely", "bahut", "too"}
EMOJI = {"😡": -2, "😠": -2, "🤬": -2, "😞": -1, "😢": -1, "👎": -1, "🙂": 1, "😊": 1, "👍": 1, "🙏": 1, "😀": 1}
TOKEN = re.compile(r"[a-z']+|[^\w\s]", re.I)


class LexiconSentiment:
    name = "lexicon"

    def score(self, text: str) -> float:
        tokens = [t.lower() for t in TOKEN.findall(text)]
        total, hits = 0.0, 0
        for i, tok in enumerate(tokens):
            val = 1.0 if tok in POSITIVE else -1.0 if tok in NEGATIVE else EMOJI.get(tok, 0)
            if not val or tok in NEGATORS:
                continue
            window = tokens[max(0, i - 3):i]
            if any(w in NEGATORS for w in window):
                val = -val * 0.8
            if any(w in INTENSIFIERS for w in window):
                val *= 1.5
            total += val
            hits += 1
        if "!" in text and total < 0:
            total -= 0.5
        return max(-1.0, min(1.0, total / max(2.0, hits + 1)))


class RobertaSentiment:
    name = "roberta"

    def __init__(self, model_name: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"):
        from transformers import pipeline
        self.pipe = pipeline("sentiment-analysis", model=model_name, top_k=None)

    def score(self, text: str) -> float:
        probs = {d["label"].lower(): d["score"] for d in self.pipe(text[:512])[0]}
        return probs.get("positive", 0.0) - probs.get("negative", 0.0)


@lru_cache
def get_sentiment_scorer(backend: str = "lexicon") -> SentimentScorer:
    return RobertaSentiment() if backend == "roberta" else LexiconSentiment()


def label_for(score: float) -> str:
    return "positive" if score > 0.15 else "negative" if score < -0.15 else "neutral"


def score_turn(turn: Turn, scorer: SentimentScorer) -> SentimentPoint | None:
    if turn.speaker != "customer":
        return None
    s = round(scorer.score(turn.text), 3)
    return SentimentPoint(turn_id=turn.turn_id, score=s, label=label_for(s))


def trajectory(points: list[SentimentPoint], window: int = 2, shift_threshold: float = 0.5,
               flat_band: float = 0.2) -> SentimentTrajectory:
    if not points:
        return SentimentTrajectory(points=[], start=None, end=None, delta=None, direction="unknown", shift_turns=[])
    start = round(mean(p.score for p in points[:window]), 3)
    end = round(mean(p.score for p in points[-window:]), 3)
    delta = round(end - start, 3)
    direction = "improved" if delta > flat_band else "worsened" if delta < -flat_band else "flat"
    shifts = [b.turn_id for a, b in zip(points, points[1:], strict=False) if abs(b.score - a.score) > shift_threshold]
    return SentimentTrajectory(points=points, start=start, end=end, delta=delta, direction=direction, shift_turns=shifts)


class RuleEngine:
    def __init__(self, config: AppConfig):
        self.prohibited = config.compiled(config.policy.prohibited_promises)
        self.churn = config.compiled(config.policy.churn_signals)
        self.commitment = re.compile(config.policy.commitment_triggers["pattern"], re.I)

    def check(self, turn: Turn) -> list[RuleFlag]:
        rules = self.prohibited if turn.speaker == "agent" else self.churn if turn.speaker == "customer" else []
        kind = "prohibited_promise" if turn.speaker == "agent" else "churn_signal"
        flags = []
        for rule, rx in rules:
            m = rx.search(turn.text)
            if m:
                flags.append(RuleFlag(rule_id=rule.id, kind=kind, turn_id=turn.turn_id, quote=m.group(0)))
        return flags

    def is_commitment(self, turn: Turn) -> bool:
        return turn.speaker == "agent" and bool(self.commitment.search(turn.text))
