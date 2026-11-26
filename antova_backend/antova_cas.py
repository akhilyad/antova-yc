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
from antova_ingest.values import CITATION_ONLY
from antova_review import open_items, review_state

CAS_KEYS = ("project", "law", "price", "delivery", "payment_bonds", "lds", "warranty", "risks", "technical",
            "approvers", "meta")
FIELD_KEYS = ("label", "state", "display", "citations", "formula", "reason")
STATES = ("cited", "computed", "inherited", "missing")
RISK_GROUPS = ("technical", "fa_conformance", "back_to_back", "budget", "missing")
NOT_KNOWN = "not known"
NOT_STATED = "not stated in the offer"
BUYER_ENTERED = "entered by the buyer"
_TBE_RISK = {ComplianceStatus.DEVIATION_DOWNGRADE.value, ComplianceStatus.CONFLICT_DOWNGRADE.value,
             ComplianceStatus.OMITTED.value, ComplianceStatus.MANUAL_INSPECTION_REQUIRED.value}


def _field(label: str, state: str, display: str | None = None, *, citations: Iterable[dict] = (),
           formula: str = "", reason: str = "") -> dict:
    if state not in STATES:
        raise ValueError(f"unknown CAS field state {state!r}")
    if state == "missing":
        return {"label": label, "state": state, "display": NOT_KNOWN, "citations": list(citations),
                "formula": "", "reason": reason or NOT_KNOWN}
    return {"label": label, "state": state, "display": display if display not in (None, "") else NOT_KNOWN,
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
        self.fa_files = {d["filename"] for d in documents if d.get("role") == "framework_agreement"}
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
        side = term.get(source)
        value = (side or {}).get("values", {}).get(name)
        if value is None:
            if source == "offered" and term.get("state") == "conflict":
                return _missing(label, "the documents disagree on this term; resolve the conflict in the contract "
                                       "review")
            return _missing(label, NOT_STATED if source == "offered" else
                            f"not stated in the {'framework agreement' if source == 'fa' else 'owner contract'}")
        display = value["value"] if name in CITATION_ONLY and isinstance(value.get("value"), str) else value["display"]
        if side["kind"] == "buyer_entered":
            return _field(label, "computed", display, formula=BUYER_ENTERED)
        citations = list(side.get("citations") or [])
        if name in set(side.get("inherited_fields") or ()):
            inherited = self._inherited_citations(citations) if side["kind"] != "inherited" else citations
            return _field(label, "inherited", display, citations=inherited)
        if source == "offered":
            own = [c for c in citations if c not in self._inherited_citations(citations)]
            citations = own or citations
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


def _price(loaded, vendor_id: str, body: dict, project: ProjectBaseline, terms: _Terms) -> dict:
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
    return out


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


def _vendor_cells(loaded, vendor_id: str):
    for param in loaded.tender.parameters:
        cell = param.vendors.get(vendor_id)
        if cell is not None:
            status = cell.status.value if hasattr(cell.status, "value") else cell.status
            yield param, cell, status


def _technical(loaded, vendor_id: str) -> dict:
    cells = list(_vendor_cells(loaded, vendor_id))
    deviations = sum(1 for _, _, s in cells if s == ComplianceStatus.DEVIATION_DOWNGRADE.value)
    items = [i for i in open_items(loaded.tender, loaded.overrides, loaded.reviews) if i.vendor_id == vendor_id]
    link = f"/audit/{loaded.meta.tender_id}"
    return {
        "status": _field("TBE status", "computed", loaded.meta.status, formula="the tender's TBE review status"),
        "deviations": _field("Deviations", "computed", str(deviations),
                             formula=f"cells of this vendor with status {ComplianceStatus.DEVIATION_DOWNGRADE.value}"),
        "open_items": _field("Open items", "computed", str(len(items)),
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
        groups.setdefault(finding["category"], []).append({
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
               removals: list[dict]) -> dict:
    suggestions = approver_rules.suggest_approvers(
        rules, findings=findings, budget_variance_pct=_variance_pct(body, vendor_id, project),
        signing_before_ntp=project.signing_before_ntp, procurement_negotiated=project.procurement_negotiated,
        budget_currency=project.frozen_budget.currency if project.frozen_budget else None, fx=project.fx)
    removed_by_role = {r["approver"]: r for r in removals}  # oldest first: the latest removal of a role counts
    suggested, removed = [], []
    for s in suggestions:
        removal = removed_by_role.get(s.role)
        if removal is None:
            suggested.append({"role": s.role, "reason": s.reason})
        else:
            removed.append({"role": s.role, "suggestion_reason": s.reason, "reason": removal["reason"],
                            "removed_by": removal["removed_by"], "removed_at": removal["created_at"]})
    return {"rules": f"{rules.name} v{rules.version}", "suggested": suggested, "removed": removed}


def suggested_roles(loaded, vendor_id: str, *, contract: Mapping, rules) -> list[str]:
    """The roles the rules suggest now, before removals."""
    body = contract.get("body") or {}
    findings = [f for f in body.get("findings") or [] if f.get("vendor_id") == vendor_id]
    project = _baseline(loaded)
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


def _baseline(loaded) -> ProjectBaseline:
    return ProjectBaseline.model_validate(loaded.tender.project or {})


def build_cas(loaded, vendor_id: str, *, contract: dict, dispositions: dict, rules, removals: list) -> dict:
    """The CAS of one vendor.

    `contract`: {"body": the contracted stage body, "terms": the vendor's stored terms, "run": the latest run
    (get_run), "gate": the gate row with its open items, "project": {"name", "buyer_entity"} or None,
    "counterparty": {"legal_name", "parent_company"} or None}. `dispositions`: finding_id -> latest disposition
    row with its state. `removals`: approver removals, oldest first, each {"approver", "reason", "removed_by",
    "created_at"}."""
    body = contract.get("body") or {}
    findings = [f for f in body.get("findings") or [] if f.get("vendor_id") == vendor_id]
    terms = _Terms(contract.get("terms") or [], loaded.documents)
    project = _baseline(loaded)
    return {
        "project": _project(loaded, contract, project, terms),
        "law": _law(terms),
        "price": _price(loaded, vendor_id, body, project, terms),
        "delivery": terms.fields("CT-12"),
        "payment_bonds": _payment_bonds(body, vendor_id, terms),
        "lds": _lds(findings, vendor_id, terms),
        "warranty": _warranty(body, vendor_id, terms),
        "risks": _risks(loaded, vendor_id, findings, dispositions),
        "technical": _technical(loaded, vendor_id),
        "approvers": _approvers(rules, findings, body, vendor_id, project, removals),
        "meta": _meta(loaded, contract, findings, dispositions),
    }
