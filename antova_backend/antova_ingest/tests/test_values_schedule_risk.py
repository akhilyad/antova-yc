"""Parsers for lead time, percentages, money caps, altitude and cited standards."""

from decimal import Decimal
from typing import get_args

import pytest

from antova_ingest import codec, config, sweep
from antova_ingest.ontology import FieldDef
from antova_ingest.types import FieldKind, FieldValue
from antova_ingest.values import (
    check, normalize_numbers, parse_field, plausible, plausible_bounds, rule_text, standards_satisfied, value_in_span,
)


def read(kind, quote, name="x"):
    return parse_field(kind, quote, name)


def shown(kind, quote, name="x"):
    value = read(kind, quote, name)
    return None if value is None else (value.display, value.qualifier)


# --- lead time -------------------------------------------------------------------------------------

@pytest.mark.parametrize("quote, expected", [
    ("Delivery 38 weeks from PO and approved drawings.", ("38 weeks", "approved_drawings")),
    ("Delivery 38 weeks from PO.", ("38 weeks", "po")),
    ("Delivery 38 weeks.", ("38 weeks", "unspecified")),
    ("Delivery 36-40 weeks ex-works after purchase order.", ("40 weeks", "po")),
    ("Lead time is 9 months from contract award.", ("39 weeks (9 months)", "po")),
    ("Delivery in 91 calendar days from NTP.", ("13 weeks (91 calendar days)", "po")),
    ("Delivery in 35 working days from PO.", ("7 weeks (35 working days)", "po")),
    # Other periods in the same bid are not the lead time.
    ("Offer validity is 12 weeks.", None),
    ("Warranty runs 24 months from delivery.", None),
    ("Payment within 30 days of invoice, delivery 20 weeks from PO.", ("20 weeks", "po")),
    ("Drawing submission within 4 weeks, delivery 30 weeks from PO.", ("30 weeks", "po")),
    # A carve-out sentence offers nothing.
    ("Delivery in 12 weeks is available on request.", None),
    ("The panels are painted grey.", None),
])
def test_lead_time(quote, expected):
    assert shown("lead_time", quote) == expected


def test_a_range_gives_its_upper_end_because_the_buyer_plans_for_the_worst_case():
    assert read("lead_time", "Delivery 36 to 40 weeks from PO.").value == 40.0


def test_months_convert_at_52_over_12_weeks():
    assert read("lead_time", "Delivery 12 months from PO.").value == pytest.approx(52.0)


# --- percentages -----------------------------------------------------------------------------------

LD = "Delay LDs of 0.5% per week of the contract price, capped at 10% of the contract price."


def test_the_field_name_picks_which_percentage():
    assert shown("percent", LD, "ld_rate") == ("0.5 % per week", "per_week")
    assert shown("percent", LD, "ld_cap") == ("10 %", "contract")
    assert read("percent", LD, "liability_cap") is None  # the cap here is on LDs, not on total liability


def test_liability_cap():
    quote = "Total liability is limited to 100% of the contract price."
    assert shown("percent", quote, "liability_cap") == ("100 %", "contract")
    assert read("percent", quote, "ld_cap") is None  # no LD wording: this is not an LD cap


def test_a_daily_rate_is_converted_to_per_week_and_shown_as_written():
    value = read("percent", "Liquidated damages of 0.1% per day for late delivery.", "ld_rate")
    assert (value.display, value.value, value.qualifier) == ("0.1 % per day", 0.7, "per_week")
    assert read("percent", "Delay LDs of 0.5 per cent weekly.", "ld_rate").value == 0.5


@pytest.mark.parametrize("quote", [
    "Liquidated damages are excluded.",
    "No liquidated damages apply.",
    "LDs not applicable to this offer.",
    "Liquidated damages are waived.",
])
def test_no_liquidated_damages_reads_as_zero(quote):
    for name in ("ld_rate", "ld_cap"):
        value = read("percent", quote, name)
        assert (value.value, value.qualifier) == (0.0, "none")
        assert value_in_span(value, quote)  # read from wording: nothing to find in the text


def test_a_percentage_with_no_rate_period_is_not_an_ld_rate():
    assert read("percent", "Delay LDs of 5% of the contract price.", "ld_rate") is None


# --- money caps ------------------------------------------------------------------------------------

@pytest.mark.parametrize("quote, expected", [
    ("Liability is capped at EUR 500,000.", ("EUR 500,000", "EUR")),
    ("Maximum aggregate liability of 1.250.000,50 EUR.", ("EUR 1,250,000.50", "EUR")),
    ("Total liability shall not exceed $2,000,000.", ("USD 2,000,000", "USD")),
    ("Liability is capped at EUR 1m.", None),            # shorthand is never read to the euro
    ("Liability is capped at 500,000.", None),           # the currency is never assumed
    ("The unit price is EUR 4,000.", None),              # not a cap
    ("A cap of EUR 50,000 is available on request.", None),
])
def test_money_cap(quote, expected):
    assert shown("money_cap", quote) == expected


