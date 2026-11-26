"""Contract review: a reviewer's disposition on each contract finding of a vendor.

Mounted under /api/review with the same token guard as antova_review_api. A disposition is one of
accept, negotiate or waiver_needed, with a reason. Rows are only ever added; the latest per finding
counts. Each row is stamped with the finding's evidence_hash, so a re-ingest that changes a cited value
shows the disposition as stale. Accepting a finding needs the role the approver rules name for it
(approvers.role_to_accept). Dispositions are frozen while the vendor's contract gate is VERIFIED.

The gate: GET .../contract shows the vendor's contract rows, verify moves the gate to VERIFIED once
nothing is open (antova_review.contract_open_items) and withdraw reopens it. Contract cells are reviewed
through the TBE review route with CT- param ids.

Dispositions and gates never touch the tender's TBE status or its open items.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy.orm import Session

from antova_auth import ROLES, ReviewerPrincipal, require_review_token
from antova_briefs_api import not_found
from antova_db import ContractGateRecord, FindingDispositionRecord, TenderRepository, fa_version, get_db
from antova_ingest import approvers
from antova_ingest.contract_ontology import CONTRACT_PARAMS_BY_ID, TBE_EQUIVALENT
from antova_review import contract_cell_state, contract_open_items

log = logging.getLogger(__name__)
DISPOSITIONS_LOCKED = "Reopen the contract review before you change dispositions."
CONTRACT_LOCKED = "Reopen the contract review before you change it."

router = APIRouter(prefix="/api/review", tags=["Contract review"], dependencies=[Depends(require_review_token)])


class DispositionBody(BaseModel):
    """What the reviewer decided on one finding, and why. Who decided comes from the token."""
    disposition: Literal["accept", "negotiate", "waiver_needed"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    waiver_role: Literal[ROLES] | None = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _waiver_role_with_waiver(self):
        if self.disposition == "waiver_needed" and self.waiver_role is None:
            raise ValueError("Name the role that must sign the waiver.")
        if self.disposition != "waiver_needed":
            self.waiver_role = None
        return self


def _row(record: FindingDispositionRecord) -> dict:
    return {
        "id": record.id, "tender_id": record.tender_id, "vendor_id": record.vendor_id,
        "finding_id": record.finding_id, "disposition": record.disposition, "reason": record.reason,
        "waiver_role": record.waiver_role, "reviewer": record.reviewer, "evidence_hash": record.evidence_hash,
        "created_at": record.created_at.isoformat(),
    }


def _state(finding: dict | None, row: dict) -> str:
    """current while the finding still exists with the evidence the reviewer saw; stale otherwise."""
    return "current" if finding is not None and finding.get("evidence_hash") == row["evidence_hash"] else "stale"


def vendor_findings(db: Session, tender_id: str, vendor_id: str) -> dict[str, dict]:
    """The vendor's contract findings of the latest run, by id."""
    return {f["id"]: f for f in TenderRepository(db).contract_findings(tender_id) if f.get("vendor_id") == vendor_id}


def latest_dispositions(db: Session, tender_id: str, vendor_id: str) -> list[dict]:
    """The latest disposition per finding (oldest finding first), each with its state."""
    records = (db.query(FindingDispositionRecord)
               .filter_by(tender_id=tender_id, vendor_id=vendor_id)
               .order_by(FindingDispositionRecord.id))
    latest: dict[str, dict] = {}
    for record in records:
        latest.pop(record.finding_id, None)
        latest[record.finding_id] = _row(record)
    findings = vendor_findings(db, tender_id, vendor_id)
    return [{**row, "state": _state(findings.get(row["finding_id"]), row)} for row in latest.values()]


def _known_vendor(tender_id: str, vendor_id: str, db: Session = Depends(get_db)) -> None:
    meta = TenderRepository(db).get_meta(tender_id)
    if meta is None:
        raise not_found()
    tender = TenderRepository(db).load_tender(tender_id)
    if tender is None or vendor_id not in {v.id for v in tender.vendors}:
        raise not_found()


def contract_gate_open(tender_id: str, vendor_id: str, db: Session, detail: str = CONTRACT_LOCKED) -> None:
    """Raise 409 while the vendor's contract gate is VERIFIED."""
    gate = db.get(ContractGateRecord, (tender_id, vendor_id))
    if gate is not None and gate.status == "VERIFIED":
        raise HTTPException(status_code=409, detail=detail)


