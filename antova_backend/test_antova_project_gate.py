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
