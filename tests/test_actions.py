from app.pipeline.live_actions import ActionTracker
from app.schemas import ActionDeltaOut, Evidence, Turn

TURNS = {3: Turn(turn_id=3, speaker="agent", text="I'll schedule a technician visit for tomorrow"),
         5: Turn(turn_id=5, speaker="agent", text="The technician visit is booked, done.")}


def delta(op, text, action_id=None, turn=3, quote="schedule a technician visit"):
    return ActionDeltaOut(op=op, action_id=action_id, text=text, owner="agent", rationale="", confidence=0.9,
                          evidence=[Evidence(turn_id=turn, quote=quote)])


def test_add_then_duplicate_add_becomes_update():
    t = ActionTracker()
    t.apply([delta("add", "Schedule technician visit")], TURNS, 3)
    changes = t.apply([delta("add", "Schedule a technician visit tomorrow")], TURNS, 4)
    assert len(t.actions) == 1 and changes[0]["op"] == "update"


def test_close_is_idempotent_and_unknown_id_rejected():
    t = ActionTracker()
    t.apply([delta("add", "Schedule technician visit")], TURNS, 3)
    close = delta("close", "Schedule technician visit", "A1", 5, "technician visit is booked")
    assert t.apply([close], TURNS, 5)[0]["status"] == "done"
    assert t.apply([close], TURNS, 6) == []
    assert t.apply([delta("update", "x", "A9")], TURNS, 6)[0]["op"] == "rejected"


def test_unverified_evidence_marks_action():
    t = ActionTracker()
    t.apply([delta("add", "Refund", quote="I promise a refund")], TURNS, 3)
    assert t.actions["A1"].verified is False
