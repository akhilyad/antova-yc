"""Money at risk from a vendor's delivery and contract terms.

These are exposures, not price adders. A late delivery, a lower cap on liquidated damages or a lower
liability limit costs the buyer only if something goes wrong, so none of it is added to the True Job Cost.
Each function returns an `Exposure` that a findings register can show next to the cell it comes from, with
the formula spelled out.

Pure code on Decimal. Two rules hold everywhere:

- An input that is missing makes the amount None, with the reason. An unknown exposure is never 0.
- A real zero is returned as 0.00 only when every input is known and the terms offered cover the terms required.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Exposure:
    kind: str
    amount: Decimal | None
    formula: str
    # Why `amount` is None; empty when it is known.
    reason: str = ""


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _unknown(kind: str, reason: str) -> Exposure:
    return Exposure(kind, None, "", reason)


def _n(value: Decimal) -> str:
    """A Decimal as plain digits without trailing zeros: 4.0 -> 4, 0.50 -> 0.5, 10 -> 10."""
    return format(value.normalize(), "f")


def _euros(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def delay_cost(weeks_late, cost_per_week) -> Exposure:
    """What a late delivery costs the buyer: weeks late times the buyer's own cost of a week's delay.

    `weeks_late` comes from the schedule float (a negative float is a late delivery) and `cost_per_week` is
    the buyer's figure; Antova has no default for it. Not late is a true zero."""
    late, rate = _dec(weeks_late), _dec(cost_per_week)
    if late is None:
        return _unknown("delay", "the delivery date could not be projected")
    if rate is None:
        return _unknown("delay", "the buyer has not stated what a week of delay costs")
    if rate < 0:
        return _unknown("delay", "the cost of a week of delay is negative")
    weeks = max(late, Decimal("0"))
    shown = _n(weeks.quantize(CENTS, rounding=ROUND_HALF_UP))
    approx = "≈ " if shown != _n(weeks) else ""  # the amount uses the exact weeks; only the display is rounded
    return Exposure("delay", _euros(weeks * rate), f"{approx}{shown} weeks late × €{rate:,.2f} per week")


def _recovered(rate_pct: Decimal, cap_pct: Decimal, weeks: Decimal) -> Decimal:
    """Share of the contract price recovered by liquidated damages after `weeks` of delay."""
    return min(rate_pct * weeks, cap_pct)


def ld_recovery_shortfall(required_rate_pct, required_cap_pct, offered_rate_pct, offered_cap_pct,
                          delay_weeks, contract_value) -> Exposure:
    """What the buyer would recover less, after `delay_weeks` of delay, than the MR's liquidated damages give.

    Each side recovers its weekly rate times the weeks, up to its cap, as a share of the contract value. The
    exposure is the difference, never below zero. `delay_weeks` is a scenario the buyer chooses (or the
    weeks a bid is projected late); it is not guessed here."""
    values = [_dec(v) for v in (required_rate_pct, required_cap_pct, offered_rate_pct, offered_cap_pct,
                                delay_weeks, contract_value)]
    names = ("the MR's LD rate", "the MR's LD cap", "the bid's LD rate", "the bid's LD cap",
             "the delay to assume", "the contract value")
    missing = [name for name, v in zip(names, values) if v is None]
    if missing:
        return _unknown("ld_recovery", "not known: " + ", ".join(missing))
    req_rate, req_cap, off_rate, off_cap, weeks, value = values
    required = _recovered(req_rate, req_cap, weeks)
    offered = _recovered(off_rate, off_cap, weeks)
    shortfall = max(required - offered, Decimal("0"))
    formula = (f"(MR {_n(required)}% − bid {_n(offered)}% of the contract value after {_n(weeks)} weeks of delay) "
               f"= {_n(shortfall)}% × €{value:,.2f}")
    return Exposure("ld_recovery", _euros(shortfall / HUNDRED * value), formula)


def liability_shortfall(required_pct, offered_pct, contract_value) -> Exposure:
    """What the liability limit leaves the buyer carrying: the gap between the limit the MR requires and the
    one offered, as a share of the contract value. A limit at or above the MR's is a true zero."""
    required, offered, value = _dec(required_pct), _dec(offered_pct), _dec(contract_value)
    missing = [name for name, v in (("the MR's liability limit", required), ("the bid's liability limit", offered),
                                    ("the contract value", value)) if v is None]
    if missing:
        return _unknown("liability", "not known: " + ", ".join(missing))
    shortfall = max(required - offered, Decimal("0"))
    return Exposure("liability", _euros(shortfall / HUNDRED * value),
                    f"(MR {_n(required)}% − bid {_n(offered)}%) = {_n(shortfall)}% × €{value:,.2f}")
