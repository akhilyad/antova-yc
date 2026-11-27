"""The contract findings: what the contract approval sheet must put in front of a person, with its money.

Built from the contract terms (contract_terms), their checks (contract_checks), the warranty and payment-timing
gaps, the back-to-back exposures and the budget lines (budget). Nothing here reads a document or calls a model;
a message is template text with the values filled in.

Four categories:

- fa_conformance: an offered value that fails the framework agreement, or one that cannot be compared with it.
- back_to_back: an offered value that fails the owner contract, or a back-to-back exposure (LDs, liability) that
  is above zero or cannot be worked out. The owner's LD and liability percentages are of the owner contract value,
  so they are compared in money (the exposure), never as a check.
- missing: the offer does not state a term on a HIGH parameter, or a field a baseline requires.
- budget: the offered price (or the true job cost) is over the frozen budget, or the variance is not known (a
  frozen budget not entered included, spec 4.4).

A passing check, a zero gap and a citation-only field (compared by hand, values.NOT_COMPARABLE) are not findings.
A baseline a vendor does not have is listed in `not_checked`, never silently skipped.

Ids are stable: f"{vendor_id}|{param_id}|{baseline}|{field}" (a missing term: baseline "offer", field "term"; an
exposure: baseline "owner", field the exposure kind), and f"{vendor_id}|budget|{label}". `evidence_hash` is the
sha256 of the canonical JSON of the finding's values and citations, so a disposition recorded against it goes stale
when a cited value changes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal

import antova_engine as engine
from antova_ingest import artifacts
from antova_ingest.budget import BudgetLine, budget_lines, price_basis
from antova_ingest.contract_checks import (_NOT_CHECKED, TermCheck, TermGap, _cents, _conflict, back_to_back_ld,
                                           check_terms, fx_convert, owner_ld_money, payment_timing, warranty_gap)
from antova_ingest.contract_ontology import CONTRACT_PARAMETERS, CONTRACT_PARAMS_BY_ID
from antova_ingest.contract_terms import ContractTerm, resolve_contract_terms
from antova_ingest.exposure import Exposure, _euros, _unknown, liability_back_to_back
from antova_ingest.findings import Money
from antova_ingest.ontology import FieldDef
from antova_ingest.types import Citation, Evidence, ProjectBaseline, TenderManifest
from antova_ingest.values import NOT_COMPARABLE, PriceRead, _dec_text, citation_only

EMPTY_BODY = {"terms": [], "checks": [], "gaps": [], "budget": {}, "findings": [], "not_checked": []}
NO_FA = "no framework agreement provided"
NO_OWNER = "no owner contract terms provided"
OWNER_SILENT = "the owner contract states none of these terms"  # read, and silent on every term (ruling R35)
_BASELINE_NAME = {"fa": "the framework agreement", "owner": "the owner contract"}
_CATEGORY = {"fa": "fa_conformance", "owner": "back_to_back"}
# The owner fields compared in money by an exposure, not by a check (contract_checks, spec 9.2).
_IN_MONEY = {"CT-01": frozenset({"ld_rate", "ld_cap"}), "CT-02": frozenset({"liability_cap", "liability_cap_money"})}
# Which check a gap belongs to: (param_id, baseline, field).
_GAP_OF = {"warranty": ("CT-03", "owner", "warranty_months"), "payment_timing": ("CT-07", "fa", "milestones")}
_VARIANCE_WHAT = {"variance": "offered price", "variance_with_options": "offered price with options",
                  "variance_true_job_cost": "true job cost"}
_ORDER = {p.id: i for i, p in enumerate(CONTRACT_PARAMETERS)}


@dataclass(frozen=True)
class TermExposure:
    """A back-to-back exposure of one vendor's term, in `currency`, with the passages it was worked out from."""
    vendor_id: str
    param_id: str
    exposure: Exposure
    currency: str | None
    citations: tuple[Citation, ...] = ()


# --- helpers ---------------------------------------------------------------------------------------------

def _field(param_id: str, name: str) -> FieldDef:
    return next(f for f in CONTRACT_PARAMS_BY_ID[param_id].fields if f.name == name)


def _label(f: FieldDef) -> str:
    return f.label if f.label[1:2].isupper() else f.label[0].lower() + f.label[1:]


def _sentence(text: str) -> str:
    return text.rstrip(". ")


def _cites(*groups: Sequence[Citation]) -> list[dict]:
    return [c.to_dict() for c in dict.fromkeys(c for group in groups for c in group)]