def test_money_cap_amount_is_a_decimal_with_its_currency():
    value = read("money_cap", "Liability is capped at EUR 500,000.")
    assert value.value == Decimal("500000") and value.qualifier == "EUR"


# --- altitude --------------------------------------------------------------------------------------

@pytest.mark.parametrize("quote, display", [
    ("Rated for altitude up to 1000 m above sea level.", "1000 m"),
    ("Site elevation 3300 ft.", "3300 ft"),
    ("Installation altitude: 2000 m a.s.l.", "2000 m"),
    ("Switchgear rated for operation at 1000 masl.", "1000 m"),
])
def test_altitude(quote, display):
    assert read("altitude", quote).display == display


@pytest.mark.parametrize("quote", [
    "Busbar length 12 m.",                                    # no altitude wording
    "Altitude derating applies above 1000 m, optional kit.",  # carve-out sentence
    "Rated 3 m/s wind at altitude.",                          # a speed, not a height
])
def test_altitude_is_not_read_from_other_lengths(quote):
    assert read("altitude", quote) is None


def test_feet_and_metres_compare_in_the_same_unit():
    field = FieldDef("altitude", "altitude", "ge", "Altitude")
    metres, feet = read("altitude", "Altitude 1000 m."), read("altitude", "Altitude 3281 ft.")
    assert check(field, metres, feet)[0] is True      # 3281 ft is 1000.1 m
    assert check(field, read("altitude", "Altitude 3000 ft."), metres)[0] is True
    assert check(field, metres, read("altitude", "Altitude 3000 ft."))[0] is False


# --- standards -------------------------------------------------------------------------------------

def test_standards_are_read_as_a_sorted_set_with_editions_when_written():
    value = read("standard", "Designed to IEC 62271-200:2011, IEC 61850 and IEEE C37.20.2.")
    assert value.value == ("IEC 61850", "IEC 62271-200:2011", "IEEE C37.20.2")
    assert read("standard", "Built to EN 50160 (2010).").value == ("EN 50160:2010",)
    assert read("standard", "Tested per IEC 62271‑200.").value == ("IEC 62271-200",)  # a non-breaking hyphen


@pytest.mark.parametrize("quote", [
    "Complies with IEC 62271-200 except clause 6.2.",
    "The enclosure is not tested to IEC 62271-200.",
    "Partially compliant with IEC 61850.",
    "Deviations from IEC 62271-1 are listed in the schedule.",
    "Compliance with IEC 62271-200 is optional.",
    "The unit weighs 120 kg.",
])
def test_a_sentence_that_negates_or_carves_out_cites_nothing(quote):
    assert read("standard", quote) is None


def test_the_latest_edition_of_a_repeated_standard_is_kept():
    value = read("standard", "IEC 62271-200:2003 and IEC 62271-200:2011 are both referenced.")
    assert value.value == ("IEC 62271-200:2011",)


@pytest.mark.parametrize("required, offered, ok", [
    (("IEC 62271-200",), ("IEC 62271-200:2011",), True),
    (("IEC 62271-200:2011",), ("IEC 62271-200:2011",), True),
    (("IEC 62271-200:2011",), ("IEC 62271-200:2021",), True),     # a newer edition meets it
    (("IEC 62271-200:2021",), ("IEC 62271-200:2011",), False),    # an older one does not
    (("IEC 62271-200:2011",), ("IEC 62271-200",), False),         # edition unstated: cannot be shown
    (("IEC 62271-200", "IEC 61850"), ("IEC 62271-200",), False),  # one missing
    (("IEC 62271-200",), ("IEC 61850", "IEC 62271-200", "EN 50160"), True),
])
def test_every_required_standard_must_be_offered(required, offered, ok):
    assert standards_satisfied(required, offered) is ok
    field = FieldDef("standards", "standard", "all", "Standards")
    passed, _ = check(field, FieldValue("standard", required, ", ".join(required)),
                      FieldValue("standard", offered, ", ".join(offered)))
    assert passed is ok


# --- comparison ------------------------------------------------------------------------------------

def test_a_shorter_lead_time_passes_a_le_check():
    field = FieldDef("lead_time", "lead_time", "le", "Lead time")
    required = read("lead_time", "Delivery 40 weeks from PO.")
    assert check(field, required, read("lead_time", "Delivery 38 weeks from PO."))[0] is True
    assert check(field, required, read("lead_time", "Delivery 40 weeks from PO."))[0] is True
    assert check(field, required, read("lead_time", "Delivery 41 weeks from PO."))[0] is False


def test_a_higher_cap_passes_a_ge_check_and_currencies_do_not_mix():
    ge = FieldDef("cap", "money_cap", "ge", "Cap")
    required = read("money_cap", "Liability is capped at EUR 500,000.")
    assert check(ge, required, read("money_cap", "Liability is capped at EUR 750,000."))[0] is True
    assert check(ge, required, read("money_cap", "Liability is capped at EUR 400,000."))[0] is False
    assert check(ge, required, read("money_cap", "Liability is capped at USD 750,000."))[0] is False


