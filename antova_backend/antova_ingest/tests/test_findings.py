"""The findings register: citations, euro provenance, severity, ordering, storage and serving."""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from antova_db import SessionLocal, TenderRepository
from antova_ingest import ontology
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import AnswerKeyLocator, PACK as PACK_402
from antova_service import persist
from antova_review import brief_payload
from antova_engine import SensitivitySettings
from samples import mutate

D = Decimal
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
PACK = SAMPLES / "TENDER-2026-418"
EXT = mutate.EXTENDED_BASE_TENDER_ID
SEVERITY_OF_CRITICALITY = {"MANDATORY": "critical", "HIGH": "major", "MEDIUM": "minor", "COMMERCIAL": "minor"}


def _run(folder: Path):
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    return run_ingest(folder, locator=AnswerKeyLocator(expected))


@pytest.fixture(scope="module")
def base():
    return _run(PACK)


def _by(findings, vendor, param, kind=None):
    return [f for f in findings if f["vendor_id"] == vendor and f["param_id"] == param
            and (kind is None or f["kind"] == kind)]


# --- the invariants, on the sample and on every mutation of it ----------------------------------------

def check_invariants(result):
    tender = result.tender
    criticality = {p.id: p.criticality.value for p in tender.parameters}
    for f in tender.findings:
        assert f["citations"], f"{f['vendor_id']} {f['param_id']} {f['kind']} cites nothing"
        for c in f["citations"]:
            assert c["filename"] and c["page"] >= 1 and c["quote"]
        if f["kind"] in ("deviation", "conflict", "omitted", "unstated"):
            assert f["severity"] == SEVERITY_OF_CRITICALITY[criticality[f["param_id"]]]
        for m in f["money"]:
            if m["amount"] is None:
                assert m["reason"], f"an unknown amount must say why: {m}"
                continue
            assert m["amount"] == f"{D(m['amount']):.2f}"
            if m["basis"] == "adder":
                lines = m["lines"]
                assert lines and D(m["amount"]) == sum(D(l["qty"]) * D(l["rate"]) for l in lines).quantize(D("0.01"))
            else:
                assert m["formula"] and m["inputs"] and all(i["source"] for i in m["inputs"])
    # No score, grade or index anywhere in the register.
    assert not [k for f in tender.findings for k in f if k in ("score", "index", "grade", "rating")]
    # Ordered by vendor, then critical first.
    order = {v.id: i for i, v in enumerate(tender.vendors)}
    rank = {"critical": 0, "major": 1, "minor": 2}
    keys = [(order[f["vendor_id"]], rank[f["severity"]]) for f in tender.findings]
    assert keys == sorted(keys)


def test_the_sample_register_holds_every_invariant(base):
    assert len(base.tender.findings) == 23  # 21, plus the two standards deviations
    check_invariants(base)


@pytest.mark.parametrize("name", sorted(mutate.operators_for(EXT)))
def test_every_mutation_of_the_extended_pack_holds_every_invariant(name, tmp_path):
    check_invariants(_run(mutate.generate([name], tmp_path, base=EXT)))


# --- what the register says ----------------------------------------------------------------------------

def test_a_priced_gap_shows_its_rate_table_lines_and_total(base):
    (finding,) = _by(base.tender.findings, "vendor-b", "PARAM-13")
    (money,) = finding["money"]
    assert (finding["kind"], finding["severity"], money["amount"]) == ("omitted", "minor", "9600.00")
    assert [l["driver"] for l in money["lines"]] == ["labour", "fixed"]


def test_an_unrated_gap_is_unknown_never_zero(base):
    (finding,) = _by(base.tender.findings, "vendor-b", "PARAM-09", "deviation")
    assert [(m["basis"], m["amount"], m["reason"]) for m in finding["money"]] == [
        ("adder", None, "The rate table has no price for this gap.")]


def test_liability_exposure_names_what_it_was_computed_from(base):
    (finding,) = _by(base.tender.findings, "vendor-b", "PARAM-11")
    exposure = next(m for m in finding["money"] if m["basis"] == "exposure")
    assert exposure["amount"] == "334400.00"
    assert {i["name"]: i["value"] for i in exposure["inputs"]} == {
        "MR liability_cap": "100 %", "bid liability_cap": "20 %", "contract value": "418000.00"}
    assert exposure["inputs"][0]["source"] == "MR p.14" and exposure["inputs"][1]["source"] == "bid p.21"