def _money(basis: str, kind: str, amount: Decimal | None, currency: str | None, formula: str, reason: str) -> dict:
    """A findings.Money, with the currency its amount is in (contract money is not always euros)."""
    shown = None if amount is None else f"{_euros(amount):.2f}"
    return {**artifacts._plain(asdict(Money(basis, kind, shown, formula, reason))),
            "currency": currency if amount is not None else None}


def _finding(id_: str, vendor_id: str, param_id: str | None, category: str, severity: str, message: str,
             money: list[dict], gap: TermGap | None, citations: list[dict], values: dict) -> dict:
    gap_plain = artifacts._plain(gap) if gap is not None else None
    hashed = {"values": {**values, "money": [(m["amount"], m["currency"], m["reason"]) for m in money],
                         "gap": None if gap_plain is None else (gap_plain["value"], gap_plain["reason"])},
              "citations": citations}
    return {"id": id_, "vendor_id": vendor_id, "param_id": param_id, "category": category, "severity": severity,
            "message": message, "money": money, "gap": gap_plain, "citations": citations,
            "evidence_hash": artifacts.sha256_of(hashed)}


def _severity(param_id: str) -> str:
    return "critical" if CONTRACT_PARAMS_BY_ID[param_id].criticality == engine.Criticality.HIGH else "medium"


def _unread(term: ContractTerm, role: str) -> list[str]:
    return [f["file"] for f in term.flags if f["code"] == "document_not_read" and f.get("role") == role]


# --- the findings ------------------------------------------------------------------------------------------

def _check_finding(check: TermCheck, term: ContractTerm, gap: TermGap | None) -> dict | None:
    param = CONTRACT_PARAMS_BY_ID[check.param_id]
    f = _field(check.param_id, check.field)
    if check.passed is True or (citation_only(f) and check.reason == NOT_COMPARABLE):
        return None  # a citation-only clause both sides state is compared by hand; any other unknown is shown
    base = _BASELINE_NAME[check.baseline]
    offered_values = term.offered.values if term.offered is not None else {}
    stated = check.field in offered_values
    if check.passed is False:
        category = _CATEGORY[check.baseline]
        message = f"{param.name}: {_label(f)} offered {check.offered}; {base} requires {check.required}."
    elif not stated and _conflict(term, f, "offered") is None:
        category = "missing"
        message = f"{param.name}: {base} requires {_label(f)} {check.required}, and {_sentence(check.reason)}."
    else:
        category = _CATEGORY[check.baseline]
        message = f"{param.name}: {_label(f)} not compared with {base}: {_sentence(check.reason)}."
    values = {"required": check.required, "offered": check.offered, "passed": check.passed, "reason": check.reason}
    return _finding(f"{check.vendor_id}|{check.param_id}|{check.baseline}|{check.field}", check.vendor_id,
                    check.param_id, category, _severity(check.param_id), message, [], gap,
                    _cites(check.offered_citations, check.required_citations), values)


def _missing_finding(term: ContractTerm, required: bool, gap: TermGap | None) -> dict | None:
    """A term the offer does not state: on a HIGH parameter always, otherwise when a baseline requires it."""
    param = CONTRACT_PARAMS_BY_ID[term.param_id]
    if term.offered is not None or term.state != "missing" or term.param_id in _NOT_CHECKED:
        return None
    if param.criticality != engine.Criticality.HIGH and not required:
        return None
    unread = _unread(term, "vendor_bid")
    message = (f"{param.name}: not known, {', '.join(unread)} was not read." if unread
               else f"{param.name}: not stated in the offer.")
    baselines = [s.citations for s in (term.fa, term.owner) if s is not None]
    return _finding(f"{term.vendor_id}|{term.param_id}|offer|term", term.vendor_id, term.param_id, "missing",
                    _severity(term.param_id), message, [], gap, _cites(*baselines),
                    {"state": term.state, "unread": unread})


def _exposure_finding(item: TermExposure) -> dict | None:
    e = item.exposure
    if e.amount is not None and e.amount <= 0:
        return None  # the vendor carries what is owed to the owner
    param = CONTRACT_PARAMS_BY_ID[item.param_id]
    if e.amount is None:
        message = f"{param.name}: the back-to-back gap cannot be worked out: {_sentence(e.reason)}."
    else:
        message = (f"{param.name}: the vendor carries {_cents(item.currency, e.amount)} less than the owner "
                   f"contract requires.")
    money = [_money("exposure", e.kind, e.amount, item.currency, e.formula, e.reason)]
    return _finding(f"{item.vendor_id}|{item.param_id}|owner|{e.kind}", item.vendor_id, item.param_id,
                    "back_to_back", _severity(item.param_id), message, money, None, _cites(item.citations), {})


