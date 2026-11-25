"""Checking each contract term against the framework agreement and the owner contract, and the warranty and
payment-timing gaps (Task 6)."""

from datetime import date
from decimal import Decimal

from antova_ingest.contract_checks import (back_to_back_ld, check_terms, fx_convert, owner_ld_money,
                                           payment_timing, warranty_gap)
from antova_ingest.contract_terms import ContractTerm, TermSource
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import BaselineFx, BaselineMoney, Citation, FieldValue, OwnerTerms
from antova_ingest.values import Milestone

D = Decimal
GBP_EUR = BaselineFx(from_currency="GBP", to_currency="EUR", rate=D("1.17"), dated=date(2026, 11, 1))


def _cite(file, page=1):
    return Citation(file, page, (0.0, 0.0, 1.0, 1.0), f"clause on page {page}", "commercial")


def _source(file, values, kind="document", inherited=()):
    return TermSource(kind, values, (_cite(file),), frozenset(inherited))


def _read(param_id, quote, file):
    """The values a quote states, as the parsers read them."""
    return evidence(param_id, "commercial", 1, quote, filename=file).values


def _term(param_id, offered=None, fa=None, owner=None, state="cited", flags=(), fa_present=None):
    return ContractTerm(param_id, "vendor-a", offered, fa, owner, state, tuple(flags),
                        fa_present=fa is not None if fa_present is None else fa_present)


def _checks(*terms, fx=()):
    return check_terms({(t.vendor_id, t.param_id): t for t in terms}, fx)


def _only(checks, baseline, field):
    found = [c for c in checks if (c.baseline, c.field) == (baseline, field)]
    assert len(found) == 1, found
    return found[0]


def _money(amount, currency):
    return FieldValue("money_cap", D(amount), f"{currency} {D(amount):,.0f}", qualifier=currency)


# --- FA conformance ---------------------------------------------------------------------------------

def test_ld_rate_week_vs_day_check():
    fa = _read("CT-01", "Liquidated damages for delay are 1% of the contract price per week.", "fa.pdf")
    offer = _read("CT-01", "Liquidated damages for delay are 0.15% of the contract price per day.", "bid.pdf")
    assert fa["ld_rate"].value == (D("1"), "week") and offer["ld_rate"].value == (D("0.15"), "day")
    check = _only(_checks(_term("CT-01", _source("bid.pdf", offer), _source("fa.pdf", fa))), "fa", "ld_rate")
    assert check.passed is True  # 0.15 × 7 = 1.05 ≥ 1, exact
    assert check.offered == "0.15% per day (1.05% per week)"
    assert check.required == "1% per week"
    assert check.required_citations == (_cite("fa.pdf"),) and check.offered_citations == (_cite("bid.pdf"),)


def test_liability_currency_needs_fx():
    term = _term("CT-02", _source("bid.pdf", {"liability_cap_money": _money("400000", "GBP")}),
                 _source("fa.pdf", {"liability_cap_money": _money("500000", "EUR")}))
    unconverted = _only(_checks(term), "fa", "liability_cap_money")
    assert unconverted.passed is None and unconverted.reason == "no GBP→EUR rate entered"

    converted = _only(_checks(term, fx=[GBP_EUR]), "fa", "liability_cap_money")
    assert converted.passed is False  # £400,000 × 1.17 = €468,000.00 < €500,000
    assert converted.offered == "GBP 400,000 × 1.17 (2026-11-01, GBP→EUR) = EUR 468,000.00"


def test_fx_uses_the_pair_either_way_and_never_assumes_a_rate():
    assert fx_convert(D("468000"), "EUR", "GBP", [GBP_EUR]).amount == D("400000")
    assert fx_convert(D("10"), "USD", "EUR", [GBP_EUR]).reason == "no USD→EUR rate entered"
    same = fx_convert(D("10"), "EUR", "EUR", [])
    assert same.amount == D("10") and same.formula == ""


def test_same_currency_money_is_compared_directly():
    term = _term("CT-02", _source("bid.pdf", {"liability_cap_money": _money("600000", "EUR")}),
                 _source("fa.pdf", {"liability_cap_money": _money("500000", "EUR")}))
    assert _only(_checks(term), "fa", "liability_cap_money").passed is True


