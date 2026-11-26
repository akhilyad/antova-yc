"""Contract review: a reviewer's disposition on each contract finding of a vendor.

Mounted under /api/review with the same token guard as antova_review_api. A disposition is one of
accept, negotiate or waiver_needed, with a reason. Rows are only ever added; the latest per finding
counts. Each row is stamped with the finding's evidence_hash, so a re-ingest that changes a cited value
shows the disposition as stale. Accepting a finding needs the role the approver rules name for it
(approvers.role_to_accept). Dispositions are frozen while the vendor's contract gate is VERIFIED.

Dispositions never touch the tender's TBE status or its open items.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StringConstraints, model_validator
from sqlalchemy.orm import Session

from antova_auth import ROLES, ReviewerPrincipal, require_review_token
from antova_briefs_api import not_found
from antova_db import ContractGateRecord, FindingDispositionRecord, TenderRepository, get_db
from antova_ingest import approvers

log = logging.getLogger(__name__)
DISPOSITIONS_LOCKED = "Reopen the contract review before you change dispositions."

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


def _open_gate(tender_id: str, vendor_id: str, db: Session = Depends(get_db),
               _vendor: None = Depends(_known_vendor)) -> None:
    """409 while the vendor's contract gate is VERIFIED. A dependency, so it runs before the body is read."""
    gate = db.get(ContractGateRecord, (tender_id, vendor_id))
    if gate is not None and gate.status == "VERIFIED":
        raise HTTPException(status_code=409, detail=DISPOSITIONS_LOCKED)


@router.put("/tenders/{tender_id}/vendors/{vendor_id}/findings/{finding_id}/disposition",
            dependencies=[Depends(_open_gate)])
def put_disposition(tender_id: str, vendor_id: str, finding_id: str, body: DispositionBody,
                    db: Session = Depends(get_db), principal: ReviewerPrincipal = Depends(require_review_token)):
    finding = vendor_findings(db, tender_id, vendor_id).get(finding_id)
    if finding is None:
        raise not_found()
    if body.disposition == "accept":
        role = approvers.role_to_accept(approvers.load_rules(), finding)
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
