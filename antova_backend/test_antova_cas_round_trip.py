"""End to end: the synthetic call-off pack TENDER-2026-419 from ingest to a final contract approval sheet.

Ingest with the answer-key locator (a synthetic manifest, so no zero-retention reference is needed), sign off
the TBE, review the flagged contract cells, dispose every Kestrel finding, sign off Kestrel's contract gate and
export the DOCX. Every figure asserted is worked out here by hand from the scenario's numbers.
"""

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import text

from antova_api import app
from antova_auth import REVIEW_HEADER, ROLES
from antova_db import SessionLocal
from antova_ingest import approvers
from antova_ingest.answer_key import AnswerKeyLocator
from antova_ingest.pipeline import run_ingest
from antova_service import persist
from samples import scenario_419

TENDER_ID = scenario_419.TENDER_ID
PACK = Path(scenario_419.__file__).resolve().parent / TENDER_ID
KESTREL = scenario_419.KESTREL.id
REVIEW = f"/api/review/tenders/{TENDER_ID}"
VENDOR = f"{REVIEW}/vendors/{KESTREL}"
# One reviewer per role, so each finding is accepted by exactly the role the approver rules name.
REVIEWERS = {role: {"name": f"Reviewer {role}", "token": f"tok-{role}", "roles": [role]} for role in ROLES}


def _as(role):
    return {REVIEW_HEADER: f"tok-{role}"}


PIA = _as("procurement")


@pytest.fixture(scope="module")
def ingested():
    expected = json.loads((PACK / "expected.json").read_text(encoding="utf-8"))
    return run_ingest(PACK, locator=AnswerKeyLocator(expected), expected_tender_id=TENDER_ID)


