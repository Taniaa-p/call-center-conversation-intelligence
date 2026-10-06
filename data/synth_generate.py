"""Generate synthetic calls with KNOWN, planted outcomes (the eval answer key).

How the labels stay trustworthy:
  1. Python (seeded RNG) decides the scenario: agent/team, reasons, resolution, churn, which
     QA failures to plant, the exact prohibited phrase, and the fake PII values.
  2. The LLM only writes the dialogue for that spec and tags the turns where things happen.
  3. CODE then verifies the dialogue: every PII value is present verbatim, the planted
     prohibited phrase is present, no un-planted regex prohibited phrase snuck in, the planted
     churn signal is tagged, etc. Calls that fail are regenerated (max 3 tries) or dropped.

The labels come from the spec (step 1), not from the LLM, except action/turn tags.

Outputs:
  data/synthetic/calls/<call_id>.json   raw call (with fake PII), voice or chat format
  data/labels/labels.jsonl              the answer key (one line per call)

Split: the first 50 calls are `dev` (tune prompts on these), the rest `heldout` (report only).
"""
import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_config  # noqa: E402
from app.llm import LLMClient, LLMError, Prompt  # noqa: E402
from app.pipeline.fast_path import RuleEngine  # noqa: E402
from app.schemas import Turn  # noqa: E402

ROOT = Path(__file__).resolve().parent
CALLS_DIR = ROOT / "synthetic" / "calls"
LABELS = ROOT / "labels" / "labels.jsonl"
COMPANY = "Nimbus Telecom"

TEAMS = {"team_north": ["agent_01", "agent_02", "agent_03"],
         "team_south": ["agent_04", "agent_05", "agent_06"],
         "team_west": ["agent_07", "agent_08"]}
AGENT_NAMES = {"agent_01": "Ravi", "agent_02": "Meera", "agent_03": "Daniel", "agent_04": "Sana",
               "agent_05": "Arjun", "agent_06": "Lucy", "agent_07": "Vikram", "agent_08": "Nisha"}
AGENT_RISK = {"agent_03": {"skip_identity_verification": 0.45},
              "agent_07": {"prohibited_promise": 0.5, "no_empathy": 0.35},
              "agent_05": {"missing_closure": 0.4, "missing_disclosure": 0.5}}
BASE_RISK = {"skip_identity_verification": 0.15, "prohibited_promise": 0.15, "missing_closure": 0.12,
             "missing_disclosure": 0.25, "missing_greeting": 0.08, "no_empathy": 0.12}

PROHIBITED_PHRASES = [
    "I guarantee you'll get a full refund on this.",
    "I promise this will never happen again.",
    "I'll waive all the fees on your account, no problem.",
    "Don't worry, this add-on will be free forever for you.",
    "Trust me, the money will definitely be back in your account by tomorrow.",
    "I can assure you there won't be any more outages in your area.",
    "I'll make sure you're never charged for roaming again.",
    "Your speed will be fixed by tonight, that's certain.",
]
DISCLOSURES = {
    "plan_change": "state the new monthly price and the date the change takes effect",
    "cancellation": "state the early-termination fee (or that there is none) and when the final bill arrives",
    "billing": "say the refund/credit is subject to approval and give the timeline (7-10 business days)",
}
ACTIONS = {
    "billing": ("refund_request", "raise a refund/credit request for the wrong charge", ["refund", "credit", "reversal", "adjust"]),
    "network": ("technician_or_ticket", "schedule a technician visit or raise a network ticket", ["technician", "ticket", "complaint", "engineer", "visit"]),
    "plan_change": ("plan_change", "process the plan change and send a confirmation SMS", ["plan", "upgrade", "downgrade", "confirmation"]),
    "cancellation": ("cancellation_or_retention", "process the cancellation request or log a retention offer", ["cancel", "retention", "offer", "disconnect", "port"]),
    "device": ("device_fix", "send a replacement SIM/device or book a device check", ["sim", "replacement", "device", "router", "handset", "esim"]),
}
FIRST = ["Priya", "Rahul", "Anjali", "Karan", "Emily", "James", "Fatima", "Rohan", "Sneha", "Michael", "Aisha", "Vivek"]
LAST = ["Sharma", "Patel", "Iyer", "Reddy", "Johnson", "Khan", "Mehta", "Brown", "Nair", "Gupta", "Das", "Wilson"]
STREETS = ["MG Road", "Park Street", "Linking Road", "Brigade Road", "Station Road", "Lake View Road"]
DIGITS = "zero one two three four five six seven eight nine".split()


class SynthTurn(BaseModel):
    speaker: str
    text: str
    tags: list[str]


class SynthCall(BaseModel):
    turns: list[SynthTurn]


