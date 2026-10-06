"""PII redaction behind a small interface.

A backend only has to FIND spans (start, end, entity_type). The shared `Redactor` turns
spans into typed placeholders like [PHONE_1] and builds the entity map.

  * same value in one call  -> same placeholder (so "[ACCOUNT_NO_1]" can be matched later)
  * entity map (placeholder -> real value) is returned separately and stored in a
    restricted table; the entity map itself is never sent to the LLM, logs or analytics.

Backends: `RegexBackend` (default, zero dependencies), `GlinerBackend` (optional ML
extra), `HybridBackend` (union of both). Choose with PII_BACKEND.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.schemas import Turn


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: str
    placeholder: str | None = None


@dataclass
class PIIEntity:
    placeholder: str
    entity_type: str
    value: str
    turn_id: int


class PIIBackend(Protocol):
    name: str

    def find(self, text: str) -> list[Span]: ...


DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DW = "|".join(DIGIT_WORDS)
SPOKEN_RUN = re.compile(rf"\b(?:(?:double|triple)\s+)?(?:{_DW})\b(?:[\s,\-]+(?:(?:double|triple)\s+)?(?:{_DW})\b)+", re.I)
NUMBER_RUN = re.compile(r"(?<![\w.$₹/])\+?\d[\d \-]{2,}\d(?![\w%/]|\.\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
ALNUM_ACCOUNT = re.compile(r"\b(?:ACC|ACCT|CUST|CID)[-\s]?\d{5,12}\b", re.I)
GOV_ID_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
DATE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*,?\s+(?:19|20)\d{2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2})\b", re.I)
TITLE = r"(?:(?i:mr|mrs|ms|miss|dr)\.?\s+)?"
NAME_INTRO = re.compile(
    r"\b(?:(?i:mr|mrs|ms|miss|dr)\.?\s+"
    r"|(?i:my name is|name is|this is|i am|i'm|im|name's|speaking with|it is|it's|main|mera naam|"
    r"thanks|thank you|hello|hi|hey|dear|sorry|good morning|good afternoon|good evening|okay|ok)[,]?\s+" + TITLE + ")"
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
VOCATIVE = re.compile(r",\s+" + TITLE + r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?=\s*[.!?])")
NAME_AT_START = re.compile(r"^(?:(?i:yes|yeah|sure|haan|ji)[,.]?\s+)?(?P<name>[A-Z][a-z]+\s+[A-Z][a-z]+)(?=[,.])")
ADDRESS = re.compile(
    r"\b\d{1,5}[,\s]+(?:[A-Z][\w']*\s+){1,4}(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|"
    r"boulevard|blvd|nagar|colony|sector|block|marg|layout|cross)\b\.?", re.I)
NOT_NAMES = {"Calling", "Sorry", "Happy", "Glad", "Here", "Just", "Not", "Very", "Really", "So",
             "The", "Your", "Going", "Looking", "Trying", "Still", "Afraid", "Unable", "Able", "Thank",
             "Thanks", "Okay", "Yes", "No", "Please", "Sure", "Correct", "Great", "Fine", "Nimbus", "Union",
             "Bahut", "Mera", "Aap", "Kya", "Hello", "Hi", "Sir", "Madam", "Bye", "Goodbye", "Today",
             "Tomorrow", "Yesterday", "Everyone", "There", "Team", "Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday", "Hey", "Good", "Mr", "Mrs", "Ms", "Miss", "Dr", "January", "February", "March", "April", "June", "July", "August",
             "September", "October", "November", "December", "For", "Again", "Too", "Though", "Ji"}
NOT_NAME_PHRASES = {"Nimbus Telecom", "Union Mobile"}


def spoken_to_digits(text: str) -> str:
    out, repeat = [], 1
    for tok in re.split(r"[\s,\-]+", text.lower()):
        if tok in ("double", "triple"):
            repeat = 2 if tok == "double" else 3
        elif tok in DIGIT_WORDS:
            out.append(DIGIT_WORDS[tok] * repeat)
            repeat = 1
    return "".join(out)


def classify_number(digits: str, context: str) -> str | None:
    ctx = context.lower()
    n = len(digits)
    if re.search(r"\b(pin|otp|code|cvv)\b", ctx) and 3 <= n <= 6:
        return "PIN"
    if re.search(r"\bcard\b", ctx) and n >= 12:
        return "CARD_NUMBER"
    if re.search(r"\b(aadhaar|aadhar|ssn|passport)\b", ctx):
        return "GOV_ID"
    if re.search(r"\b(phone|mobile|number to reach|call me|contact|whatsapp)\b", ctx) and 10 <= n <= 13:
        return "PHONE"
    if re.search(r"\b(account|acct|customer id|consumer|reference)\b", ctx) and n >= 5:
        return "ACCOUNT_NO"
    if 13 <= n <= 19:
        return "CARD_NUMBER"
    if n in (10, 11, 12) or (n == 12 and digits.startswith("91")):
        return "PHONE"
    if 6 <= n <= 9:
        return "ACCOUNT_NO"
    return None


class RegexBackend:
    name = "regex"

    def find(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for rx, etype in ((EMAIL, "EMAIL"), (ALNUM_ACCOUNT, "ACCOUNT_NO"), (GOV_ID_PAN, "GOV_ID"),
                          (DATE, "DATE"), (ADDRESS, "ADDRESS")):
            spans += [Span(m.start(), m.end(), etype) for m in rx.finditer(text)]
        for m in list(NAME_INTRO.finditer(text)) + list(NAME_AT_START.finditer(text)) + list(VOCATIVE.finditer(text)):
            if m.group("name").split()[0] not in NOT_NAMES and m.group("name") not in NOT_NAME_PHRASES:
                spans.append(Span(m.start("name"), m.end("name"), "PERSON"))
        for rx, to_digits in ((NUMBER_RUN, lambda s: re.sub(r"\D", "", s)), (SPOKEN_RUN, spoken_to_digits)):
            for m in rx.finditer(text):
                digits = to_digits(m.group())
                etype = classify_number(digits, text[max(0, m.start() - 40):m.start()])
                if etype:
                    spans.append(Span(m.start(), m.end(), etype))
        return spans


GLINER_LABELS = {
    "person": "PERSON", "phone number": "PHONE", "email": "EMAIL", "address": "ADDRESS",
    "credit card number": "CARD_NUMBER", "account number": "ACCOUNT_NO", "date of birth": "DATE",
    "aadhaar number": "GOV_ID",
}


PRONOUNS = {"i", "you", "me", "my", "we", "he", "she", "they", "sir", "madam", "maam", "ma'am"}
NUMERIC_TYPES = {"PHONE", "ACCOUNT_NO", "CARD_NUMBER", "GOV_ID", "DATE"}


def plausible(value: str, etype: str) -> bool:
    v = value.strip()
    if etype == "PERSON":
        return v.lower() not in PRONOUNS and any(tok[:1].isupper() for tok in v.split()) and len(v) > 2
    if etype in NUMERIC_TYPES:
        return sum(ch.isdigit() for ch in v) >= 4
    if etype == "EMAIL":
        return "@" in v
    if etype == "ADDRESS":
        return any(ch.isdigit() for ch in v)
    return True


class GlinerBackend:
    name = "gliner"

    def __init__(self, model_name: str = "urchade/gliner_multi_pii-v1", threshold: float = 0.5,
                 filter_false_positives: bool = True):
        from gliner import GLiNER
        self.model = GLiNER.from_pretrained(model_name)
        self.threshold = threshold
        self.filter = filter_false_positives

    def find(self, text: str) -> list[Span]:
        ents = self.model.predict_entities(text, list(GLINER_LABELS), threshold=self.threshold)
        spans = [Span(e["start"], e["end"], GLINER_LABELS[e["label"]]) for e in ents]
        return [s for s in spans if plausible(text[s.start:s.end], s.entity_type)] if self.filter else spans


class HybridBackend:
    name = "hybrid"

    def __init__(self):
        self.backends = [RegexBackend(), GlinerBackend()]

    def find(self, text: str) -> list[Span]:
        return [s for b in self.backends for s in b.find(text)]


def resolve_overlaps(spans: list[Span]) -> list[Span]:
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (-(s.end - s.start), s.start)):
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


@dataclass
class RedactionContext:
    by_value: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    name_tokens: dict[str, str] = field(default_factory=dict)

    def remember_name(self, value: str, placeholder: str) -> None:
        for tok in value.split():
            if len(tok) > 2 and tok[0].isupper() and tok not in NOT_NAMES:
                self.name_tokens.setdefault(tok, placeholder)

    def memory_spans(self, text: str) -> list[Span]:
        return [Span(m.start(), m.end(), "PERSON", ph) for tok, ph in self.name_tokens.items()
                for m in re.finditer(rf"\b{re.escape(tok)}\b", text)]

    def placeholder_for(self, etype: str, value: str) -> str:
        key = re.sub(r"[\W_]", "", value.lower())
        if key not in self.by_value:
            self.counters[etype] = self.counters.get(etype, 0) + 1
            self.by_value[key] = f"[{etype}_{self.counters[etype]}]"
        return self.by_value[key]

    def restore(self, placeholder: str, etype: str, value: str) -> None:
        self.by_value[re.sub(r"[\W_]", "", value.lower())] = placeholder
        if etype == "PERSON":
            self.remember_name(value, placeholder)
        ptype, n = placeholder.strip("[]").rsplit("_", 1)
        self.counters[ptype] = max(self.counters.get(ptype, 0), int(n))


class Redactor:
    def __init__(self, backend: PIIBackend):
        self.backend = backend

    def redact_text(self, text: str, turn_id: int, ctx: RedactionContext) -> tuple[str, list[PIIEntity]]:
        out, entities, cursor = [], [], 0
        found = self.backend.find(text)
        for span in found:
            if span.entity_type == "PERSON":
                value = text[span.start:span.end]
                ctx.remember_name(value, ctx.name_tokens.get(value) or ctx.placeholder_for("PERSON", value))
        for span in resolve_overlaps(found + ctx.memory_spans(text)):
            value = text[span.start:span.end]
            known = ctx.name_tokens.get(value) if span.entity_type == "PERSON" else None
            ph = span.placeholder or known or ctx.placeholder_for(span.entity_type, value)
            out += [text[cursor:span.start], ph]
            entities.append(PIIEntity(ph, span.entity_type, value, turn_id))
            cursor = span.end
        out.append(text[cursor:])
        return "".join(out), entities

    def redact_turn(self, turn: Turn, ctx: RedactionContext) -> tuple[Turn, list[PIIEntity]]:
        text, entities = self.redact_text(turn.text, turn.turn_id, ctx)
        return turn.model_copy(update={"text": text}), entities

    def redact_turns(self, turns: list[Turn], ctx: RedactionContext | None = None) -> tuple[list[Turn], list[PIIEntity]]:
        ctx = ctx or RedactionContext()
        redacted, entities = [], []
        for t in turns:
            rt, ents = self.redact_turn(t, ctx)
            redacted.append(rt)
            entities += ents
        return redacted, entities


@lru_cache
def get_redactor(backend: str = "regex") -> Redactor:
    backends = {"regex": RegexBackend, "gliner": GlinerBackend, "hybrid": HybridBackend}
    return Redactor(backends[backend]())
