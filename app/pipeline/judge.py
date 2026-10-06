"""Online quality check: an LLM judge re-checks a SAMPLE of finished analyses.

judge.v2 is BLIND: the judge sees only the transcript and the definitions (checklist, resolution,
churn) and gives its OWN answers. Code then compares them with the system's answers; every
disagreement is a flagged claim. Why blind: judge.v1 was shown the system's verdicts and asked
"is this supported?". It agreed with 100% of claims and caught 0 of the real errors on the
held-out set (anchoring / sycophancy). Asking for an independent answer removes the anchor.
judge.v3 adds the policy lists (prohibited promises, required disclosures): v2 lacked them and
5 of its 8 false alarms were on correct_disclosure.

A different model family (Gemma by default) is used, because a model grading its own output
tends to agree with itself.
"""

from pydantic import BaseModel

from app.config import AppConfig
from app.llm import LLMClient, Prompt
from app.pipeline.normalize import format_turns
from app.pipeline.qa_scoring import policy_text, render_checklist
from app.schemas import CallAnalysis, LLMMeta, Resolution, Turn, Verdict


class JudgeQAItem(BaseModel):
    item_id: str
    reason: str
    verdict: Verdict


class JudgeResolution(BaseModel):
    reason: str
    status: Resolution


class JudgeChurn(BaseModel):
    reason: str
    flag: bool


class JudgeOut(BaseModel):
    resolution: JudgeResolution
    churn: JudgeChurn
    qa: list[JudgeQAItem]


JUDGE = Prompt(
    name="judge",
    version="judge.v3",
    system="""You are an independent senior QA auditor for a telecom contact center. Read the
conversation and give YOUR OWN answers (reason first, one sentence each):

1. resolution.status:
   - resolved: the issue was actually fixed during the call
   - partial: partly fixed, or a fix is only arranged/booked and not yet confirmed
   - unresolved: nothing fixed and nothing concrete arranged
   - escalated: handed to another team/supervisor for follow-up
2. churn.flag: true if the customer signals intent to cancel, switch provider or port out,
   or makes a strong threat to leave; otherwise false.
3. qa: one verdict per checklist item (pass | partial | fail | na | insufficient_evidence):
$checklist
Company policy. Prohibited promises:
$prohibited
Required disclosures (apply when the call matches):
$disclosures
Placeholders like [ACCOUNT_NO_1] are redacted values that were really said.""",
    user="""Conversation (format: [turn_id] speaker: text):
$transcript""",
)


def compare(a: CallAnalysis, out: JudgeOut) -> list[dict]:
    items = []

    def add(claim_id: str, system, judge, reason: str) -> None:
        items.append({"claim_id": claim_id, "system": system, "judge": judge, "reason": reason,
                      "support": "supported" if system == judge else "unsupported"})

    if a.final:
        add("resolution", a.final.resolution, out.resolution.status, out.resolution.reason)
        add("churn", a.final.churn_flag, out.churn.flag, out.churn.reason)
    if a.qa:
        judged = {j.item_id: j for j in out.qa}
        for i in a.qa.items:
            if i.item_id in judged:
                add(f"qa:{i.item_id}", i.verdict, judged[i.item_id].verdict, judged[i.item_id].reason)
    return items


def agreement(items: list[dict]) -> float | None:
    return round(sum(i["support"] == "supported" for i in items) / len(items), 3) if items else None


async def judge_analysis(a: CallAnalysis, turns: list[Turn], llm: LLMClient, config: AppConfig,
                         model: str) -> tuple[list[dict], float | None, LLMMeta]:
    out, meta = await llm.generate(task="judge", prompt=JUDGE, schema=JudgeOut, tier="strong", call_id=a.call_id,
                                   model_override=model,
                                   variables={"transcript": format_turns(turns), "checklist": render_checklist(config),
                                              **policy_text(config)})
    items = compare(a, out)
    return items, agreement(items), meta
