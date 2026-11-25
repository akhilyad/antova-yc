"""Contract field kinds: rates per period, project events, payment milestones, law and forum."""

from decimal import Decimal

from antova_ingest.ontology import ORDINALS, FieldDef
from antova_ingest.types import FieldValue
import pytest

from antova_ingest.values import Milestone, check, no_lds, parse_field, per_week, value_in_span


def fv(kind, value):
    return FieldValue(kind, value, str(value))


def test_rate_per_day():
    v = parse_field("rate_per_period", "0.5 % of the Price for each Day of delay", "ld_rate")
    assert v.value == (Decimal("0.5"), "day") and v.display == "0.5% per day"


def test_rate_per_week():
    v = parse_field("rate_per_period", "liquidated damages of 1% per week of delay", "ld_rate")
    assert v.value == (Decimal("1"), "week")


def test_per_week_is_exact():
    assert per_week((Decimal("0.15"), "day")) == Decimal("1.05")


def test_event_start_points():
    assert parse_field("event", "warranty starts on the delivery date", "start").value == "delivery"
    assert parse_field("event", "from Provisional Acceptance (PAC)", "start").value == "pac"


def test_milestones_parse_and_sum():
    v = parse_field("milestone_list",
        "85% within 30 days after delivery; 15% within 240 days after delivery against a warranty bond", "payment")
    assert [m.percent for m in v.value] == [Decimal("85"), Decimal("15")]
    assert v.value[1].days_after == 240 and v.value[1].condition == "warranty bond"


def test_law_and_forum():
    assert parse_field("law", "governed by the laws of Germany", "law").value == "german"
    assert parse_field("forum", "German Arbitration Institute (DIS)", "forum").value == "dis"


def test_event_check_later_start_is_better():
    f = FieldDef("start", "event", "ord", "Warranty start")
    assert check(f, fv("event", "delivery"), fv("event", "pac"))[0] is True
    assert check(f, fv("event", "pac"), fv("event", "delivery"))[0] is False


def test_unparseable_rate_is_none():
    assert parse_field("rate_per_period", "liquidated damages as agreed", "ld_rate") is None


# --- Beyond the brief's minimum: the edges the parsers must not guess at. ---------------------------

def test_rate_variants_and_a_monthly_rate_is_not_read():
    assert parse_field("rate_per_period", "LDs: 0.2%/day of the delayed portion", "r").value == (Decimal("0.2"), "day")
    assert parse_field("rate_per_period", "1.5 per cent weekly", "r").value == (Decimal("1.5"), "week")
    assert parse_field("rate_per_period", "1% per complete week of delay", "r").display == "1% per week"
    # A month is not a whole number of weeks: no exact conversion, so it is not read.
    assert parse_field("rate_per_period", "2% per month of delay", "r") is None


def test_per_week_of_a_weekly_rate_is_itself():
    assert per_week((Decimal("1"), "week")) == Decimal("1")


def test_rate_check_compares_per_week_and_shows_the_conversion():
    f = FieldDef("ld_rate", "rate_per_period", "ge", "LD rate")
    weekly = parse_field("rate_per_period", "1% per week", "ld_rate")
    daily = parse_field("rate_per_period", "0.15% per day", "ld_rate")
    assert check(f, weekly, daily) == (True, "0.15% per day (1.05% per week)")
    low = parse_field("rate_per_period", "0.1% per day", "ld_rate")
    assert check(f, weekly, low) == (False, "0.1% per day (0.7% per week)")


def test_rate_value_in_span():
    quote = "0.5 % of the Price for each Day of delay"
    value = parse_field("rate_per_period", quote, "ld_rate")
    assert value_in_span(value, quote)
    assert not value_in_span(value, quote.replace("0.5", "0.6"))


def test_event_order_is_earliest_first():
    assert ORDINALS["event"] == ("notice_to_proceed", "delivery", "cold_commissioning", "hot_commissioning",
                                 "commissioning", "cod", "pac", "fac")