def test_ld_exposure_is_unknown_until_the_buyer_states_a_delay_to_assume(base):
    for vendor in ("vendor-b", "vendor-c"):
        (finding,) = _by(base.tender.findings, vendor, "PARAM-10")
        exposure = next(m for m in finding["money"] if m["basis"] == "exposure")
        assert exposure["amount"] is None and "the delay to assume" in exposure["reason"]


def test_a_silent_bid_is_a_finding_that_cites_the_mr(base):
    (finding,) = _by(base.tender.findings, "vendor-c", "PARAM-14")
    assert (finding["kind"], finding["severity"], finding["money"]) == ("unstated", "critical", [])
    assert [c["filename"] for c in finding["citations"]] == ["MR-33kV-GIS-Rev4.pdf"]


def test_a_compliant_cell_with_nothing_to_report_is_not_a_finding(base):
    assert not [f for f in base.tender.findings if f["vendor_id"] == "vendor-a" and f["param_id"] != "PARAM-09"]


# --- the schedule: project dates and buyer estimates -----------------------------------------------------

@pytest.fixture(scope="module")
def scheduled(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scheduled") / "t"
    shutil.copytree(PACK, folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["project"] = {"po_award_date": {"value": "2027-01-15"}, "required_on_site_date": {"value": "2027-09-01"}}
    manifest["engineering_estimates"] |= {"delay_cost_per_week": 10000, "ld_delay_weeks": 8}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return _run(folder)


def test_a_late_delivery_is_a_critical_breach_with_a_delay_exposure(scheduled):
    # DDP, 36 weeks from the PO: Sep 24 against a Sep 1 need, 23 days late.
    (finding,) = _by(scheduled.tender.findings, "vendor-a", "PARAM-09")
    assert (finding["kind"], finding["severity"]) == ("critical_path_breach", "critical")
    (money,) = finding["money"]
    assert money["amount"] == "32857.14" and money["formula"] == "≈ 3.29 weeks late × €10,000.00 per week"
    assert [i["name"] for i in money["inputs"]] == ["weeks late", "cost of a week of delay"]
    assert "PO award 2027-01-15" in money["inputs"][0]["source"]


def test_a_delivery_that_ends_before_site_with_no_transit_is_unassessed_not_guessed(scheduled):
    (finding,) = _by(scheduled.tender.findings, "vendor-b", "PARAM-09", "schedule_unassessed")
    assert "before the site" in finding["message"]


def test_the_stated_delay_makes_the_ld_exposure_a_number(scheduled):
    (b,) = _by(scheduled.tender.findings, "vendor-b", "PARAM-10")
    assert next(m for m in b["money"] if m["basis"] == "exposure")["amount"] == "16720.00"   # 4% of 418,000
    (c,) = _by(scheduled.tender.findings, "vendor-c", "PARAM-10")
    assert next(m for m in c["money"] if m["basis"] == "exposure")["amount"] == "0.00"       # 8 weeks: still under both caps


def test_the_scheduled_register_holds_every_invariant(scheduled):
    check_invariants(scheduled)


# --- who gets a register, and how it is stored and served ----------------------------------------------

def test_only_the_extended_class_carries_a_register():
    assert ontology.FINDINGS_CLASSES == {ontology.EXTENDED_EQUIPMENT_CLASS}
    assert _run(PACK_402).tender.findings is None


def test_the_register_survives_the_database_and_is_served(base):
    db = SessionLocal()
    try:
        persist(base, db)
        repo = TenderRepository(db)
        loaded = repo.load_tender("TENDER-2026-418")
        meta = repo.get_meta("TENDER-2026-418")
        payload = brief_payload(loaded, meta, [], {}, SensitivitySettings())
    finally:
        db.close()
    assert loaded.findings == base.tender.findings
    assert payload["tender"]["findings"] == base.tender.findings


def test_a_tender_without_a_register_is_served_without_the_key():
    db = SessionLocal()
    try:
        persist(_run(PACK_402), db)
        repo = TenderRepository(db)
        payload = brief_payload(repo.load_tender("TENDER-2026-402"), repo.get_meta("TENDER-2026-402"), [], {},
                                SensitivitySettings())
    finally:
        db.close()
    assert "findings" not in payload["tender"]
