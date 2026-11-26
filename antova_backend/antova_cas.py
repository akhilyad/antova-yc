"""The contract approval sheet (CAS) of one tender and vendor (spec 4.5, 4.6).

Assembled in code from what is stored: the contracted stage body (terms, gaps, budget lines, findings), the
reviewer's dispositions, the approver rules and the recorded removals. No model output is written here.

Every field is {"label", "state", "display", "citations", "formula", "reason"} in exactly one state:
- cited: the offer states it; `citations` hold the quote and page. A citation-only field (values.CITATION_ONLY)
  shows its quote verbatim and is never interpreted.
- inherited: the offer is silent and the field comes from the framework agreement through an incorporation clause
  (TermSource.inherited_fields, never TermSource.kind); `citations` hold the incorporation clause and the FA clause.
- computed: worked out in code (a budget line, a gap, a count) or entered by the buyer; `formula` says how.
- missing: not known; `reason` says what is needed. `display` is never "0" or blank.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from antova_engine import ComplianceStatus
from antova_ingest import approvers as approver_rules
from antova_ingest.contract_checks import _cents
from antova_ingest.contract_ontology import CONTRACT_PARAMS_BY_ID
from antova_ingest.types import ProjectBaseline
from antova_ingest import schedule
from antova_ingest.ontology import EXTENDED_PARAMETERS, PARAMS_BY_ID
from antova_ingest.values import CITATION_ONLY, parse_field
from antova_review import open_items, review_state

CAS_KEYS = ("project", "law", "price", "delivery", "payment_bonds", "lds", "warranty", "risks", "technical",
            "approvers", "meta")
FIELD_KEYS = ("label", "state", "display", "citations", "formula", "reason")
STATES = ("cited", "computed", "inherited", "missing")
RISK_GROUPS = ("technical", "fa_conformance", "back_to_back", "budget", "missing")
NOT_KNOWN = "not known"
NOT_STATED = "not stated in the offer"
BUYER_ENTERED = "entered by the buyer"
CONFLICT_CODES = frozenset({"passages_disagree", "amendment_order_unknown"})
ENGINEERING = "engineering"
_TBE_RISK = {ComplianceStatus.DEVIATION_DOWNGRADE.value, ComplianceStatus.CONFLICT_DOWNGRADE.value,
             ComplianceStatus.OMITTED.value, ComplianceStatus.MANUAL_INSPECTION_REQUIRED.value}


def _field(label: str, state: str, display: str | None = None, *, citations: Iterable[dict] = (),
           formula: str = "", reason: str = "") -> dict:
    if state not in STATES:
        raise ValueError(f"unknown CAS field state {state!r}")
    if state != "missing" and (display is None or not str(display).strip()):
        # A cited, computed or inherited field always shows its value; without one it is not known.
        state, reason = "missing", f"the stored {state} value has no text to show"
    if state == "missing":
        return {"label": label, "state": state, "display": NOT_KNOWN, "citations": list(citations),
                "formula": "", "reason": reason or NOT_KNOWN}
    return {"label": label, "state": state, "display": str(display),
            "citations": list(citations), "formula": formula, "reason": ""}


def _missing(label: str, reason: str, citations: Iterable[dict] = ()) -> dict:
    return _field(label, "missing", reason=reason, citations=citations)


def _buyer(label: str, value: Any, what: str) -> dict:
    if value is None:
        return _missing(label, f"{what} not entered")
    return _field(label, "computed", str(value), formula=BUYER_ENTERED)


# --- contract terms ---------------------------------------------------------------------------------------

class _Terms:
    """The vendor's stored terms, and which citations are the offer's, the incorporation clause's or the FA's."""

    def __init__(self, terms: list[dict], documents: list[dict]):
        self.by_id = {t["param_id"]: t for t in terms}
        # The FA's files: those the manifest names, and those the terms' FA sides cite.
        self.fa_files = {d["filename"] for d in documents if d.get("role") == "framework_agreement"} | {
            c.get("filename") for t in terms for c in ((t.get("fa") or {}).get("citations") or [])}
        incorporation = self.by_id.get("CT-16", {}).get("offered") or {}
        self.incorporation = [c for c in incorporation.get("citations") or [] if c.get("filename") not in self.fa_files]

    def _inherited_citations(self, citations: list[dict]) -> list[dict]:
        return [c for c in citations if c.get("filename") in self.fa_files or c in self.incorporation]

    def field(self, param_id: str, name: str, source: str = "offered") -> dict:
        param = CONTRACT_PARAMS_BY_ID[param_id]
        label = next(f.label for f in param.fields if f.name == name)
        if source != "offered":
            label = f"{label} ({'framework agreement' if source == 'fa' else 'owner contract'})"
        term = self.by_id.get(param_id)
        if term is None:
            return _missing(label, f"{param.name} was not assessed for this vendor")
        manual = next((f for f in term.get("flags") or [] if f.get("code") == "manual_term"), None)
        if manual is not None:  # ruling R7: below the recall bar
            return _missing(label, manual.get("message") or NOT_KNOWN)
        side = term.get(source)
        value = (side or {}).get("values", {}).get(name)
        where = {"offered": "the offer", "fa": "the framework agreement", "owner": "the owner contract"}[source]
        conflict = next((f for f in term.get("flags") or [] if f.get("code") in CONFLICT_CODES
                         and f.get("source") == source and f.get("field") == name), None)  # R27: field and source
        if conflict is not None:
            return _missing(label, f"the documents of {where} disagree on this field; resolve the conflict in the "
                                   f"contract review. {conflict.get('message', '')}".strip())
        if value is None:
            return _missing(label, NOT_STATED if source == "offered" else f"not stated in {where}")
        display = value["value"] if name in CITATION_ONLY and isinstance(value.get("value"), str) else value["display"]
        if side["kind"] == "buyer_entered":
            return _field(label, "computed", display, formula=BUYER_ENTERED)
        citations = list(side.get("citations") or [])
        if name in set(side.get("inherited_fields") or ()):
            inherited = self._inherited_citations(citations) if side["kind"] != "inherited" else citations
            if len(inherited) < 2 or not any(c.get("filename") in self.fa_files for c in inherited):
                return _missing(label, "inherited from the framework agreement, but the incorporation clause and "
                                       "the agreement's clause are not both cited", inherited)
            return _field(label, "inherited", display, citations=inherited)
        if source == "offered":
            citations = [c for c in citations if c not in self._inherited_citations(citations)]
        if not citations:
            return _missing(label, f"{where} states this field, but its own citation is not stored")
        return _field(label, "cited", display, citations=citations)

    def fields(self, param_id: str, source: str = "offered", prefix: str = "") -> dict:
        return {f"{prefix}{f.name}": self.field(param_id, f.name, source) for f in CONTRACT_PARAMS_BY_ID[param_id].fields}


# --- money ------------------------------------------------------------------------------------------------

def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


decimal_or_none = _dec


def _budget_field(label: str, line: dict | None, citations: list[dict], missing_reason: str) -> dict:
    if line is None:
        return _missing(label, missing_reason)
    amount = _dec(line.get("amount"))
    if amount is None or not line.get("currency"):
        return _missing(label, line.get("reason") or NOT_KNOWN)
    return _field(label, "computed", _cents(line["currency"], amount), citations=citations, formula=line["formula"])


def _money_field(label: str, money: dict | None, citations: list[dict], missing_reason: str) -> dict:
    if money is None:
        return _missing(label, missing_reason)
    amount = _dec(money.get("amount"))
    if amount is None or not money.get("currency"):
        return _missing(label, money.get("reason") or NOT_KNOWN, citations)
    return _field(label, "computed", _cents(money["currency"], amount), citations=citations,
                  formula=money.get("formula") or "")


def _gap_field(label: str, gap: dict | None, missing_reason: str) -> dict:
    if gap is None:
        return _missing(label, missing_reason)
    value = _dec(gap.get("value"))
    if value is None:
        return _missing(label, gap.get("reason") or NOT_KNOWN)
    return _field(label, "computed", f"{value} {gap['unit']}", formula=gap.get("formula") or "")


def _finding(findings: list[dict], finding_id: str) -> dict | None:
    return next((f for f in findings if f["id"] == finding_id), None)


def _first_money(finding: dict | None) -> dict | None:
    return (finding.get("money") or [None])[0] if finding else None


# --- the sections -----------------------------------------------------------------------------------------

def _project(loaded, contract: Mapping, project: ProjectBaseline, terms: _Terms) -> dict:
    record = contract.get("project") or {}
    counterparty = contract.get("counterparty") or {}
    start = None
    if project.signing_before_ntp is not None:
        start = "signing, before notice to proceed" if project.signing_before_ntp else "notice to proceed"
    return {
        "project": _buyer("Project", record.get("name"), "the project"),
        "buyer_entity": _buyer("Buyer entity", record.get("buyer_entity"), "the buyer entity"),
        "seller_entity": _buyer("Seller entity", counterparty.get("legal_name"),
                                "the seller's counterparty record"),
        "parent_company": _buyer("Parent company", counterparty.get("parent_company"), "the seller's parent company"),
        "contract_purpose": _missing("Contract purpose", "contract purpose (supply, supply and service, service or "
                                                         "O&M) not entered"),
        "contract_start": _buyer("Contract start", start, "whether the contract is signed before notice to proceed"),
        **terms.fields("CT-10"),
    }


def _law(terms: _Terms) -> dict:
    return {**terms.fields("CT-14"), **terms.fields("CT-14", "fa", "fa_"), **terms.fields("CT-14", "owner", "owner_")}


def _price(loaded, vendor_id: str, body: dict, project: ProjectBaseline, terms: _Terms,
           history: Mapping | None = None) -> dict:
    vendor = next((v for v in loaded.tender.vendors if v.id == vendor_id), None)
    headline = [vendor.headline_citation] if vendor is not None and vendor.headline_citation else []
    lines = {line["label"]: line for line in (body.get("budget") or {}).get(vendor_id) or []}
    budget = project.frozen_budget
    out = {
        "headline": (_field("Headline price", "cited", headline[0]["quote"], citations=headline) if headline
                     else _missing("Headline price", "the offer's headline price was not found")),
        "offered": _budget_field("Offered price", lines.get("offered"), headline, "the offered price is not known"),
        **terms.fields("CT-13"),
        "frozen_budget": (_field("Frozen budget", "computed", _cents(budget.currency, budget.amount),
                                 formula=BUYER_ENTERED) if budget is not None
                          else _missing("Frozen budget", "frozen budget not entered")),
        "variance": _budget_field("Budget variance", lines.get("variance"), headline, "frozen budget not entered"),
    }
    if "offered_with_options" in lines:
        out["offered_with_options"] = _budget_field("Offered price with options", lines["offered_with_options"],
                                                    headline, NOT_KNOWN)
        out["variance_with_options"] = _budget_field("Budget variance with options",
                                                     lines.get("variance_with_options"), headline, NOT_KNOWN)
    out["variance_true_job_cost"] = _budget_field("Variance against the true job cost",
                                                  lines.get("variance_true_job_cost"), [], "true job cost not known")
    out["history"] = _history(history)
    return out


def _history(history: Mapping | None) -> dict:
    """The offer's price per Wp / per kVA against the median of the buyer's past offers, with the set listed."""
    label = "Price against your past offers"
    comparison = (history or {}).get("comparison")
    if not comparison:
        return _missing(label, (history or {}).get("reason") or "fewer than 3 comparable past offers")
    unit, currency = comparison["unit"], comparison["currency"]
    def q(raw, places="0.0001") -> str:
        return f"{Decimal(raw):.{len(places) - 2}f}"  # formatting never raises InvalidOperation like quantize

    value, mid = q(comparison["value"]), q(comparison["median"])
    delta = q(comparison["delta_pct"], "0.1")
    delta = delta if delta.startswith("-") else f"+{delta}"
    display = (f"{currency} {value}/{unit} vs median {currency} {mid}/{unit} "
               f"({delta}%)")
    listed = "; ".join(f"{r['project']} {r['offer_date']} {currency} {q(r['unit_price'])}/{unit}"
                       for r in comparison["rows"])
    window = comparison["window"]
    formula = (f"offered price / project capacity, against the median of {comparison['count']} past offers of the "
               f"same equipment in {currency}, {window['from']} to {window['to']}: {listed}")
    if comparison["excluded_other_currency"]:
        formula += f"; {comparison['excluded_other_currency']} in another currency excluded"
    if comparison.get("excluded_no_capacity"):
        formula += f"; {comparison['excluded_no_capacity']} without a capacity in {unit} excluded"
    return _field(label, "computed", display, formula=formula)


def _gaps(body: dict, vendor_id: str) -> dict[str, dict]:
    return {g["param_id"]: g for g in body.get("gaps") or [] if g.get("vendor_id") == vendor_id}


def _payment_bonds(body: dict, vendor_id: str, terms: _Terms) -> dict:
    return {**terms.fields("CT-07"),
            "payment_timing": _gap_field("Payment timing against the framework agreement",
                                         _gaps(body, vendor_id).get("CT-07"), NOT_KNOWN),
            **terms.fields("CT-04"), **terms.fields("CT-05"), **terms.fields("CT-06"), **terms.fields("CT-15")}


def _lds(findings: list[dict], vendor_id: str, terms: _Terms) -> dict:
    finding = _finding(findings, f"{vendor_id}|CT-01|owner|back_to_back_ld")
    shortfall = _money_field("LD recovery shortfall for the delay scenario", _first_money(finding),
                             finding["citations"] if finding else [],
                             "no back-to-back LD gap: no owner LD terms, or the offer recovers them in full")
    return {**terms.fields("CT-01"), "ld_shortfall": shortfall}


def _warranty(body: dict, vendor_id: str, terms: _Terms) -> dict:
    return {**terms.fields("CT-03"),
            "warranty_shortfall": _gap_field("Warranty shortfall", _gaps(body, vendor_id).get("CT-03"), NOT_KNOWN),
            **terms.fields("CT-04", prefix="bond_")}


LEAD_TIME_PARAM = "PARAM-09"
INCOTERM_PARAM = "PARAM-06"


def _cell_field(loaded, param_id: str, vendor_id: str, name: str):
    """(the TBE cell's FieldValue for `name`, its citation), read from the cell's stored field citation with the
    same parser the TBE used; (None, None) when the tender has no such cell or field."""
    param = next((p for p in loaded.tender.parameters if p.id == param_id), None)
    cell = param.vendors.get(vendor_id) if param is not None else None
    citation = (cell.field_citations or {}).get(name) if cell is not None else None
    if not citation:
        return None, None
    definition = {p.id: p for p in (*PARAMS_BY_ID.values(), *EXTENDED_PARAMETERS)}[param_id]
    kind = next(f.kind for f in definition.fields if f.name == name)
    return parse_field(kind, citation["quote"], name), citation


def _delivery(loaded, vendor_id: str, terms: "_Terms") -> dict:
    """CT-12, the lead time from the TBE cell (PARAM-09) and the schedule float (schedule.schedule_float), with the
    inputs the TBE uses for it: the tender's project dates and the TBE's Incoterm (PARAM-06)."""
    lead, lead_citation = _cell_field(loaded, LEAD_TIME_PARAM, vendor_id, "lead_time")
    if lead is None:
        lead_field = _missing("Lead time", "the TBE has no delivery lead time for this vendor (PARAM-09)")
    else:
        lead_field = _field("Lead time", "cited", lead.display, citations=[lead_citation])
    if lead is None:
        float_field = _missing("Schedule float", "no lead time to schedule from")
    else:
        incoterm, _ = _cell_field(loaded, INCOTERM_PARAM, vendor_id, "incoterm")
        basis = schedule.arrival_basis_for_incoterm(incoterm.value if incoterm is not None else None)
        result = schedule.schedule_float(loaded.tender.project, lead, arrival_basis=basis)
        if result.float_days is None:
            float_field = _missing("Schedule float", result.reason, [lead_citation])
        else:
            float_field = _field("Schedule float", "computed", f"{result.float_days:+d} days",
                                 citations=[lead_citation], formula="; ".join(result.steps))
    return {**terms.fields("CT-12"), "lead_time": lead_field, "schedule_float": float_field}


def _vendor_cells(loaded, vendor_id: str):
    for param in loaded.tender.parameters:
        cell = param.vendors.get(vendor_id)
        if cell is not None:
            status = cell.status.value if hasattr(cell.status, "value") else cell.status
            yield param, cell, status


def _technical_open(loaded, vendor_id: str) -> int:
    """The vendor's open TBE items (unpriced, to inspect, unreviewed flags)."""
    return sum(1 for i in open_items(loaded.tender, loaded.overrides, loaded.reviews) if i.vendor_id == vendor_id)


def _technical(loaded, vendor_id: str) -> dict:
    cells = list(_vendor_cells(loaded, vendor_id))
    deviations = sum(1 for _, _, s in cells if s == ComplianceStatus.DEVIATION_DOWNGRADE.value)
    link = f"/audit/{loaded.meta.tender_id}"
    return {
        "status": _field("TBE status", "computed", loaded.meta.status, formula="the tender's TBE review status"),
        "deviations": _field("Deviations", "computed", str(deviations),
                             formula=f"cells of this vendor with status {ComplianceStatus.DEVIATION_DOWNGRADE.value}"),
        "open_items": _field("Open items", "computed", str(_technical_open(loaded, vendor_id)),
                             formula="the TBE open items of this vendor (unpriced, to inspect, unreviewed flags)"),
        "link": _field("Full evaluation", "computed", link, formula="the technical bid evaluation of this tender"),
    }


def _risks(loaded, vendor_id: str, findings: list[dict], dispositions: Mapping[str, dict]) -> dict:
    groups: dict[str, list[dict]] = {g: [] for g in RISK_GROUPS}
    for param, cell, status in _vendor_cells(loaded, vendor_id):
        if status not in _TBE_RISK:
            continue
        groups["technical"].append({
            "finding_id": f"{vendor_id}|{param.id}|tbe", "category": "technical",
            "message": f"{param.name}: {status}", "money": [], "gap": None, "disposition": None,
            "disposition_state": review_state(cell, loaded.reviews.get((param.id, vendor_id))),
            "citations": [cell.citation] if cell.citation else []})
    for finding in findings:
        row = dispositions.get(finding["id"])
        if finding["category"] not in groups:
            raise ValueError(f"finding {finding['id']!r} has unknown category {finding['category']!r}")
        groups[finding["category"]].append({
            "finding_id": finding["id"], "category": finding["category"], "message": finding["message"],
            "money": finding.get("money") or [], "gap": finding.get("gap"),
            "disposition": row["disposition"] if row else None,
            "disposition_state": row["state"] if row else "undecided",
            "citations": finding.get("citations") or []})
    return groups


def _variance_pct(body: dict, vendor_id: str, project: ProjectBaseline) -> Decimal | None:
    line = next((x for x in (body.get("budget") or {}).get(vendor_id) or [] if x["label"] == "variance"), None)
    variance = _dec(line.get("amount")) if line else None
    budget = project.frozen_budget
    if variance is None or budget is None or budget.amount == 0:
        return None
    return (variance / budget.amount * 100).quantize(Decimal("0.01"))


def _approvers(rules, findings: list[dict], body: dict, vendor_id: str, project: ProjectBaseline,
               removals: list[dict], technical_open: int = 0) -> dict:
    suggestions = approver_rules.suggest_approvers(
        rules, findings=findings, budget_variance_pct=_variance_pct(body, vendor_id, project),
        signing_before_ntp=project.signing_before_ntp, procurement_negotiated=project.procurement_negotiated,
        budget_currency=project.frozen_budget.currency if project.frozen_budget else None, fx=project.fx)
    removed_by_role = {r["approver"]: r for r in removals}  # oldest first: the latest removal of a role counts
    suggested, removed = [], []
    for s in suggestions:
        removal = removed_by_role.get(s.role)
        reason = s.reason
        if s.role == ENGINEERING and technical_open:  # spec 4.6: blocked while technical items are open
            reason = f"{reason} Blocked: {technical_open} technical item{'s' if technical_open != 1 else ''} open."
        if removal is None:
            suggested.append({"role": s.role, "reason": reason})
        else:
            removed.append({"role": s.role, "suggestion_reason": reason, "reason": removal["reason"],
                            "removed_by": removal["removed_by"], "removed_at": removal["created_at"]})
    return {"rules": f"{rules.name} v{rules.version}", "suggested": suggested, "removed": removed}


def suggested_roles(loaded, vendor_id: str, *, contract: Mapping, rules) -> list[str]:
    """The roles the rules suggest now, before removals."""
    body = contract.get("body") or {}
    findings = [f for f in body.get("findings") or [] if f.get("vendor_id") == vendor_id]
    project = baseline(loaded)
    return [s.role for s in approver_rules.suggest_approvers(
        rules, findings=findings, budget_variance_pct=_variance_pct(body, vendor_id, project),
        signing_before_ntp=project.signing_before_ntp, procurement_negotiated=project.procurement_negotiated,
        budget_currency=project.frozen_budget.currency if project.frozen_budget else None, fx=project.fx)]


def _meta(loaded, contract: Mapping, findings: list[dict], dispositions: Mapping[str, dict]) -> dict:
    run = contract.get("run") or {}
    parts = run.get("parts") or {}
    gate = contract.get("gate") or {}
    undecided = sum(1 for f in findings if (dispositions.get(f["id"]) or {}).get("state") != "current")
    return {
        "run_id": run.get("run_id"), "run_fingerprint": run.get("fingerprint"),
        "rate_table_version": loaded.tender.rate_table_version or None,
        "ontology_hash": parts.get("ontology") or None, "contract_ontology_hash": parts.get("contract_ontology") or None,
        "zero_retention_ref": parts.get("zero_retention_ref") or None,
        "tbe_status": loaded.meta.status, "contract_gate_status": gate.get("status"),
        "unreviewed_count": len(gate.get("open_items") or []) if gate else undecided,
    }


def baseline(loaded) -> ProjectBaseline:
    return ProjectBaseline.model_validate(loaded.tender.project or {})


def build_cas(loaded, vendor_id: str, *, contract: dict, dispositions: dict, rules, removals: list) -> dict:
    """The CAS of one vendor.

    `contract`: {"body": the contracted stage body, "terms": the vendor's stored terms, "run": the latest run
    (get_run), "gate": the gate row with its open items, "project": {"name", "buyer_entity"} or None,
    "counterparty": {"legal_name", "parent_company"} or None, "price_history": {"comparison", "reason"} or
    absent}. `dispositions`: finding_id -> latest disposition
    row with its state. `removals`: approver removals, oldest first, each {"approver", "reason", "removed_by",
    "created_at"}."""
    body = contract.get("body") or {}
    findings = [f for f in body.get("findings") or [] if f.get("vendor_id") == vendor_id]
    terms = _Terms(contract.get("terms") or [], loaded.documents)
    project = baseline(loaded)
    return {
        "project": _project(loaded, contract, project, terms),
        "law": _law(terms),
        "price": _price(loaded, vendor_id, body, project, terms, contract.get("price_history")),
        "delivery": _delivery(loaded, vendor_id, terms),
        "payment_bonds": _payment_bonds(body, vendor_id, terms),
        "lds": _lds(findings, vendor_id, terms),
        "warranty": _warranty(body, vendor_id, terms),
        "risks": _risks(loaded, vendor_id, findings, dispositions),
        "technical": _technical(loaded, vendor_id),
        "approvers": _approvers(rules, findings, body, vendor_id, project, removals,
                                _technical_open(loaded, vendor_id)),
        "meta": _meta(loaded, contract, findings, dispositions),
    }
