from app.config import get_config
from app.pipeline.fast_path import LexiconSentiment, RuleEngine, SentimentPoint, trajectory
from app.schemas import Turn


def test_lexicon_polarity_and_negation():
    s = LexiconSentiment()
    assert s.score("I am really frustrated, this is terrible!") < -0.3
    assert s.score("Great, thanks so much, that's perfect") > 0.3
    assert s.score("not happy") < 0


def test_trajectory_direction_and_shift():
    pts = [SentimentPoint(turn_id=i, score=s, label="neutral") for i, s in [(2, -0.8), (4, -0.6), (6, 0.2), (8, 0.6)]]
    tr = trajectory(pts)
    assert tr.direction == "improved" and tr.start == -0.7 and tr.end == 0.4 and tr.shift_turns == [6]


def test_rules_fire_on_correct_speaker():
    rules = RuleEngine(get_config())
    agent = Turn(turn_id=1, speaker="agent", text="I guarantee a full refund")
    cust = Turn(turn_id=2, speaker="customer", text="I'm switching to another provider")
    assert [f.rule_id for f in rules.check(agent)] == ["guaranteed_refund"]
    assert [f.kind for f in rules.check(cust)] == ["churn_signal"]
    assert rules.check(Turn(turn_id=3, speaker="customer", text="I guarantee a full refund")) == []
