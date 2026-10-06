"""QA checklist scoring with evidence, plus compliance violations.

The LLM gives a verdict per item. CODE does the rest:
  * checks evidence (grounding) and the N/A rules from the YAML
  * computes the weighted score  (LLMs are bad at arithmetic, and code is auditable)
  * turns failed CRITICAL items into violations, merged with the regex rule hits
"""
from app.config import AppConfig
from app.llm import LLMClient
from app.pipeline.grounding import check_evidence, generate_grounded, is_grounded
from app.pipeline.normalize import format_turns
from app.prompts import QA_SCORING
from app.schemas import (CheckedEvidence, LLMMeta, QAItemOut, QAItemResult, QAOut, QAResult, ReviewItem,
                         RuleFlag, Turn, Violation)

NEEDS_EVIDENCE = {"pass", "partial", "fail"}


def render_checklist(config: AppConfig) -> str:
    lines = []
    for it in config.checklist.items:
        na = f"N/A allowed when: {it.na_when}" if it.allow_na else "N/A NOT allowed."
        lines.append(f"- item_id: {it.id} ({it.name}). {it.description.strip()} {na} "
                     f"Absence evidence: {it.absence_evidence or 'cite the most relevant turn'}.")
    return "\n".join(lines)


def policy_text(config: AppConfig) -> dict[str, str]:
    policy = config.policy
    return {
        "prohibited": "\n".join(f"- {p.description}" for p in policy.prohibited_promises),
        "disclosures": "\n".join(f"- for {', '.join(d.applies_to_reasons)}"
                                 f"{' (' + d.applies_when + ')' if d.applies_when else ''}: {d.description}"
                                 for d in policy.required_disclosures),
    }


def collect(out: QAOut):
    return [(f"qa:{i.item_id}", i.evidence, i.verdict in NEEDS_EVIDENCE) for i in out.items]


async def run_qa_scoring(turns: list[Turn], llm: LLMClient, config: AppConfig, rule_flags: list[RuleFlag],
                         call_id: str | None = None, model_override: str | None = None
                         ) -> tuple[QAResult, list[ReviewItem], list[LLMMeta]]:
    turns_by_id = {t.turn_id: t for t in turns}
    variables = {"checklist": render_checklist(config), **policy_text(config), "transcript": format_turns(turns)}
    if model_override:
        out, meta = await llm.generate(task="qa_scoring", prompt=QA_SCORING, variables=variables, schema=QAOut,
                                       tier="strong", call_id=call_id, model_override=model_override)
        metas = [meta]
    else:
        out, metas = await generate_grounded(llm, task="qa_scoring", prompt=QA_SCORING, variables=variables,
                                             schema=QAOut, tier="strong", call_id=call_id,
                                             turns_by_id=turns_by_id, collect=collect)
    result, review = score(out, turns_by_id, config, rule_flags)
    return result, review, metas


def score(out: QAOut, turns_by_id: dict[int, Turn], config: AppConfig, rule_flags: list[RuleFlag]
          ) -> tuple[QAResult, list[ReviewItem]]:
    cl = config.checklist
    by_id: dict[str, QAItemOut] = {}
    for i in out.items:
        by_id.setdefault(i.item_id, i)
    items: list[QAItemResult] = []
    review: list[ReviewItem] = []
    for spec in cl.items:
        raw = by_id.get(spec.id)
        verdict = raw.verdict if raw else "insufficient_evidence"
        reason = None if raw else "missing_item"
        if verdict == "na" and not spec.allow_na:
            verdict, reason = "insufficient_evidence", "invalid_na"
        scale = spec.scale or cl.verdict_points
        if verdict in NEEDS_EVIDENCE and verdict not in scale and reason is None:
            reason = "verdict_not_on_scale"
        ev = check_evidence(raw.evidence, turns_by_id) if raw else []
        verified = is_grounded(ev, required=verdict in NEEDS_EVIDENCE)
        conf = max(0.0, min(1.0, raw.confidence)) if raw else 0.0
        points = scale.get(verdict, 0.0) if verdict in NEEDS_EVIDENCE else None
        item = QAItemResult(item_id=spec.id, verdict=verdict, points=points,
                            weight=spec.weight, critical=spec.critical, confidence=round(conf, 3),
                            rationale=raw.rationale if raw else "", evidence=ev, verified=verified)
        items.append(item)
        if reason is None and not verified:
            reason = "unverified_evidence"
        if reason is None and verdict in NEEDS_EVIDENCE and conf < cl.review_below_confidence:
            reason = "low_confidence"
        if reason:
            review.append(ReviewItem(item_type="qa_item", item_ref=spec.id, reason=reason, payload=item.model_dump()))

    violations, extra_review = build_violations(items, config, rule_flags)
    return QAResult(items=items, score_pct=weighted_score(items), violations=violations), review + extra_review


def weighted_score(items: list[QAItemResult]) -> float | None:
    scored = [i for i in items if i.points is not None and i.verified]
    total_w = sum(i.weight for i in scored)
    if not total_w:
        return None
    return round(100 * sum(i.points * i.weight for i in scored) / total_w, 1)


def build_violations(items: list[QAItemResult], config: AppConfig, rule_flags: list[RuleFlag]
                     ) -> tuple[list[Violation], list[ReviewItem]]:
    specs = {s.id: s for s in config.checklist.items}
    rule_hits = [f for f in rule_flags if f.kind == "prohibited_promise"]
    rule_ev = [CheckedEvidence(turn_id=f.turn_id, quote=f.quote, verified=True) for f in rule_hits]
    violations, review = [], []
    for it in items:
        spec = specs[it.item_id]
        llm_fail = spec.critical and it.verdict in config.checklist.violation_verdicts
        if it.item_id == "no_prohibited_promises" and rule_hits:
            source = "rule+llm" if llm_fail else "rule"
            violations.append(Violation(item_id=it.item_id, severity=spec.severity, source=source,
                                        description=f"{spec.name}: " + ", ".join(sorted({f.rule_id for f in rule_hits})),
                                        evidence=rule_ev + (it.evidence if llm_fail else []), verified=True))
            if not llm_fail:
                review.append(ReviewItem(item_type="violation", item_ref=it.item_id, reason="rule_llm_disagreement",
                                         payload={"rule_hits": [f.model_dump() for f in rule_hits], "llm_verdict": it.verdict}))
        elif llm_fail:
            violations.append(Violation(item_id=it.item_id, severity=spec.severity, source="llm",
                                        description=f"{spec.name} ({it.verdict}): {it.rationale}", evidence=it.evidence,
                                        verified=it.verified))
    return violations, review