def test_percent_cap_compared_in_decimal():
    fa = {"ld_cap": FieldValue("percent", 10.0, "10 %", qualifier="contract")}
    offer = {"ld_cap": FieldValue("percent", 9.9, "9.9 %", qualifier="contract")}
    check = _only(_checks(_term("CT-01", _source("bid.pdf", offer), _source("fa.pdf", fa))), "fa", "ld_cap")
    assert check.passed is False


def test_percent_of_a_different_base_is_not_compared():
    fa = {"ld_cap": FieldValue("percent", 10.0, "10 %", qualifier="contract")}
    offer = {"ld_cap": FieldValue("percent", 15.0, "15 %", qualifier="delayed_portion")}
    check = _only(_checks(_term("CT-01", _source("bid.pdf", offer), _source("fa.pdf", fa))), "fa", "ld_cap")
    assert check.passed is None and "delayed portion" in check.reason


def test_citation_only_field_is_never_compared():
    place = FieldValue("scope", "DAP Site, Rotterdam", "DAP Site, Rotterdam")
    term = _term("CT-12", _source("bid.pdf", {"named_place": place}), _source("fa.pdf", {"named_place": place}))
    check = _only(_checks(term), "fa", "named_place")
    assert check.passed is None and check.reason == "compare the quoted clauses by hand"


def test_offer_silent_is_not_a_pass():
    fa = _read("CT-03", "The warranty period is 60 months from delivery.", "fa.pdf")
    check = _only(_checks(_term("CT-03", None, _source("fa.pdf", fa), state="missing")), "fa", "warranty_months")
    assert check.passed is None and check.offered == "not stated"
    assert "does not state" in check.reason and check.offered_citations == ()


def test_offer_not_read_says_so():
    fa = _read("CT-03", "The warranty period is 60 months from delivery.", "fa.pdf")
    unread = {"code": "document_not_read", "message": "bid.pdf was not read, so the terms it states are unknown."}
    check = _only(_checks(_term("CT-03", None, _source("fa.pdf", fa), state="missing", flags=[unread])),
                  "fa", "warranty_months")
    assert check.passed is None and "bid.pdf was not read" in check.reason


def test_conflict_in_the_offer_is_not_guessed():
    fa = _read("CT-03", "The warranty period is 60 months from delivery.", "fa.pdf")
    disagree = {"code": "passages_disagree", "field": "warranty_months", "source": "offered",
                "message": "CT-03 Warranty months: bid.pdf p.1 says 24; bid.pdf p.2 says 36. Neither outranks the "
                           "other, so a person decides which governs."}
    offer = TermSource("document", {}, (_cite("bid.pdf", 1), _cite("bid.pdf", 2)))
    checks = _checks(_term("CT-03", offer, _source("fa.pdf", fa), state="conflict", flags=[disagree]))
    check = _only(checks, "fa", "warranty_months")
    assert check.passed is None and check.reason == disagree["message"]


def test_conflict_in_the_fa_is_a_check_without_a_requirement():
    disagree = {"code": "amendment_order_unknown", "field": "warranty_months", "source": "fa",
                "message": "CT-03 Warranty months: fa-amd.pdf amends fa.pdf but has no date, so it is not known "
                           "whether 60 or 66 governs."}
    offer = _read("CT-03", "The warranty period is 60 months from delivery.", "bid.pdf")
    fa = TermSource("document", {}, (_cite("fa.pdf"), _cite("fa-amd.pdf")))
    check = _only(_checks(_term("CT-03", _source("bid.pdf", offer), fa, state="conflict", flags=[disagree])),
                  "fa", "warranty_months")
    assert check.passed is None and check.reason == disagree["message"]
    # The owner side has no such conflict and no requirement: no owner check is made.
    assert not [c for c in _checks(_term("CT-03", _source("bid.pdf", offer), fa, state="conflict",
                                         flags=[disagree])) if c.baseline == "owner"]


