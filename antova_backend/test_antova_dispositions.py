"""Finding dispositions (accept / negotiate / waiver_needed) and the negotiation questions they add.

All names, numbers and clause wording here are invented.
"""

import csv
import dataclasses
import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_db import SessionLocal
from antova_ingest import clarify
from antova_ingest.artifacts import make_artifact, sha256_of
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, load_expected
from antova_service import persist

TENDER_ID = "TENDER-2026-402"
REVIEW = f"/api/review/tenders/{TENDER_ID}"
FA_ID = "vendor-b|CT-03|fa|warranty_months"
OWNER_ID = "vendor-b|CT-01|owner|ld_exposure"
REVIEWERS = {
    "pia": {"name": "Pia Procura", "token": "tok-procurement", "roles": ["procurement"]},
    "eli": {"name": "Eli Engineer", "token": "tok-engineering", "roles": ["engineering"]},
}


def _citation(filename, page, quote):
    return {"filename": filename, "page": page, "bbox": [0, 0, 1, 1], "quote": quote, "section": "commercial",
            "paragraph": "", "match": "exact", "rects": []}


def _finding(id_, param_id, category, offered):
    citations = [_citation("Bid_vendor-b_VoltSwitch.pdf", 7, f"Warranty: {offered} months."),
                 _citation("Owner-Contract-Halden.pdf", 3, "Owner warranty 36 months.")]
    return {"id": id_, "vendor_id": "vendor-b", "param_id": param_id, "category": category, "severity": "medium",
            "message": f"The offer gives {offered} months; the owner contract value is EUR 4,000,000.",
            "money": [], "gap": None, "citations": citations,
            "evidence_hash": sha256_of({"values": {"offered": offered}, "citations": citations})}


def _contracted(offered=24):
    return {
        "terms": [{"param_id": "CT-03", "vendor_id": "vendor-b", "state": "cited", "offered": None, "fa": None,
                   "owner": None, "flags": [], "fa_present": True, "ld_exclusion_passages": []}],
        "checks": [], "gaps": [], "budget": {},
        "findings": [_finding(FA_ID, "CT-03", "fa_conformance", offered),
                     _finding(OWNER_ID, "CT-01", "back_to_back", offered)],
        "not_checked": [],
    }


@pytest.fixture(scope="module")
def ingested():
    return run_ingest(PACK, locator=AnswerKeyLocator(load_expected()))


def _persist(ingested, offered=24):
    result = dataclasses.replace(ingested, artifacts={
        **ingested.artifacts, "contracted": make_artifact("contracted", _contracted(offered))})
    db = SessionLocal()
    try:
        persist(result, db)
        for table in ("cell_reviews", "adder_overrides", "finding_dispositions"):
            db.execute(text(f"DELETE FROM {table} WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client(ingested, monkeypatch):
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    _persist(ingested)
    yield TestClient(app)
    _persist(ingested)


def _put(client, token, finding_id, disposition, reason="checked against the FA", **extra):
    return client.put(f"{REVIEW}/vendors/vendor-b/findings/{finding_id}/disposition",
                      json={"disposition": disposition, "reason": reason, **extra},
                      headers={REVIEW_HEADER: token})


def test_procurement_accepts_an_fa_finding_as_itself(client):
    response = _put(client, "tok-procurement", FA_ID, "accept", reviewer="someone-else")
    assert response.status_code == 200
    body = response.json()
    assert (body["finding_id"], body["disposition"], body["reviewer"], body["state"]) == (
        FA_ID, "accept", "pia", "current")


def test_engineering_cannot_accept_an_fa_finding(client):
    response = _put(client, "tok-engineering", FA_ID, "accept")
    assert response.status_code == 403
    assert response.json() == {"detail": "Accepting this finding needs the procurement role."}


def test_negotiate_adds_a_question_to_the_vendors_clarifications(client):
    url = f"{REVIEW}/clarifications.csv"
    before = list(csv.DictReader(io.StringIO(client.get(url, params={"vendor": "vendor-b"},
                                                         headers={REVIEW_HEADER: "tok-engineering"}).text)))
    assert _put(client, "tok-engineering", FA_ID, "negotiate", reason="ask for 36 months").status_code == 200
    after = list(csv.DictReader(io.StringIO(client.get(url, params={"vendor": "vendor-b"},
                                                        headers={REVIEW_HEADER: "tok-engineering"}).text)))
    added = [r for r in after if r["kind"] == "negotiation"]
    assert len(after) == len(before) + 1 and len(added) == 1
    row = added[0]
    assert (row["vendor_id"], row["param_id"], row["param_name"]) == ("vendor-b", "CT-03", "Warranty")
    assert row["citations"] == "Bid_vendor-b_VoltSwitch.pdf p.7"  # the owner contract is never cited to the vendor
    assert "EUR" not in row["question"] and "4,000,000" not in row["question"]
    others = client.get(url, params={"vendor": "vendor-a"}, headers={REVIEW_HEADER: "tok-engineering"}).text
    assert "negotiation" not in others


def test_disposition_goes_stale_on_reingest(client, ingested):
    """Review Focus 5."""
    assert _put(client, "tok-procurement", FA_ID, "accept").status_code == 200
    url = f"{REVIEW}/vendors/vendor-b/dispositions"
    rows = client.get(url, headers={REVIEW_HEADER: "tok-procurement"}).json()
    assert [(r["finding_id"], r["state"]) for r in rows] == [(FA_ID, "current")]
    db = SessionLocal()
    try:  # re-ingest with a changed cited value, keeping the dispositions
        persist(dataclasses.replace(ingested, artifacts={
            **ingested.artifacts, "contracted": make_artifact("contracted", _contracted(offered=18))}), db)
    finally:
        db.close()
    rows = client.get(url, headers={REVIEW_HEADER: "tok-procurement"}).json()
    assert [(r["finding_id"], r["state"]) for r in rows] == [(FA_ID, "stale")]


def test_latest_disposition_counts_and_stale_negotiation_asks_nothing():
    finding = _finding(FA_ID, "CT-03", "fa_conformance", 24)
    current = {"finding_id": FA_ID, "disposition": "negotiate", "evidence_hash": finding["evidence_hash"]}
    assert len(clarify.contract_rows([finding], [current])) == 1
    assert clarify.contract_rows([finding], [{**current, "evidence_hash": "old"}]) == []
    assert clarify.contract_rows([finding], [{**current, "disposition": "accept"}]) == []


def test_verified_gate_freezes_dispositions(client):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE contract_gates SET status = 'VERIFIED' WHERE tender_id = :id AND vendor_id = 'vendor-b'"),
                   {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()
    assert _put(client, "tok-procurement", FA_ID, "accept").status_code == 409


def test_unknown_finding_is_404_and_waiver_needs_a_role(client):
    assert _put(client, "tok-procurement", "vendor-b|CT-99|fa|x", "negotiate").status_code == 404
    assert _put(client, "tok-procurement", FA_ID, "waiver_needed").status_code == 422
    response = _put(client, "tok-procurement", FA_ID, "waiver_needed", waiver_role="legal")
    assert response.status_code == 200 and response.json()["waiver_role"] == "legal"


def test_dispositions_leave_the_tbe_status_alone(client):
    assert _put(client, "tok-procurement", FA_ID, "accept").status_code == 200
    tender = client.get(REVIEW, headers={REVIEW_HEADER: "tok-procurement"}).json()
    rows = client.get("/api/review/tenders", headers={REVIEW_HEADER: "tok-procurement"}).json()
    row = next(r for r in rows if r["tender_id"] == TENDER_ID)
    assert row["status"] == "DRAFT" and tender is not None