def _clean():
    db = SessionLocal()
    try:
        for table in ("cell_reviews", "adder_overrides", "finding_dispositions", "approver_removals"):
            db.execute(text(f"DELETE FROM {table} WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(ingested):
    mp = pytest.MonkeyPatch()
    mp.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    db = SessionLocal()
    try:
        persist(ingested, db)
        db.commit()
    finally:
        db.close()
    _clean()
    yield TestClient(app)
    _clean()
    mp.undo()


def _ld_gap_by_hand() -> Decimal:
    """Owner: 48,000,000 x 0.5% per week = 240,000 per week; 8 weeks = 1,920,000 (under the 10% cap of 4,800,000);
    this package carries 2.5% = 48,000.00. Kestrel pays back 441,000 x min(0.5% x 8 weeks = 4%, its 5% cap)
    = 17,640.00. The gap is 48,000.00 - 17,640.00."""
    project = scenario_419.PROJECT
    value = Decimal(project["owner_terms"]["contract_value"]["amount"])
    share = Decimal(project["owner_terms"]["package_ld_share_pct"]) / 100
    weeks = Decimal(project["delay_scenario_weeks"]["value"])
    rate, owner_cap, vendor_cap = Decimal("0.005"), Decimal("0.10"), Decimal("0.05")
    owed = min(value * rate * weeks, value * owner_cap) * share
    price = Decimal(scenario_419.KESTREL.headline)
    paid = price * min(rate * weeks, vendor_cap)
    return (owed - paid).quantize(Decimal("0.01"))


@pytest.fixture(scope="module")
def signed_off(client):
    """The whole review, in order; returns the final CAS and the DOCX bytes."""
    # 1. The TBE: both bids are fully compliant; what is open is the buyer-entered project baseline, which
    # the reviewer confirms field by field before signing off.
    blocked = client.post(f"{REVIEW}/verify", json={}, headers=PIA)
    assert blocked.status_code == 409
    items = blocked.json()["open_items"]
    assert {i["kind"] for i in items} == {"project_field"}
    for item in items:
        response = client.put(f"{REVIEW}/reviews/{item['param_id']}/{item['vendor_id']}",
                              json={"action": "approve", "note": "matches the project baseline"}, headers=PIA)
        assert response.status_code == 200, (item, response.json())
    response = client.post(f"{REVIEW}/verify", json={}, headers=PIA)
    assert response.status_code == 200, response.json()

    contract = client.get(f"{VENDOR}/contract", headers=PIA).json()
    run_id = contract["run_id"]
    # 2. The flagged contract cells (conflicts, flags, the cited order of precedence) are reviewed.
    cells = [i["param_id"] for i in contract["gate"]["open_items"] if i["kind"] == "contract_cell"]
    assert "CT-17" in cells
    assert "CT-13" in cells  # the agreement's price-basis clauses are quoted together (R40): compared by hand
    for param_id in cells:
        response = client.put(f"{REVIEW}/reviews/{param_id}/{KESTREL}",
                              json={"action": "approve", "note": "checked against the clause"}, headers=PIA)
        assert response.status_code == 200, (param_id, response.json())

    # 3. Every Kestrel finding gets a disposition: the LD cap gap is accepted by procurement, the warranty start
    # is negotiated, and the rest are accepted by the role the approver rules name.
    rules = approvers.load_rules()
    findings = client.get(f"{VENDOR}/cas", headers=PIA).json()["risks"]
    for group in findings.values():
        for finding in group:
            if finding["category"] == "technical":
                continue
            fid = finding["finding_id"]
            if fid.endswith("|warranty_start"):
                body, role = {"disposition": "negotiate", "reason": "warranty to run from PAC"}, "procurement"
            else:
                role = approvers.role_to_accept(rules, {"param_id": fid.split("|")[1], "category": finding["category"]})
                if fid == f"{KESTREL}|CT-01|fa|ld_cap":
                    assert role == "procurement"
                body = {"disposition": "accept", "reason": "accepted for this call-off"}
            response = client.put(f"{VENDOR}/findings/{fid}/disposition", json=body, headers=_as(role))
            assert response.status_code == 200, (fid, response.json())

    # 4. Kestrel's contract gate.
    response = client.post(f"{VENDOR}/contract/verify", json={"run_id": run_id}, headers=PIA)
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "VERIFIED"

    cas = client.get(f"{VENDOR}/cas", headers=PIA).json()
    docx = client.get(f"{VENDOR}/cas.docx", headers=PIA)
    assert docx.status_code == 200, docx.text
    return {"cas": cas, "docx": docx.content, "contract": client.get(f"{VENDOR}/contract", headers=PIA).json()}


def test_the_ld_cap_is_cited_from_the_kestrel_offer_page(signed_off):
    field = signed_off["cas"]["lds"]["ld_cap"]
    assert (field["state"], field["display"]) == ("cited", "5 %")
    assert [(c["filename"], c["page"]) for c in field["citations"]] == [(scenario_419.KESTREL.file, 21)]


def test_the_back_to_back_ld_exposure_is_the_hand_computed_gap(signed_off):
    assert _ld_gap_by_hand() == Decimal("30360.00")
    field = signed_off["cas"]["lds"]["ld_shortfall"]
    assert field["state"] == "computed"
    assert field["display"] == f"EUR {_ld_gap_by_hand():,.2f}"
    assert "EUR 48,000.00" in field["formula"] and "EUR 17,640.00" in field["formula"]


def test_the_warranty_gap_is_unknown_because_the_starts_differ(signed_off):
    field = signed_off["cas"]["warranty"]["warranty_shortfall"]
    assert field["state"] == "missing"
    assert field["reason"] == ("the vendor's warranty starts at delivery and the owner warranty at PAC; "
                               "there are no dates to bridge them")


def test_title_is_inherited_from_the_framework_agreement(signed_off):
    row = next(r for r in signed_off["contract"]["rows"] if r["param_id"] == "CT-09")
    assert row["state"] == "inherited"
    files = {c["filename"] for c in row["offered"]["citations"]}
    assert files == {scenario_419.KESTREL.file, scenario_419.FA_KESTREL}  # the CT-16 clause and the FA clause


def test_the_liability_check_shows_the_gbp_to_eur_conversion(signed_off):
    row = next(r for r in signed_off["contract"]["rows"] if r["param_id"] == "CT-02")
    check = next(c for c in row["checks"] if c["baseline"] == "fa" and c["field"] == "liability_cap_money")
    # GBP 350,000 x 1.17 = EUR 409,500.00, short of the agreement's EUR 450,000.
    assert check["offered"] == "GBP 350,000 × 1.17 (2026-11-01, GBP→EUR) = EUR 409,500.00"
    assert (check["required"], check["passed"]) == ("EUR 450,000", False)


def test_approvers_include_legal_and_not_the_business_director(signed_off):
    roles = [a["role"] for a in signed_off["cas"]["approvers"]["suggested"]]
    assert "legal" in roles  # a finding on CT-02, a legal term
    assert any(f["finding_id"].startswith(f"{KESTREL}|CT-02|") for f in signed_off["cas"]["risks"]["fa_conformance"])
    # 441,000 against a frozen budget of 480,000: under budget, so no business director.
    assert Decimal(scenario_419.KESTREL.headline) < Decimal(scenario_419.PROJECT["frozen_budget"]["amount"])
    assert "business_director" not in roles, signed_off["cas"]["approvers"]


def test_the_sheet_is_signed_off_and_the_final_docx_has_no_draft_mark(signed_off):
    meta = signed_off["cas"]["meta"]
    assert (meta["tbe_status"], meta["contract_gate_status"], meta["unreviewed_count"]) == ("VERIFIED", "VERIFIED", 0)
    document = Document(io.BytesIO(signed_off["docx"]))
    texts = [p.text for p in document.paragraphs]
    texts += [cell.text for table in document.tables for row in table.rows for cell in row.cells]
    for section in document.sections:
        texts += [p.text for p in section.header.paragraphs] + [p.text for p in section.footer.paragraphs]
    assert texts and not any("DRAFT" in t for t in texts)
