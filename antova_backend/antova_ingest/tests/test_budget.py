"""The offered price on its contract basis, and the budget lines: offered price and variance against the frozen
budget, converted only at the buyer-entered rate (Task 7, spec 4.4 and 9.7)."""

from datetime import date
from decimal import Decimal

from antova_ingest.budget import BudgetLine, PriceBasis, budget_lines, price_basis
from antova_ingest.contract_terms import ContractTerm, TermSource
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import BaselineFx, BaselineMoney, Citation
from antova_ingest.values import PriceRead

D = Decimal
GBP_EUR = BaselineFx(from_currency="GBP", to_currency="EUR", rate=D("1.17"), dated=date(2026, 11, 1))
EUR_BUDGET = BaselineMoney(amount=D("2400000"), currency="EUR")
OPTION = ("25 kA upgrade", D("50000"))
BASIS = PriceBasis(D("2000000"), "GBP", (OPTION,), (), ())


def _lines(lines: list[BudgetLine]) -> dict[str, BudgetLine]:
    by_label = {line.label: line for line in lines}
    assert len(by_label) == len(lines), lines
    return by_label


def _cite(file="bid.pdf", page=4):
    return Citation(file, page, (0.0, 0.0, 1.0, 1.0), f"clause on page {page}", "commercial")


def _ct13(quote, file="bid.pdf"):
    values = evidence("CT-13", "commercial", 4, quote, filename=file).values
    return ContractTerm("CT-13", "vendor-a", TermSource("document", values, (_cite(file),)), None, None, "cited", (),
                        fa_present=False)


# --- budget lines ------------------------------------------------------------------------------------

def test_offered_and_variance_in_the_budget_currency_at_the_buyer_rate():
    lines = _lines(budget_lines(BASIS, None, None, EUR_BUDGET, [GBP_EUR]))
    assert list(lines) == ["offered", "offered_with_options", "variance", "variance_with_options"]
    assert (lines["offered"].amount, lines["offered"].currency) == (D("2340000.00"), "EUR")
    assert lines["offered_with_options"].amount == D("2398500.00")
    assert lines["variance"].amount == D("-60000.00")
    assert lines["variance_with_options"].amount == D("-1500.00")
    for line in lines.values():
        assert line.currency == "EUR" and line.reason == ""
        assert "1.17 (2026-11-01)" in line.formula, line.formula
        assert "GBP→EUR" in line.formula
    assert "25 kA upgrade" in lines["offered_with_options"].formula
    assert "EUR 2,400,000.00" in lines["variance"].formula


def test_no_rate_for_the_pair_leaves_every_converted_line_unknown():
    lines = _lines(budget_lines(BASIS, None, None, EUR_BUDGET, []))
    for line in lines.values():
        assert line.amount is None
        assert line.reason == "no GBP→EUR rate entered"
    usd = BaselineFx(from_currency="USD", to_currency="EUR", rate=D("0.92"), dated=date(2026, 11, 1))
    assert budget_lines(BASIS, None, None, EUR_BUDGET, [usd])[0].reason == "no GBP→EUR rate entered"


def test_rate_entered_the_other_way_round_governs_the_pair():
    eur_gbp = BaselineFx(from_currency="EUR", to_currency="GBP", rate=D("0.8"), dated=date(2026, 10, 15))
    offered = budget_lines(PriceBasis(D("1000000"), "GBP", (), (), ()), None, None, EUR_BUDGET, [eur_gbp])[0]
    assert offered.amount == D("1250000.00")
    assert "÷ 0.8 (2026-10-15)" in offered.formula


def test_missing_budget_makes_every_variance_unknown():
    lines = _lines(budget_lines(BASIS, D("2457000"), "EUR", None, [GBP_EUR]))
    assert (lines["offered"].amount, lines["offered"].currency) == (D("2000000.00"), "GBP")
    assert lines["offered_with_options"].amount == D("2050000.00")
    for label in ("variance", "variance_with_options", "variance_true_job_cost"):
        assert lines[label].amount is None
        assert lines[label].reason == "frozen budget not entered"
        assert lines[label].formula == ""


def test_variable_parts_are_listed_and_never_summed():
    escalation = "copper price adjustment per Annex 4 (quoted, not computed)"
    with_variable = PriceBasis(D("2000000"), "GBP", (OPTION,), (escalation,), ())
    plain = _lines(budget_lines(BASIS, None, None, EUR_BUDGET, [GBP_EUR]))
    lines = _lines(budget_lines(with_variable, None, None, EUR_BUDGET, [GBP_EUR]))
    for label, line in lines.items():
        assert line.amount == plain[label].amount
    for label in ("offered", "offered_with_options"):
        assert f"not summed: {escalation}" in lines[label].formula
        assert "+ " + escalation not in lines[label].formula


def test_true_job_cost_converts_to_the_budget_currency_at_the_same_buyer_rate():
    gbp_budget = BaselineMoney(amount=D("2000000"), currency="GBP")
    lines = _lines(budget_lines(BASIS, D("2457000"), "EUR", gbp_budget, [GBP_EUR]))
    tjc = lines["variance_true_job_cost"]
    assert (tjc.amount, tjc.currency) == (D("100000.00"), "GBP")  # 2,457,000 ÷ 1.17 = 2,100,000.00
    assert "EUR 2,457,000 ÷ 1.17 (2026-11-01)" in tjc.formula
    assert "GBP 2,000,000.00" in tjc.formula
    assert lines["variance"].amount == D("0.00")  # a true zero: every input known


