from app.pipeline.grounding import check_evidence, is_grounded, quote_in_text
from app.schemas import Evidence, Turn

TURNS = {1: Turn(turn_id=1, speaker="agent", text="I guarantee you a full refund today, okay? Your ID is [ACCOUNT_NO_1].")}


def test_exact_and_forgiving_match():
    assert quote_in_text("guarantee you a full refund", TURNS[1].text)
    assert quote_in_text("I GUARANTEE you a full refund today okay", TURNS[1].text)
    assert quote_in_text("I guarantee ... [ACCOUNT_NO_1]", TURNS[1].text)


def test_paraphrase_and_wrong_turn_fail():
    assert not quote_in_text("I promise a refund", TURNS[1].text)
    checked = check_evidence([Evidence(turn_id=2, quote="full refund")], TURNS)
    assert not checked[0].verified and not is_grounded(checked)


def test_no_evidence_rules():
    assert not is_grounded([], required=True)
    assert is_grounded([], required=False)