def test_event_variants():
    assert parse_field("event", "following Cold Commissioning", "e").value == "cold_commissioning"
    assert parse_field("event", "from the Commercial Operation Date", "e").value == "cod"
    assert parse_field("event", "upon issue of the Final Acceptance Certificate", "e").value == "fac"
    assert parse_field("event", "within 3 months of Notice to Proceed", "e").value == "notice_to_proceed"


def test_event_start_word_settles_two_events_and_otherwise_none():
    assert parse_field("event", "24 months from PAC, for equipment delivered to site", "e").value == "pac"
    assert parse_field("event", "from delivery or commissioning, whichever is earlier", "e") is None
    assert parse_field("event", "the warranty is as agreed", "e") is None


def test_milestone_list_shapes():
    v = parse_field("milestone_list", "10% at Notice to Proceed, 80% on delivery and 10% at PAC against invoice", "p")
    assert v.value == (Milestone(Decimal("10"), "notice_to_proceed", None, None),
                       Milestone(Decimal("80"), "delivery", None, None),
                       Milestone(Decimal("10"), "pac", None, "invoice"))
    assert v.display == "10% at notice to proceed; 80% at delivery; 10% at PAC against invoice"
    # A payment tied to an event that is not a project event ("order") is not read.
    assert parse_field("milestone_list", "30% at order, 70% on delivery", "p") is None


def test_milestone_list_that_does_not_add_up_or_has_no_event_is_none():
    assert parse_field("milestone_list", "85% within 30 days after delivery", "p") is None
    assert parse_field("milestone_list", "50% in advance; 50% later", "p") is None
    assert parse_field("milestone_list", "100% within 2 months after delivery", "p") is None
    assert parse_field("milestone_list", "100% within 30 working days after delivery", "p") is None


def test_milestone_value_in_span():
    quote = "85% within 30 days after delivery; 15% within 240 days after delivery against a warranty bond"
    value = parse_field("milestone_list", quote, "payment")
    assert value_in_span(value, quote)
    assert not value_in_span(value, quote.replace("240", "180"))


def test_law_variants_and_two_laws_is_none():
    assert parse_field("law", "This Contract is governed by English law.", "law").value == "english"
    assert parse_field("law", "the laws of England and Wales shall apply", "law").value == "english"
    assert parse_field("law", "governed by the laws of the State of New York", "law").value == "new_york"
    assert parse_field("law", "Swiss law applies, excluding the CISG.", "law").value == "swiss"
    assert parse_field("law", "Delivery to Germany.", "law") is None
    assert parse_field("law", "governed by German law or English law as the Buyer elects", "law") is None


def test_forum_variants():
    assert parse_field("forum", "finally settled under the Rules of Arbitration of the ICC", "f").value == "icc"
    assert parse_field("forum", "London Court of International Arbitration", "f").value == "lcia"
    assert parse_field("forum", "the exclusive jurisdiction of the courts of Munich", "f").value == "courts:munich"
    assert parse_field("forum", "disputes go to the courts of Frankfurt am Main", "f").value == "courts:frankfurt_am_main"
    assert parse_field("forum", "Delivery DAP per ICC Incoterms 2020", "f") is None
    assert parse_field("forum", "ICC arbitration; or the courts of Munich", "f") is None


def test_law_and_forum_check_is_equality():
    law = FieldDef("law", "law", "eq", "Law")
    assert check(law, fv("law", "german"), fv("law", "german"))[0] is True
    assert check(law, fv("law", "german"), fv("law", "english"))[0] is False
    forum = FieldDef("forum", "forum", "eq", "Forum")
    assert check(forum, fv("forum", "dis"), fv("forum", "icc"))[0] is False


