"""Exposures (delay, LD recovery, liability) and the priced scope exclusions in the extended rate table."""

import json
from decimal import Decimal

import pytest

import antova_engine as engine
from antova_ingest.exposure import (back_to_back_ld_gap, delay_cost, ld_recovery_shortfall, liability_back_to_back,
                                    liability_shortfall)
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import AnswerKeyLocator
from samples import mutate
from pathlib import Path

D = Decimal
PACK = Path(__file__).resolve().parents[2] / "samples" / "TENDER-2026-418"


# --- delay -----------------------------------------------------------------------------------------

def test_delay_cost_is_weeks_late_times_the_buyers_weekly_cost():
    e = delay_cost(4, 12_500)
    assert (e.amount, e.formula) == (D("50000.00"), "4 weeks late × €12,500.00 per week")


def test_a_delivery_that_is_not_late_is_a_true_zero():
    assert delay_cost(-3, 12_500).amount == D("0.00")
    assert delay_cost(0, 12_500).amount == D("0.00")


@pytest.mark.parametrize("weeks, cost, reason", [
    (None, 12_500, "projected"),
    (4, None, "has not stated"),
    (4, -1, "negative"),
])
def test_an_unknown_delay_cost_is_none_with_a_reason_never_zero(weeks, cost, reason):
    e = delay_cost(weeks, cost)
    assert e.amount is None and reason in e.reason


# --- liquidated damages ------------------------------------------------------------------------------

# MR: 0.5% a week, capped at 10%. Contract values are the sample bids' headline prices.

def test_a_bid_that_excludes_liquidated_damages_leaves_the_whole_recovery_uncovered():
    e = ld_recovery_shortfall(0.5, 10, 0, 0, 8, 418_000)
    assert e.amount == D("16720.00")  # 4% of 418,000 after 8 weeks


def test_a_lower_cap_costs_nothing_until_the_delay_reaches_it():
    # 0.5% a week hits the offered 5% cap at week 10; before that both sides recover the same.
    assert ld_recovery_shortfall(0.5, 10, 0.5, 5, 8, 439_000).amount == D("0.00")
    assert ld_recovery_shortfall(0.5, 10, 0.5, 5, 12, 439_000).amount == D("4390.00")   # 6% vs 5%
    assert ld_recovery_shortfall(0.5, 10, 0.5, 5, 30, 439_000).amount == D("21950.00")  # 10% vs 5%


def test_terms_at_least_as_good_as_the_mr_are_a_true_zero_never_negative():
    assert ld_recovery_shortfall(0.5, 10, 1.0, 15, 30, 452_000).amount == D("0.00")


def test_the_ld_formula_shows_both_recoveries():
    assert "MR 4% − bid 0%" in ld_recovery_shortfall(0.5, 10, 0, 0, 8, 418_000).formula


@pytest.mark.parametrize("missing", range(6))
def test_any_missing_ld_input_makes_the_exposure_unknown(missing):
    args = [0.5, 10, 0.5, 5, 12, 439_000]
    args[missing] = None
    e = ld_recovery_shortfall(*args)
    assert e.amount is None and e.reason.startswith("not known")


# --- liability ---------------------------------------------------------------------------------------

def test_liability_shortfall_is_the_gap_as_a_share_of_the_contract_value():
    assert liability_shortfall(100, 20, 418_000).amount == D("334400.00")
    assert liability_shortfall(100, 100, 452_000).amount == D("0.00")
    assert liability_shortfall(100, 150, 452_000).amount == D("0.00")


def test_unknown_liability_inputs_are_none_never_zero():
    assert liability_shortfall(100, None, 418_000).amount is None
    assert liability_shortfall(None, 20, 418_000).amount is None
    assert "contract value" in liability_shortfall(100, 20, None).reason


def test_amounts_round_half_up_to_the_cent():
    assert liability_shortfall(100, 99, "50.5").amount == D("0.51")  # 0.505 rounds up
    assert delay_cost(1, "0.005").amount == D("0.01")


# --- the extended rate table prices the exclusions ---------------------------------------------------

@pytest.fixture(scope="module")
def tender():
    expected = json.loads((PACK / "expected.json").read_text(encoding="utf-8"))
    return run_ingest(PACK, locator=AnswerKeyLocator(expected)).tender


