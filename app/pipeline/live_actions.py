"""Live follow-up actions, updated as the conversation flows.

Two pieces:
  * `ActionTracker`: a pure state machine that applies LLM deltas (add/update/close).
    It enforces the lifecycle and makes updates idempotent:
      - action IDs are assigned by CODE (A1, A2...), never invented by the LLM
      - an "add" that looks like an existing open action becomes an "update" (no duplicates)
      - update/close of an unknown ID is rejected
  * `LiveSession`: one per live call. Turns go into an asyncio.Queue and ONE consumer task
    processes them in order (deltas depend on the previous action list, so order matters).
    If the LLM is slower than the conversation, the consumer drains everything waiting and
    sends it in one call (coalescing). Every triggering turn is still covered, but cost and
    latency stay bounded. That is the backpressure strategy.
"""
import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable

from app import metrics
from app.config import AppConfig
from app.llm import LLMClient, LLMError
from app.pipeline.fast_path import RuleEngine
from app.pipeline.grounding import check_evidence, is_grounded
from app.pipeline.normalize import format_turns
from app.prompts import LIVE_ACTIONS
from app.schemas import Action, ActionDeltaOut, LiveUpdateOut, LLMMeta, Turn

log = logging.getLogger(__name__)
STOPWORDS = {"the", "a", "an", "to", "for", "of", "and", "on", "in", "with", "customer", "customer's", "s", "be"}
EventSink = Callable[[dict], Awaitable[None]]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


class ActionTracker:
    DUPLICATE_THRESHOLD = 0.5

    def __init__(self, actions: list[Action] | None = None):
        self.actions: dict[str, Action] = {a.action_id: a for a in actions or []}

    def _next_id(self) -> str:
        return f"A{len(self.actions) + 1}"

    def _find_duplicate(self, text: str) -> Action | None:
        open_actions = [a for a in self.actions.values() if a.status == "open"]
        best = max(open_actions, key=lambda a: similarity(a.text, text), default=None)
        return best if best and similarity(best.text, text) >= self.DUPLICATE_THRESHOLD else None

    def apply(self, deltas: list[ActionDeltaOut], turns_by_id: dict[int, Turn], at_turn: int) -> list[dict]:
        changes = []
        for d in deltas:
            ev = check_evidence(d.evidence, turns_by_id)
            op, target = d.op, self.actions.get(d.action_id or "")
            if op == "add":
                target = self._find_duplicate(d.text)
                op = "update" if target else "add"
            if op == "add":
                action = Action(action_id=self._next_id(), text=d.text, owner=d.owner, created_turn=at_turn,
                                updated_turn=at_turn, evidence=ev, verified=is_grounded(ev),
                                rationale=d.rationale, confidence=round(d.confidence, 3))
                self.actions[action.action_id] = action
            elif target is None:
                changes.append({"op": "rejected", "reason": f"unknown action_id {d.action_id}", "text": d.text})
                continue
            elif op == "update":
                target.text, target.owner, target.updated_turn = d.text or target.text, d.owner, at_turn
                target.evidence, target.verified = target.evidence + ev, target.verified or is_grounded(ev)
                action = target
            else:
                if target.status != "open":
                    continue
                target.status, target.updated_turn = "done", at_turn
                target.evidence = target.evidence + ev
                action = target
            changes.append({"op": op, **action.model_dump(mode="json")})
        return changes

    def as_prompt_json(self) -> str:
        return json.dumps([{"action_id": a.action_id, "text": a.text, "owner": a.owner, "status": a.status}
                           for a in self.actions.values()]) or "[]"


class LiveSession:
    def __init__(self, call_id: str, llm: LLMClient, config: AppConfig, on_event: EventSink | None = None,
                 context_turns: int = 6, actions: list[Action] | None = None):
        self.call_id = call_id
        self.llm = llm
        self.rules = RuleEngine(config)
        self.on_event = on_event
        self.context_turns = context_turns
        self.tracker = ActionTracker(actions)
        self.turns: dict[int, Turn] = {}
        self.summary = ""
        self.llm_calls: list[LLMMeta] = []
        self._queue: asyncio.Queue[Turn] = asyncio.Queue()
        self._carry: list[Turn] = []
        self._task: asyncio.Task | None = None
        self.base_timeout = llm.settings.live_timeout_s
        self.timeout = self.base_timeout

    def should_trigger(self, turn: Turn) -> bool:
        return turn.speaker == "customer" or self.rules.is_commitment(turn)

    async def add_turn(self, turn: Turn) -> bool:
        if turn.turn_id in self.turns:
            return False
        self.turns[turn.turn_id] = turn
        if self.should_trigger(turn):
            await self._queue.put(turn)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._consume())
        return True

    async def add_turn_sync(self, turn: Turn) -> list[dict]:
        if turn.turn_id in self.turns:
            return []
        self.turns[turn.turn_id] = turn
        return await self.process_batch([turn], budget=False) if self.should_trigger(turn) else []

    async def flush(self) -> None:
        await self._queue.join()
        if self._carry:
            await self.process_batch([], budget=False)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()

    async def _consume(self) -> None:
        while not self._queue.empty():
            batch = [await self._queue.get()]
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await self.process_batch(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def process_batch(self, new_turns: list[Turn], budget: bool = True) -> list[dict]:
        new_turns = self._carry + new_turns
        self._carry = []
        if not new_turns:
            return []
        started = time.perf_counter()
        metrics.LIVE_BATCH_SIZE.observe(len(new_turns))
        first = new_turns[0].turn_id
        context = [t for t in sorted(self.turns.values(), key=lambda t: t.turn_id) if t.turn_id < first][-self.context_turns:]
        last = new_turns[-1].turn_id
        window = [t for t in sorted(self.turns.values(), key=lambda t: t.turn_id) if first <= t.turn_id <= last]
        call = self.llm.generate(
            task="live_actions", prompt=LIVE_ACTIONS, schema=LiveUpdateOut, tier="fast", call_id=self.call_id,
            variables={"summary": self.summary or "(start of call)", "actions": self.tracker.as_prompt_json(),
                       "context": format_turns(context) or "(none)", "new_turns": format_turns(window)})
        try:
            out, meta = await (asyncio.wait_for(call, timeout=self.timeout) if budget else call)
            self.timeout = self.base_timeout
        except asyncio.TimeoutError:
            metrics.LIVE_TIMEOUTS.inc()
            self._carry = new_turns
            await self._emit({"type": "live_delayed", "turn_ids": [t.turn_id for t in new_turns],
                              "budget_s": self.timeout})
            self.timeout = min(self.timeout * 2, self.base_timeout * 4)
            return []
        except LLMError as e:
            log.warning("live update failed for %s: %s", self.call_id, e)
            metrics.PIPELINE_ERRORS.labels("live_actions").inc()
            self._carry = new_turns
            await self._emit({"type": "live_error", "turn_ids": [t.turn_id for t in new_turns], "error": "llm_unavailable"})
            return []
        self.llm_calls.append(meta)
        self.summary = out.rolling_summary
        changes = self.tracker.apply(out.deltas, self.turns, at_turn=last)
        metrics.TURN_LATENCY.labels("live").observe(time.perf_counter() - started)
        await self._emit({"type": "actions", "turn_ids": [t.turn_id for t in new_turns], "changes": changes,
                          "actions": [a.model_dump(mode="json") for a in self.tracker.actions.values()],
                          "summary": self.summary, "latency_ms": int((time.perf_counter() - started) * 1000)})
        return changes

    async def _emit(self, event: dict) -> None:
        if self.on_event:
            await self.on_event(event)