def test_the_sweep_signals_see_what_the_contract_parsers_read():
    from antova_ingest import sweep
    assert sweep.SIGNALS["rate_per_period"].search("0.5 % of the Price for each Day of delay")
    assert sweep.SIGNALS["event"].search("from Provisional Acceptance (PAC)")
    assert sweep.SIGNALS["milestone_list"].search("85% within 30 days after delivery")
    assert sweep.SIGNALS["law"].search("governed by the laws of Germany")
    assert sweep.SIGNALS["forum"].search("German Arbitration Institute (DIS)")



# --- Review fixes (a025ebe): a wrong value is worse than None. ------------------------------------

def test_a_decimal_comma_rate_is_not_read_ten_times_too_high():
    quote = "LDs of 0,5 % per day of delay"
    assert parse_field("rate_per_period", quote, "ld_rate") is None
    wrong = FieldValue("rate_per_period", (Decimal("5"), "day"), "5% per day")
    assert not value_in_span(wrong, quote)
    assert parse_field("milestone_list", "87,5% within 30 days after delivery; 12,5% at PAC", "p") is None


def test_milestone_days_must_count_forward_from_the_event_itself():
    assert parse_field("milestone_list",
                       "20% 30 days before delivery; 80% within 30 days after delivery", "p") is None
    assert parse_field("milestone_list", "20% prior to delivery; 80% at PAC", "p") is None
    assert parse_field("milestone_list", "10% in advance of delivery; 90% at PAC", "p") is None
    assert parse_field("milestone_list",
                       "90% within 30 days of receipt of invoice following delivery; 10% at PAC", "p") is None
    v = parse_field("milestone_list", "90% within 30 days from the date of delivery; 10% at PAC", "p")
    assert v.value[0] == Milestone(Decimal("90"), "delivery", 30, None)


def test_a_court_title_is_not_a_city():
    assert parse_field("forum", "the courts of the Federal Republic of Germany shall have jurisdiction", "f") is None
    assert parse_field("forum", "disputes go to the ordinary courts of the Canton of Zurich", "f") is None
    assert parse_field("forum", "the courts of the State of New York", "f") is None


# --- Task 2 (R12, R13): plausible LD rates, decimal-comma percentages, figures in brackets. --------

def test_a_rate_per_period_is_plausible_on_its_weekly_equivalent():
    from antova_ingest.values import plausible, plausible_bounds
    rate = FieldDef("ld_rate", "rate_per_period", "ge", "LD rate", plausible=("0.01% per day", "5% per day"))
    low, high = plausible_bounds(rate)
    assert low.value == (Decimal("0.01"), "day") and high.value == (Decimal("5"), "day")
    assert plausible(rate, parse_field("rate_per_period", "LDs of 0.5% per day of delay", "ld_rate"))
    assert plausible(rate, parse_field("rate_per_period", "LDs of 1% per week of delay", "ld_rate"))
    assert plausible(rate, parse_field("rate_per_period", "LDs of 5% per day of delay", "ld_rate"))  # the bound
    assert not plausible(rate, parse_field("rate_per_period", "LDs of 0.001% per day of delay", "ld_rate"))
    assert not plausible(rate, parse_field("rate_per_period", "LDs of 40% per week of delay", "ld_rate"))


def test_a_decimal_comma_percentage_is_not_read():
    assert parse_field("percent", "liquidated damages capped at 7,5 % of the Contract Price", "ld_cap") is None
    assert parse_field("percent", "LDs of 0,5 % per week of delay", "ld_rate") is None
    assert parse_field("percent", "total liability limited to 12,5% of the contract price", "liability_cap") is None
    # A grouped number is not a percentage either: "1,500 %" is not 500 %.
    assert parse_field("percent", "liability limited to 1,500 % of the order value", "liability_cap") is None
    # A comma followed by a space is a clause break, not a decimal comma.
    v = parse_field("percent", "LDs of 0.5% per week, capped at 10 % of the contract price", "ld_cap")
    assert v.value == 10.0
    # Unrelated decimal commas elsewhere in the quote do not block the read.
    v = parse_field("percent", "LDs of 0,5% per week apply, total liability is limited to 100% of the order value",
                    "liability_cap")
    assert v.value == 100.0