def test_conflicts_are_matched_on_their_keys_not_their_wording():
    """Ruling R27: rewording a flag's message must not change which check it lands on."""
    fa = _read("CT-03", "The warranty period is 60 months from delivery.", "fa.pdf")
    offer = TermSource("document", {}, (_cite("bid.pdf", 1), _cite("bid.pdf", 2)))
    reworded = {"code": "passages_disagree", "field": "warranty_months", "source": "offered",
                "message": "Two pages of the offer give different warranty periods."}
    check = _only(_checks(_term("CT-03", offer, _source("fa.pdf", fa), state="conflict", flags=[reworded])),
                  "fa", "warranty_months")
    assert check.reason == reworded["message"]

    # A message worded exactly like a conflict on this field, but keyed to another field or source, is not one.
    for field, source in (("warranty_start", "offered"), ("warranty_months", "owner")):
        decoy = {"code": "passages_disagree", "field": field, "source": source,
                 "message": "CT-03 Warranty months: bid.pdf p.1 says 24; bid.pdf p.2 says 36."}
        check = _only(_checks(_term("CT-03", offer, _source("fa.pdf", fa), state="conflict", flags=[decoy])),
                      "fa", "warranty_months")
        assert check.reason == "the offer does not state the warranty months"


def test_fa_silent_or_absent_produces_no_fa_check():
    offer = _read("CT-03", "The warranty period is 60 months from delivery.", "bid.pdf")
    assert _checks(_term("CT-03", _source("bid.pdf", offer), None, fa_present=True)) == []
    assert _checks(_term("CT-03", _source("bid.pdf", offer), None, fa_present=False)) == []


def test_inherited_field_is_compared_and_marked():
    fa = _read("CT-03", "The warranty period is 60 months from delivery.", "fa.pdf")
    offered = TermSource("inherited", dict(fa), (_cite("bid.pdf"), _cite("fa.pdf")), frozenset(fa))
    owner = TermSource("buyer_entered", {"warranty_months": FieldValue("months", 66, "66", qualifier="delivery"),
                                         "warranty_start": FieldValue("event", "delivery", "delivery")}, ())
    checks = _checks(_term("CT-03", offered, _source("fa.pdf", fa), owner, state="inherited"))
    check = _only(checks, "owner", "warranty_months")
    assert check.passed is False and "inherited" in check.offered
    assert check.offered_citations == (_cite("bid.pdf"), _cite("fa.pdf"))


def test_warranty_months_from_different_starts_are_not_compared():
    fa = _read("CT-03", "The warranty period is 24 months from provisional acceptance.", "fa.pdf")
    offer = _read("CT-03", "The warranty period is 36 months from delivery.", "bid.pdf")
    checks = _checks(_term("CT-03", _source("bid.pdf", offer), _source("fa.pdf", fa)))
    assert _only(checks, "fa", "warranty_months").passed is None
    assert _only(checks, "fa", "warranty_start").passed is False  # delivery is earlier than PAC


def test_owner_percentages_are_not_compared_with_the_offers():
    owner = TermSource("buyer_entered", {"ld_rate": FieldValue("rate_per_period", (D("0.1"), "day"),
                                                               "0.1% per day")}, ())
    offer = {"ld_rate": FieldValue("rate_per_period", (D("0.5"), "day"), "0.5% per day")}
    check = _only(_checks(_term("CT-01", _source("bid.pdf", offer), owner=owner)), "owner", "ld_rate")
    assert check.passed is None and "in money" in check.reason


def test_contract_structure_terms_are_not_checked():
    ref = FieldValue("scope", "the Framework Agreement", "the Framework Agreement")
    assert _checks(_term("CT-16", _source("bid.pdf", {"fa_reference": ref}),
                         _source("fa.pdf", {"fa_reference": ref}))) == []


# --- warranty gap -----------------------------------------------------------------------------------

def _owner_warranty(months, start):
    return TermSource("buyer_entered", {"warranty_months": FieldValue("months", months, str(months)),
                                        "warranty_start": FieldValue("event", start, start)}, ())


