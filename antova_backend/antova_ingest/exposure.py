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


# --- back-to-back with the owner contract (spec 9.2) ---------------------------------------------------
# The owner's and the vendor's percentages are of different contract values, so these compare money: what
# the EPC would owe the owner for this package against what the vendor pays back. One currency throughout;
# a caller with two currencies converts first with the buyer-entered rate and passes the conversion in
# `conversions`, so it is part of the formula.

def _money(currency: str, value: Decimal) -> str:
    """An amount as shown in a formula: rounded half up to the cent like every result, so a shown step never
    disagrees with the amount by a cent. Only the display is rounded; the arithmetic uses the exact value."""
    return f"{currency} {_euros(value):,.2f}"


def _steps(conversions, delay_weeks, days: Decimal) -> list[str]:
    steps = list(conversions)
    weeks = _dec(delay_weeks)
    if weeks is not None:
        steps.append(f"{_n(weeks)} weeks × 7 = {_n(days)} days of delay")  # ruling R21: the scenario is in weeks
    return steps


def back_to_back_ld_gap(owner_rate_money_per_day, owner_cap_money, package_share_pct,
                        vendor_rate: tuple[Decimal, str] | None, vendor_cap_pct, po_value, delay_days, *,
                        currency: str, delay_weeks=None, conversions: tuple[str, ...] = (),
                        vendor_provides_no_lds: bool = False) -> Exposure:
    """What the EPC would owe the owner for `delay_days` of this package's delay, less what the vendor pays back.

    Owed: min(owner rate per day × days, owner cap) × the package's share of owner LDs. Paid back:
    min(order value × vendor rate × days, order value × vendor cap), a weekly rate applied as rate × days / 7.
    Never below zero; rounded to the cent only at the end. The caller passes the delay scenario in days
    (weeks × 7) and may pass `delay_weeks` to show that conversion. `currency` is that of every amount passed;
    there is no default. `vendor_provides_no_lds`: the offer explicitly excludes LDs, so the vendor pays back
    nothing and its rate, cap and the order value are not needed; an offer that only does not state a rate is
    not this, and stays unknown."""
    kind = "back_to_back_ld"
    owner_rate, owner_cap, share, cap, po, days = (_dec(v) for v in (
        owner_rate_money_per_day, owner_cap_money, package_share_pct, vendor_cap_pct, po_value, delay_days))
    rate, period = (_dec(vendor_rate[0]), vendor_rate[1]) if vendor_rate is not None else (None, None)
    vendor_inputs = () if vendor_provides_no_lds else (
        ("the vendor's LD rate", rate), ("the vendor's LD cap", cap), ("the order value", po))
    missing = [name for name, v in (
        ("the owner LD rate in money per day", owner_rate), ("the owner LD cap in money", owner_cap),
        ("the package's share of owner LDs", share), *vendor_inputs, ("the delay to assume", days)) if v is None]
    if missing:
        return _unknown(kind, "not known: " + ", ".join(missing))
    if not vendor_provides_no_lds and period not in ("day", "week"):
        return _unknown(kind, f"the vendor's LD rate is per {period}, not per day or per week")
    if days < 0:
        return _unknown(kind, "the delay to assume is negative")
    owed = min(owner_rate * days, owner_cap) * share / HUNDRED
    if vendor_provides_no_lds:
        pays = Decimal("0")
        paid_back = f"paid back by the vendor: {_money(currency, pays)} (the offer provides no liquidated damages)"
    else:
        rate_for_delay = rate * days if period == "day" else rate * days / 7
        pays = min(po * rate_for_delay / HUNDRED, po * cap / HUNDRED)
        times = f"{_n(days)} days" if period == "day" else f"{_n(days)}/7 weeks"
        paid_back = (f"paid back by the vendor: min({_n(rate)}% per {period} × {times}, {_n(cap)}% cap) × "
                     f"{_money(currency, po)} = {_money(currency, pays)}")
    gap = max(owed - pays, Decimal("0"))
    formula = "; ".join([*_steps(conversions, delay_weeks, days),
        f"owed to the owner: min({_money(currency, owner_rate)} per day × {_n(days)} days, "
        f"{_money(currency, owner_cap)} cap) × {_n(share)}% share = {_money(currency, owed)}",
        paid_back,
        f"gap max({_money(currency, owed)} − {_money(currency, pays)}, 0) = {_money(currency, gap)}"])
    return Exposure(kind, _euros(gap), formula)


def liability_back_to_back(owner_required_money, vendor_cap_pct, po_value, *, currency: str,
                           conversions: tuple[str, ...] = ()) -> Exposure:
    """The part of the liability the EPC owes the owner that the vendor's cap (a percentage of its order) does
    not carry: owner amount − order value × cap, never below zero."""
    kind = "back_to_back_liability"
    owner, cap, po = _dec(owner_required_money), _dec(vendor_cap_pct), _dec(po_value)
    missing = [name for name, v in (("the owner liability cap", owner), ("the vendor's liability cap", cap),
                                    ("the order value", po)) if v is None]
    if missing:
        return _unknown(kind, "not known: " + ", ".join(missing))
    carried = po * cap / HUNDRED
    gap = max(owner - carried, Decimal("0"))
    formula = "; ".join([*conversions,
        f"gap max(owner cap {_money(currency, owner)} − vendor {_n(cap)}% × {_money(currency, po)} "
        f"({_money(currency, carried)}), 0) = {_money(currency, gap)}"])
    return Exposure(kind, _euros(gap), formula)
