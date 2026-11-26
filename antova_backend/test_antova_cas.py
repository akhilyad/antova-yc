"""The contract approval sheet (CAS): every field cited, computed, inherited or missing (Task 13).

All names, numbers and clause wording here are invented.
"""

import dataclasses
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_cas import CAS_KEYS, FIELD_KEYS, STATES
from antova_db import SessionLocal
from antova_ingest.artifacts import make_artifact
from antova_ingest.contract_findings import assess
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, load_expected
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import BaselineMoney, DocumentEntry, OwnerTerms, ProjectBaseline, TenderManifest
from antova_ingest.values import PriceRead
from antova_service import persist

D = Decimal
TENDER_ID = "TENDER-2026-402"
CAS = f"/api/review/tenders/{TENDER_ID}/vendors/vendor-b/cas"
REVIEWERS = {
    "pia": {"name": "Pia Procura", "token": "tok-procurement", "roles": ["procurement"]},
    "lea": {"name": "Lea Legal", "token": "tok-legal", "roles": ["legal"]},
}
PIA = {REVIEW_HEADER: "tok-procurement"}

MR = DocumentEntry(file="mr.pdf", role="mr_spec")
BID = DocumentEntry(file="Bid_vendor-b_VoltSwitch.pdf", role="vendor_bid", vendor_id="vendor-b",
                    counterparty_id="cp-b")
FA = DocumentEntry(file="fa-velmar.pdf", role="framework_agreement", counterparty_id="cp-b", dated=date(2025, 3, 1))
INCORPORATION = "The Framework Agreement dated 10 January 2026 is incorporated into and forms part of this offer."
LDS = "LDs are 0.5% per day, capped at 10% of the order value."
NAMED_PLACE = "Delivery FCA, named place: Port of Velmar."
FA_WARRANTY = "The warranty period is 24 months from delivery."
PROJECT = ProjectBaseline(
    owner_terms=OwnerTerms(contract_value=BaselineMoney(amount=D("20000000"), currency="EUR"), ld_rate=D("0.1"),
                           ld_rate_basis="percent_per_day", ld_cap=D("10"), ld_cap_basis="percent",
                           package_ld_share_pct=D("10"), warranty_months=36, warranty_start="delivery"),
    delay_scenario_weeks={"value": "4"}, frozen_budget=BaselineMoney(amount=D("2000000"), currency="EUR"),
    fx=[{"from_currency": "GBP", "to_currency": "EUR", "rate": "1.17", "dated": "2026-11-01"}],
    signing_before_ntp=False)


def _ev(file, param_id, quote, page):
    return evidence(param_id, "commercial", page, quote, filename=file)


def _contracted():
    manifest = TenderManifest(tender_id=TENDER_ID, title="t", client="c", equipment_class="mv_gis_switchgear",
                              rate_table="mv_gis_switchgear_v2", documents=[MR, BID, FA], engineering_estimates={},
                              synthetic=True, project=PROJECT)
    return assess(manifest, {
        BID.file: [_ev(BID.file, "CT-16", INCORPORATION, 20), _ev(BID.file, "CT-01", LDS, 9),
                   _ev(BID.file, "CT-12", NAMED_PLACE, 11)],
        FA.file: [_ev(FA.file, "CT-03", FA_WARRANTY, 4)]},
        {"vendor-b": (PriceRead(D("1800000"), "GBP", None), None)}, {"vendor-b": (None, None)})


@pytest.fixture(scope="module")
def ingested():
    return run_ingest(PACK, locator=AnswerKeyLocator(load_expected()))


def _persist(ingested):
    tender = ingested.tender.model_copy(update={"project": PROJECT.model_dump(mode="json", exclude_none=True)})
    result = dataclasses.replace(ingested, tender=tender, artifacts={
        **ingested.artifacts, "contracted": make_artifact("contracted", _contracted())})
    db = SessionLocal()
    try:
        persist(result, db)
        for table in ("cell_reviews", "adder_overrides", "finding_dispositions", "approver_removals"):
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


@pytest.fixture
def cas(client):
    response = client.get(CAS, headers=PIA)
    assert response.status_code == 200
    return response.json()


