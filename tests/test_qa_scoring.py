from app.config import get_config
from app.pipeline.qa_scoring import score
from app.schemas import Evidence, QAItemOut, QAOut, RuleFlag, Turn

TURNS = {1: Turn(turn_id=1, speaker="agent", text="Hello, thanks for calling"),
         2: Turn(turn_id=2, speaker="agent", text="I guarantee a full refund"),
         3: Turn(turn_id=3, speaker="agent", text="Bye")}


def item(item_id, verdict, turn=1, quote="Hello, thanks for calling", conf=0.9):
    ev = [Evidence(turn_id=turn, quote=quote)] if verdict not in ("na", "insufficient_evidence") else []
    return QAItemOut(item_id=item_id, evidence=ev, rationale="r", verdict=verdict, confidence=conf)


def test_weighted_score_violations_and_na_rules():
    cfg = get_config()
    out = QAOut(items=[
        item("greeting", "pass"),
        item("identity_verification", "na"),
        item("empathy", "partial"),
        item("correct_disclosure", "na"),
        item("no_prohibited_promises", "fail", 2, "guarantee a full refund"),
        item("proper_closure", "na"),
    ])
    flags = [RuleFlag(rule_id="guaranteed_refund", kind="prohibited_promise", turn_id=2, quote="guarantee a full refund")]
    result, review = score(out, TURNS, cfg, flags)
    assert result.score_pct == round(100 * (1 * 1 + 0.5 * 1.5) / (1 + 1.5 + 3), 1)
    assert [(v.item_id, v.source) for v in result.violations] == [("no_prohibited_promises", "rule+llm")]
    assert any(r.item_ref == "proper_closure" and r.reason == "invalid_na" for r in review)


def test_rule_llm_disagreement_goes_to_review():
    cfg = get_config()
    out = QAOut(items=[item("no_prohibited_promises", "pass", 2, "guarantee a full refund")])
    flags = [RuleFlag(rule_id="guaranteed_refund", kind="prohibited_promise", turn_id=2, quote="guarantee a full refund")]
    result, review = score(out, TURNS, cfg, flags)
    assert result.violations[0].source == "rule"
    assert any(r.reason == "rule_llm_disagreement" for r in review)
    assert any(r.item_ref == "greeting" and r.reason == "missing_item" for r in review)


def test_unverified_evidence_excluded_from_score():
    cfg = get_config()
    out = QAOut(items=[item("greeting", "pass", quote="Good morning sir"), item("proper_closure", "pass", 3, "Bye")])
    result, review = score(out, TURNS, cfg, [])
    assert result.score_pct == 100.0
    assert any(r.item_ref == "greeting" and r.reason == "unverified_evidence" for r in review)


def test_per_item_scale_partial_on_binary_item_scores_zero_and_is_reviewed():
    cfg = get_config()
    spec = next(i for i in cfg.checklist.items if i.id == "no_prohibited_promises")
    assert spec.scale == {"pass": 1.0, "fail": 0.0}
    out = QAOut(items=[item("no_prohibited_promises", "partial", 2, "guarantee a full refund")])
    result, review = score(out, TURNS, cfg, [])
    it = next(i for i in result.items if i.item_id == "no_prohibited_promises")
    assert it.points == 0.0
    assert any(r.item_ref == "no_prohibited_promises" and r.reason == "verdict_not_on_scale" for r in review)


def test_blind_judge_disagreements_are_flagged_in_code():
    from datetime import datetime, timezone
    from app.pipeline.judge import JudgeChurn, JudgeOut, JudgeQAItem, JudgeResolution, agreement, compare
    from app.schemas import CallAnalysis, FinalAnalysis, SentimentTrajectory
    final = FinalAnalysis(summary="s", reasons=[], resolution="resolved", resolution_confidence=0.9,
                          resolution_rationale="", resolution_evidence=[], churn_flag=False, churn_score=0.1,
                          churn_rationale="", churn_evidence=[], verified=True)
    a = CallAnalysis(call_id="c", final=final, qa=None, rule_flags=[], actions=[], review_items=[], llm_calls=[],
                     sentiment=SentimentTrajectory(points=[], start=None, end=None, delta=None, direction="unknown", shift_turns=[]),
                     config_version="x", created_at=datetime.now(timezone.utc))
    out = JudgeOut(resolution=JudgeResolution(reason="only booked", status="partial"),
                   churn=JudgeChurn(reason="no threat", flag=False), qa=[JudgeQAItem(item_id="greeting", reason="", verdict="pass")])
    items = compare(a, out)
    assert [(i["claim_id"], i["support"]) for i in items] == [("resolution", "unsupported"), ("churn", "supported")]
    assert agreement(items) == 0.5


def test_partial_on_critical_item_is_a_violation():
    cfg = get_config()
    out = QAOut(items=[item("correct_disclosure", "partial", 2, "guarantee a full refund")])
    result, _ = score(out, TURNS, cfg, [])
    assert [v.item_id for v in result.violations] == ["correct_disclosure"]
