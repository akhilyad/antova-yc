"""Checking each contract term against the framework agreement and the owner contract, and the warranty and
payment-timing gaps (Task 6)."""

from datetime import date
from decimal import Decimal

import pytest

from antova_ingest.contract_checks import (back_to_back_ld, check_terms, fx_convert, owner_ld_money,
                                           payment_timing, warranty_gap)
from antova_ingest.contract_terms import ContractTerm, TermSource, contract_evidence, resolve_contract_terms
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import (BaselineFx, BaselineMoney, Citation, DocumentEntry, FieldValue, OwnerTerms,
                                 TenderManifest)
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


def test_warranty_months_with_one_start_unknown_are_not_compared():
    fa = _read("CT-03", "The warranty period is 60 months from provisional acceptance.", "fa.pdf")
    offer = _read("CT-03", "The warranty is 60 months.", "bid.pdf")
    assert "warranty_start" not in offer and fa["warranty_start"].value == "pac"
    check = _only(_checks(_term("CT-03", _source("bid.pdf", offer), _source("fa.pdf", fa))), "fa", "warranty_months")
    assert check.passed is None and "the offer does not state when its warranty starts" in check.reason
    mirror = _only(_checks(_term("CT-03", _source("bid.pdf", fa), _source("fa.pdf", offer))), "fa", "warranty_months")
    assert mirror.passed is None and "the framework agreement does not state when its warranty starts" in mirror.reason


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


def test_owner_weekly_rate_is_divided_exactly_and_rounded_only_at_the_end():
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("10000"), ld_rate_basis="money_per_week",
                       ld_cap=D("1000000"), ld_cap_basis="money", package_ld_share_pct=D("100"))
    assert owner_ld_money(None, owner).rate_per_day == D("10000") / 7  # not 1,428.57
    gap = back_to_back_ld(_term("CT-01", _vendor_ld(rate="0.1")), owner, (D("1000000"), "EUR"), D("1"))
    assert gap.amount == D("3000.00")  # 10,000 owed in the week − 7,000 paid back; 1,428.57 × 7 would give 2,999.99
    assert "EUR 10,000.00 per week ÷ 7 = EUR 1,428.57 per day" in gap.formula


def test_offer_with_no_lds_leaves_the_whole_owner_amount():
    offer = _read("CT-01", "Liquidated damages are excluded.", "bid.pdf")
    assert "ld_rate" not in offer and offer["ld_cap"].qualifier == "none"
    owner = OwnerTerms(contract_value=EUR_10M, ld_rate=D("10000"), ld_rate_basis="money_per_day",
                       ld_cap=D("1000000"), ld_cap_basis="money", package_ld_share_pct=D("100"))
    gap = back_to_back_ld(_term("CT-01", _source("bid.pdf", offer)), owner, None, D("4"))
    assert gap.amount == D("280000.00") and "no liquidated damages" in gap.formula
    # A rate that is only not stated stays unknown.
    unstated = _source("bid.pdf", {"ld_cap": FieldValue("percent", 10.0, "10 %", qualifier="contract")})
    assert back_to_back_ld(_term("CT-01", unstated), owner, (D("2000000"), "EUR"), D("4")).amount is None


OWNER_MONEY_LD = OwnerTerms(contract_value=EUR_10M, ld_rate=D("10000"), ld_rate_basis="money_per_day",
                            ld_cap=D("1000000"), ld_cap_basis="money", package_ld_share_pct=D("100"))


def _quoted(quote, file="bid.pdf"):
    """An offer whose one citation is the quote itself, with the values the parsers read from it."""
    return TermSource("document", _read("CT-01", quote, file),
                      (Citation(file, 1, (0.0, 0.0, 1.0, 1.0), quote, "commercial"),))


def _gap(quote):
    return back_to_back_ld(_term("CT-01", _quoted(quote)), OWNER_MONEY_LD, (D("2000000"), "EUR"), D("4"))


RATE = " LDs are 0.5% per day, capped at 10% of the order value."