GEN_PROMPT = Prompt(
    name="synth_call",
    version="synth_call.v1",
    system=f"""You write realistic synthetic {COMPANY} (a telecom company) customer-support conversations
for testing a quality-assurance system. Follow the SPEC exactly: every MUST and MUST NOT matters,
because the conversation is used as a labelled test case.

Return JSON: {{"turns": [{{"speaker": "agent"|"customer", "text": "...", "tags": [...]}}]}}
Tags mark the turn where something happens. Allowed tags: greeting, identity_verification,
empathy, disclosure, prohibited_promise, closure, churn_signal, and action:<key> for the turn
where the agent commits to a follow-up action. Leave tags empty for ordinary turns.
Write natural dialogue: no narration, no stage directions, no placeholders like [NAME].""",
    user="$spec",
)


def luhn_card(rng: random.Random) -> str:
    digits = [4] + [rng.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        d = d * 2 if i % 2 == 0 else d
        total += d - 9 if d > 9 else d
    return "".join(map(str, digits + [(10 - total % 10) % 10]))


def make_pii(rng: random.Random, verify: bool) -> dict:
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    phone = f"9{rng.randint(100000000, 999999999)}"
    pii = {"PERSON": name, "PHONE": f"{phone[:5]} {phone[5:]}"}
    if verify:
        pii["ACCOUNT_NO"] = str(rng.randint(10_000_000, 99_999_999))
    if rng.random() < 0.3:
        pii["EMAIL"] = f"{name.split()[0].lower()}.{rng.randint(10, 99)}@examplemail.com"
    if rng.random() < 0.25:
        pii["ADDRESS"] = f"{rng.randint(2, 480)} {rng.choice(STREETS)}"
    if rng.random() < 0.15:
        card = luhn_card(rng)
        pii["CARD_NUMBER"] = " ".join(DIGITS[int(c)] for c in card)
    if verify and rng.random() < 0.3:
        pii["DATE"] = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1965, 2002)}"
    return pii


def make_spec(i: int, rng: random.Random, start: datetime) -> dict:
    team = rng.choice(list(TEAMS))
    agent = rng.choice(TEAMS[team])
    risk = {**BASE_RISK, **AGENT_RISK.get(agent, {})}
    main = rng.choice(["billing", "billing", "network", "network", "plan_change", "cancellation", "device"])
    reasons = [main] + ([rng.choice([r for r in ACTIONS if r != main])] if rng.random() < 0.3 else [])
    resolution = rng.choices(["resolved", "partial", "unresolved", "escalated"], [0.45, 0.2, 0.15, 0.2])[0]
    churn = "cancellation" in reasons or (resolution in ("unresolved", "escalated") and rng.random() < 0.5) or rng.random() < 0.08
    direction = ("improved" if resolution == "resolved" else "worsened" if resolution == "unresolved"
                 else rng.choice(["improved", "flat", "worsened"]))
    planted = {k: rng.random() < p for k, p in risk.items()}
    disclosure_reasons = [r for r in reasons if r in DISCLOSURES]
    if not disclosure_reasons:
        planted["missing_disclosure"] = False
    channel = "chat" if rng.random() < 0.2 else "voice"
    language = "hinglish" if rng.random() < 0.15 else "en"
    return {
        "call_id": f"syn_{i:03d}", "split": "dev" if i <= 50 else "heldout",
        "agent_id": agent, "team_id": team, "agent_name": AGENT_NAMES[agent],
        "channel": channel, "language": language, "reasons": reasons, "resolution": resolution,
        "churn": churn, "sentiment_direction": direction, "planted": planted,
        "disclosures": [DISCLOSURES[r] for r in disclosure_reasons],
        "prohibited_phrase": rng.choice(PROHIBITED_PHRASES) if planted["prohibited_promise"] else None,
        "actions": [ACTIONS[r] for r in reasons if r in ACTIONS and resolution != "unresolved"],
        "pii": make_pii(rng, verify=not planted["skip_identity_verification"]),
        "started_at": (start - timedelta(days=rng.randint(0, 6), minutes=rng.randint(0, 600))).isoformat(),
    }


