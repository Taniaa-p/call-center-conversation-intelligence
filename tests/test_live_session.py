"""LiveSession latency budget: a slow model must not stall the live view."""
import asyncio
from types import SimpleNamespace

from app.config import get_config
from app.pipeline.live_actions import LiveSession
from app.schemas import ActionDeltaOut, Evidence, LiveUpdateOut, LLMMeta, Turn


class FakeLLM:
    def __init__(self, delays):
        self.settings = SimpleNamespace(live_timeout_s=0.05)
        self.delays = delays
        self.seen_batches = []

    async def generate(self, **kw):
        self.seen_batches.append(kw["variables"]["new_turns"])
        await asyncio.sleep(self.delays.pop(0))
        delta = ActionDeltaOut(op="add", action_id=None, text="Schedule technician visit", owner="agent",
                               rationale="", confidence=0.9, evidence=[Evidence(turn_id=2, quote="technician")])
        return LiveUpdateOut(rolling_summary="s", deltas=[delta]), LLMMeta(task="live_actions", model="m", prompt_version="v")


async def test_timeout_carries_turns_and_budget_grows_then_resets():
    events = []

    async def sink(e):
        events.append(e)

    llm = FakeLLM(delays=[0.2, 0.0])
    s = LiveSession("c", llm, get_config(), on_event=sink)
    s.turns = {1: Turn(turn_id=1, speaker="customer", text="no internet"),
               2: Turn(turn_id=2, speaker="customer", text="send a technician please")}
    await s.process_batch([s.turns[1]])
    assert events[-1]["type"] == "live_delayed" and s.timeout == 0.1
    await s.process_batch([s.turns[2]])
    assert events[-1]["type"] == "actions" and events[-1]["turn_ids"] == [1, 2]
    assert s.timeout == 0.05 and "A1" in s.tracker.actions


async def test_no_budget_for_final_flush():
    llm = FakeLLM(delays=[0.2])
    s = LiveSession("c", llm, get_config())
    s.turns = {1: Turn(turn_id=1, speaker="customer", text="send a technician")}
    await s.process_batch([s.turns[1]], budget=False)
    assert "A1" in s.tracker.actions