def _cell(tender, vendor, param):
    return next(p for p in tender.parameters if p.id == param).vendors[vendor]


def test_an_excluded_item_is_priced_from_the_table(tender):
    spares, supervision = _cell(tender, "vendor-b", "PARAM-12"), _cell(tender, "vendor-b", "PARAM-13")
    assert (spares.pricing_status, spares.cost_adder) == ("PRICED", 14_000.0)
    assert (supervision.pricing_status, supervision.cost_adder) == ("PRICED", 9_600.0)
    assert [line.driver for line in supervision.cost_lines] == ["labour", "fixed"]


def test_a_priced_cell_carries_its_band_and_the_band_holds(tender):
    base = engine.SensitivitySettings()
    for param in ("PARAM-12", "PARAM-13"):
        lines = _cell(tender, "vendor-b", param).cost_lines
        low = sum(engine.line_amount(line, base, "low") for line in lines)
        mid = sum(engine.line_amount(line, base, "base") for line in lines)
        high = sum(engine.line_amount(line, base, "high") for line in lines)
        assert low <= mid <= high and low < high


def test_the_labour_slider_moves_supervision_and_not_spares(tender):
    slider = engine.SensitivitySettings(site_labor_rate=90.0)
    supervision = _cell(tender, "vendor-b", "PARAM-13").cost_lines
    spares = _cell(tender, "vendor-b", "PARAM-12").cost_lines
    assert sum(engine.line_amount(line, slider) for line in supervision) == D("13200.00")  # 120 h x 90 + 2,400
    assert sum(engine.line_amount(line, slider) for line in spares) == D("14000.00")


def test_delivery_ld_liability_and_altitude_stay_unpriced_not_free(tender):
    for vendor, param in (("vendor-b", "PARAM-09"), ("vendor-b", "PARAM-10"), ("vendor-b", "PARAM-11"),
                          ("vendor-b", "PARAM-14"), ("vendor-c", "PARAM-10")):
        cell = _cell(tender, vendor, param)
        assert cell.pricing_status == "UNPRICED" and cell.cost_adder == 0.0 and not cell.cost_lines


def test_a_bid_that_is_silent_is_not_given_an_invented_cost(tmp_path):
    target = mutate.generate(["spares_silent"], tmp_path, base=mutate.EXTENDED_BASE_TENDER_ID)
    expected = json.loads((target / "expected.json").read_text(encoding="utf-8"))
    cell = _cell(run_ingest(target, locator=AnswerKeyLocator(expected)).tender, "vendor-a", "PARAM-12")
    assert (cell.status.value, cell.pricing_status, cell.cost_adder) == ("MANUAL_INSPECTION_REQUIRED", "NONE", 0.0)


def test_a_vendors_true_price_adds_the_priced_exclusions_and_stays_a_lower_bound(tender):
    financials = {f["vendor_id"]: f for f in engine.evaluate_vendor_financials(tender, engine.SensitivitySettings(), [])}
    b = financials["vendor-b"]
    assert (b["total_adders"], b["true_evaluated_price"]) == (85_250.0, 503_250.0)
    assert b["total_adders_low"] <= b["total_adders"] <= b["total_adders_high"]
    assert b["true_evaluated_low"] <= b["true_evaluated_price"] <= b["true_evaluated_high"]
    assert b["price_status"] == "UNPRICED_ITEMS"  # delivery, LD, liability and altitude gaps are still unpriced


# --- back-to-back with the owner contract (Task 6) ------------------------------------------------------

# Owner: EUR 10,000 a day, capped at EUR 1,000,000, all of it carried by this package.
# Vendor: 0.5% of a EUR 2,000,000 order a day, capped at 10% (EUR 200,000).

def _b2b(days, vendor_rate=(D("0.5"), "day"), share=D("100"), **kw):
    return back_to_back_ld_gap(D("10000"), D("1000000"), share, vendor_rate, D("10"), D("2000000"), days,
                               currency="EUR", **kw)


def test_back_to_back_gap():
    e = _b2b(D("28"))
    assert (e.kind, e.amount) == ("back_to_back_ld", D("80000.00"))  # 280,000 owed − 200,000 at the vendor's cap
    assert "EUR 280,000.00" in e.formula and "EUR 200,000.00" in e.formula


def test_back_to_back_true_zero():
    assert _b2b(D("14")).amount == D("0.00")  # 140,000 owed, 140,000 paid back