def _open_gate(tender_id: str, vendor_id: str, db: Session = Depends(get_db),
               _vendor: None = Depends(_known_vendor)) -> None:
    """409 while the vendor's contract gate is VERIFIED. A dependency, so it runs before the body is read."""
    contract_gate_open(tender_id, vendor_id, db, DISPOSITIONS_LOCKED)


@router.put("/tenders/{tender_id}/vendors/{vendor_id}/findings/{finding_id}/disposition",
            dependencies=[Depends(_open_gate)])
def put_disposition(tender_id: str, vendor_id: str, finding_id: str, body: DispositionBody,
                    db: Session = Depends(get_db), principal: ReviewerPrincipal = Depends(require_review_token)):
    finding = vendor_findings(db, tender_id, vendor_id).get(finding_id)
    if finding is None:
        raise not_found()
    if body.disposition == "accept":
        try:
            role = approvers.role_to_accept(approvers.load_rules(), finding)
        except ValueError:
            log.warning("no approver rule: tender=%s vendor=%s finding=%s category=%s", tender_id, vendor_id,
                        finding_id, finding.get("category"))
            raise HTTPException(status_code=422, detail=(
                "No approver rule names who may accept this finding. Add its category to the approver rules."
            )) from None
        if role not in principal.roles:
            raise HTTPException(status_code=403, detail=f"Accepting this finding needs the {role} role.")
    record = FindingDispositionRecord(
        tender_id=tender_id, vendor_id=vendor_id, finding_id=finding_id, disposition=body.disposition,
        reason=body.reason, waiver_role=body.waiver_role, reviewer=principal.id,
        evidence_hash=finding["evidence_hash"], created_at=datetime.now(timezone.utc))
    db.add(record)
    db.commit()
    log.info("finding disposed: tender=%s vendor=%s finding=%s disposition=%s by=%s", tender_id, vendor_id,
             finding_id, body.disposition, principal.id)
    row = _row(record)
    return {**row, "state": _state(finding, row)}


@router.get("/tenders/{tender_id}/vendors/{vendor_id}/dispositions", dependencies=[Depends(_known_vendor)])
def get_dispositions(tender_id: str, vendor_id: str, db: Session = Depends(get_db)):
    return latest_dispositions(db, tender_id, vendor_id)


# --- the contract gate ----------------------------------------------------------------------------

class GateVerifyBody(BaseModel):
    """The run the reviewer saw: a re-ingest after it makes the verify a conflict."""
    run_id: Annotated[str, StringConstraints(max_length=64)]


def _gate(db: Session, tender_id: str, vendor_id: str) -> ContractGateRecord:
    gate = db.get(ContractGateRecord, (tender_id, vendor_id))
    if gate is None:  # the tender does not go to contract approval
        raise not_found()
    return gate


def gate_open_items(db: Session, tender_id: str, vendor_id: str) -> list:
    """What blocks the vendor's contract gate now, from what is stored. Raises 404 without a gate."""
    gate = _gate(db, tender_id, vendor_id)
    repo = TenderRepository(db)
    counterparty = repo.vendor_counterparty(tender_id, vendor_id)
    return contract_open_items(
        repo.contract_terms(tender_id, vendor_id), list(vendor_findings(db, tender_id, vendor_id).values()),
        latest_dispositions(db, tender_id, vendor_id), repo.latest_reviews(tender_id),
        fa_version(db, counterparty) if counterparty else None, gate.fa_version)


def _gate_row(gate: ContractGateRecord | None) -> dict | None:
    if gate is None:
        return None
    return {"status": gate.status, "verified_at": gate.verified_at.isoformat() if gate.verified_at else None,
            "verified_by": gate.verified_by, "verified_run_id": gate.verified_run_id, "fa_version": gate.fa_version}


def _mr(tender, param_id: str, vendor_id: str) -> dict | None:
    """The TBE cell on the same subject as a contract term (TBE_EQUIVALENT), or None."""
    tbe_id = TBE_EQUIVALENT.get(param_id)
    param = next((p for p in tender.parameters if p.id == tbe_id), None) if tender is not None else None
    cell = param.vendors.get(vendor_id) if param is not None else None
    if cell is None:
        return None
    status = cell.status.value if hasattr(cell.status, "value") else cell.status
    return {"param_id": param.id, "name": param.name, "requirement": param.mr_requirement, "status": status,
            "extracted_text": cell.extracted_text, "citation": cell.citation}


