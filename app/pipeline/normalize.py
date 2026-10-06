"""Turn any input (voice transcript text, chat log, corpus rows) into a list of Turns.

Everything downstream only ever sees `Turn(turn_id, speaker, text, ts)`.

Chat logs are messier than transcripts: people send "hi" / "my bill" / "is wrong" as three
messages. We merge consecutive messages from the same speaker that arrive within
`merge_window_s` seconds into one turn, so the LLM and sentiment see whole thoughts.
"""
import re
from datetime import datetime, timedelta
from typing import Any

from app.schemas import Turn

SPEAKER_ALIASES = {
    "agent": "agent", "rep": "agent", "csr": "agent", "advisor": "agent", "support": "agent",
    "customer": "customer", "client": "customer", "caller": "customer", "user": "customer",
    "system": "system", "bot": "system",
}

LINE_RE = re.compile(
    r"^\s*(?:\[?(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]?\s*)?(?P<speaker>[A-Za-z ]{2,20}?)\s*:\s*(?P<text>.+)$"
)


def map_speaker(raw: str) -> str:
    key = raw.strip().lower()
    for alias, speaker in SPEAKER_ALIASES.items():
        if key == alias or key.startswith(alias):
            return speaker
    return "customer"


def parse_ts(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def from_transcript(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if m and m.group("speaker").strip().lower().split()[0] in SPEAKER_ALIASES:
            turns.append(Turn(turn_id=len(turns) + 1, speaker=map_speaker(m.group("speaker")),
                              text=clean_text(m.group("text"))))
        elif turns:
            turns[-1].text = clean_text(turns[-1].text + " " + line)
    return turns


def from_messages(messages: list[dict], merge_window_s: float = 60.0) -> list[Turn]:
    turns: list[Turn] = []
    for msg in messages:
        raw_speaker = msg.get("speaker") or msg.get("sender") or msg.get("role") or msg.get("from") or ""
        text = clean_text(str(msg.get("text") or msg.get("message") or msg.get("content") or ""))
        if not text:
            continue
        speaker = map_speaker(raw_speaker)
        ts = parse_ts(msg.get("ts") or msg.get("timestamp") or msg.get("date_time") or msg.get("time"))
        prev = turns[-1] if turns else None
        if prev and prev.speaker == speaker and _within(prev.ts, ts, merge_window_s):
            prev.text = f"{prev.text} {text}"
            continue
        turns.append(Turn(turn_id=len(turns) + 1, speaker=speaker, text=text, ts=ts))
    return turns


def _within(a: datetime | None, b: datetime | None, seconds: float) -> bool:
    return a is not None and b is not None and abs(b - a) <= timedelta(seconds=seconds)


def normalize(payload: str | list[dict], kind: str = "auto") -> list[Turn]:
    if kind == "transcript" or (kind == "auto" and isinstance(payload, str)):
        return from_transcript(str(payload))
    return from_messages(list(payload))


def format_turns(turns: list[Turn]) -> str:
    return "\n".join(f"[{t.turn_id}] {t.speaker}: {t.text}" for t in turns)
