"""Deleting one counterparty's data, the retention purge, and what the public brief leaves out.

All names, numbers and documents here are invented.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_db import (CellReviewRecord, ContractGateRecord, ContractTermRecord, CounterpartyRecord,
                       DeletionLogRecord, FindingDispositionRecord, IngestRunRecord, SessionLocal,
                       SourceDocumentRecord, StageArtifactRecord, TenderRecord, VendorRecord, init_db)
from antova_ingest import retention
from antova_review import public_brief_payload

T1, T2 = "TENDER-2026-901", "TENDER-2026-902"
CP, OTHER_CP = "cp-kestrel-test", "cp-osprey-test"
FA_BYTES = b"%PDF-1.4 invented Kestrel framework agreement"
OTHER_FA_BYTES = b"%PDF-1.4 invented Osprey framework agreement"
FA_SHA = hashlib.sha256(FA_BYTES).hexdigest()
OTHER_SHA = hashlib.sha256(OTHER_FA_BYTES).hexdigest()
FOUNDER = {REVIEW_HEADER: "test-review-token"}
REVIEWERS = {
    "eno": {"name": "Eno Engineer", "token": "tok-eng", "roles": ["engineering"]},
    "pam": {"name": "Pam Projects", "token": "tok-pm", "roles": ["project_management"]},
}
NOW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


def _tender(tender_id, at=NOW, status="VERIFIED"):
    return TenderRecord(id=tender_id, title="Invented substation", client="Invented EPC", mr_doc="MR.pdf",
                        ingested_at=at.replace(tzinfo=None), share_id=f"share-{tender_id}", status=status,
                        verified_at=at.replace(tzinfo=None) if status == "VERIFIED" else None,
                        verified_by="founder" if status == "VERIFIED" else None,
                        stage_log=json.dumps([{"stage": "done", "pct": 100, "t_ms": 1}]))


def _vendor(tender_id, vendor_id, counterparty_id=None):
    return VendorRecord(tender_id=tender_id, id=vendor_id, name=vendor_id, country="DE", headline_price=1.0,
                        doc_name=f"Bid_{vendor_id}.pdf", cover_claim="", badge="", counterparty_id=counterparty_id)


def _doc(tender_id, filename, data, counterparty_id):
    return SourceDocumentRecord(tender_id=tender_id, filename=filename, sha256=hashlib.sha256(data).hexdigest(),
                                role="framework_agreement", page_count=1, counterparty_id=counterparty_id)


def _term(tender_id, vendor_id, param_id):
    return ContractTermRecord(tender_id=tender_id, vendor_id=vendor_id, param_id=param_id,
                              body=json.dumps({"quote": "invented clause"}), evidence_hash="h")


def _review(tender_id, vendor_id, param_id):
    return CellReviewRecord(tender_id=tender_id, param_id=param_id, vendor_id=vendor_id, action="approve",
                            reviewer="founder", note="ok", evidence_hash="h", created_at=NOW)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTOVA_TENDERS_DIR", str(tmp_path))
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    _write(tmp_path / T1 / "FA_Kestrel.pdf", FA_BYTES)
    _write(tmp_path / T1 / "FA_Osprey.pdf", OTHER_FA_BYTES)
    _write(tmp_path / T2 / "FA_Kestrel_copy.pdf", FA_BYTES)
    _write(tmp_path / T1 / "saved_responses" / f"{FA_SHA}__v9-contract-x__model.json", b"{\"quote\": 1}")
    _write(tmp_path / T1 / "saved_responses" / f"{OTHER_SHA}__v9-contract-x__model.json", b"{}")
    db = SessionLocal()
    try:
        for tender_id in (T1, T2):  # a fresh start: an earlier test may have left these rows
            retention.delete_tender_rows(db, tender_id)
        db.query(CounterpartyRecord).filter(CounterpartyRecord.id.in_([CP, OTHER_CP])).delete()
        db.commit()
        db.add_all([_tender(T1), _tender(T2),
                    CounterpartyRecord(id=CP, legal_name="Kestrel Invented AG", created_at=NOW),
                    CounterpartyRecord(id=OTHER_CP, legal_name="Osprey Invented BV", created_at=NOW)])
        db.flush()
        db.add_all([
            _vendor(T1, "vendor-k", CP), _vendor(T1, "vendor-o", OTHER_CP), _vendor(T2, "vendor-k", CP),
            _doc(T1, "FA_Kestrel.pdf", FA_BYTES, CP), _doc(T1, "FA_Osprey.pdf", OTHER_FA_BYTES, OTHER_CP),
            _doc(T2, "FA_Kestrel_copy.pdf", FA_BYTES, CP),
            _term(T1, "vendor-k", "CT-01"), _term(T1, "vendor-k", "CT-03"), _term(T2, "vendor-k", "CT-01"),
            _term(T1, "vendor-o", "CT-01"),
            ContractGateRecord(tender_id=T1, vendor_id="vendor-k", status="VERIFIED"),
            ContractGateRecord(tender_id=T2, vendor_id="vendor-k", status="VERIFIED"),
            FindingDispositionRecord(tender_id=T1, vendor_id="vendor-k", finding_id="f1", disposition="accept",
                                     reason="ok", reviewer="founder", evidence_hash="h", created_at=NOW),
            _review(T1, "vendor-k", "CT-01"), _review(T1, "vendor-k", "PARAM-01"), _review(T1, "vendor-o", "CT-01"),
            IngestRunRecord(run_id="run-901", tender_id=T1, fingerprint="f", created_at=NOW),
            IngestRunRecord(run_id="run-902", tender_id=T2, fingerprint="f", created_at=NOW),
        ])
        db.flush()
        db.add_all([StageArtifactRecord(run_id=r, stage="located", sha256="s", body="{}")
                    for r in ("run-901", "run-902")])
        db.commit()
    finally:
        db.close()
    yield tmp_path


def _files_with_sha(root, sha):
    return [p for p in root.rglob("*") if p.is_file()
            and (p.name.startswith(sha) or hashlib.sha256(p.read_bytes()).hexdigest() == sha)]


def test_deleting_a_counterparty_removes_its_files_and_rows(seeded):
    client = TestClient(app)
    response = client.delete(f"/api/review/counterparties/{CP}", headers=FOUNDER)
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    assert counts == {"documents": 2, "files": 2, "saved_responses": 1, "contract_terms": 3,
                      "finding_dispositions": 1, "cell_reviews": 1, "approver_removals": 0,
                      "stage_artifacts": 2, "tenders_reset": 2, "gates_reset": 2, "vendor_links": 2,
                      "counterparties": 1}
    assert _files_with_sha(seeded, FA_SHA) == []
    assert len(_files_with_sha(seeded, OTHER_SHA)) == 2  # the other counterparty's FA and its response stay

    db = SessionLocal()
    try:
        assert db.query(SourceDocumentRecord).filter_by(counterparty_id=CP).count() == 0
        assert db.query(SourceDocumentRecord).filter_by(counterparty_id=OTHER_CP).count() == 1
        assert db.query(ContractTermRecord).filter_by(vendor_id="vendor-k").filter(
            ContractTermRecord.tender_id.in_([T1, T2])).count() == 0
        assert db.get(ContractTermRecord, (T1, "vendor-o", "CT-01")) is not None
        reviews = {(r.vendor_id, r.param_id) for r in db.query(CellReviewRecord).filter_by(tender_id=T1)}
        assert reviews == {("vendor-k", "PARAM-01"), ("vendor-o", "CT-01")}
        assert db.query(StageArtifactRecord).filter(StageArtifactRecord.run_id.in_(["run-901", "run-902"])).count() == 0
        for tender_id in (T1, T2):  # the second tender sharing the counterparty is reset too
            tender = db.get(TenderRecord, tender_id)
            assert tender.status == "DRAFT" and tender.verified_at is None
            assert json.loads(tender.stage_log)[-1]["stage"] == retention.PURGED_NOTE
            assert db.get(ContractGateRecord, (tender_id, "vendor-k")).status == "DRAFT"
            assert db.get(VendorRecord, (tender_id, "vendor-k")).counterparty_id is None
        assert db.get(CounterpartyRecord, CP) is None
        log = db.query(DeletionLogRecord).filter_by(counterparty_id=CP).one()
        assert log.deleted_by == "founder" and json.loads(log.counts) == counts
        assert "Kestrel" not in log.counts and "invented" not in log.counts  # counts, no content
    finally:
        db.close()


def test_project_management_may_delete_engineering_may_not(seeded):
    client = TestClient(app)
    refused = client.delete(f"/api/review/counterparties/{CP}", headers={REVIEW_HEADER: "tok-eng"})
    assert refused.status_code == 403
    assert len(_files_with_sha(seeded, FA_SHA)) == 3  # nothing was deleted
    allowed = client.delete(f"/api/review/counterparties/{CP}", headers={REVIEW_HEADER: "tok-pm"})
    assert allowed.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(DeletionLogRecord).filter_by(counterparty_id=CP).order_by(
            DeletionLogRecord.id.desc()).first().deleted_by == "pam"
    finally:
        db.close()


def test_deleting_an_unknown_counterparty_is_404(seeded):
    assert TestClient(app).delete("/api/review/counterparties/no-such-cp", headers=FOUNDER).status_code == 404


# --- retention ------------------------------------------------------------------------------------

@pytest.fixture
def own_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTOVA_TENDERS_DIR", str(tmp_path / "tenders"))
    bind = create_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    init_db(bind)
    with Session(bind) as db:
        db.add_all([_tender("TENDER-2026-931", NOW - timedelta(days=31)),
                    _tender("TENDER-2026-929", NOW - timedelta(days=29))])
        db.flush()
        db.add_all([_vendor("TENDER-2026-931", "vendor-a"), _vendor("TENDER-2026-929", "vendor-a"),
                    _term("TENDER-2026-931", "vendor-a", "CT-01"),
                    _review("TENDER-2026-931", "vendor-a", "PARAM-01")])
        db.commit()
    for tender_id in ("TENDER-2026-931", "TENDER-2026-929"):
        _write(tmp_path / "tenders" / tender_id / "Bid.pdf", b"%PDF-1.4 invented bid")
        _write(tmp_path / "tenders" / tender_id / "saved_responses" / "abc__x__m.json", b"{}")
    yield bind, tmp_path / "tenders"


def test_purge_removes_tenders_older_than_the_retention_period(own_db, monkeypatch):
    bind, root = own_db
    monkeypatch.setenv("ANTOVA_RETENTION_DAYS", "30")
    with Session(bind) as db:
        assert retention.purge_expired(db, NOW) == ["TENDER-2026-931"]
        assert db.get(TenderRecord, "TENDER-2026-931") is None
        assert db.get(TenderRecord, "TENDER-2026-929") is not None
        assert db.query(VendorRecord).filter_by(tender_id="TENDER-2026-931").count() == 0
        assert db.query(ContractTermRecord).filter_by(tender_id="TENDER-2026-931").count() == 0
        assert db.query(CellReviewRecord).filter_by(tender_id="TENDER-2026-931").count() == 0
    assert not (root / "TENDER-2026-931").exists()
    assert (root / "TENDER-2026-929" / "Bid.pdf").is_file()


def test_no_purge_without_a_retention_period(own_db, monkeypatch):
    bind, root = own_db
    monkeypatch.delenv("ANTOVA_RETENTION_DAYS", raising=False)
    with Session(bind) as db:
        assert retention.purge_expired(db, NOW) == []
        assert db.get(TenderRecord, "TENDER-2026-931") is not None
    assert (root / "TENDER-2026-931" / "Bid.pdf").is_file()


# --- the public brief -----------------------------------------------------------------------------

def test_public_brief_leaves_out_contract_documents_terms_findings_and_the_cas():
    payload = {
        "ingest_stats": {"api_cost_usd": 1.0, "pages": 3},
        "documents": [{"filename": "MR.pdf", "role": "mr_spec", "vendor_id": None, "page_count": 3},
                      {"filename": "FA.pdf", "role": "framework_agreement", "vendor_id": None, "page_count": 9},
                      {"filename": "Owner.pdf", "role": "owner_contract", "vendor_id": None, "page_count": 9}],
        "tender": {
            "compute_cogs": 1.0,
            "parameters": [{"id": "PARAM-01", "name": "Rated voltage"}, {"id": "CT-01", "name": "LDs"}],
            "findings": [{"vendor_id": "vendor-a", "param_id": "PARAM-01", "kind": "gap"},
                         {"vendor_id": "vendor-a", "param_id": "CT-03", "category": "fa_conformance",
                          "message": "framework_agreement says 36 months"},
                         {"vendor_id": "vendor-a", "param_id": None, "category": "budget"}],
            "contract_terms": [{"param_id": "CT-01"}],
        },
        "cas": {"sections": []},
        "contract": {"terms": []},
    }
    public = public_brief_payload(payload)
    text = json.dumps(public)
    assert "framework_agreement" not in text and "owner_contract" not in text
    assert [d["filename"] for d in public["documents"]] == ["MR.pdf"]
    assert [p["id"] for p in public["tender"]["parameters"]] == ["PARAM-01"]
    assert public["tender"]["findings"] == [{"vendor_id": "vendor-a", "param_id": "PARAM-01", "kind": "gap"}]
    assert "contract_terms" not in public["tender"] and "cas" not in public and "contract" not in public
    assert "compute_cogs" not in public["tender"] and public["ingest_stats"] == {"pages": 3}


def test_public_brief_contract_categories_match_the_approver_rules():
    from antova_ingest.approvers import CATEGORIES
    from antova_review import _CONTRACT_FINDING_CATEGORIES

    assert set(_CONTRACT_FINDING_CATEGORIES) == set(CATEGORIES)