def _fields(cas):
    for key in CAS_KEYS:
        if key in ("risks", "approvers", "meta"):
            continue
        for name, field in cas[key].items():
            yield key, name, field


def test_top_level_keys_and_every_field_shape(cas):
    assert tuple(cas) == CAS_KEYS == ("project", "law", "price", "delivery", "payment_bonds", "lds", "warranty",
                                      "risks", "technical", "approvers", "meta")
    for section, name, field in _fields(cas):
        assert set(field) == set(FIELD_KEYS), (section, name)
        assert field["state"] in STATES, (section, name)
        if field["state"] == "missing":
            assert field["reason"], (section, name)
        if field["state"] in ("cited", "inherited"):
            assert field["citations"], (section, name)
        if field["state"] == "computed":
            assert field["formula"], (section, name)


def test_ld_cap_is_cited_with_the_offer_citation(cas):
    field = cas["lds"]["ld_cap"]
    assert field["state"] == "cited"
    assert [(c["filename"], c["page"], c["quote"]) for c in field["citations"]] == [(BID.file, 9, LDS)]


def test_an_inherited_warranty_is_inherited_with_two_citations(cas):
    field = cas["warranty"]["warranty_months"]
    assert field["state"] == "inherited"
    assert [(c["filename"], c["quote"]) for c in field["citations"]] == [(BID.file, INCORPORATION),
                                                                       (FA.file, FA_WARRANTY)]


def test_a_missing_ntp_deadline_is_missing_with_its_reason(cas):
    field = cas["project"]["ntp_deadline_months"]
    assert field["state"] == "missing"
    assert field["reason"] == "not stated in the offer"
    assert field["citations"] == [] and field["display"] != "0"


def test_price_variance_is_computed_with_the_fx_formula(cas):
    field = cas["price"]["variance"]
    assert field["state"] == "computed"
    assert field["display"] == "EUR 106,000.00"
    assert "× 1.17 (2026-11-01) buyer-entered GBP→EUR rate" in field["formula"]
    assert cas["price"]["variance_true_job_cost"]["state"] == "missing"
    assert cas["price"]["variance_true_job_cost"]["reason"] == "true job cost not known"


def test_the_price_carries_the_headline_citation(cas):
    field = cas["price"]["offered"]
    assert field["state"] == "computed" and field["citations"]
    assert field["citations"][0]["filename"].startswith("Bid_vendor-b")


def test_a_citation_only_field_shows_its_quote(cas):
    field = cas["delivery"]["named_place"]
    assert (field["state"], field["display"]) == ("cited", NAMED_PLACE)


def test_no_missing_field_is_shown_as_zero(cas):
    for section, name, field in _fields(cas):
        if field["state"] == "missing":
            assert field["display"] not in ("0", "", "0.00"), (section, name)


def test_risks_are_grouped_and_carry_dispositions(client):
    finding = "vendor-b|CT-03|owner|warranty_months"
    put = client.put(f"{CAS.rsplit('/', 1)[0]}/findings/{finding}/disposition",
                     json={"disposition": "negotiate", "reason": "ask for 36 months"}, headers=PIA)
    assert put.status_code == 200
    risks = client.get(CAS, headers=PIA).json()["risks"]
    assert {"technical", "fa_conformance", "back_to_back", "budget"} <= set(risks)
    (row,) = [r for r in risks["back_to_back"] if r["finding_id"] == finding]
    assert set(row) == {"finding_id", "category", "message", "money", "gap", "disposition", "disposition_state",
                        "citations"}
    assert (row["disposition"], row["disposition_state"]) == ("negotiate", "current")
    (budget,) = risks["budget"]
    assert budget["disposition"] is None and budget["disposition_state"] == "undecided"


def test_technical_section_links_the_evaluation(cas):
    technical = cas["technical"]
    assert technical["link"]["display"] == f"/audit/{TENDER_ID}"
    assert technical["status"]["display"] == "DRAFT"
    assert technical["deviations"]["state"] == "computed"
    assert technical["open_items"]["state"] == "computed"


