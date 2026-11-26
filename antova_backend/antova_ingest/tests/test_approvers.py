"""Approver rules: who must approve a contract, and which role may accept each finding."""

from decimal import Decimal

import pytest

from antova_ingest.approvers import ApproverRules, Suggestion, load_rules, role_to_accept, suggest_approvers
from antova_ingest.costs import ConfigError

RULES = load_rules("default_v1")


def _finding(category="back_to_back", param_id="CT-01", amounts=(), currency="EUR"):
    money = [{"basis": "exposure", "kind": "k", "amount": a, "currency": currency if a is not None else None,
              "formula": "", "reason": ""} for a in amounts]
    return {"id": "F1", "vendor_id": "V1", "param_id": param_id, "category": category, "severity": "major",
            "message": "", "money": money, "gap": None, "citations": [], "evidence_hash": "x"}


def _suggest(findings=(), variance=None, ntp=None, negotiated=None, currency="EUR"):
    return suggest_approvers(RULES, findings=list(findings), budget_variance_pct=variance, signing_before_ntp=ntp,
                             procurement_negotiated=negotiated, budget_currency=currency)


def _roles(suggestions):
    return [s.role for s in suggestions]


def _reason(suggestions, role):
    return next(s.reason for s in suggestions if s.role == role)


def test_no_findings_no_flags_is_pm_engineering_finance():
    assert _roles(_suggest()) == ["project_management", "engineering", "finance"]


def test_procurement_when_negotiated():
    got = _suggest(negotiated=True)
    assert "procurement" in _roles(got)
    assert _reason(got, "procurement") == "Procurement negotiated this contract."
    assert "procurement" not in _roles(_suggest(negotiated=False))


def test_legal_when_a_legal_term_has_a_finding():
    got = _suggest([_finding(param_id="CT-14")])
    assert _reason(got, "legal") == "A finding is on a legal term (CT-14)."
    assert "legal" not in _roles(_suggest([_finding(param_id="CT-01")]))


def test_director_when_signing_before_ntp():
    got = _suggest(ntp=True)
    assert _reason(got, "business_director") == "The contract is to be signed before notice to proceed."


def test_director_when_over_budget():
    got = _suggest(variance=Decimal("2.5"))
    assert _reason(got, "business_director") == "The contract is 2.5% over the frozen budget."
    assert "business_director" not in _roles(_suggest(variance=Decimal("0")))
    assert "business_director" not in _roles(_suggest(variance=None))


def test_director_when_money_reaches_threshold():
    got = _suggest([_finding(amounts=["100000.00"])])
    assert _reason(got, "business_director") == "A finding carries EUR 100000.00, at or over EUR 100000."
    assert "business_director" not in _roles(_suggest([_finding(amounts=["99999.99", None])]))


def test_money_in_another_currency_does_not_meet_threshold():
    assert "business_director" not in _roles(_suggest([_finding(amounts=["250000.00"], currency="USD")]))


def test_unknown_budget_currency_does_not_meet_threshold():
    assert "business_director" not in _roles(_suggest([_finding(amounts=["250000.00"])], currency=None))


def test_ct14_back_to_back_finding_requires_legal():
    f = _finding(category="back_to_back", param_id="CT-14")
    assert role_to_accept(RULES, f) == "legal"
    assert "legal" in _roles(_suggest([f]))


@pytest.mark.parametrize("category,role", [("fa_conformance", "procurement"), ("back_to_back", "project_management"),
                                           ("budget", "finance"), ("missing", "procurement")])
def test_acceptance_role_by_category(category, role):
    assert role_to_accept(RULES, _finding(category=category, param_id="CT-01")) == role


def test_unknown_category_raises():
    with pytest.raises(ValueError):
        role_to_accept(RULES, _finding(category="nope"))


def test_bad_rule_name_and_missing_file():
    with pytest.raises(ValueError):
        load_rules("../etc")
    with pytest.raises(ConfigError):
        load_rules("no_such_rules")


def test_suggestion_shape():
    s = _suggest()[0]
    assert isinstance(s, Suggestion) and isinstance(RULES, ApproverRules)