def spec_text(s: dict) -> str:
    p = s["planted"]
    lines = [f"SPEC for call {s['call_id']}",
             f"- Channel: {'live CHAT (short informal messages, a few emojis, typos ok)' if s['channel'] == 'chat' else 'VOICE call transcript'}.",
             f"- Language: {'Hinglish: the customer code-mixes Hindi and English in Latin script (e.g. Mera bill bahut zyada aaya hai, please check karo); the agent replies mostly in English with some Hindi' if s['language'] == 'hinglish' else 'English'}.",
             f"- Length: 10 to 18 turns, alternating speakers. Agent's name is {s['agent_name']}.",
             f"- Call reasons (all must clearly come up): {', '.join(s['reasons'])}.",
             f"- Outcome: {s['resolution']} (resolved = fixed on the call; partial = partly fixed or fix arranged but not confirmed; "
             "unresolved = nothing fixed and nothing concrete arranged; escalated = handed to another team/supervisor).",
             f"- Customer sentiment: {dict(improved='starts frustrated/upset, ends satisfied and thankful', worsened='starts calm, ends angry and dissatisfied', flat='stays roughly the same mood throughout')[s['sentiment_direction']]}.",
             "- The customer MUST express a problem or frustration early on."]
    lines.append("- Greeting: the agent's first turn MUST NOT greet, MUST NOT name the company or themselves; they jump straight in (e.g. 'Yeah, what's the problem?')."
                 if p["missing_greeting"] else f"- Greeting: the agent's first turn greets, names {COMPANY} and themselves, and offers help (tag greeting).")
    if p["skip_identity_verification"]:
        lines.append("- Identity verification: the agent MUST NOT ask for any account number, PIN, OTP, date of birth or registered number; they discuss and change the account straight away. The customer never gives an account number.")
    else:
        lines.append("- Identity verification: BEFORE discussing account details, the agent asks for the account number (and possibly date of birth); the customer gives it (tag identity_verification on the customer's turn).")
    lines.append("- Empathy: the agent ignores the customer's frustration completely and stays curt and transactional; MUST NOT apologise or say they understand."
                 if p["no_empathy"] else "- Empathy: the agent acknowledges the customer's frustration naturally (tag empathy).")
    if s["disclosures"]:
        lines.append("- Disclosure: the agent MUST NOT mention any price, fee, effective date, approval or timeline when making the change/refund/cancellation."
                     if p["missing_disclosure"] else "- Disclosure: the agent MUST clearly " + "; and ".join(s["disclosures"]) + " (tag disclosure).")
    else:
        lines.append("- Disclosure: none needed; no plan change, refund or cancellation is agreed.")
    if s["prohibited_phrase"]:
        lines.append(f"- Prohibited promise: the agent MUST say this sentence word for word: \"{s['prohibited_phrase']}\" (tag prohibited_promise).")
    else:
        lines.append("- The agent MUST NOT guarantee refunds or outcomes, MUST NOT promise problems will never happen again, MUST NOT promise free service forever or waive all fees.")
    lines.append("- Churn: the customer clearly says they will cancel, switch to another provider or port out (tag churn_signal)."
                 if s["churn"] else "- Churn: the customer MUST NOT mention leaving, cancelling, switching provider or porting out.")
    for key, desc, _ in s["actions"]:
        lines.append(f"- Follow-up action: the agent explicitly commits to: {desc} (tag action:{key} on that agent turn).")
    lines.append("- Closure: the call ends abruptly right after the agent's last action statement; the agent MUST NOT ask 'anything else', MUST NOT summarise, MUST NOT say goodbye."
                 if p["missing_closure"] else "- Closure: the agent asks if there is anything else, summarises next steps and says goodbye politely (tag closure).")
    lines.append("- Include these personal details VERBATIM (exact characters) where natural; the customer says them:")
    lines += [f"    {k}: {v}" for k, v in s["pii"].items()]
    if "CARD_NUMBER" in s["pii"]:
        lines.append("    (the card number is read aloud digit by digit exactly as written above, e.g. to pay a bill)")
    return "\n".join(lines)


def verify(spec: dict, call: SynthCall, rules: RuleEngine) -> list[str]:
    problems = []
    text_all = " ".join(t.text for t in call.turns)
    if not 8 <= len(call.turns) <= 24:
        problems.append(f"length {len(call.turns)} not in 8..24")
    for etype, value in spec["pii"].items():
        if value not in text_all:
            problems.append(f"PII {etype} value '{value}' missing verbatim")
    agent_turns = [Turn(turn_id=i + 1, speaker="agent", text=t.text) for i, t in enumerate(call.turns) if t.speaker == "agent"]
    cust_turns = [Turn(turn_id=i + 1, speaker="customer", text=t.text) for i, t in enumerate(call.turns) if t.speaker == "customer"]
    regex_hits = [f for t in agent_turns for f in rules.check(t)]
    if spec["prohibited_phrase"]:
        if not any(spec["prohibited_phrase"].lower().rstrip(".") in t.text.lower() for t in agent_turns):
            problems.append("planted prohibited phrase missing")
    elif regex_hits:
        problems.append(f"unplanted prohibited promise: {regex_hits[0].quote}")
    if spec["planted"]["skip_identity_verification"] and any(
            re.search(r"\b(account number|pin|otp|date of birth|dob|security)\b", t.text, re.I) for t in agent_turns):
        problems.append("verification was planted as skipped but the agent asked for a knowledge factor")
    tags = [tag for t in call.turns for tag in t.tags]
    if spec["churn"] and "churn_signal" not in tags:
        problems.append("churn planted but no churn_signal tag")
    if not spec["churn"] and any(f.kind == "churn_signal" and f.rule_id != "fed_up" for t in cust_turns for f in rules.check(t)):
        problems.append("unplanted churn language")
    for key, _, _ in spec["actions"]:
        if f"action:{key}" not in tags:
            problems.append(f"action {key} not tagged")
    return problems