def test_milestone_days_written_in_words_and_figures():
    v = parse_field("milestone_list", "90% within thirty (30) days after delivery; 10% at PAC", "p")
    assert v.value == (Milestone(Decimal("90"), "delivery", 30, None), Milestone(Decimal("10"), "pac", None, None))
    assert value_in_span(v, "90% within thirty (30) days after delivery; 10% at PAC")
    v = parse_field("milestone_list", "90% within sixty (60) calendar days from the date of delivery; 10% at PAC", "p")
    assert v.value[0].days_after == 60
    # The anchor and direction checks still apply to the bracketed figure.
    assert parse_field("milestone_list", "90% thirty (30) days before delivery; 10% at PAC", "p") is None
    assert parse_field("milestone_list",
                       "90% within thirty (30) days of receipt of invoice; 10% at PAC", "p") is None
    # Days in words only: a period this reader cannot count is not read as "at delivery".
    assert parse_field("milestone_list", "90% within thirty days after delivery; 10% at PAC", "p") is None


def test_an_earlier_event_passes_an_at_most_check():
    title = FieldDef("title_event", "event", "le", "Title passes")
    assert check(title, fv("event", "delivery"), fv("event", "notice_to_proceed"))[0]
    assert check(title, fv("event", "delivery"), fv("event", "delivery"))[0]
    assert not check(title, fv("event", "delivery"), fv("event", "pac"))[0]
    assert not check(title, fv("event", "delivery"), fv("event", "not_an_event"))[0]


# --- R14: every contract-term field reads from its usual wording. -----------------------------------

def test_bond_percentages_belong_to_the_bond_named_before_them():
    assert parse_field("percent", "a warranty bond of 5% of the Contract Price", "warranty_bond_pct").value == 5.0
    assert parse_field("percent", "performance bond equal to ten percent (10%) of the Price",
                       "performance_bond_pct").value == 10.0
    assert parse_field("percent", "advance payment bond of 10%", "advance_bond_pct").value == 10.0
    both = "an advance payment bond of 10% and a performance bond of 15% of the Contract Price"
    assert parse_field("percent", both, "advance_bond_pct").value == 10.0
    assert parse_field("percent", both, "performance_bond_pct").value == 15.0
    assert parse_field("percent", both, "warranty_bond_pct") is None
    assert parse_field("percent", "a warranty bond of 2,5% of the Contract Price", "warranty_bond_pct") is None


def test_termination_fee_percentage():
    quote = "a termination fee of 5% of the price of the affected Goods"
    assert parse_field("percent", quote, "termination_fee_pct").value == 5.0
    assert parse_field("percent", "a termination fee of 7,5% of the price", "termination_fee_pct") is None
    assert parse_field("percent", "a warranty bond of 5% of the Contract Price", "termination_fee_pct") is None


def test_ntp_deadline_in_months_and_in_days():
    v = parse_field("months", "The NTP shall be issued within 8 months from the Project Purchase Order.",
                    "ntp_deadline_months")
    assert v.value == 8 and v.qualifier == "months"
    # Without the issuing of the NTP, a period from the order is any period (review round 1: a delivery
    # period reads the same), so the bare fragment is not read.
    assert parse_field("months", "within 8 months from the Project Purchase Order", "ntp_deadline_months") is None
    quote = "The Notice to Proceed shall be issued no later than 60 days from PO."
    v = parse_field("months", quote, "ntp_deadline_months")
    assert v.value == Decimal(60) * 12 / 365 and v.qualifier == "days"
    assert v.display == "60 days (1.97 months at 365/12 days per month)"
    assert value_in_span(v, quote)
    # A warranty period is not an NTP deadline.
    assert parse_field("months", "warranty of 24 months from delivery", "ntp_deadline_months") is None