def test_meta_names_the_run(cas):
    meta = cas["meta"]
    assert len(meta["run_fingerprint"]) == 64
    assert len(meta["ontology_hash"]) == 64 and len(meta["contract_ontology_hash"]) == 64
    assert "zero_retention_ref" in meta and meta["rate_table_version"]
    assert (meta["tbe_status"], meta["contract_gate_status"]) == ("DRAFT", "DRAFT")
    assert meta["unreviewed_count"] > 0


def test_approvers_drop_a_removed_role_and_show_its_reason(client, cas):
    roles = [s["role"] for s in cas["approvers"]["suggested"]]
    assert "business_director" in roles  # 5.3 % over the frozen budget
    response = client.put(f"{CAS}/approvers/business_director", json={"removed": True, "reason": "budget re-approved"},
                          headers=PIA)
    assert response.status_code == 200
    approvers = client.get(CAS, headers=PIA).json()["approvers"]
    assert "business_director" not in [s["role"] for s in approvers["suggested"]]
    (removed,) = approvers["removed"]
    assert (removed["role"], removed["reason"], removed["removed_by"]) == (
        "business_director", "budget re-approved", "pia")
    assert removed["suggestion_reason"]


def test_removing_a_role_not_suggested_is_refused(client):
    response = client.put(f"{CAS}/approvers/procurement", json={"removed": True, "reason": "x"}, headers=PIA)
    assert response.status_code == 422


def test_cas_routes_need_the_review_token(client):
    assert client.get(CAS).status_code == 401
    assert client.put(f"{CAS}/approvers/business_director", json={"removed": True, "reason": "r"}).status_code == 401


# --- delivery: lead time and schedule float (ruling R38) ------------------------------------------------------

from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from antova_cas import _delivery, _Terms  # noqa: E402

PACK_418 = Path(__file__).resolve().parent / "samples" / "TENDER-2026-418"
DATES = {"po_award_date": {"value": "2027-01-15"}, "required_on_site_date": {"value": "2027-12-01"}}
DAP = "Prices are quoted DAP site, Velmar substation."


@pytest.fixture(scope="module")
def tender_418():
    expected = json.loads((PACK_418 / "expected.json").read_text(encoding="utf-8"))
    return run_ingest(PACK_418, locator=AnswerKeyLocator(expected), expected_tender_id="TENDER-2026-418").tender


def _loaded(tender, project, incoterm_quote=DAP):
    tender = tender.model_copy(deep=True, update={"project": project})
    cell = next(p for p in tender.parameters if p.id == "PARAM-06").vendors["vendor-b"]
    cell.field_citations = {**cell.field_citations, "incoterm": {**cell.field_citations["incoterm"],
                                                                 "quote": incoterm_quote}}
    return SimpleNamespace(tender=tender, documents=[])


def test_lead_time_is_cited_from_the_tbe_cell(tender_418):
    field = _delivery(_loaded(tender_418, DATES), "vendor-b", _Terms([], []))["lead_time"]
    assert (field["state"], field["display"]) == ("cited", "44 weeks")
    assert [(c["filename"], c["page"]) for c in field["citations"]] == [("Bid_vendor-b_VoltSwitch.pdf", 21)]


def test_schedule_float_is_computed_with_the_project_dates(tender_418):
    field = _delivery(_loaded(tender_418, DATES), "vendor-b", _Terms([], []))["schedule_float"]
    # 2027-01-15 + 44 weeks (308 days) = 2027-11-19; 2027-12-01 − 2027-11-19 = +12 days.
    assert (field["state"], field["display"]) == ("computed", "+12 days")
    assert "PO award 2027-01-15" in field["formula"] and "float +12 days" in field["formula"]


def test_schedule_float_is_missing_without_a_required_on_site_date(tender_418):
    project = {"po_award_date": DATES["po_award_date"]}
    field = _delivery(_loaded(tender_418, project), "vendor-b", _Terms([], []))["schedule_float"]
    assert field["state"] == "missing" and field["display"] != "0"
    assert field["reason"] == "the project has no required on-site date"


def test_no_lead_time_cell_is_missing(cas):
    assert cas["delivery"]["lead_time"]["state"] == "missing"  # TENDER-2026-402 has no PARAM-09
    assert cas["delivery"]["schedule_float"]["reason"] == "no lead time to schedule from"
