"""Each contract term checked against the framework agreement and the owner contract, and the gaps between
them in months and days (spec 4.3, 9.2).

Two baselines, each a separate check with its own citation pair:

- fa: FA conformance. Has the vendor slipped a term back from what was agreed? The FA's percentages are of
  the same order value as the offer's, so they are compared as percentages.
- owner: back-to-back. Does the vendor carry the EPC's obligations to the owner? The owner's percentages are
  of the owner contract value, so a percentage is never compared with the offer's: LDs and liability are
  compared in money (`back_to_back_ld`, `exposure.liability_back_to_back`), everything else as stated.

A baseline that has no value for a field requires nothing there, so no check is made: an FA that is silent
(or not read) and a missing FA both produce no FA check; a missing baseline is listed by the contract
findings, never here. Where the required side has a value and the offer has none (silent, not read, or in
conflict), the check is made with `passed=None` and the reason: unknown is never a pass or a fail.

CT-16 (incorporation) and CT-17 (order of precedence) describe how the documents fit together; they are
used to resolve the terms (contract_terms) and are not checked as terms.

Pure code on Decimal. A money value in another currency is converted only with the buyer-entered rate for
that pair (`BaselineFx`), with the conversion shown; otherwise it is not compared.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from antova_ingest.contract_ontology import CONTRACT_PARAMS_BY_ID
from antova_ingest.contract_terms import ContractTerm, TermSource
from antova_ingest.exposure import Exposure, _euros, _unknown, back_to_back_ld_gap
from antova_ingest.ontology import FieldDef
from antova_ingest.types import BaselineFx, Citation, FieldValue, OwnerTerms
from antova_ingest.values import (EVENT_LABELS, delayed_rate_clause, NOT_COMPARABLE, Milestone, _dec_text, check, citation_only,
                                  ld_exclusions)

Baseline = Literal["fa", "owner"]
SourceName = Literal["offered", "fa", "owner"]  # the "source" key of a field flag (contract_terms, ruling R27)

HUNDRED = Decimal("100")
_NOT_CHECKED = frozenset({"CT-16", "CT-17"})
_CONFLICT_CODES = frozenset({"passages_disagree", "amendment_order_unknown"})
_BASELINE_NAME = {"fa": "the framework agreement", "owner": "the owner contract"}
# What a percentage is of (values._CAP_BASIS); "unspecified" and "none" (no LDs) are not a base.
_PERCENT_BASES = {"contract": "the contract price", "delayed_portion": "the delayed portion"}


@dataclass(frozen=True)
class TermCheck:
    param_id: str
    vendor_id: str
    baseline: Baseline
    field: str
    passed: bool | None  # None: not comparable, see reason
    required: str
    offered: str
    required_citations: tuple[Citation, ...]
    offered_citations: tuple[Citation, ...]
    reason: str = ""


@dataclass(frozen=True)
class TermGap:
    kind: Literal["warranty", "payment_timing"]
    value: Decimal | None
    unit: Literal["months", "days"]
    formula: str
    reason: str = ""


# --- currency ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Converted:
    amount: Decimal | None  # exact, not rounded
    formula: str  # empty when no conversion was needed
    reason: str = ""


def _cents(currency: str, value: Decimal) -> str:
    """An amount shown in a formula, rounded half up like every result; the arithmetic keeps the exact value."""
    return f"{currency} {_euros(value):,.2f}"


def _amount_text(value: Decimal) -> str:
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{_euros(value):,.2f}"


def buyer_rate(from_currency: str, to_currency: str,
               fx: Sequence[BaselineFx]) -> tuple[BaselineFx, Literal["×", "÷"]] | None:
    """The buyer-entered rate for the pair, matched in either direction (the baseline holds one per pair), and
    the operation that takes an amount from `from_currency` to `to_currency` with it; None when none is entered."""
    for rate in fx:
        if (rate.from_currency, rate.to_currency) == (from_currency, to_currency):
            return rate, "×"
        if (rate.from_currency, rate.to_currency) == (to_currency, from_currency):
            return rate, "÷"
    return None


def no_rate_reason(from_currency: str, to_currency: str) -> str:
    return f"no {from_currency}→{to_currency} rate entered"


def fx_convert(amount: Decimal, from_currency: str, to_currency: str, fx: Sequence[BaselineFx]) -> Converted:
    """`amount` in `to_currency`, with the buyer-entered rate for the pair (in either direction). Antova never
    fetches or assumes a rate: without one, the amount is None with the reason."""
    if from_currency == to_currency:
        return Converted(amount, "")
    found = buyer_rate(from_currency, to_currency, fx)
    if found is None:
        return Converted(None, "", no_rate_reason(from_currency, to_currency))
    rate, op = found
    value = amount * rate.rate if op == "×" else amount / rate.rate
    pair = f"{rate.from_currency}→{rate.to_currency}"
    return Converted(value, f"{from_currency} {_amount_text(amount)} {op} {_dec_text(rate.rate)} "
                            f"({rate.dated.isoformat()}, {pair}) = {_cents(to_currency, value)}")


# --- the checks -------------------------------------------------------------------------------------

def _label(f: FieldDef) -> str:
    """The field label inside a sentence: "warranty months", but "LD rate" keeps its capitals."""
    return f.label if f.label[1:2].isupper() else f.label[0].lower() + f.label[1:]


def _conflict(term: ContractTerm, f: FieldDef, source: SourceName) -> str | None:
    """The conflict note on this field in that source, matched on the flag's "field" and "source" keys only
    (ruling R27): the message is shown, never read."""
    for note in term.flags:
        if note["code"] in _CONFLICT_CODES and note.get("field") == f.name and note.get("source") == source:
            return note["message"]
    return None


def _no_offer(term: ContractTerm, f: FieldDef) -> str:
    """Why the offer has no value for the field."""
    conflict = _conflict(term, f, "offered")
    if conflict:
        return conflict
    if f.name == "liability_cap_money" and term.offered is not None and "liability_cap" in term.offered.values:
        return (f"the offer states the liability cap as {term.offered.values['liability_cap'].display} of its price, "
                f"not as an amount; an amount needs the order value")
    unread = [note["message"] for note in term.flags if note["code"] == "document_not_read"]
    if unread:
        return f"the {_label(f)} offered is not known: " + " ".join(unread)
    return f"the offer does not state the {_label(f)}"


def _op(operator: str, offered: Decimal, required: Decimal) -> bool:
    return {"ge": offered >= required, "le": offered <= required}.get(operator, offered == required)


def _compare(f: FieldDef, baseline: Baseline, required: FieldValue, offered: FieldValue,
             req_source: TermSource, off_source: TermSource,
             fx: Sequence[BaselineFx]) -> tuple[bool | None, str, str]:
    """(passed, offered as shown, reason when not compared)."""
    if citation_only(f):
        return None, offered.display, NOT_COMPARABLE
    base = _BASELINE_NAME[baseline]
    if baseline == "owner" and f.kind in ("percent", "rate_per_period"):
        return None, offered.display, (
            f"the owner contract's {_label(f)} is a percentage of the owner contract value and the offer's of its "
            f"own price; percentages of different contract values are not compared, they are compared in money")
    if f.kind == "money_cap":
        if offered.qualifier is None or required.qualifier is None:
            return None, offered.display, "the currency is not stated"
        converted = fx_convert(Decimal(offered.value), offered.qualifier, required.qualifier, fx)
        if converted.amount is None:
            return None, offered.display, converted.reason
        shown = converted.formula or offered.display  # the conversion starts from the offered amount
        return _op(f.operator, converted.amount, Decimal(required.value)), shown, ""
    if f.kind == "milestone_list":
        gap = _timing_gap(offered.value, required.value, base)
        if gap.value is None:
            return None, offered.display, gap.reason
        return gap.value >= 0, f"{offered.display} ({gap.formula})", ""  # paying later is better for the buyer
    if f.kind == "percent":
        bases = (offered.qualifier, required.qualifier)
        if all(b in _PERCENT_BASES for b in bases) and bases[0] != bases[1]:
            return None, offered.display, (f"the offer's {_label(f)} is a percentage of {_PERCENT_BASES[bases[0]]} "
                                           f"and {base}'s of {_PERCENT_BASES[bases[1]]}, so they are not compared")
        return _op(f.operator, Decimal(str(offered.value)), Decimal(str(required.value))), offered.display, ""
    if f.name == "warranty_months":
        starts = (off_source.values.get("warranty_start"), req_source.values.get("warranty_start"))
        if all(starts) and starts[0].value != starts[1].value:
            return None, offered.display, (f"the offer's warranty starts at {EVENT_LABELS[starts[0].value]} and "
                                           f"{base}'s at {EVENT_LABELS[starts[1].value]}; there are no dates to "
                                           f"bridge them")
        # Months from an unknown start are not comparable with months from a known one (as warranty_gap).
        if starts[0] is None and starts[1] is not None:
            return None, offered.display, (f"the offer does not state when its warranty starts, so its months are "
                                           f"not compared with {base}'s from {EVENT_LABELS[starts[1].value]}")
        if starts[1] is None and starts[0] is not None:
            return None, offered.display, (f"{base} does not state when its warranty starts, so its months are not "
                                           f"compared with the offer's from {EVENT_LABELS[starts[0].value]}")
    passed, shown = check(f, required, offered)
    return passed, shown, ""


def _check_field(term: ContractTerm, f: FieldDef, baseline: Baseline, source: TermSource,
                 fx: Sequence[BaselineFx]) -> TermCheck | None:
    required = source.values.get(f.name)
    offered_source = term.offered
    offered = offered_source.values.get(f.name) if offered_source is not None else None
    shown_offer = offered.display if offered is not None else "not stated"
    common = dict(param_id=term.param_id, vendor_id=term.vendor_id, baseline=baseline, field=f.name,
                  required_citations=source.citations,
                  offered_citations=offered_source.citations if offered_source is not None else ())
    if required is None:
        conflict = _conflict(term, f, baseline)
        if conflict is None:
            return None  # the baseline requires nothing here
        return TermCheck(**common, passed=None, required="unresolved", offered=shown_offer, reason=conflict)
    if offered is None:
        return TermCheck(**common, passed=None, required=required.display, offered=shown_offer,
                         reason=_no_offer(term, f))
    passed, shown, reason = _compare(f, baseline, required, offered, source, offered_source, fx)
    if f.name in offered_source.inherited_fields:
        shown = f"{shown} (inherited from the framework agreement)"
    return TermCheck(**common, passed=passed, required=required.display, offered=shown, reason=reason)


def check_terms(terms: Mapping[tuple[str, str], ContractTerm], fx: Sequence[BaselineFx]) -> list[TermCheck]:
    """Every field of every term against each baseline that states it: the FA first, then the owner contract,
    fields in ontology order."""
    checks: list[TermCheck] = []
    for term in terms.values():
        if term.param_id in _NOT_CHECKED:
            continue
        param = CONTRACT_PARAMS_BY_ID[term.param_id]
        for baseline, source in (("fa", term.fa), ("owner", term.owner)):
            if source is None:
                continue
            for f in param.fields:
                found = _check_field(term, f, baseline, source, fx)
                if found is not None:
                    checks.append(found)
    return checks


# --- warranty gap (CT-03, offered vs owner) ------------------------------------------------------------

def _field(param_id: str, name: str) -> FieldDef:
    return next(f for f in CONTRACT_PARAMS_BY_ID[param_id].fields if f.name == name)


def warranty_gap(offered: ContractTerm) -> TermGap:
    """The months of warranty the EPC owes the owner that the vendor does not give, from the same start event.
    A longer vendor warranty is a surplus, shown as 0. Different start events with no dates to bridge them
    are None, never 0."""
    term = offered
    months_field, start_field = _field("CT-03", "warranty_months"), _field("CT-03", "warranty_start")

    def unknown(reason: str) -> TermGap:
        return TermGap("warranty", None, "months", "", reason)

    vendor = term.offered.values if term.offered is not None else {}
    owner = term.owner.values if term.owner is not None else {}
    for f in (months_field, start_field):
        if f.name not in vendor:
            return unknown(_no_offer(term, f))
        if f.name not in owner:
            return unknown(_conflict(term, f, "owner") or f"the owner contract's {_label(f)} is not known")
    vendor_start, owner_start = vendor["warranty_start"].value, owner["warranty_start"].value
    if vendor_start != owner_start:
        return unknown(f"the vendor's warranty starts at {EVENT_LABELS[vendor_start]} and the owner warranty at "
                       f"{EVENT_LABELS[owner_start]}; there are no dates to bridge them")
    owed, given = Decimal(str(owner["warranty_months"].value)), Decimal(str(vendor["warranty_months"].value))
    start = EVENT_LABELS[owner_start]
    diff = owed - given
    said = f"owner {_dec_text(owed)} months from {start} − offer {_dec_text(given)} months from {start}"
    if diff >= 0:
        return TermGap("warranty", diff, "months", f"{said} = {_dec_text(diff)} months short")
    return TermGap("warranty", Decimal("0"), "months",
                   f"{said} = {_dec_text(-diff)} months surplus, so no shortfall (0)")


# --- payment timing (CT-07, offered vs FA) -------------------------------------------------------------

def _weighted(milestones: Sequence[Milestone]) -> tuple[Decimal, str, str]:
    """(weighted days after the event, the event, the sum written out). A payment at the event is 0 days
    after it (ruling R11)."""
    parts = [f"{_dec_text(m.percent)}% × {m.days_after} days after {EVENT_LABELS[m.event]}" if m.days_after is not None
             else f"{_dec_text(m.percent)}% at {EVENT_LABELS[m.event]} (0 days)" for m in milestones]
    days = sum((m.percent * (m.days_after or 0) for m in milestones), Decimal("0")) / HUNDRED
    return days, milestones[0].event, " + ".join(parts)


def _events(milestones: Sequence[Milestone]) -> list[str]:
    return list(dict.fromkeys(m.event for m in milestones))


def _timing_gap(offered: Sequence[Milestone], required: Sequence[Milestone], base: str) -> TermGap:
    """The offer's weighted days to payment less the required side's, after the one event both count from.
    Positive: the offer pays later."""
    def unknown(reason: str) -> TermGap:
        return TermGap("payment_timing", None, "days", "", reason)

    if not offered or not required:
        return unknown("a payment schedule has no milestones")
    labelled = lambda events: ", ".join(EVENT_LABELS[e] for e in events)  # noqa: E731
    for who, ms in (("the offer", offered), (base, required)):
        if len(_events(ms)) > 1:
            return unknown(f"{who}'s milestones are tied to more than one event ({labelled(_events(ms))}); "
                           f"there are no dates to bridge them")
    off_days, off_event, off_sum = _weighted(offered)
    req_days, req_event, req_sum = _weighted(required)
    if off_event != req_event:
        return unknown(f"the offer's milestones are tied to {EVENT_LABELS[off_event]} and {base}'s to "
                       f"{EVENT_LABELS[req_event]}; there are no dates to bridge them")
    diff = off_days - req_days
    direction = "later" if diff > 0 else "earlier" if diff < 0 else "the same"
    formula = (f"the offer: {off_sum} = {_dec_text(off_days)} days; {base}: {req_sum} = {_dec_text(req_days)} days; "
               f"{_dec_text(off_days)} − {_dec_text(req_days)} = {_dec_text(diff)} days ({direction})")
    return TermGap("payment_timing", diff, "days", formula)


def payment_timing(offered: ContractTerm) -> TermGap:
    """How many days later (positive) or earlier (negative) the offer pays on average than the FA, weighted by
    each milestone's percentage. Milestones tied to different events, or no FA, are None with the reason."""
    term, f = offered, _field("CT-07", "milestones")
    if term.offered is None or "milestones" not in term.offered.values:
        return TermGap("payment_timing", None, "days", "", _no_offer(term, f))
    if not term.fa_present:
        return TermGap("payment_timing", None, "days", "", "no framework agreement is linked to this vendor")
    if term.fa is None or "milestones" not in term.fa.values:
        reason = _conflict(term, f, "fa") or "the framework agreement states no payment milestones"
        unread = [note["message"] for note in term.flags if note["code"] == "document_not_read"]
        return TermGap("payment_timing", None, "days", "", " ".join([reason, *unread]))
    return _timing_gap(term.offered.values["milestones"].value, term.fa.values["milestones"].value,
                       _BASELINE_NAME["fa"])


