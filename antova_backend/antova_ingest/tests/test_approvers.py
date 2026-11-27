"""Approver rules: who must approve a contract, and which role may accept each finding."""

from datetime import date
from decimal import Decimal

import pytest

from antova_ingest.approvers import ApproverRules, Suggestion, load_rules, role_to_accept, suggest_approvers
from antova_ingest.costs import ConfigError
from antova_ingest.types import BaselineFx

GBP_EUR = BaselineFx(from_currency="GBP", to_currency="EUR", rate=Decimal("1.17"), dated=date(2026, 11, 1))

RULES = load_rules("default_v1")


def _finding(category="back_to_back", param_id="CT-01", amounts=(), currency="EUR"):
    money = [{"basis": "exposure", "kind": "k", "amount": a, "currency": currency if a is not None else None,
              "formula": "", "reason": ""} for a in amounts]
    return {"id": "F1", "vendor_id": "V1", "param_id": param_id, "category": category, "severity": "major",
            "message": "", "money": money, "gap": None, "citations": [], "evidence_hash": "x"}


def _suggest(findings=(), variance=None, ntp=None, negotiated=None, currency="EUR", fx=()):
    return suggest_approvers(RULES, findings=list(findings), budget_variance_pct=variance, signing_before_ntp=ntp,
                             procurement_negotiated=negotiated, budget_currency=currency, fx=fx)


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


def test_converted_amount_above_threshold():
    got = _suggest([_finding(amounts=["90000.00"], currency="GBP")], fx=[GBP_EUR])
    reason = _reason(got, "business_director")
    assert reason.startswith("A finding carries GBP 90000.00 (= GBP 90,000 × 1.17 (2026-11-01, GBP→EUR) = EUR 105,300.00)")
    assert reason.endswith(", at or over EUR 100000.")


def test_converted_amount_below_threshold():
    assert "business_director" not in _roles(_suggest([_finding(amounts=["80000.00"], currency="GBP")], fx=[GBP_EUR]))


def test_unconvertible_amount_adds_director():
    got = _suggest([_finding(amounts=["250.00"], currency="USD")], fx=[GBP_EUR])
    assert _reason(got, "business_director") == (
        "A finding carries USD 250.00 (no USD→EUR rate entered), which could not be compared with the "
        "EUR 100000 threshold.")


def test_no_budget_currency_adds_director_when_money_present():
    got = _suggest([_finding(amounts=["10.00"])], currency=None)
    assert _reason(got, "business_director") == (
        "A finding carries EUR 10.00 (no budget currency given), which could not be compared with the "
        "100000 threshold.")
    assert "business_director" not in _roles(_suggest([_finding(amounts=[None])], currency=None))
    assert "business_director" not in _roles(_suggest([], currency=None))


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


def test_a_back_to_back_liability_exposure_over_the_threshold_brings_in_the_director():
    # The shape contract_findings gives a CT-02 back-to-back liability gap: an exposure in the vendor's currency.
    finding = {**_finding(param_id="CT-02", amounts=["40625641.03"], currency="GBP"),
               "id": "V1|CT-02|owner|back_to_back_liability"}
    finding["money"][0]["kind"] = "back_to_back_liability"
    got = _suggest([finding], fx=[GBP_EUR])
    assert "business_director" in _roles(got) and "legal" in _roles(got)
    assert _reason(got, "business_director").startswith("A finding carries GBP 40625641.03")