def test_design_life_in_years_is_held_in_months():
    v = parse_field("months", "minimum operating period of 35 years", "design_life_months")
    assert v.value == 420 and v.display == "35 years (420 months)" and v.qualifier == "years"
    assert value_in_span(v, "minimum operating period of 35 years")
    assert not value_in_span(v, "minimum operating period of 30 years")
    assert parse_field("months", "warranty of 35 years", "design_life_months") is None
    # The warranty reader is unchanged.
    assert parse_field("months", "warranty of 24 months from delivery", "warranty_months").value == 24


def test_ld_triggers_are_the_delayed_events():
    v = parse_field("scope", "Liquidated damages apply for late delivery and/or any late Cold Commissioning "
                             "and/or Hot Commissioning.", "ld_triggers")
    assert v.value == ("delivery", "cold_commissioning", "hot_commissioning")
    v = parse_field("scope", "LDs of 0.5% per day of delay in delivery, capped at 10% of the price payable at PAC.",
                    "ld_triggers")
    assert v.value == ("delivery",)
    assert parse_field("scope", "No liquidated damages apply for late delivery.", "ld_triggers") is None
    triggers = FieldDef("ld_triggers", "scope", "ge", "LD triggers")
    required = fv("scope", ("delivery", "cold_commissioning"))
    assert check(triggers, required, fv("scope", ("delivery", "cold_commissioning", "hot_commissioning")))[0]
    assert not check(triggers, required, fv("scope", ("delivery",)))[0]


def test_fa_reference_names_the_incorporated_agreement():
    v = parse_field("scope", "This Purchase Order is subject to the terms of the Framework Agreement dated "
                             "10 January 2026.", "fa_reference")
    assert v.value == "framework agreement dated 10 january 2026"
    v = parse_field("scope", "The Framework Agreement No. FA-2025-017 forms an integral part of this offer.",
                    "fa_reference")
    assert v.display == "Framework Agreement No. FA-2025-017"
    assert parse_field("scope", "We have no framework agreement with the group yet", "fa_reference") is None


def test_order_of_precedence_is_the_ranked_document_list():
    v = parse_field("scope", "In case of conflict, the documents shall prevail in the following order: (1) the "
                             "Purchase Order; (2) the Framework Agreement; (3) the Technical Specification; "
                             "(4) the Offer.", "order")
    assert v.value == ("purchase_order", "framework_agreement", "technical_specification", "offer")
    assert v.display == "Purchase Order > Framework Agreement > Technical Specification > Offer"
    v = parse_field("scope", "Order of precedence: 1. Special Conditions, 2. General Conditions of Purchase, "
                             "3. Material Requisition and 4. the Vendor Offer.", "order")
    assert v.value == ("special_conditions", "general_conditions", "technical_specification", "offer")
    # Prose rankings are left to the reader.
    assert parse_field("scope", "The Purchase Order prevails over the Offer.", "order") is None
    order = FieldDef("order", "scope", "eq", "Order")
    assert not check(order, v, fv("scope", tuple(reversed(v.value))))[0]


def test_warranty_coverage_and_place_of_repair():
    assert parse_field("scope", "The warranty covers all components of the supply.", "warranty_coverage").value == 1
    assert parse_field("scope", "The warranty covers only the components listed in Annex 3.",
                       "warranty_coverage").value == 0
    assert parse_field("scope", "Defective parts shall be repaired or replaced on site at the Seller's cost.",
                       "warranty_on_site").value == 1
    assert parse_field("scope", "Defective parts shall be returned to the factory for repair.",
                       "warranty_on_site").value == 0


