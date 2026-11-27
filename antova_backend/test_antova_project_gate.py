"""A buyer-entered project value goes through the real pipeline, the database and the review API."""

import json
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_db import SessionLocal
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, load_expected
from antova_service import persist

TENDER_ID = "TENDER-2026-402"
REVIEW = f"/api/review/tenders/{TENDER_ID}"
AUTH = {REVIEW_HEADER: "test-review-token"}  # set in conftest.py
FIELD = "PROJECT.po_award_date"


@pytest.fixture
def with_baseline(tmp_path):
    folder = tmp_path / "tender"
    shutil.copytree(PACK, folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["project"] = {"po_award_date": {"value": "2027-01-15"}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run_ingest(folder, locator=AnswerKeyLocator(load_expected()))
    db = SessionLocal()
    try:
        persist(result, db)
        db.execute(text("DELETE FROM cell_reviews WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()
    yield result
    db = SessionLocal()
    try:
        persist(run_ingest(PACK, locator=AnswerKeyLocator(load_expected())), db)
        db.execute(text("DELETE FROM cell_reviews WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()


def _open(client):
    return [i["param_id"] for i in client.get(REVIEW).json()["open_items"] if i["kind"] == "project_field"]


def test_the_pipeline_carries_the_baseline_into_the_tender(with_baseline):
    assert with_baseline.tender.project == {"po_award_date": {"value": "2027-01-15", "source": "buyer_entered"}}


def test_a_buyer_value_blocks_until_a_reviewer_confirms_it(with_baseline):
    client = TestClient(app, headers=AUTH)
    assert _open(client) == [FIELD]
    put = client.put(f"{REVIEW}/reviews/{FIELD}/-", headers=AUTH, json={"action": "approve", "note": "matches PO plan"})
    assert put.status_code == 200 and put.json()["state"] == "reviewed"
    assert _open(client) == []


def test_an_unknown_project_field_is_a_404(with_baseline):
    client = TestClient(app, headers=AUTH)
    response = client.put(f"{REVIEW}/reviews/PROJECT.energization_date/-", headers=AUTH,
                          json={"action": "approve", "note": "x"})
    assert response.status_code == 404


# --- final review A: the owner's terms and the frozen budget are never on the public brief ----------------

PRIVATE_PROJECT = {
    "po_award_date": {"value": "2027-01-15"},
    "frozen_budget": {"amount": "1500000", "currency": "EUR"},
    "owner_terms": {"contract_value": {"amount": "10000000", "currency": "EUR"},
                    "ld_rate": "0.5", "ld_rate_basis": "percent_per_week",
                    "ld_cap": "10", "ld_cap_basis": "percent", "package_ld_share_pct": "100"},
}


@pytest.fixture
def with_private_project(tmp_path):
    folder = tmp_path / "tender"
    shutil.copytree(PACK, folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["project"] = PRIVATE_PROJECT
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run_ingest(folder, locator=AnswerKeyLocator(load_expected()))
    db = SessionLocal()
    try:
        persist(result, db)
        db.execute(text("DELETE FROM cell_reviews WHERE tender_id = :id"), {"id": TENDER_ID})
        db.execute(text("DELETE FROM adder_overrides WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()
    yield result
    db = SessionLocal()
    try:
        persist(run_ingest(PACK, locator=AnswerKeyLocator(load_expected())), db)
        db.execute(text("DELETE FROM cell_reviews WHERE tender_id = :id"), {"id": TENDER_ID})
        db.execute(text("DELETE FROM adder_overrides WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()


def test_the_public_brief_omits_the_owner_terms_and_the_frozen_budget(with_private_project):
    client = TestClient(app, headers=AUTH)
    for field in _open(client):
        assert client.put(f"{REVIEW}/reviews/{field}/-", json={"action": "approve", "note": "checked"}).status_code == 200
    # The sample's one open cell (see test_antova_briefs._resolve_warranty).
    assert client.put(f"{REVIEW}/reviews/PARAM-06/vendor-b",
                      json={"action": "approve", "reviewer": "a.yadav", "note": "checked"}).status_code == 200
    assert client.put(f"{REVIEW}/overrides/PARAM-06/vendor-b",
                      json={"adder_eur": "9300.00", "note": "priced"}).status_code == 204
    founder = client.get(REVIEW).json()
    assert {"owner_terms", "frozen_budget"} <= set(founder["tender"]["project"])
    verified = client.post(f"{REVIEW}/verify")
    assert verified.status_code == 200, verified.text
    share = founder["share_id"]
    served = TestClient(app).get(f"/api/briefs/{share}")
    assert served.status_code == 200
    body = served.json()
    assert body["tender"]["project"] == {"po_award_date": {"value": "2027-01-15", "source": "buyer_entered"}}
    assert not [i for i in body["open_items"]
                if i["param_id"] in ("PROJECT.owner_terms", "PROJECT.frozen_budget")]
    assert "owner_terms" not in served.text and "frozen_budget" not in served.text