def test_warranty_gap_same_start():
    offer = _read("CT-03", "The warranty period is 60 months from delivery.", "bid.pdf")
    gap = warranty_gap(_term("CT-03", _source("bid.pdf", offer), owner=_owner_warranty(66, "delivery")))
    assert (gap.kind, gap.value, gap.unit) == ("warranty", D("6"), "months")
    assert "66" in gap.formula and "60" in gap.formula and gap.reason == ""


def test_warranty_surplus_is_zero():
    offer = _read("CT-03", "The warranty period is 72 months from delivery.", "bid.pdf")
    gap = warranty_gap(_term("CT-03", _source("bid.pdf", offer), owner=_owner_warranty(66, "delivery")))
    assert gap.value == D("0") and "surplus" in gap.formula


def test_warranty_gap_different_start_is_none():
    offer = _read("CT-03", "The warranty period is 60 months from delivery.", "bid.pdf")
    gap = warranty_gap(_term("CT-03", _source("bid.pdf", offer), owner=_owner_warranty(24, "pac")))
    assert gap.value is None
    assert gap.reason == ("the vendor's warranty starts at delivery and the owner warranty at PAC; "
                          "there are no dates to bridge them")


def test_warranty_gap_without_an_owner_warranty_is_none():
    offer = _read("CT-03", "The warranty period is 60 months from delivery.", "bid.pdf")
    gap = warranty_gap(_term("CT-03", _source("bid.pdf", offer)))
    assert gap.value is None and "owner" in gap.reason


# --- payment timing ---------------------------------------------------------------------------------

def _milestones(*items):
    ms = tuple(Milestone(D(p), event, days, None) for p, event, days in items)
    return {"milestones": FieldValue("milestone_list", ms, "; ".join(m.display for m in ms))}


def test_payment_timing():
    fa = _milestones(("100", "delivery", 30))
    offer = _milestones(("85", "delivery", 30), ("15", "delivery", 240))
    term = _term("CT-07", _source("bid.pdf", offer), _source("fa.pdf", fa))
    gap = payment_timing(term)
    assert (gap.kind, gap.value, gap.unit) == ("payment_timing", D("31.5"), "days")
    assert "61.5" in gap.formula and "30" in gap.formula
    check = _only(_checks(term), "fa", "milestones")
    assert check.passed is True  # the offer pays later


def test_payment_earlier_than_the_fa_fails():
    fa = _milestones(("100", "delivery", 60))
    offer = _milestones(("100", "delivery", 30))
    term = _term("CT-07", _source("bid.pdf", offer), _source("fa.pdf", fa))
    assert payment_timing(term).value == D("-30")
    assert _only(_checks(term), "fa", "milestones").passed is False


def test_payment_at_the_event_counts_zero_days():
    fa = _milestones(("100", "delivery", 30))
    offer = _milestones(("90", "delivery", 30), ("10", "delivery", None))
    gap = payment_timing(_term("CT-07", _source("bid.pdf", offer), _source("fa.pdf", fa)))
    assert gap.value == D("-3") and "at delivery" in gap.formula


def test_payment_milestones_on_different_events_are_none():
    fa = _milestones(("100", "delivery", 30))
    offer = _milestones(("90", "delivery", 30), ("10", "pac", 30))
    term = _term("CT-07", _source("bid.pdf", offer), _source("fa.pdf", fa))
    gap = payment_timing(term)
    assert gap.value is None and "no dates to bridge them" in gap.reason
    check = _only(_checks(term), "fa", "milestones")
    assert check.passed is None and check.reason == gap.reason


def test_payment_timing_without_an_fa_is_none():
    offer = _milestones(("100", "delivery", 30))
    gap = payment_timing(_term("CT-07", _source("bid.pdf", offer), fa_present=False))
    assert gap.value is None and "framework agreement" in gap.reason


# --- back-to-back LDs from the owner terms ------------------------------------------------------------

EUR_10M = BaselineMoney(amount=D("10000000"), currency="EUR")