# --- back-to-back LDs from the owner terms (CT-01) ------------------------------------------------------

@dataclass(frozen=True)
class OwnerLd:
    """The owner's LD rate per day and cap, in money of `currency`, and how they were worked out."""
    rate_per_day: Decimal | None
    cap: Decimal | None
    currency: str | None
    steps: tuple[str, ...]
    reason: str = ""


def owner_ld_money(owner_source: TermSource | None, owner: OwnerTerms | None) -> OwnerLd:
    """The owner LDs in money. Read from the owner contract's documents when there are any (percentages of
    the owner contract value), else from the owner terms the buyer entered (percent or money, per day or per
    week). A percentage is turned into money with `OwnerTerms.contract_value`, a weekly rate into a daily one
    by exact division by 7; each step is part of the formula. Money entered has the contract value's currency."""
    value = owner.contract_value if owner is not None else None
    currency = value.currency if value is not None else None
    if owner_source is not None and owner_source.kind == "document":
        rate_fv, cap_fv = owner_source.values.get("ld_rate"), owner_source.values.get("ld_cap")
        rate = (rate_fv.value[0], "percent_per_" + rate_fv.value[1]) if rate_fv is not None else None
        cap = (Decimal(str(cap_fv.value)), "percent") if cap_fv is not None else None
        if cap_fv is not None and cap_fv.qualifier == "delayed_portion":
            return OwnerLd(None, None, currency, (), "the owner LD cap is a percentage of the delayed portion, "
                                                     "whose value is not known")
    elif owner is not None:
        rate = (owner.ld_rate, owner.ld_rate_basis) if owner.ld_rate is not None else None
        cap = (owner.ld_cap, owner.ld_cap_basis) if owner.ld_cap is not None else None
    else:
        return OwnerLd(None, None, None, (), "no owner contract terms are entered or cited")
    missing = [name for name, v in (("the owner LD rate", rate), ("the owner LD cap", cap)) if v is None]
    if missing:
        return OwnerLd(None, None, currency, (), "not known: " + ", ".join(missing))
    if value is None:
        return OwnerLd(None, None, None, (), "the owner contract value is not entered, so the owner LDs cannot be "
                                             "put in money (and their currency is not known)")
    shown_value = _cents(currency, value.amount)
    steps: list[str] = []
    number, basis = rate
    unit, period = basis.split("_per_")
    per_period = number * value.amount / HUNDRED if unit == "percent" else number
    if unit == "percent":
        steps.append(f"owner LD rate {_dec_text(number)}% per {period} × {shown_value} = "
                     f"{_cents(currency, per_period)} per {period}")
    per_day = per_period / 7 if period == "week" else per_period
    if period == "week":
        steps.append(f"{_cents(currency, per_period)} per week ÷ 7 = {_cents(currency, per_day)} per day")
    number, basis = cap
    cap_money = number * value.amount / HUNDRED if basis == "percent" else number
    if basis == "percent":
        steps.append(f"owner LD cap {_dec_text(number)}% × {shown_value} = {_cents(currency, cap_money)}")
    return OwnerLd(per_day, cap_money, currency, tuple(steps))


