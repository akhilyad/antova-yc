"""TENDER-2026-418 through the HTTP API: ingest, brief, evaluate, review gate, TBE and clarification exports.

Everything the extended class adds (PARAM-09..15, the findings register, exposures, the project baseline)
must reach the routes a buyer and a reviewer use, and nothing may come out as final until a person has
looked at it.
"""

import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import text

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_db import SessionLocal
from antova_ingest import clarify, ontology
from antova_ingest.costs import ConfigError, parse_rate_table
from antova_ingest.tests.fakes import AnswerKeyLocator
from antova_workers import job_manager

TENDER_ID = "TENDER-2026-418"
PACK = Path(__file__).resolve().parent / "samples" / TENDER_ID
HEADERS = {REVIEW_HEADER: "test-review-token"}  # token set in conftest.py
REVIEW = f"/api/review/tenders/{TENDER_ID}"
EXPOSURE_CELLS = {("vendor-b", "PARAM-09"), ("vendor-b", "PARAM-10"), ("vendor-b", "PARAM-11"), ("vendor-c", "PARAM-10")}


def _expected():
    return json.loads((PACK / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    previous = job_manager.locator_factory
    job_manager.locator_factory = lambda tender_dir, mode: AnswerKeyLocator(_expected())
    try:
        with TestClient(app, headers=HEADERS) as c:
            job_id = c.post("/api/jobs/ingest", json={"tender_id": TENDER_ID, "mode": "replay"}).json()["job_id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = c.get(f"/api/jobs/{job_id}").json()
                if status["status"] in ("COMPLETED", "FAILED"):
                    break
                time.sleep(0.05)
            assert status["status"] == "COMPLETED", status
            yield c
    finally:
        job_manager.locator_factory = previous
        # Leave the shared test database as other files expect it: no reviews or overrides from this tender.
        db = SessionLocal()
        try:
            for table in ("cell_reviews", "adder_overrides"):
                db.execute(text(f"DELETE FROM {table} WHERE tender_id = :id"), {"id": TENDER_ID})
            db.commit()
        finally:
            db.close()


@pytest.fixture
def brief(client):
    return client.get(REVIEW).json()


def test_the_brief_carries_the_extended_parameters_and_the_register(brief):
    ids = [p["id"] for p in brief["tender"]["parameters"]]
    assert ids == [p.id for p in ontology.parameters_for(ontology.EXTENDED_EQUIPMENT_CLASS)]
    assert len(brief["tender"]["findings"]) == 23
    assert brief["tender"]["equipment_class"] == ontology.EXTENDED_EQUIPMENT_CLASS


def test_evaluate_reproduces_the_answer_key_prices(client):
    body = client.post(f"{REVIEW}/evaluate", json={}).json()
    want = {v: float(d["true_evaluated_price"]) for v, d in _expected()["vendors"].items()}
    got = {f["vendor_id"]: f["true_evaluated_price"] for f in body["financials"]}
    assert got == want


# --- the review gate ---------------------------------------------------------------------------------

def _items(brief):
    return {(i["kind"], i["vendor_id"], i["param_id"]) for i in brief["open_items"]}


def test_an_exposure_gap_asks_for_a_reviewers_acceptance_not_an_adder(brief):
    items = _items(brief)
    for vendor, param in EXPOSURE_CELLS:
        assert ("review_flag", vendor, param) in items
        assert ("unpriced", vendor, param) not in items


def test_a_gap_that_is_neither_priced_nor_an_exposure_still_needs_an_adder_or_override(brief):
    items = _items(brief)
    for cell in (("vendor-b", "PARAM-14"), ("vendor-b", "PARAM-15"), ("vendor-c", "PARAM-15")):
        assert ("unpriced", *cell) in items
    assert ("needs_inspection", "vendor-c", "PARAM-14") in items


def test_every_new_cell_that_is_not_compliant_is_in_front_of_the_gate(brief):
    items = {(v, p) for _, v, p in _items(brief)}
    new = {p.id for p in ontology.EXTENDED_PARAMETERS}
    for param in brief["tender"]["parameters"]:
        if param["id"] not in new:
            continue
        for vendor, cell in param["vendors"].items():
            priced = cell["pricing_status"] == "PRICED"
            if cell["status"] != "COMPLIANT" and not priced:
                assert (vendor, param["id"]) in items, (vendor, param["id"], cell["status"])
    assert not brief["review"]["final"]


def test_accepting_an_exposure_clears_its_item(client):
    before = _items(client.get(REVIEW).json())
    assert ("review_flag", "vendor-b", "PARAM-11") in before
    put = client.put(f"{REVIEW}/reviews/PARAM-11/vendor-b",
                     json={"action": "approve", "note": "liability cap accepted at 20%, exposure noted"})
    assert put.status_code == 200 and put.json()["state"] == "reviewed"
    after = _items(client.get(REVIEW).json())
    assert ("review_flag", "vendor-b", "PARAM-11") not in after
    assert ("unpriced", "vendor-b", "PARAM-11") not in after


# --- exports -----------------------------------------------------------------------------------------

def _clear_the_gate(client) -> dict:
    """Do what a reviewer must before the brief can be published: accept every flag, and give every
    unpriced or uninspected cell an adder (0 here, with a note)."""
    for item in client.get(REVIEW).json()["open_items"]:
        cell = f"{item['param_id']}/{item['vendor_id']}"
        if item["kind"] == "review_flag":
            assert client.put(f"{REVIEW}/reviews/{cell}", json={"action": "approve", "note": "checked"}).status_code == 200
        elif item["kind"] in ("unpriced", "needs_inspection"):
            assert client.put(f"{REVIEW}/overrides/{cell}",
                              json={"adder_eur": "0", "note": "pending vendor clarification"}).status_code == 204
    brief = client.get(REVIEW).json()
    assert brief["open_items"] == []
    return brief


def test_the_brief_cannot_be_exported_before_it_is_verified(client):
    assert client.get(f"{REVIEW}/export-tbe.xlsx").status_code == 409


def test_the_tbe_workbook_opens_and_lists_every_finding(client):
    brief = _clear_the_gate(client)
    assert client.post(f"{REVIEW}/verify").status_code == 200
    response = client.get(f"{REVIEW}/export-tbe.xlsx")
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content))
    assert "Findings" in wb.sheetnames and "Adders" in wb.sheetnames
    rows = list(wb["Findings"].iter_rows(values_only=True))
    assert rows[0][:5] == ("Vendor ID", "Parameter", "Severity", "Kind", "Finding")
    assert len(rows) - 1 == len(brief["tender"]["findings"])
    liability = next(r for r in rows if r[0] == "vendor-b" and r[1] == "PARAM-11")
    assert (liability[5], liability[6]) == ("not known", 334400.0)  # no adder rate; the exposure is known
    supervision = next(r for r in rows if r[0] == "vendor-b" and r[1] == "PARAM-13")
    assert supervision[5] == 9600.0 and "120 h" in supervision[7]
    assert all(r[8] for r in rows[1:]), "every finding names its pages"


def test_the_clarification_exports_ask_about_every_new_gap(client, brief):
    csv = client.get(f"{REVIEW}/clarifications.csv").text
    for param in ("PARAM-09", "PARAM-10", "PARAM-11", "PARAM-12", "PARAM-13", "PARAM-14", "PARAM-15"):
        assert f"vendor-b,VoltSwitch Industrial,{param}" in csv  # vendor-b falls short on all seven
    assert "vendor-c,Deltaport Skid Consortium B.V.,PARAM-14" in csv  # silent on altitude: asked for the value
    response = client.get(f"{REVIEW}/clarifications.xlsx?vendor=vendor-b")
    assert response.status_code == 200
    rows = list(load_workbook(io.BytesIO(response.content)).active.iter_rows(values_only=True))
    assert {r[3] for r in rows[1:]} >= {"PARAM-09", "PARAM-10", "PARAM-11", "PARAM-12", "PARAM-13", "PARAM-15"}
    assert all(r[1] == "vendor-b" for r in rows[1:])  # one vendor's file names no other bidder


# --- coverage of the extended class by templates and tables -------------------------------------------

def test_every_new_field_has_its_own_clarification_wording():
    for param in ontology.EXTENDED_PARAMETERS:
        for f in param.fields:
            assert f.name in clarify.FIELD_TEMPLATES, f"{param.id} {f.name} would fall back to the generic question"


def test_an_exposure_class_must_also_be_listed_unpriced():
    table = {"version": "t", "effective_date": "2026-11-14", "currency": "EUR", "base_labour_rate": "60.00",
             "deviation_rates": {}, "unpriced_classes": [], "exposure_classes": ["PARAM-09.lead_time_long"]}
    with pytest.raises(ConfigError, match="must also be listed under unpriced_classes"):
        parse_rate_table(table)
