"""Deleting data: one counterparty's documents and evidence on request, and whole tenders past retention.

Per-counterparty deletion (spec 4.7) removes, across every tender: the documents the manifest names for
that counterparty (their PDFs and saved locator responses, and any other copy with the same sha256 under
the tenders folder), the contract terms, dispositions, approver removals and CT- cell reviews of the
vendors linked to it, and the stage artifacts of the affected tenders' runs (they hold the documents'
text). Each affected tender and its contract gates go back to DRAFT with a stage-log note; a re-ingest
builds them again. A DeletionLogRecord keeps who, when and how many rows, never content.

Retention: ANTOVA_RETENTION_DAYS (unset or not a positive number: nothing is purged) is the deployment's
stated period. purge_expired deletes every tender ingested longer ago than that, its rows and its folder.
Run it from a scheduler: python -m antova_ingest.retention

Files are deleted only under ANTOVA_TENDERS_DIR; without it (development on the bundled samples) only
database rows are deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from antova_db import (ApproverRemovalRecord, Base, CellReviewRecord, ContractGateRecord, ContractTermRecord,
                       CounterpartyRecord, DeletionLogRecord, FindingDispositionRecord, IngestRunRecord,
                       SourceDocumentRecord, StageArtifactRecord, TenderRecord, TenderRepository, VendorRecord)

log = logging.getLogger(__name__)

PURGED_NOTE = "artifacts purged after a counterparty deletion; re-ingest needed"
RESPONSES_DIR = "saved_responses"


def _files_root() -> Optional[Path]:
    configured = os.environ.get("ANTOVA_TENDERS_DIR")
    return Path(configured).resolve() if configured else None


def _tender_folder(root: Path, tender_id: str) -> Optional[Path]:
    folder = (root / tender_id).resolve()
    return folder if folder.is_relative_to(root) and folder != root else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clear_crop_cache() -> None:
    """Evidence crops (page images) are rendered from the PDFs and cached in memory: drop them."""
    try:
        from antova_evidence import _render_crop_cached
    except ImportError:  # pragma: no cover - the API is not loaded in this process
        return
    _render_crop_cached.cache_clear()


def _delete_files(shas: set[str]) -> tuple[int, int]:
    """Delete every document and saved response with one of these sha256s under the tenders folder.
    Returns (documents, saved responses) deleted."""
    root = _files_root()
    if root is None or not shas or not root.is_dir():
        return 0, 0
    documents = responses = 0
    for folder in (p for p in root.iterdir() if p.is_dir()):
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() == ".pdf" and _sha256(path) in shas:
                path.unlink()
                documents += 1
        store = folder / RESPONSES_DIR
        if store.is_dir():
            for path in store.iterdir():
                if path.is_file() and path.name.split("__", 1)[0] in shas:
                    path.unlink()
                    responses += 1
    _clear_crop_cache()
    return documents, responses


def delete_counterparty(db: Session, counterparty_id: str, deleted_by: str,
                        now: Optional[datetime] = None) -> Optional[dict[str, int]]:
    """Delete one counterparty's documents and evidence everywhere. None when nothing names it.

    Returns the counts written to the deletion log."""
    now = now or datetime.now(timezone.utc)
    repo = TenderRepository(db)
    documents = db.query(SourceDocumentRecord).filter_by(counterparty_id=counterparty_id).all()
    # The vendors that are this counterparty: linked by a person, or named on their bid documents.
    linked: set[tuple[str, str]] = set()
    for vendor in db.query(VendorRecord):
        if repo.vendor_counterparty(vendor.tender_id, vendor.id) == counterparty_id:
            linked.add((vendor.tender_id, vendor.id))
    counterparty = db.get(CounterpartyRecord, counterparty_id)
    if counterparty is None and not documents and not linked:
        return None

    tenders = {d.tender_id for d in documents} | {t for t, _ in linked}
    shas = {d.sha256 for d in documents}
    counts = {"documents": len(documents), "files": 0, "saved_responses": 0, "contract_terms": 0,
              "finding_dispositions": 0, "cell_reviews": 0, "approver_removals": 0, "stage_artifacts": 0,
              "tenders_reset": 0, "gates_reset": 0, "vendor_links": 0, "counterparties": 0}
    for document in documents:
        db.delete(document)
    for tender_id, vendor_id in sorted(linked):
        scope = {"tender_id": tender_id, "vendor_id": vendor_id}
        counts["contract_terms"] += db.query(ContractTermRecord).filter_by(**scope).delete()
        counts["finding_dispositions"] += db.query(FindingDispositionRecord).filter_by(**scope).delete()
        counts["approver_removals"] += db.query(ApproverRemovalRecord).filter_by(**scope).delete()
        counts["cell_reviews"] += (db.query(CellReviewRecord).filter_by(**scope)
                                   .filter(CellReviewRecord.param_id.like("CT-%"))
                                   .delete(synchronize_session=False))
        vendor = db.get(VendorRecord, (tender_id, vendor_id))
        if vendor is not None and vendor.counterparty_id == counterparty_id:
            vendor.counterparty_id = None
            counts["vendor_links"] += 1
    for tender_id in sorted(tenders):
        runs = [r for (r,) in db.query(IngestRunRecord.run_id).filter_by(tender_id=tender_id)]
        if runs:
            counts["stage_artifacts"] += (db.query(StageArtifactRecord)
                                          .filter(StageArtifactRecord.run_id.in_(runs))
                                          .delete(synchronize_session=False))
        tender = db.get(TenderRecord, tender_id)
        if tender is not None:
            tender.status, tender.verified_at, tender.verified_by, tender.verified_run_id = "DRAFT", None, None, None
            stage_log = json.loads(tender.stage_log or "[]")
            stage_log.append({"stage": PURGED_NOTE, "pct": 0, "t_ms": 0})
            tender.stage_log = json.dumps(stage_log)
            counts["tenders_reset"] += 1
        for gate in db.query(ContractGateRecord).filter_by(tender_id=tender_id):
            gate.status, gate.verified_at, gate.verified_by, gate.verified_run_id = "DRAFT", None, None, None
            counts["gates_reset"] += 1
    if counterparty is not None:
        db.delete(counterparty)
        counts["counterparties"] = 1
    db.flush()
    # Files go last, once every row is gone in this transaction; then the log and the commit.
    counts["files"], counts["saved_responses"] = _delete_files(shas)
    db.add(DeletionLogRecord(counterparty_id=counterparty_id, deleted_by=deleted_by, deleted_at=now,
                             counts=json.dumps(counts, sort_keys=True)))
    db.commit()
    log.info("counterparty deleted: counterparty=%s by=%s counts=%s", counterparty_id, deleted_by, counts)
    return counts


def delete_tender_rows(db: Session, tender_id: str) -> None:
    """Delete every database row of one tender (not committed)."""
    runs = [r for (r,) in db.query(IngestRunRecord.run_id).filter_by(tender_id=tender_id)]
    if runs:
        db.query(StageArtifactRecord).filter(StageArtifactRecord.run_id.in_(runs)).delete(synchronize_session=False)
    for table in reversed(Base.metadata.sorted_tables):  # children before the tenders row
        if table.name == "tenders":
            continue
        column = table.c.get("tender_id")
        if column is not None:
            db.execute(table.delete().where(column == tender_id))
    db.execute(TenderRecord.__table__.delete().where(TenderRecord.id == tender_id))


def retention_days() -> Optional[int]:
    raw = os.environ.get("ANTOVA_RETENTION_DAYS", "").strip()
    try:
        days = int(raw)
    except ValueError:
        return None
    return days if days > 0 else None


def purge_expired(db: Session, now: datetime) -> list[str]:
    """Delete the tenders ingested more than ANTOVA_RETENTION_DAYS before `now`, with their folders.
    Returns their IDs, sorted. Nothing is purged without a retention period."""
    days = retention_days()
    if days is None:
        return []
    cutoff = (now.astimezone(timezone.utc) if now.tzinfo else now).replace(tzinfo=None) - timedelta(days=days)
    expired = sorted(t for (t,) in db.query(TenderRecord.id).filter(TenderRecord.ingested_at < cutoff))
    for tender_id in expired:
        delete_tender_rows(db, tender_id)
    db.commit()
    root = _files_root()
    if root is not None:
        for tender_id in expired:
            folder = _tender_folder(root, tender_id)
            if folder is not None and folder.is_dir():
                shutil.rmtree(folder)
        if expired:
            _clear_crop_cache()
    log.info("retention purge: days=%d tenders=%s", days, expired)
    return expired


def main() -> int:
    from antova_db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        purged = purge_expired(db, datetime.now(timezone.utc))
    finally:
        db.close()
    if retention_days() is None:
        print("ANTOVA_RETENTION_DAYS is not set: nothing purged.")
    else:
        print(f"Purged {len(purged)} tender(s): {', '.join(purged) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