def test_scoped_ld_exclusion_is_not_no_lds():
    gap = _gap('Liquidated damages are not applicable to delays caused by the Buyer. LDs apply at the framework agreement rate, capped at 10% of the order value.')
    assert gap.amount is None and gap.reason == "not known: the vendor's LD rate"
    gap = _gap('No liquidated damages shall apply to delays caused by force majeure.' + RATE)
    assert gap.amount == D("80000.00")  # 280,000 owed − min(0.5% × 28 days, 10% cap) × 2,000,000 = 200,000
    assert "no liquidated damages" not in gap.formula
    assert gap.formula.endswith("; assumes the delay is the vendor's "
                                "(the offer excludes LDs for 'delays caused by force majeure')")


def test_a_waiver_for_some_weeks_is_not_a_full_gap():
    # Ruling R29: the vendor pays only for days 15–28, which the values do not show; no number, not 80,000.
    sentence = 'Liquidated damages are waived during the first two weeks of delay.'
    gap = _gap(sentence + RATE)
    assert gap.amount is None
    assert gap.reason == f"the offer limits its liquidated damages ('{sentence}'); check the scope by hand"


# --- an LD exclusion with words before it (Task 6 review r4, ruling R29) -----------------------------------

HEAD_PROBES = [
    "During the first two weeks no LDs apply.",
    "In case of force majeure no liquidated damages shall apply.",
    "For Buyer-caused delays no LDs apply.",
    "Except for late commissioning no liquidated damages apply.",
    "Where the delay is caused by the Buyer no LDs apply.",
]


@pytest.mark.parametrize("sentence", HEAD_PROBES)
def test_an_exclusion_after_other_words_alone_is_not_known(sentence):
    # Before: 280,000.00 "the offer provides no liquidated damages", overriding any FA rate.
    gap = _gap(sentence)
    assert gap.amount is None and gap.reason == "not known: the vendor's LD rate, the vendor's LD cap"


@pytest.mark.parametrize("sentence, scope", [
    ("In case of force majeure no liquidated damages shall apply.", "'force majeure'"),
    ("For Buyer-caused delays no LDs apply.", "'Buyer-caused delays'"),
    ("Where the delay is caused by the Buyer no LDs apply.", "'Where the delay is caused by the Buyer'"),
    ("No LDs in force majeure situations.", "'force majeure situations'"),
    ("Liquidated damages are not applicable to delays attributable to the Purchaser.",
     "'delays attributable to the Purchaser'"),
])
def test_an_exclusion_for_delays_the_vendor_does_not_cause_keeps_the_gap(sentence, scope):
    gap = _gap(sentence + RATE)
    assert gap.amount == D("80000.00")
    assert f"; assumes the delay is the vendor's (the offer excludes LDs for {scope})" in gap.formula


def test_a_buyer_caused_head_keeps_the_gap_with_the_caveat():
    gap = _gap("Where the delay is caused by the Buyer no LDs apply. LDs are 0.5% per day, capped at 10% of the order value.")
    assert gap.amount == D("80000.00")
    assert gap.formula.endswith("; assumes the delay is the vendor's "
                                "(the offer excludes LDs for 'Where the delay is caused by the Buyer')")


@pytest.mark.parametrize("sentence", [
    "During the first two weeks no LDs apply.",
    "Except for late commissioning no liquidated damages apply.",
    "Liquidated damages are not applicable before the end of the grace period.",
    "Liquidated damages are not applicable to the spare parts.",
    "Liquidated damages are not applicable to the order's spare parts.",
    "No liquidated damages apply to the contract options.",
    "LDs are waived for force majeure and during the first week.",
])
def test_an_exclusion_with_any_other_scope_gives_no_number(sentence):
    gap = _gap(sentence + RATE)
    assert gap.amount is None
    assert gap.reason == f"the offer limits its liquidated damages ('{sentence}'); check the scope by hand"


def test_an_exclusion_from_this_offer_leaves_the_whole_owner_amount():
    gap = _gap("Liquidated damages are excluded from this offer.")
    assert gap.amount == D("280000.00") and "the offer provides no liquidated damages" in gap.formula
    assert "assumes" not in gap.formula