def _vendor_ld(rate="0.5", period="day", cap=10.0, basis="contract"):
    return _source("bid.pdf", {"ld_rate": FieldValue("rate_per_period", (D(rate), period), f"{rate}% per {period}"),
                               "ld_cap": FieldValue("percent", cap, f"{cap:g} %", qualifier=basis)})


def test_owner_ld_in_percent_is_converted_with_the_contract_value():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("0.7"), ld_rate_basis="percent_per_week",
                       ld_cap=D("10"), ld_cap_basis="percent", package_ld_share_pct=D("100"))
    money = owner_ld_money(None, owner)
    assert money.rate_per_day == D("10000")  # 0.7% × 10,000,000 ÷ 7, Decimal division
    assert money.cap == D("1000000") and money.currency == "EUR"
    gap = back_to_back_ld(_term("CT-01", _vendor_ld()), owner, (D("2000000"), "EUR"), D("4"))
    assert gap.amount == D("80000.00")
    assert "0.7% per week × EUR 10,000,000.00" in gap.formula and "4 weeks × 7 = 28 days" in gap.formula


def test_owner_ld_in_money_needs_no_conversion():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("10000"), ld_rate_basis="money_per_day",
                       ld_cap=D("1000000"), ld_cap_basis="money", package_ld_share_pct=D("100"))
    gap = back_to_back_ld(_term("CT-01", _vendor_ld()), owner, (D("2000000"), "EUR"), D("2"))
    assert gap.amount == D("0.00")


def test_owner_ld_in_percent_without_contract_value_is_none():
    owner = OwnerTerms(ld_rate=D("0.1"), ld_rate_basis="percent_per_day", ld_cap=D("10"), ld_cap_basis="percent",
                       package_ld_share_pct=D("100"))
    assert owner_ld_money(None, owner).rate_per_day is None
    gap = back_to_back_ld(_term("CT-01", _vendor_ld()), owner, (D("2000000"), "EUR"), D("4"))
    assert gap.amount is None and "contract value" in gap.reason


def test_back_to_back_ld_in_another_currency_needs_fx():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("0.1"), ld_rate_basis="percent_per_day",
                       ld_cap=D("10"), ld_cap_basis="percent", package_ld_share_pct=D("100"))
    term = _term("CT-01", _vendor_ld())
    assert back_to_back_ld(term, owner, (D("2000000"), "GBP"), D("4")).reason == "no EUR→GBP rate entered"
    gap = back_to_back_ld(term, owner, (D("2000000"), "GBP"), D("4"), fx=[GBP_EUR])
    assert gap.amount is not None and "1.17 (2026-11-01" in gap.formula


def test_vendor_cap_on_the_delayed_portion_is_none():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("0.1"), ld_rate_basis="percent_per_day",
                       ld_cap=D("10"), ld_cap_basis="percent", package_ld_share_pct=D("100"))
    gap = back_to_back_ld(_term("CT-01", _vendor_ld(basis="delayed_portion")), owner, (D("2000000"), "EUR"), D("4"))
    assert gap.amount is None and "delayed portion" in gap.reason


def test_owner_document_ld_is_converted_with_the_contract_value():
    owner_doc = _source("owner.pdf", _read("CT-01", "Liquidated damages for delay are 0.1% of the contract price "
                                                    "per day, capped at 10% of the contract price.", "owner.pdf"))
    owner = OwnerTerms(contract_value=EUR_10M, package_ld_share_pct=D("100"))
    money = owner_ld_money(owner_doc, owner)
    assert (money.rate_per_day, money.cap) == (D("10000"), D("1000000"))
    gap = back_to_back_ld(_term("CT-01", _vendor_ld(), owner=owner_doc), owner, (D("2000000"), "EUR"), D("4"))
    assert gap.amount == D("80000.00")


def test_back_to_back_ld_without_a_delay_scenario_is_none():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("0.1"), ld_rate_basis="percent_per_day",
                       ld_cap=D("10"), ld_cap_basis="percent", package_ld_share_pct=D("100"))
    gap = back_to_back_ld(_term("CT-01", _vendor_ld()), owner, (D("2000000"), "EUR"), None)
    assert gap.amount is None and "delay" in gap.reason