def _budget_findings(vendor_id: str, lines: Sequence[BudgetLine], citations: Sequence[Citation]) -> list[dict]:
    found = []
    for line in lines:
        if line.label not in _VARIANCE_WHAT:
            continue
        money = [_money("exposure", f"budget_{line.label}", line.amount, line.currency, line.formula, line.reason)]
        if line.amount is not None and line.amount > 0:
            severity = "high"
            message = (f"The {_VARIANCE_WHAT[line.label]} is {_cents(line.currency, line.amount)} over the frozen "
                       f"budget.")
        elif line.amount is None and line.label != "variance_true_job_cost":
            # The true job cost line says on the sheet when it is not known; a finding would only repeat it.
            severity, message = "medium", f"Budget variance not known: {_sentence(line.reason)}."
        else:
            continue
        found.append(_finding(f"{vendor_id}|budget|{line.label}", vendor_id, None, "budget", severity, message,
                              money, None, _cites(citations), {}))
    return found


def _not_checked(vendor_id: str, terms: Sequence[ContractTerm], has_exposure: bool,
                 owner_documents: bool) -> list[dict]:
    entries = []
    fa_unread = list(dict.fromkeys(file for t in terms for file in _unread(t, "framework_agreement")))
    if not any(t.fa_present for t in terms):
        entries.append({"vendor_id": vendor_id, "baseline": "fa", "reason": NO_FA})
    elif fa_unread:
        entries.append({"vendor_id": vendor_id, "baseline": "fa",
                        "reason": f"the framework agreement was not read ({', '.join(fa_unread)})"})
    owner_unread = list(dict.fromkeys(file for t in terms for file in _unread(t, "owner_contract")))
    if owner_unread:  # even beside buyer-entered owner terms: what the contract states is unknown
        entries.append({"vendor_id": vendor_id, "baseline": "owner",
                        "reason": f"the owner contract was not read ({', '.join(owner_unread)})"})
    elif not any(t.owner is not None for t in terms) and not has_exposure:
        entries.append({"vendor_id": vendor_id, "baseline": "owner",
                        "reason": OWNER_SILENT if owner_documents else NO_OWNER})
    return entries


def build_contract_findings(terms: Mapping[tuple[str, str], ContractTerm], checks: Sequence[TermCheck],
                            gaps: Mapping[tuple[str, str], TermGap], exposures: Sequence[TermExposure],
                            budget: Mapping[str, Sequence[BudgetLine]], *,
                            budget_citations: Mapping[str, Sequence[Citation]] | None = None,
                            owner_documents: bool = False) -> tuple[list[dict], list[dict]]:
    """(findings, not_checked), ordered by vendor, then term in ontology order, then baseline; budget last.

    `terms` keyed (vendor_id, param_id); `gaps` keyed (vendor_id, param_id) (CT-03 warranty, CT-07 payment timing);
    `budget` keyed vendor_id, with `budget_citations` the passages its price comes from. `owner_documents`: the
    tender has owner contract documents, so an owner side with no term is a contract that states none of them."""
    budget_citations = budget_citations or {}
    vendors = list(dict.fromkeys([*(v for v, _ in terms), *budget]))
    exposed = {(e.vendor_id, e.param_id) for e in exposures}
    checked = {(c.vendor_id, c.param_id) for c in checks}

    def gap_for(vendor_id: str, param_id: str, baseline: str, field: str) -> TermGap | None:
        gap = gaps.get((vendor_id, param_id))
        return gap if gap is not None and _GAP_OF.get(gap.kind) == (param_id, baseline, field) else None

    findings: list[dict] = []
    for check in checks:
        term = terms[(check.vendor_id, check.param_id)]
        if term.state == "missing" and term.offered is None:
            continue  # the term's own missing finding
        if (check.baseline == "owner" and check.passed is None and check.field in _IN_MONEY.get(check.param_id, ())
                and (check.vendor_id, check.param_id) in exposed):
            continue  # compared in money: the exposure's finding
        found = _check_finding(check, term, gap_for(check.vendor_id, check.param_id, check.baseline, check.field))
        if found is not None:
            findings.append(found)
    for (vendor_id, param_id), term in terms.items():
        gap = gaps.get((vendor_id, param_id))
        found = _missing_finding(term, (vendor_id, param_id) in checked, gap)
        if found is not None:
            findings.append(found)
    for item in exposures:
        term = terms.get((item.vendor_id, item.param_id))
        if item.exposure.amount is None and term is not None and term.state == "missing" and term.offered is None:
            continue  # the term's own missing finding says why
        found = _exposure_finding(item)
        if found is not None:
            findings.append(found)
    for vendor_id in vendors:
        findings.extend(_budget_findings(vendor_id, budget.get(vendor_id, ()), budget_citations.get(vendor_id, ())))

    rank = {v: i for i, v in enumerate(vendors)}
    baseline_rank = {"fa": 0, "owner": 1, "offer": 2}

    def order(f: dict):
        _, param_id, *rest = f["id"].split("|")
        if param_id == "budget":
            return rank[f["vendor_id"]], len(_ORDER), 0, rest[0]
        return rank[f["vendor_id"]], _ORDER[param_id], baseline_rank[rest[0]], rest[1]

    findings.sort(key=order)
    ids = [f["id"] for f in findings]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate finding ids: {sorted(i for i in set(ids) if ids.count(i) > 1)}")
    not_checked = [entry for v in vendors for entry in _not_checked(
        v, [t for (vid, _), t in terms.items() if vid == v], any(vid == v for vid, _ in exposed), owner_documents)]
    return findings, not_checked