def test_true_job_cost_line_only_when_one_is_given_and_unknown_when_not_known():
    assert "variance_true_job_cost" not in _lines(budget_lines(BASIS, None, None, EUR_BUDGET, [GBP_EUR]))
    unknown = _lines(budget_lines(BASIS, None, "EUR", EUR_BUDGET, [GBP_EUR]))["variance_true_job_cost"]
    assert unknown.amount is None and unknown.reason == "the true job cost is not known"
    no_currency = _lines(budget_lines(BASIS, D("2400000"), None, EUR_BUDGET, [GBP_EUR]))["variance_true_job_cost"]
    assert no_currency.amount is None and no_currency.reason == "the true job cost has no currency"
    no_rate = _lines(budget_lines(BASIS, D("2400000"), "USD", EUR_BUDGET, [GBP_EUR]))["variance_true_job_cost"]
    assert no_rate.amount is None and no_rate.reason == "no USD→EUR rate entered"


def test_same_currency_needs_no_rate():
    lines = _lines(budget_lines(PriceBasis(D("2000000"), "EUR", (), (), ()), D("2350000"), "EUR",
                                EUR_BUDGET, []))
    assert lines["offered"].amount == D("2000000.00")
    assert lines["variance"].amount == D("-400000.00")
    assert lines["variance_true_job_cost"].amount == D("-50000.00")
    assert "offered_with_options" not in lines and "variance_with_options" not in lines


def test_each_line_adds_up_from_the_figures_it_shows():
    # Ruling R28 style: 1,000,000.03 × 1.17 = 1,170,000.0351 is shown as 1,170,000.04, and the variance is
    # taken from that shown figure, not from the exact product.
    budget = BaselineMoney(amount=D("1170000"), currency="EUR")
    basis = PriceBasis(D("1000000.03"), "GBP", (("spares", D("0.03")),), (), ())
    lines = _lines(budget_lines(basis, None, None, budget, [GBP_EUR]))
    assert lines["offered"].amount == D("1170000.04")
    assert lines["variance"].amount == D("0.04")
    assert "= EUR 1,170,000.04" in lines["variance"].formula
    # The options are added in the price currency, then converted once: 1,000,000.06 × 1.17 = 1,170,000.0702.
    assert lines["offered_with_options"].amount == D("1170000.07")
    assert "GBP 1,000,000.06 × 1.17" in lines["offered_with_options"].formula
    assert lines["variance_with_options"].amount == D("0.07")


def test_unknown_lump_sum_or_currency_is_never_zero():
    for basis, reason in ((PriceBasis(None, "GBP", (OPTION,), (), ()), "the offered lump sum is not known"),
                          (PriceBasis(D("2000000"), None, (OPTION,), (), ()), "the offered price has no currency")):
        for line in budget_lines(basis, None, None, EUR_BUDGET, [GBP_EUR]):
            assert line.amount is None and line.reason == reason, line


# --- price basis -------------------------------------------------------------------------------------

def test_price_basis_from_the_headline_and_the_price_basis_clause():
    term = _ct13("The Contract Price is a fixed lump sum in GBP and is fixed after the notice to proceed.")
    basis = price_basis(term, PriceRead(D("2000000"), "GBP", None))
    assert (basis.lump_sum, basis.currency) == (D("2000000"), "GBP")
    assert basis.options == () and basis.variable == ()  # a fixed price: the clause is not a variable part
    assert basis.citations == (_cite(),)


def test_price_basis_lists_the_escalation_clause_of_a_price_that_moves():
    quote = ("The Contract Price is a lump sum in GBP subject to price adjustment by the formula in Annex 4: "
             "P = P0 x (0.4 + 0.6 x Cu/Cu0).")
    basis = price_basis(_ct13(quote), PriceRead(D("2000000"), "GBP", None))
    assert basis.lump_sum == D("2000000")
    assert basis.variable == (quote,)


def test_price_basis_currency_from_the_clause_when_the_headline_names_none():
    term = _ct13("The Contract Price is a fixed lump sum in GBP and is fixed after the notice to proceed.")
    assert price_basis(term, PriceRead(D("2000000"), None, None)).currency == "GBP"
    assert price_basis(None, PriceRead(D("2000000"), None, None)).currency is None


def test_price_basis_currencies_that_disagree_are_unknown():
    term = _ct13("The Contract Price is a fixed lump sum in EUR and is fixed after the notice to proceed.")
    basis = price_basis(term, PriceRead(D("2000000"), "GBP", None))
    assert basis.currency is None and basis.lump_sum == D("2000000")


def test_price_basis_unit_rate_is_not_a_lump_sum():
    assert price_basis(None, PriceRead(D("45000"), "GBP", "per bay")).lump_sum is None


def test_price_basis_with_nothing_known():
    assert price_basis(None, None) == PriceBasis(None, None, (), (), ())