def test_citation_only_fields_are_the_quoted_clause():
    # Ruling R18: liability exclusions, named place and escalation formula are recorded as quoted.
    quote = "The limitation of liability shall not apply in case of gross negligence,\n  wilful misconduct or fraud."
    v = parse_field("scope", quote, "liability_exclusions")
    clause = "The limitation of liability shall not apply in case of gross negligence, wilful misconduct or fraud."
    assert v.value == v.display == clause
    assert parse_field("scope", "Total liability is limited to 100% of the price.", "liability_exclusions") is None
    v = parse_field("scope", "Delivery DAP Site Rotterdam (Incoterms 2020).", "named_place")
    assert v.value == v.display == "Delivery DAP Site Rotterdam (Incoterms 2020)."
    v = parse_field("scope", "Price adjustment formula: P = P0 x (0.15 + 0.85 x Cu/Cu0).", "escalation_formula")
    assert v.value == v.display == "Price adjustment formula: P = P0 x (0.15 + 0.85 x Cu/Cu0)."


def test_price_basis_and_credit_insurance():
    assert parse_field("scope", "The Contract Price is in EUR.", "price_currency").value == "EUR"
    assert parse_field("scope", "All prices are quoted in pounds sterling.", "price_currency").value == "GBP"
    assert parse_field("scope", "Prices in EUR; spares in GBP.", "price_currency") is None
    assert parse_field("scope", "The price is fixed and firm from the issue of the Notice to Proceed.",
                       "price_fixed_after_ntp").value == 1
    assert parse_field("scope", "The price is fixed until NTP; thereafter it is subject to escalation.",
                       "price_fixed_after_ntp").value == 0
    assert parse_field("scope", "The Contract Price is a fixed lump sum.", "price_type").value == "fixed_lump_sum"
    assert parse_field("scope", "The price is partly variable: the steel content is indexed.",
                       "price_type").value == "partly_variable"
    assert parse_field("scope", "The Supplier holds credit insurance with Northgate Credit Assurance plc.",
                       "credit_insurer").display == "Northgate Credit Assurance plc"
    v = parse_field("money_cap", "secured value of EUR 2,000,000 under the credit insurance policy",
                    "credit_secured_value")
    assert v.value == Decimal("2000000") and v.qualifier == "EUR"



# --- an LD exclusion scoped to some delays is not "no LDs" (Task 6 review r2) ----------------------------

SCOPED_BUYER = 'Liquidated damages are not applicable to delays caused by the Buyer. LDs apply at the framework agreement rate, capped at 10% of the order value.'
SCOPED_FM = 'No liquidated damages shall apply to delays caused by force majeure. LDs are 0.5% per day, capped at 10% of the order value.'


def test_ld_exclusion_scoped_to_buyer_delay_keeps_the_stated_cap():
    cap = parse_field("percent", SCOPED_BUYER, "ld_cap")
    assert (cap.value, cap.qualifier) == (10.0, "contract")
    assert parse_field("rate_per_period", SCOPED_BUYER, "ld_rate") is None  # "the framework agreement rate"


def test_ld_exclusion_scoped_to_force_majeure_keeps_rate_and_cap():
    assert parse_field("rate_per_period", SCOPED_FM, "ld_rate").value == (Decimal("0.5"), "day")
    cap = parse_field("percent", SCOPED_FM, "ld_cap")
    assert (cap.value, cap.qualifier) == (10.0, "contract")


@pytest.mark.parametrize("sentence, whole_offer", [
    ("Liquidated damages are excluded.", True),
    ("No liquidated damages apply.", True),
    ("LDs not applicable to this offer.", True),
    ("Liquidated damages are not accepted for this order.", True),
    ("Liquidated damages are not applicable to delays caused by the Buyer.", False),
    ("No liquidated damages shall apply to delays caused by force majeure.", False),
    ("No LDs in case of force majeure.", False),
    ("For Buyer-caused delay, liquidated damages are waived.", False),
])
def test_no_lds_is_an_exclusion_of_the_whole_offer(sentence, whole_offer):
    assert no_lds(sentence) is whole_offer
