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


# --- review round 1 ---------------------------------------------------------------------------------

def test_deletion_takes_the_documents_out_of_the_manifest(seeded):
    """R39: a re-ingest after the deletion reads a manifest without the deleted documents."""
    manifest = {"tender_id": T1, "documents": [{"file": "MR.pdf", "role": "mr_spec"},
                                               {"file": "FA_Kestrel.pdf", "role": "framework_agreement",
                                                "counterparty_id": CP},
                                               {"file": "FA_Osprey.pdf", "role": "framework_agreement",
                                                "counterparty_id": OTHER_CP}]}
    (seeded / T1 / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert TestClient(app).delete(f"/api/review/counterparties/{CP}", headers=FOUNDER).status_code == 200
    kept = json.loads((seeded / T1 / "manifest.json").read_text(encoding="utf-8"))
    assert [d["file"] for d in kept["documents"]] == ["MR.pdf", "FA_Osprey.pdf"]
    assert kept["tender_id"] == T1


def test_public_brief_from_a_tender_with_an_fa_names_no_contract_file(tmp_path):
    from antova_briefs_api import get_brief
    from antova_ingest.tests.test_db import DOCS, _tender as tbe_tender
    from antova_service import persist
    from test_antova_contract_db import BID, CONTRACTED, FA, _result

    bind = create_engine(f"sqlite:///{(tmp_path / 'b.db').as_posix()}")
    init_db(bind)
    result = _result(tbe_tender(), [*DOCS[:1], BID, FA], CONTRACTED)
    result.stats.update({
        "locales": {FA.filename: "en", BID.filename: "en"},
        "no_text_pages": {FA.filename: [3], BID.filename: [4]},
        "stage_log": [{"stage": "Parse", "pct": 20, "t_ms": 1}, {"stage": "Read contract terms", "pct": 95, "t_ms": 2}],
    })
    result = __import__("dataclasses").replace(result, warnings=[f"{FA.filename}: not read.", "b.pdf: page 4 has no text"])
    with Session(bind) as db:
        persist(result, db)
        tender = db.query(TenderRecord).one()
        tender.status, tender.verified_at = "VERIFIED", NOW.replace(tzinfo=None)
        stats = json.loads(tender.ingest_stats)
        stats.update(result.stats)
        stats["warnings"] = result.warnings
        tender.ingest_stats = json.dumps(stats)
        tender.stage_log = json.dumps(result.stats["stage_log"])
        db.commit()
        body = json.loads(json.dumps(get_brief(tender.share_id, db), default=str))
    text = json.dumps(body)
    assert FA.filename not in text and "framework_agreement" not in text
    assert body["ingest_stats"]["locales"] == {BID.filename: "en"}
    assert body["ingest_stats"]["no_text_pages"] == {BID.filename: [4]}
    assert [s["stage"] for s in body["stage_log"]] == ["Parse"]


def test_a_reviewer_cannot_put_files_into_a_synthetic_tender(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTOVA_TENDERS_DIR", str(tmp_path))
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    folder = tmp_path / "TENDER-2026-977"
    _write(folder / "manifest.json", json.dumps({"tender_id": "TENDER-2026-977", "synthetic": True}).encode())
    pdf = (__import__("antova_ingest.tests.fakes", fromlist=["PACK"]).PACK / "Bid_vendor-a_Nordwerk.pdf").read_bytes()
    url = "/api/review/tenders/TENDER-2026-977/files/Real_contract.pdf"
    client = TestClient(app)
    refused = client.put(url, content=pdf, headers={REVIEW_HEADER: "tok-pm", "Content-Type": "application/pdf"})
    assert refused.status_code == 403 and not (folder / "Real_contract.pdf").exists()
    assert client.put(url, content=pdf, headers={**FOUNDER, "Content-Type": "application/pdf"}).status_code == 201


def test_a_refused_synthetic_manifest_creates_no_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTOVA_TENDERS_DIR", str(tmp_path))
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    response = TestClient(app).put("/api/review/tenders/TENDER-2026-978/manifest",
                                   content=json.dumps({"tender_id": "TENDER-2026-978", "synthetic": True}),
                                   headers={REVIEW_HEADER: "tok-pm"})
    assert response.status_code == 403 and not (tmp_path / "TENDER-2026-978").exists()


def test_a_failed_folder_removal_is_swept_by_the_next_purge(own_db, monkeypatch):
    bind, root = own_db
    monkeypatch.setenv("ANTOVA_RETENTION_DAYS", "30")
    real_rmtree = retention.shutil.rmtree

    def failing(path, *a, **k):
        raise OSError("disk busy")

    monkeypatch.setattr(retention.shutil, "rmtree", failing)
    with Session(bind) as db:
        assert retention.purge_expired(db, NOW) == ["TENDER-2026-931"]
        assert db.get(TenderRecord, "TENDER-2026-931") is None
    leftovers = [p.name for p in root.iterdir()]
    assert "TENDER-2026-931" not in leftovers
    assert any(n.startswith(retention.TOMBSTONE_PREFIX) for n in leftovers)  # kept, to sweep later
    monkeypatch.setattr(retention.shutil, "rmtree", real_rmtree)
    with Session(bind) as db:
        assert retention.purge_expired(db, NOW) == []
    assert sorted(p.name for p in root.iterdir()) == ["TENDER-2026-929"]


def test_an_old_folder_without_a_tender_row_is_swept_a_new_one_kept(own_db, monkeypatch):
    import os
    bind, root = own_db
    monkeypatch.setenv("ANTOVA_RETENTION_DAYS", "30")
    old, new = root / "TENDER-2026-950", root / "TENDER-2026-951"
    _write(old / "x.pdf", b"%PDF-1.4")
    _write(new / "x.pdf", b"%PDF-1.4")  # an upload not yet ingested
    stamp = (NOW - timedelta(days=40)).timestamp()
    os.utime(old, (stamp, stamp))
    with Session(bind) as db:
        retention.purge_expired(db, datetime.now(timezone.utc))
    assert not old.exists() and new.is_dir()


@pytest.mark.parametrize("value", ["thirty", "0", "-5", ""])
def test_a_retention_period_that_is_not_a_positive_number_purges_nothing(own_db, monkeypatch, value):
    bind, root = own_db
    monkeypatch.setenv("ANTOVA_RETENTION_DAYS", value)
    with Session(bind) as db:
        assert retention.purge_expired(db, NOW) == []
    assert (root / "TENDER-2026-931").is_dir()


def test_purge_reads_a_non_utc_now_as_the_same_instant(own_db, monkeypatch):
    bind, _ = own_db
    monkeypatch.setenv("ANTOVA_RETENTION_DAYS", "30")
    # 30.5 days after the newer tender's ingest, given in UTC+5: it is past retention only if read as UTC.
    later = (NOW + timedelta(days=1, hours=12)).astimezone(timezone(timedelta(hours=5)))
    with Session(bind) as db:
        assert retention.purge_expired(db, later) == ["TENDER-2026-929", "TENDER-2026-931"]