# --- the exposures -----------------------------------------------------------------------------------------

def _ld_exposure(term: ContractTerm, project: ProjectBaseline, po_value) -> TermExposure | None:
    owner = project.owner_terms
    owner_values = term.owner.values if term.owner is not None else {}
    if not ({"ld_rate", "ld_cap"} & set(owner_values)
            or (owner is not None and (owner.ld_rate is not None or owner.ld_cap is not None))):
        return None  # the owner requires no LDs: nothing to pass back
    weeks = project.delay_scenario_weeks.value if project.delay_scenario_weeks is not None else None
    exposure = back_to_back_ld(term, owner, po_value, weeks, project.fx)
    currency = po_value[1] if po_value is not None else owner_ld_money(term.owner, owner).currency
    cited = (term.offered.citations if term.offered is not None else (), term.ld_exclusion_passages,
             term.owner.citations if term.owner is not None else ())
    return TermExposure(term.vendor_id, term.param_id, exposure, currency, tuple(c for g in cited for c in g))


def _liability_exposure(term: ContractTerm, project: ProjectBaseline, po_value) -> TermExposure | None:
    """The owner liability cap the vendor's cap does not carry. An offer cap as a percentage of its order is put in
    money with the order value; an offer cap as an amount is compared as it stands. An owner amount against an
    offer amount is left to the money_cap check (both are amounts)."""
    kind = "back_to_back_liability"
    owner_values = term.owner.values if term.owner is not None else {}
    offered = term.offered.values if term.offered is not None else {}
    offer_money = offered.get("liability_cap_money")
    if (not {"liability_cap", "liability_cap_money"} & set(owner_values)
            or (offer_money is not None and "liability_cap_money" in owner_values)):
        return None
    cited = tuple(dict.fromkeys((*(term.offered.citations if term.offered is not None else ()),
                                 *term.owner.citations)))
    steps: list[str] = []
    if "liability_cap_money" in owner_values:
        money = owner_values["liability_cap_money"]
        amount, currency = Decimal(str(money.value)), money.qualifier
    else:
        value = project.owner_terms.contract_value if project.owner_terms is not None else None
        pct = Decimal(str(owner_values["liability_cap"].value))
        if value is None:
            return TermExposure(term.vendor_id, term.param_id, _unknown(kind, (
                "the owner contract value is not entered, so the owner liability cap cannot be put in money")),
                None, cited)
        amount, currency = pct * value.amount / Decimal("100"), value.currency
        steps.append(f"owner liability cap {_dec_text(pct)}% × {_cents(currency, value.amount)} = "
                     f"{_cents(currency, amount)}")
    if currency is None:
        return TermExposure(term.vendor_id, term.param_id, _unknown(kind, "the owner liability cap has no currency"),
                            None, cited)
    if offer_money is not None:
        return TermExposure(term.vendor_id, term.param_id,
                            _amount_gap(kind, amount, currency, offer_money, project.fx, steps), offer_money.qualifier,
                            cited)
    if po_value is not None and po_value[1] != currency:
        converted = fx_convert(amount, currency, po_value[1], project.fx)
        if converted.amount is None:
            return TermExposure(term.vendor_id, term.param_id, _unknown(kind, converted.reason), None, cited)
        steps.append(f"owner liability cap: {converted.formula}")
        amount, currency = converted.amount, po_value[1]
    cap = offered.get("liability_cap")
    conflict = _conflict(term, _field("CT-02", "liability_cap"), "offered")
    if conflict:
        return TermExposure(term.vendor_id, term.param_id, _unknown(kind, conflict), currency, cited)
    exposure = liability_back_to_back(amount, Decimal(str(cap.value)) if cap is not None else None,
                                      po_value[0] if po_value is not None else None, currency=currency,
                                      conversions=tuple(steps))
    return TermExposure(term.vendor_id, term.param_id, exposure, currency, cited)