def test_percent_ge_check():
    ge = FieldDef("ld_cap", "percent", "ge", "LD cap")
    ten = read("percent", LD, "ld_cap")
    assert check(ge, ten, read("percent", "LDs are capped at 15% of the contract price.", "ld_cap"))[0] is True
    assert check(ge, ten, read("percent", "LDs are capped at 5% of the contract price.", "ld_cap"))[0] is False


def test_rule_text_names_the_new_operators():
    from antova_ingest.ontology import ParameterDef
    from antova_engine import Criticality
    param = ParameterDef("P", "p", "c", Criticality.HIGH, (
        FieldDef("lead", "lead_time", "le", "Lead"), FieldDef("std", "standard", "all", "Std")), "", "")
    text = rule_text(param, {"lead": read("lead_time", "Delivery 40 weeks."),
                             "std": read("standard", "IEC 61850.")})
    assert text == "Lead ≤ 40 weeks AND Std include IEC 61850"


# --- the value is found in the cited text, and survives storage ------------------------------------

@pytest.mark.parametrize("kind, quote", [
    ("lead_time", "Delivery 36-40 weeks from PO."),
    ("lead_time", "Lead time is 9 months from contract award."),
    ("money_cap", "Liability is capped at EUR 500,000."),
    ("altitude", "Rated for altitude up to 1000 m above sea level."),
    ("standard", "Designed to IEC 62271-200:2011 and IEEE C37.20.2."),
])
def test_value_in_span_finds_what_was_read_and_rejects_a_different_number(kind, quote):
    value = read(kind, quote)
    assert value_in_span(value, quote)
    if kind != "standard":
        other = quote.replace("40", "41").replace("9 months", "8 months").replace("500,000", "501,000") \
                     .replace("1000", "1001")
        assert not value_in_span(value, other)
    else:
        assert not value_in_span(value, "Designed to IEC 62271-1.")


def test_percent_value_in_span():
    value = read("percent", LD, "ld_cap")
    assert value_in_span(value, LD)
    assert not value_in_span(value, LD.replace("10%", "12%"))


@pytest.mark.parametrize("kind, quote, name", [
    ("lead_time", "Delivery 38 weeks from PO.", "x"),
    ("percent", LD, "ld_rate"),
    ("money_cap", "Liability is capped at EUR 500,000.", "x"),
    ("altitude", "Altitude 3300 ft.", "x"),
    ("altitude", "Altitude 1000 m.", "x"),
    ("standard", "IEC 62271-200:2011 and IEC 61850.", "x"),
])
def test_values_round_trip_through_the_stage_artifacts(kind, quote, name):
    value = read(kind, quote, name)
    back = codec.value_from_plain(codec.value_to_plain(value))
    assert (back.kind, back.display, back.qualifier) == (value.kind, value.display, value.qualifier)
    if kind == "altitude":
        assert back.value == value.value
    else:
        assert back.value == value.value and type(back.value) is type(value.value)


# --- plausible ranges and registry coverage --------------------------------------------------------

def test_plausible_bounds_read_bare_values():
    lead = FieldDef("lead", "lead_time", "le", "Lead", plausible=("1 weeks", "150 weeks"))
    assert plausible(lead, read("lead_time", "Delivery 38 weeks from PO."))
    assert not plausible(lead, read("lead_time", "Delivery 400 weeks from PO."))
    altitude = FieldDef("alt", "altitude", "ge", "Alt", plausible=("0 m", "4500 m"))
    assert plausible(altitude, read("altitude", "Altitude 3300 ft."))
    assert not plausible(altitude, read("altitude", "Altitude 9000 m."))
    percent = FieldDef("cap", "percent", "ge", "Cap", plausible=("0 %", "100 %"))
    assert plausible_bounds(percent) is not None
    assert not plausible(percent, FieldValue("percent", 250.0, "250 %"))


def test_every_field_kind_has_a_parser_and_a_sweep_signal():
    for kind in get_args(FieldKind):
        assert kind in config.PARSED_KINDS, f"{kind} has no parser"
        assert kind in sweep.SIGNALS, f"{kind} has no sweep signal"


def test_the_sweep_signals_see_what_the_parsers_read():
    assert sweep.SIGNALS["lead_time"].search("Delivery 38 weeks from PO")
    assert sweep.SIGNALS["percent"].search("capped at 10% of the price")
    assert sweep.SIGNALS["percent"].search("No liquidated damages apply")
    assert sweep.SIGNALS["money_cap"].search("capped at EUR 500,000")
    assert sweep.SIGNALS["altitude"].search("up to 1000 m")
    assert sweep.SIGNALS["standard"].search("designed to IEC 62271-200")
