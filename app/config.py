"""Loads and validates the YAML config (QA checklist, taxonomy, policy).

The config version is a short hash of the file contents, so every stored analysis
records exactly which config produced it, without anyone bumping a number by hand.
"""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.settings import get_settings


class QAItem(BaseModel):
    id: str
    name: str
    description: str
    weight: float
    scale: dict[str, float] | None = None
    critical: bool = False
    severity: str = "medium"
    allow_na: bool = False
    na_when: str | None = None
    absence_evidence: str | None = None


class QAChecklist(BaseModel):
    version: int
    verdict_points: dict[str, float]
    review_below_confidence: float = 0.6
    violation_verdicts: list[str] = ["fail"]
    items: list[QAItem]


class Reason(BaseModel):
    id: str
    description: str


class Taxonomy(BaseModel):
    version: int
    reasons: list[Reason]

    @property
    def ids(self) -> list[str]:
        return [r.id for r in self.reasons]


class PatternRule(BaseModel):
    id: str
    pattern: str
    description: str = ""


class Disclosure(BaseModel):
    id: str
    applies_to_reasons: list[str]
    description: str
    applies_when: str | None = None


class Policy(BaseModel):
    version: int
    prohibited_promises: list[PatternRule]
    required_disclosures: list[Disclosure]
    churn_signals: list[PatternRule]
    commitment_triggers: dict[str, str]


class AppConfig(BaseModel):
    checklist: QAChecklist
    taxonomy: Taxonomy
    policy: Policy
    version: str

    def compiled(self, rules: list[PatternRule]) -> list[tuple[PatternRule, re.Pattern]]:
        return [(r, re.compile(r.pattern, re.IGNORECASE)) for r in rules]


FILES = ("qa_checklist.yaml", "taxonomy.yaml", "policy.yaml")
TENANT_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def load_config(config_dir: Path | None = None, tenant: str = "default") -> AppConfig:
    if not TENANT_RE.match(tenant):
        raise ValueError(f"invalid tenant id {tenant!r}")
    config_dir = config_dir or get_settings().config_dir
    tenant_dir = config_dir / "tenants" / tenant
    if tenant != "default" and not tenant_dir.is_dir():
        raise FileNotFoundError(f"unknown tenant {tenant!r}")
    raw = {name: ((tenant_dir / name) if (tenant_dir / name).exists() else (config_dir / name)).read_bytes()
           for name in FILES}
    prefix = b"" if tenant == "default" else tenant.encode()
    digest = hashlib.sha256(prefix + b"".join(raw[n] for n in FILES)).hexdigest()[:12]
    return AppConfig(
        checklist=QAChecklist(**yaml.safe_load(raw["qa_checklist.yaml"])),
        taxonomy=Taxonomy(**yaml.safe_load(raw["taxonomy.yaml"])),
        policy=Policy(**yaml.safe_load(raw["policy.yaml"])),
        version=digest,
    )


@lru_cache
def get_config(tenant: str = "default") -> AppConfig:
    return load_config(tenant=tenant)
