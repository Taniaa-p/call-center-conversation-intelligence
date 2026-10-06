"""End-of-call analysis with the STRONG model: summary, reasons, resolution, churn."""
from app.config import AppConfig
from app.llm import LLMClient
from app.pipeline.grounding import check_evidence, generate_grounded, is_grounded
from app.pipeline.normalize import format_turns
from app.prompts import FINAL_ANALYSIS
from app.schemas import FinalAnalysis, FinalAnalysisOut, LLMMeta, ReasonResult, ReviewItem, Turn


def clamp(x: float) -> float:
    return round(max(0.0, min(1.0, float(x))), 3)


def collect(out: FinalAnalysisOut):
    items = [(f"reason:{r.label}", r.evidence, True) for r in out.reasons]
    return items + [("resolution", out.resolution.evidence, True), ("churn", out.churn.evidence, True)]


async def run_final_analysis(turns: list[Turn], llm: LLMClient, config: AppConfig,
                             call_id: str | None = None) -> tuple[FinalAnalysis, list[ReviewItem], list[LLMMeta]]:
    turns_by_id = {t.turn_id: t for t in turns}
    taxonomy = "\n".join(f"   - {r.id}: {r.description}" for r in config.taxonomy.reasons)
    out, metas = await generate_grounded(
        llm, task="final_analysis", prompt=FINAL_ANALYSIS, schema=FinalAnalysisOut, tier="strong",
        variables={"taxonomy": taxonomy, "transcript": format_turns(turns)},
        call_id=call_id, turns_by_id=turns_by_id, collect=collect)
    final, review = build_result(out, turns_by_id, config)
    return final, review, metas


def build_result(out: FinalAnalysisOut, turns_by_id: dict[int, Turn], config: AppConfig):
    threshold = config.checklist.review_below_confidence
    review: list[ReviewItem] = []
    reasons: list[ReasonResult] = []
    seen: set[str] = set()
    for r in out.reasons:
        label = r.label.strip().lower()
        if label not in config.taxonomy.ids or label in seen:
            continue
        seen.add(label)
        ev = check_evidence(r.evidence, turns_by_id)
        res = ReasonResult(label=label, confidence=clamp(r.confidence), rationale=r.rationale,
                           evidence=ev, verified=is_grounded(ev))
        reasons.append(res)
        if not res.verified:
            review.append(ReviewItem(item_type="reason", item_ref=label, reason="unverified_evidence", payload=res.model_dump()))
    res_ev = check_evidence(out.resolution.evidence, turns_by_id)
    churn_ev = check_evidence(out.churn.evidence, turns_by_id)
    final = FinalAnalysis(
        summary=out.summary.strip(),
        reasons=reasons,
        resolution=out.resolution.status,
        resolution_confidence=clamp(out.resolution.confidence),
        resolution_rationale=out.resolution.rationale,
        resolution_evidence=res_ev,
        churn_flag=bool(out.churn.flag),
        churn_score=clamp(out.churn.risk_score),
        churn_rationale=out.churn.rationale,
        churn_evidence=churn_ev,
        verified=False,
    )
    for name, ev, conf in (("resolution", res_ev, final.resolution_confidence), ("churn", churn_ev, None)):
        if not is_grounded(ev):
            review.append(ReviewItem(item_type=name, item_ref=name, reason="unverified_evidence",
                                     payload={"evidence": [e.model_dump() for e in ev]}))
        elif conf is not None and conf < threshold:
            review.append(ReviewItem(item_type=name, item_ref=name, reason="low_confidence", payload={"confidence": conf}))
    final.verified = all(r.verified for r in reasons) and is_grounded(res_ev) and is_grounded(churn_ev)
    return final, review
