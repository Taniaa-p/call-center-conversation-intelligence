import pytest

from app.config import load_config


def test_tenant_overrides_checklist_and_falls_back_for_other_files():
    base, acme = load_config(), load_config(tenant="acme_telecom")
    assert [i.id for i in acme.checklist.items][-1] == "recording_disclosure"
    assert len(acme.checklist.items) == len(base.checklist.items) + 1
    assert acme.taxonomy == base.taxonomy and acme.policy == base.policy
    assert acme.version != base.version


def test_unknown_or_malicious_tenant_rejected():
    with pytest.raises(FileNotFoundError):
        load_config(tenant="no_such_client")
    with pytest.raises(ValueError):
        load_config(tenant="../../etc")


def test_tenant_keeps_the_base_scoring_policy():
    base, acme = load_config(), load_config(tenant="acme_telecom")
    assert acme.checklist.violation_verdicts == base.checklist.violation_verdicts
    assert acme.checklist.verdict_points == base.checklist.verdict_points
    base_items = {i.id: i for i in base.checklist.items}
    assert all(i == base_items[i.id] for i in acme.checklist.items if i.id in base_items)