def _delayed_rate_reason(source: TermSource) -> str:
    """Why an LD rate of the delayed portion gives no gap, quoting the clause that states it."""
    clause = next((c for c in (delayed_rate_clause(cite.quote) for cite in source.citations) if c), None)
    quoted = f" ('{clause}')" if clause else ""
    return f"the LD rate applies to the delayed portion only; the delayed value is not known{quoted}"


def back_to_back_ld(term: ContractTerm, owner: OwnerTerms | None, po_value: tuple[Decimal, str] | None,
                    delay_weeks, fx: Sequence[BaselineFx] = ()) -> Exposure:
    """`exposure.back_to_back_ld_gap` for one vendor's CT-01, with the owner LDs put in money and in the
    order's currency, and the delay scenario (weeks, spec 4.1) turned into days (× 7, ruling R21)."""
    kind = "back_to_back_ld"
    owner_rate_fv = (term.owner.values.get("ld_rate")
                     if term.owner is not None and term.owner.kind == "document" else None)
    if owner_rate_fv is not None and owner_rate_fv.qualifier == "delayed_portion":
        return _unknown(kind, _delayed_rate_reason(term.owner))
    money = owner_ld_money(term.owner, owner)
    if money.reason:
        return _unknown(kind, money.reason)
    offered = term.offered.values if term.offered is not None else {}
    rate, cap = offered.get("ld_rate"), offered.get("ld_cap")
    # A rate or cap whose passages disagree is unknown, whatever else the offer says (R27 keys).
    for f in (_field("CT-01", "ld_rate"), _field("CT-01", "ld_cap")):
        conflict = _conflict(term, f, "offered")
        if conflict:
            return _unknown(kind, conflict)
    # Every clause that excludes or waives LDs, in the offer's cited quotes and in its passages kept for their
    # scope (ContractTerm.ld_exclusion_passages, ruling R30), classified as ruling R29 sets out.
    quotes = [*(c.quote for c in (term.offered.citations if term.offered is not None else ())),
              *(c.quote for c in term.ld_exclusion_passages)]
    exclusions = list({e.clause: e for q in quotes for e in ld_exclusions(q)}.values())
    # An exclusion of LDs from the whole offer: the vendor pays back nothing. In a quote it reads as a 0% cap
    # with base "none"; a passage kept for its scope gives no value. Beside a stated LD rate or cap, the two
    # disagree and a person decides. A rate that is only not stated is not this.
    whole = next((e for e in exclusions if e.scope == "whole"), None)
    stated = rate is not None or (cap is not None and cap.qualifier != "none")
    if whole is not None and stated:
        return _unknown(kind, f"the offer excludes liquidated damages ('{whole.clause}') but an LD rate or cap is "
                              f"also stated for it; a person decides which governs")
    no_lds = (rate is None and cap is not None and cap.qualifier == "none") or (whole is not None and not stated)
    if cap is not None and cap.qualifier == "delayed_portion":
        return _unknown(kind, "the vendor's LD cap is a percentage of the delayed portion, whose value is not known")
    if rate is not None and rate.qualifier == "delayed_portion":
        return _unknown(kind, _delayed_rate_reason(term.offered))
    steps = list(money.steps)
    owner_rate, owner_cap, currency = money.rate_per_day, money.cap, money.currency
    if po_value is not None and po_value[1] != money.currency:
        rate_c, cap_c = (fx_convert(v, money.currency, po_value[1], fx) for v in (owner_rate, owner_cap))
        if rate_c.amount is None:
            return _unknown(kind, rate_c.reason)
        owner_rate, owner_cap, currency = rate_c.amount, cap_c.amount, po_value[1]
        steps += [f"owner LD rate per day: {rate_c.formula}", f"owner LD cap: {cap_c.formula}"]
    weeks = None if delay_weeks is None else Decimal(str(delay_weeks))
    gap = back_to_back_ld_gap(owner_rate, owner_cap, owner.package_ld_share_pct if owner else None,
                              rate.value if rate is not None else None,
                              Decimal(str(cap.value)) if cap is not None else None,
                              po_value[0] if po_value is not None else None,
                              weeks * 7 if weeks is not None else None,
                              delay_weeks=weeks, currency=currency, conversions=tuple(steps),
                              vendor_provides_no_lds=no_lds)
    if gap.amount is None:
        return gap
    # An exclusion of LDs from part of the offer (rulings R29, R30). One scoped to force majeure
    # or to delays the Buyer causes leaves the gap as computed: the delay scenario is the vendor's delay, and the
    # formula says so. Any other scope (some weeks, a grace period, part of the supply) changes what the vendor
    # pays back in a way the values do not show, so no amount is given.
    limited = next((e for e in exclusions if e.scope == "other"), None)
    if limited is not None:
        return _unknown(kind, f"the offer limits its liquidated damages ('{limited.clause}'); check the scope by hand")
    causes = tuple(dict.fromkeys(c for e in exclusions if e.scope == "cause" for c in e.causes))
    if causes:
        scope = " and ".join(f"'{c}'" for c in causes)
        gap = replace(gap, formula=f"{gap.formula}; assumes the delay is the vendor's (the offer excludes LDs for {scope})")
    return gap