@router.get("/tenders/{tender_id}/vendors/{vendor_id}/contract", dependencies=[Depends(_known_vendor)])
def get_contract(tender_id: str, vendor_id: str, db: Session = Depends(get_db)):
    """The vendor's contract terms beside the TBE cell on the same subject, their checks, the baselines not
    checked, and the gate with what still blocks it (gate is null for a tender without contract approval)."""
    repo = TenderRepository(db)
    body = repo.contracted_body(tender_id)
    tender = repo.load_tender(tender_id)
    reviews = repo.latest_reviews(tender_id)
    checks = [c for c in body.get("checks") or [] if c.get("vendor_id") == vendor_id]
    rows = []
    for term in repo.contract_terms(tender_id, vendor_id):
        param = CONTRACT_PARAMS_BY_ID.get(term["param_id"])
        rows.append({
            "param_id": term["param_id"], "name": param.name if param else term["param_id"],
            "offered": term.get("offered"), "fa": term.get("fa"), "owner": term.get("owner"),
            "mr": _mr(tender, term["param_id"], vendor_id), "state": term.get("state"),
            "checks": [c for c in checks if c.get("param_id") == term["param_id"]],
            "review": contract_cell_state(term, reviews.get((term["param_id"], vendor_id))),
        })
    gate_row = _gate_row(db.get(ContractGateRecord, (tender_id, vendor_id)))
    if gate_row is not None:
        gate_row["open_items"] = [asdict(i) for i in gate_open_items(db, tender_id, vendor_id)]
    run = repo.get_run(tender_id)
    return {"rows": rows, "gate": gate_row, "run_id": run["run_id"] if run else None,
            "not_checked": [n for n in body.get("not_checked") or [] if n.get("vendor_id") == vendor_id]}


@router.post("/tenders/{tender_id}/vendors/{vendor_id}/contract/verify", dependencies=[Depends(_known_vendor)])
def verify_contract(tender_id: str, vendor_id: str, body: GateVerifyBody, db: Session = Depends(get_db),
                    principal: ReviewerPrincipal = Depends(require_review_token)):
    """Verify the vendor's contract gate if it is the run the reviewer saw and nothing is open.

    As the TBE verify: the gate row is claimed first (SQLite's write lock), then the run and the open items are
    read again from what is stored, and the change commits only if nothing changed. The TBE status is untouched.
    """
    from antova_review_api import RUN_CHANGED, _open_items_conflict  # antova_review_api imports this module

    gate = _gate(db, tender_id, vendor_id)
    run = TenderRepository(db).get_run(tender_id)
    run_id = run["run_id"] if run else None
    if body.run_id != run_id:
        return JSONResponse(status_code=409, content=RUN_CHANGED)
    remaining = gate_open_items(db, tender_id, vendor_id)
    if remaining:
        return _open_items_conflict(remaining)
    if gate.status == "VERIFIED":
        return _gate_row(gate)
    at = datetime.now(timezone.utc)
    try:
        claimed = (db.query(ContractGateRecord)
                   .filter_by(tender_id=tender_id, vendor_id=vendor_id, status="DRAFT")
                   .update({"status": "VERIFIED", "verified_at": at, "verified_by": principal.id,
                            "verified_run_id": run_id}, synchronize_session=False))
        db.expire_all()  # read what is stored now, not what this session cached
        again = TenderRepository(db).get_run(tender_id)
        if claimed != 1 or (again["run_id"] if again else None) != run_id:
            db.rollback()
            return JSONResponse(status_code=409, content=RUN_CHANGED)
        remaining = gate_open_items(db, tender_id, vendor_id)
        if remaining:
            db.rollback()
            return _open_items_conflict(remaining)
        db.commit()
    except Exception:
        db.rollback()
        raise
    log.info("contract gate verified: tender=%s vendor=%s run=%s by=%s", tender_id, vendor_id, run_id, principal.id)
    return _gate_row(db.get(ContractGateRecord, (tender_id, vendor_id)))


@router.post("/tenders/{tender_id}/vendors/{vendor_id}/contract/withdraw", dependencies=[Depends(_known_vendor)])
def withdraw_contract(tender_id: str, vendor_id: str, db: Session = Depends(get_db),
                      principal: ReviewerPrincipal = Depends(require_review_token)):
    """Reopen the vendor's contract gate so its cells and dispositions can change."""
    gate = _gate(db, tender_id, vendor_id)
    gate.status, gate.verified_at, gate.verified_by, gate.verified_run_id = "DRAFT", None, None, None
    db.commit()
    log.info("contract gate withdrawn: tender=%s vendor=%s by=%s", tender_id, vendor_id, principal.id)
    return {"status": "DRAFT"}
