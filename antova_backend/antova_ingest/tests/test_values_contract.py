"""Contract field kinds: rates per period, project events, payment milestones, law and forum."""

from decimal import Decimal

from antova_ingest.ontology import ORDINALS, FieldDef
from antova_ingest.types import FieldValue
from antova_ingest.values import Milestone, check, parse_field, per_week, value_in_span


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