def test_ld_rate_in_conflict_never_takes_the_no_lds_path():
    offer = _source("bid.pdf", {"ld_cap": FieldValue("percent", 0.0, "0 %", qualifier="none")})
    disagree = {"code": "passages_disagree", "field": "ld_rate", "source": "offered",
                "message": "CT-01 LD rate: bid.pdf p.3 says 0.5% per day; bid.pdf p.9 says 1% per week."}
    gap = back_to_back_ld(_term("CT-01", offer, state="conflict", flags=[disagree]), OWNER_MONEY_LD,
                          (D("2000000"), "EUR"), D("4"))
    assert gap.amount is None and gap.reason == disagree["message"]


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
    # EUR 10,000 a day ÷ 1.17 × 28 days = GBP 239,316.2393…, less GBP 200,000 at the vendor's cap; rounded once,
    # at the end (rounding the daily rate first would give 39,316.28).
    assert gap.amount == D("39316.24")
    assert "1.17 (2026-11-01" in gap.formula


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


# --- an LD exclusion cited as its own passage (ruling R30) -------------------------------------------------

def _pipeline_ct01(*passages):
    """CT-01 for vendor-a, from verified-stage rows on bid-a.pdf, one per (page, quote), as the pipeline reads it."""
    bid = DocumentEntry(file="bid-a.pdf", role="vendor_bid", vendor_id="vendor-a")
    manifest = TenderManifest(tender_id="T-1", title="t", client="c", equipment_class="mv_gis_switchgear",
                              rate_table="mv_gis_switchgear_v2",
                              documents=[DocumentEntry(file="mr.pdf", role="mr_spec"), bid],
                              engineering_estimates={}, synthetic=True)
    rows = [{"param_id": "CT-01", "source": "locator",
             "citation": Citation("bid-a.pdf", page, (1.0, 2.0, 3.0, 4.0), quote, "commercial").to_dict()}
            for page, quote in passages]
    verified = {"locales": {"bid-a.pdf": "en"},
                "contract": {"citations": {"bid-a.pdf": rows}, "accepted": 0, "rejected": 0, "not_stated": {}}}
    found, _ = contract_evidence(verified, manifest, failed_vendors=())
    return resolve_contract_terms(manifest, found)[("vendor-a", "CT-01")]


def _pipeline_gap(*passages):
    return back_to_back_ld(_pipeline_ct01(*passages), OWNER_MONEY_LD, (D("2000000"), "EUR"), D("4"))


LD_RATE_CAP = "LDs are 0.5% per day, capped at 10% of the order value."


def test_a_waiver_in_its_own_passage_gives_no_number():
    gap = _pipeline_gap((9, LD_RATE_CAP), (12, "LDs are waived during the first two weeks."))
    assert gap.amount is None
    assert gap.reason == ("the offer limits its liquidated damages ('LDs are waived during the first two weeks.'); "
                          "check the scope by hand")
    assert _pipeline_gap((9, LD_RATE_CAP)).amount == D("80000.00")  # without it, the gap is computed


def test_a_force_majeure_exclusion_in_its_own_passage_keeps_the_gap_with_the_caveat():
    gap = _pipeline_gap((9, LD_RATE_CAP), (12, "No liquidated damages apply in case of force majeure."))
    assert gap.amount == D("80000.00")
    assert gap.formula.endswith("; assumes the delay is the vendor's (the offer excludes LDs for 'force majeure')")


def test_a_whole_offer_exclusion_in_its_own_passage_is_no_lds():
    # The 0 % cap of "no LDs" is not a plausible value, so the passage states none; it is kept for its scope.
    term = _pipeline_ct01((12, "No liquidated damages apply."))
    assert term.offered is None and len(term.ld_exclusion_passages) == 1
    gap = back_to_back_ld(term, OWNER_MONEY_LD, None, D("4"))
    assert gap.amount == D("280000.00") and "the offer provides no liquidated damages" in gap.formula


def test_a_whole_offer_exclusion_beside_a_stated_rate_is_for_a_person():
    gap = _pipeline_gap((9, LD_RATE_CAP), (12, "No liquidated damages apply."))
    assert gap.amount is None
    assert gap.reason == ("the offer excludes liquidated damages ('No liquidated damages apply.') but an LD rate "
                          "or cap is also stated for it; a person decides which governs")