def test_back_to_back_weekly_rate():
    weekly = _b2b(D("28"), vendor_rate=(D("3.5"), "week"))
    assert weekly.amount == _b2b(D("28")).amount and "28/7" in weekly.formula


def test_back_to_back_share_missing():
    e = _b2b(D("28"), share=None)
    assert e.amount is None and "share of owner LDs" in e.reason


def test_back_to_back_shows_the_delay_in_weeks():
    assert "4 weeks × 7 = 28 days" in _b2b(D("28"), delay_weeks=D("4")).formula


def test_back_to_back_any_missing_input_is_none():
    def gap(*args):
        return back_to_back_ld_gap(*args, currency="EUR")
    assert gap(None, D("1"), D("100"), (D("1"), "day"), D("10"), D("1"), D("1")).amount is None
    assert gap(D("1"), D("1"), D("100"), None, D("10"), D("1"), D("1")).amount is None
    assert "delay" in gap(D("1"), D("1"), D("100"), (D("1"), "day"), D("10"), D("1"), None).reason


def test_liability_back_to_back():
    def gap(*args):
        return liability_back_to_back(*args, currency="EUR")
    e = gap(D("500000"), D("10"), D("2000000"))
    assert (e.kind, e.amount) == ("back_to_back_liability", D("300000.00"))
    assert gap(D("500000"), D("30"), D("2000000")).amount == D("0.00")
    assert gap(D("500000"), 12.5, D("2000000")).amount == D("250000.00")  # float percent read exactly
    assert gap(D("500000"), None, D("2000000")).amount is None


def test_back_to_back_gap_formulas_show_the_clamp_at_zero():
    assert "gap max(EUR 140,000.00 − EUR 140,000.00, 0) = EUR 0.00" in _b2b(D("14")).formula
    zero = liability_back_to_back(D("500000"), D("30"), D("2000000"), currency="EUR")
    assert ("gap max(owner cap EUR 500,000.00 − vendor 30% × EUR 2,000,000.00 (EUR 600,000.00), 0) = EUR 0.00"
            in zero.formula)


def test_shown_steps_round_like_the_result():
    # Owed is exactly EUR 0.005: shown half up (0.01), as the amount is, never 0.00 beside a 0.01 result.
    e = back_to_back_ld_gap(D("0.005"), D("100"), D("100"), None, None, None, D("1"), currency="EUR",
                            vendor_provides_no_lds=True)
    assert e.amount == D("0.01")
    assert "share = EUR 0.01" in e.formula and "max(EUR 0.01 − EUR 0.00, 0) = EUR 0.01" in e.formula


def test_gap_is_taken_from_the_rounded_figures_so_the_formula_adds_up():
    # Ruling R28: owed 100.005 -> 100.01 and paid back 50.004 -> 50.00 are rounded first; the gap is their
    # difference (the exact 50.001 would round to 50.00 beside "100.01 − 50.00").
    e = back_to_back_ld_gap(100.005, 1e6, 100, (0.5, "day"), 100, 10000.8, 1, currency="EUR")
    assert e.amount == D("50.01")
    assert "max(EUR 100.01 − EUR 50.00, 0) = EUR 50.01" in e.formula
    liability = liability_back_to_back(D("100.005"), D("0.5"), D("10000.8"), currency="EUR")
    assert liability.amount == D("50.01") and "= EUR 50.01" in liability.formula


def test_a_zero_gap_is_shown_to_the_cent():
    assert str(liability_back_to_back(100, 10, 2000000, currency="EUR").amount) == "0.00"
    assert str(_b2b(D("14")).amount) == "0.00"


def test_vendor_with_no_lds_pays_back_nothing():
    e = back_to_back_ld_gap(D("10000"), D("1000000"), D("50"), None, None, None, D("28"), currency="EUR",
                            vendor_provides_no_lds=True)
    assert e.amount == D("140000.00")  # 280,000 owed × 50% share, nothing paid back
    assert "no liquidated damages" in e.formula


def test_back_to_back_currency_has_no_default():
    with pytest.raises(TypeError):
        back_to_back_ld_gap(D("1"), D("1"), D("100"), (D("1"), "day"), D("10"), D("1"), D("1"))
    with pytest.raises(TypeError):
        liability_back_to_back(D("1"), D("10"), D("1"))
