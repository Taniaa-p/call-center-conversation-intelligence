"""Grounding validator: does each evidence quote really appear in the turn it cites?

This is the main defence against hallucinated scores. The LLM must copy quotes verbatim;
code then checks them. Matching is forgiving about case, whitespace, punctuation and
curly quotes, but NOT about words. A quote may use "..." to skip text inside one turn;
each piece must then appear in order.
"""
import re
import unicodedata
from typing import Callable, TypeVar

from pydantic import BaseModel

from app import metrics
from app.llm import LLMClient, LLMError, Prompt
from app.schemas import CheckedEvidence, Evidence, LLMMeta, Turn

T = TypeVar("T", bound=BaseModel)
Collector = Callable[[T], list[tuple[str, list[Evidence], bool]]]

MIN_QUOTE_CHARS = 3


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[^\w\s\[\]']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    haystack = normalize(text)
    pos = 0
    pieces = [normalize(p) for p in re.split(r"\.\.\.|…", quote)]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < MIN_QUOTE_CHARS:
        return False
    for piece in pieces:
        idx = haystack.find(piece, pos)
        if idx < 0:
            return False
        pos = idx + len(piece)
    return True


def check_evidence(evidence: list[Evidence], turns_by_id: dict[int, Turn]) -> list[CheckedEvidence]:
    checked = []
    for ev in evidence:
        turn = turns_by_id.get(ev.turn_id)
        ok = turn is not None and quote_in_text(ev.quote, turn.text)
        checked.append(CheckedEvidence(turn_id=ev.turn_id, quote=ev.quote, verified=ok))
        metrics.GROUNDING.labels("verified" if ok else "unverified").inc()
    return checked


def is_grounded(checked: list[CheckedEvidence], required: bool = True) -> bool:
    if not checked:
        return not required
    return all(c.verified for c in checked)


def failures_feedback(failures: list[tuple[str, list[Evidence]]]) -> str:
    lines = ["Some evidence quotes were NOT found verbatim in the cited turns. "
             "Re-answer the whole task. For these items copy quotes EXACTLY from the turn text "
             "(or pick the correct turn_id). Failed items:"]
    for key, evs in failures:
        cited = "; ".join(f"turn {e.turn_id}: \"{e.quote}\"" for e in evs) or "no evidence given"
        lines.append(f"- {key}: {cited}")
    return "\n".join(lines)


def find_failures(out: BaseModel, collect: Collector, turns_by_id: dict[int, Turn]) -> list[tuple[str, list[Evidence]]]:
    failures = []
    for key, evidence, required in collect(out):
        ok_all = all(ev.turn_id in turns_by_id and quote_in_text(ev.quote, turns_by_id[ev.turn_id].text)
                     for ev in evidence)
        if (required and not evidence) or not ok_all:
            failures.append((key, evidence))
    return failures


async def generate_grounded(llm: LLMClient, *, task: str, prompt: Prompt, variables: dict,
                            schema: type[T], tier: str, call_id: str | None,
                            turns_by_id: dict[int, Turn], collect: Collector) -> tuple[T, list[LLMMeta]]:
    out, meta = await llm.generate(task=task, prompt=prompt, variables=variables, schema=schema,
                                   tier=tier, call_id=call_id)
    metas = [meta]
    failures = find_failures(out, collect, turns_by_id)
    if failures:
        try:
            retry, meta2 = await llm.generate(task=f"{task}_regrounding", prompt=prompt, variables=variables,
                                              schema=schema, tier=tier, call_id=call_id,
                                              feedback=failures_feedback(failures))
            metas.append(meta2)
            if len(find_failures(retry, collect, turns_by_id)) < len(failures):
                out = retry
        except LLMError:
            pass
    return out, metas