def to_record(spec: dict, call: SynthCall, rng: random.Random) -> tuple[dict, dict]:
    ts = datetime.fromisoformat(spec["started_at"])
    turns, action_turns = [], {}
    for i, t in enumerate(call.turns, start=1):
        ts += timedelta(seconds=rng.randint(15, 120) if spec["channel"] == "chat" else rng.randint(4, 25))
        speaker = "agent" if t.speaker.lower().startswith("agent") else "customer"
        turns.append({"turn_id": i, "speaker": speaker, "text": t.text, "ts": ts.isoformat()})
        for tag in t.tags:
            if tag.startswith("action:"):
                action_turns.setdefault(tag.split(":", 1)[1], i)
    base = {k: spec[k] for k in ("call_id", "agent_id", "team_id", "channel", "language", "started_at")}
    if spec["channel"] == "chat":
        call_file = base | {"messages": [{"sender": t["speaker"], "message": t["text"], "timestamp": t["ts"]} for t in turns],
                            "turns": turns}
    else:
        call_file = base | {"transcript": "\n".join(f"{t['speaker'].capitalize()}: {t['text']}" for t in turns), "turns": turns}
    p = spec["planted"]
    qa = {"greeting": "fail" if p["missing_greeting"] else "pass",
          "identity_verification": "fail" if p["skip_identity_verification"] else "pass",
          "empathy": "fail" if p["no_empathy"] else "pass",
          "correct_disclosure": ("fail" if p["missing_disclosure"] else "pass") if spec["disclosures"] else "na",
          "no_prohibited_promises": "fail" if p["prohibited_promise"] else "pass",
          "proper_closure": "fail" if p["missing_closure"] else "pass"}
    critical = {i.id for i in get_config().checklist.items if i.critical}
    label = base | {
        "split": spec["split"], "reasons": spec["reasons"], "resolution": spec["resolution"], "churn": spec["churn"],
        "sentiment_direction": spec["sentiment_direction"], "planted": p, "qa_expected": qa,
        "violations_expected": sorted(k for k, v in qa.items() if v == "fail" and k in critical),
        "prohibited_phrase": spec["prohibited_phrase"],
        "pii": [{"type": k, "value": v} for k, v in spec["pii"].items()],
        "expected_actions": [{"key": k, "keywords": kw, "turn_id": action_turns.get(k)} for k, _, kw in spec["actions"]],
    }
    return call_file, label


async def generate_one(spec: dict, llm: LLMClient, rules: RuleEngine, rng: random.Random,
                       model: str | None) -> tuple[dict, dict] | None:
    feedback = ""
    for attempt in range(3):
        try:
            call, _ = await llm.generate(task="synth_generate", prompt=GEN_PROMPT, schema=SynthCall, tier="strong",
                                         variables={"spec": spec_text(spec) + feedback}, model_override=model)
        except LLMError as e:
            print(f"  {spec['call_id']}: LLM error {e}")
            continue
        problems = verify(spec, call, rules)
        if not problems:
            call_file, label = to_record(spec, call, rng)
            (CALLS_DIR / f"{spec['call_id']}.json").write_text(json.dumps(call_file, indent=1, ensure_ascii=False))
            print(f"  ok {spec['call_id']}", flush=True)
            return call_file, label
        print(f"  {spec['call_id']} attempt {attempt + 1}: {problems}", flush=True)
        feedback = "\n\nYOUR PREVIOUS ATTEMPT BROKE THE SPEC: " + "; ".join(problems) + ". Fix these."
    return None


async def main(n: int, seed: int, model: str | None) -> None:
    rng = random.Random(seed)
    start = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
    specs = [make_spec(i, rng, start) for i in range(1, n + 1)]
    llm, rules = LLMClient(), RuleEngine(get_config())
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    results = await asyncio.gather(*(generate_one(s, llm, rules, random.Random(seed + i), model) for i, s in enumerate(specs)))
    kept = [r for r in results if r]
    LABELS.write_text("".join(json.dumps(label, ensure_ascii=False) + "\n" for _, label in kept))
    splits = [label["split"] for _, label in kept]
    print(f"kept {len(kept)}/{n} calls ({splits.count('dev')} dev, {splits.count('heldout')} heldout)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=70)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="gemma-4-31b-it")
    args = ap.parse_args()
    asyncio.run(main(args.n, args.seed, args.model))
