"""The offered price on its contract basis, and the budget lines of the contract approval sheet (spec 4.4, 9.7).

- Offered price: the contract lump sum as stated. Selected option lines are listed and summed into a second
  total; variable or provisional parts are listed and never summed.
- Variance: offered price, converted to the budget currency, minus the frozen budget; a second pair with the
  options; and the true job cost minus the budget, always shown (unknown when the true job cost is not known).
- Currency: the buyer-entered rate for the pair governs every conversion here (`BaselineFx`, one per pair, matched
  in either direction by `contract_checks.buyer_rate`), and each formula names the rate and its date. The rate
  table's FX (costs.py) is never used here: it governs the gap adders inside the true job cost only.

Pure code on Decimal. An unknown is None with the reason, never 0. Every figure a formula shows is rounded half
up to the cent first and the result is worked out from the shown figures (ruling R28 style), so each line adds
up on the sheet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from antova_ingest.contract_checks import _amount_text, _cents, buyer_rate, fx_convert
from antova_ingest.contract_terms import ContractTerm
from antova_ingest.exposure import _euros
from antova_ingest.types import BaselineFx, BaselineMoney, Citation
from antova_ingest.values import PriceRead, _dec_text

NO_BUDGET = "frozen budget not entered"
TJC_UNKNOWN = "true job cost not known"
_NOT_FIXED_TYPES = frozenset({"partly_variable", "variable"})

Label = Literal["offered", "offered_with_options", "variance", "variance_with_options", "variance_true_job_cost"]


@dataclass(frozen=True)
class PriceBasis:
    lump_sum: Decimal | None  # the stated price; None when not read or worded per unit
    currency: str | None
    options: tuple[tuple[str, Decimal], ...]  # selected option lines, in the price currency
    variable: tuple[str, ...]  # listed, never summed
    citations: tuple[Citation, ...]
    # Why `currency` is None when the offer states it but its documents disagree (ruling R32); empty otherwise.
    reason: str = ""


@dataclass(frozen=True)
class BudgetLine:
    label: Label
    amount: Decimal | None
    currency: str | None
    formula: str
    reason: str = ""


def price_basis(term_ct13: ContractTerm | None, headline: PriceRead | None) -> PriceBasis:
    """The offer's price basis: the headline price as its lump sum, and the CT-13 price basis clause.

    The currency is the headline's; where the headline names none, the price basis clause's (it is cited); where
    the two disagree, it is not known and `reason` says why (ruling R32). A headline worded per unit ("per bay") is a unit rate, not the lump sum.
    A price that moves (partly variable, variable, or not fixed after NTP) lists its escalation clause as quoted:
    it is citation-only and never computed. No option lines are read here; a caller with selected options adds
    them to `options`."""
    offered = term_ct13.offered if term_ct13 is not None else None
    values = offered.values if offered is not None else {}
    clause_currency = values["price_currency"].value if "price_currency" in values else None
    head_currency = headline.currency if headline is not None else None
    reason = ""
    if head_currency and clause_currency and head_currency != clause_currency:
        currency = None
        reason = f"the headline price is in {head_currency} but the price basis clause says {clause_currency}"
    else:
        currency = head_currency or clause_currency
    lump_sum = headline.amount if headline is not None and not headline.per_unit else None
    return PriceBasis(lump_sum, currency, (), _variable_parts(values), offered.citations if offered else (), reason)


def _variable_parts(values: dict) -> tuple[str, ...]:
    price_type = values.get("price_type")
    fixed = values.get("price_fixed_after_ntp")
    moves = (price_type is not None and price_type.value in _NOT_FIXED_TYPES) or (fixed is not None and fixed.value == 0)
    if not moves:
        return ()  # the escalation field also records "fixed and firm" clauses: not a variable part
    if "escalation_formula" in values:
        return (values["escalation_formula"].display,)
    said = price_type.display if price_type is not None else fixed.display
    return (f"the price is {said}; no escalation clause is quoted",)


# --- the budget lines --------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Shown:
    """An amount as a formula shows it: rounded to the cent, with the steps that produced it."""
    amount: Decimal | None
    steps: tuple[str, ...]
    reason: str = ""


def _to_budget(what: str, amount: Decimal, currency: str, to_currency: str | None,
               fx: Sequence[BaselineFx]) -> _Shown:
    """`amount` (already rounded to the cent) in the budget currency at the buyer-entered rate, rounded half up;
    with no budget, the amount as it stands."""
    if to_currency is None or to_currency == currency:
        return _Shown(amount, (f"{what} {_cents(currency, amount)}",))
    converted = fx_convert(amount, currency, to_currency, fx)
    if converted.amount is None:
        return _Shown(None, (), converted.reason)
    rate, op = buyer_rate(currency, to_currency, fx)
    value = _euros(converted.amount)
    return _Shown(value, (f"{what} {currency} {_amount_text(amount)} {op} {_dec_text(rate.rate)} "
                          f"({rate.dated.isoformat()}) buyer-entered {rate.from_currency}→{rate.to_currency} rate "
                          f"= {_cents(to_currency, value)}",))


def _line(label: Label, shown: _Shown, currency: str | None, notes: tuple[str, ...] = ()) -> BudgetLine:
    if shown.amount is None:
        return BudgetLine(label, None, None, "", shown.reason)
    return BudgetLine(label, shown.amount, currency, "; ".join((*shown.steps, *notes)))


def _variance(label: Label, shown: _Shown, budget: BaselineMoney | None) -> BudgetLine:
    if budget is None:
        return BudgetLine(label, None, None, "", NO_BUDGET)
    if shown.amount is None:
        return BudgetLine(label, None, None, "", shown.reason)
    frozen = _euros(budget.amount)
    variance = shown.amount - frozen
    return BudgetLine(label, variance, budget.currency, "; ".join((
        *shown.steps, f"{_cents(budget.currency, shown.amount)} − frozen budget {_cents(budget.currency, frozen)} "
                      f"= {_cents(budget.currency, variance)}")))


def _offered(price: PriceBasis, to_currency: str | None, fx: Sequence[BaselineFx]) -> tuple[_Shown, _Shown | None]:
    """The lump sum, and the lump sum with the options (None when no option is selected), in the budget currency."""
    if price.lump_sum is None:
        unknown = _Shown(None, (), "the offered lump sum is not known")
        return unknown, unknown if price.options else None
    if price.currency is None:
        unknown = _Shown(None, (), price.reason or "the offered price has no currency")
        return unknown, unknown if price.options else None
    currency, lump = price.currency, _euros(price.lump_sum)
    offered = _to_budget("offered lump sum", lump, currency, to_currency, fx)
    if not price.options:
        return offered, None
    # Options are added in the price currency, then converted once, so the sheet shows one conversion.
    options = [(name, _euros(amount)) for name, amount in price.options]
    total = lump + sum((amount for _, amount in options), Decimal("0"))
    added = " + ".join(f"{name} {currency} {_amount_text(amount)}" for name, amount in options)
    summed = f"offered lump sum {currency} {_amount_text(lump)} + {added} = {currency} {_amount_text(total)}"
    with_options = _to_budget("total", total, currency, to_currency, fx)
    return offered, _Shown(with_options.amount, (summed, *with_options.steps), with_options.reason)


def _true_job_cost(true_job_cost: Decimal | None, tjc_currency: str | None, to_currency: str | None,
                   fx: Sequence[BaselineFx]) -> _Shown:
    if true_job_cost is None or tjc_currency is None:
        return _Shown(None, (), TJC_UNKNOWN)
    return _to_budget("true job cost", _euros(true_job_cost), tjc_currency, to_currency, fx)


def budget_lines(price: PriceBasis, true_job_cost: Decimal | None, tjc_currency: str | None,
                 budget: BaselineMoney | None, fx: Sequence[BaselineFx]) -> list[BudgetLine]:
    """The offered price and its variance against the frozen budget, in the budget currency.

    Lines, in order: offered; offered_with_options (when an option is selected); variance; variance_with_options
    (when an option is selected); variance_true_job_cost, always (ruling R33): None with "true job cost not
    known" when the true job cost or its currency is not known. Without a budget the offered lines stay in the price currency and
    every variance line is None with "frozen budget not entered". Variable parts are named in the offered
    formulas and never summed."""
    to_currency = budget.currency if budget is not None else None
    offered, with_options = _offered(price, to_currency, fx)
    notes = tuple(f"not summed: {part}" for part in price.variable)
    shown_currency = to_currency or price.currency
    lines = [_line("offered", offered, shown_currency, notes)]
    if with_options is not None:
        lines.append(_line("offered_with_options", with_options, shown_currency, notes))
    lines.append(_variance("variance", offered, budget))
    if with_options is not None:
        lines.append(_variance("variance_with_options", with_options, budget))
    lines.append(_variance("variance_true_job_cost",
                           _true_job_cost(true_job_cost, tjc_currency, to_currency, fx), budget))
    return lines