def _amount_gap(kind: str, owner_amount: Decimal, currency: str, offer_money, fx, steps: list[str]) -> Exposure:
    """Owner amount less the offer's cap amount, in the offer's currency, both rounded to the cent first."""
    to = offer_money.qualifier
    if to is None:
        return _unknown(kind, "the offer's liability cap amount has no currency")
    if to != currency:
        converted = fx_convert(owner_amount, currency, to, fx)
        if converted.amount is None:
            return _unknown(kind, converted.reason)
        steps.append(f"owner liability cap: {converted.formula}")
        owner_amount = converted.amount
    owner, carried = _euros(owner_amount), _euros(Decimal(str(offer_money.value)))
    gap = max(owner - carried, Decimal("0.00"))
    formula = "; ".join([*steps, f"gap max(owner cap {_cents(to, owner)} − vendor cap {_cents(to, carried)}, 0) = "
                                 f"{_cents(to, gap)}"])
    return Exposure(kind, gap, formula)


# --- one tender ----------------------------------------------------------------------------------------------

def assess(manifest: TenderManifest, evidence: Mapping[str, list[Evidence]],
           prices: Mapping[str, tuple[PriceRead | None, Citation | None]],
           true_job_costs: Mapping[str, tuple[Decimal | None, str | None]]) -> dict:
    """The contracted stage's body for one tender, as plain JSON.

    `evidence`: file -> verified contract evidence (contract_terms.contract_evidence); a file missing was not read.
    `prices`: vendor_id -> the headline price as read and its citation. `true_job_costs`: vendor_id -> the true job
    cost and its currency, (None, None) when not known. No option line is selected in v1."""
    project = manifest.project or ProjectBaseline()
    terms = resolve_contract_terms(manifest, evidence)
    checks = check_terms(terms, project.fx)
    vendors = list(dict.fromkeys(v for v, _ in terms))
    gaps: dict[tuple[str, str], TermGap] = {}
    exposures: list[TermExposure] = []
    budget: dict[str, list[BudgetLine]] = {}
    budget_citations: dict[str, tuple[Citation, ...]] = {}
    for vendor_id in vendors:
        gaps[(vendor_id, "CT-03")] = warranty_gap(terms[(vendor_id, "CT-03")])
        gaps[(vendor_id, "CT-07")] = payment_timing(terms[(vendor_id, "CT-07")])
        headline, headline_citation = prices.get(vendor_id, (None, None))
        price = price_basis(terms[(vendor_id, "CT-13")], headline)
        po_value = (price.lump_sum, price.currency) if price.lump_sum is not None and price.currency else None
        for build in (_ld_exposure, _liability_exposure):
            param_id = "CT-01" if build is _ld_exposure else "CT-02"
            item = build(terms[(vendor_id, param_id)], project, po_value)
            if item is not None:
                exposures.append(item)
        tjc, tjc_currency = true_job_costs.get(vendor_id, (None, None))
        budget[vendor_id] = budget_lines(price, tjc, tjc_currency, project.frozen_budget, project.fx)
        budget_citations[vendor_id] = tuple(dict.fromkeys(
            (*((headline_citation,) if headline_citation is not None else ()), *price.citations)))
    owner_documents = any(d.role == "owner_contract" for d in manifest.documents)
    findings, not_checked = build_contract_findings(terms, checks, gaps, exposures, budget,
                                                    budget_citations=budget_citations,
                                                    owner_documents=owner_documents)
    return artifacts._plain({
        "terms": list(terms.values()),
        "checks": checks,
        "gaps": [{"vendor_id": v, "param_id": p, **asdict(g)} for (v, p), g in gaps.items()],
        "budget": budget,
        "findings": findings,
        "not_checked": not_checked,
    })
