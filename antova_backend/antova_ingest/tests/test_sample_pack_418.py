"""The extended sample pack, TENDER-2026-418, end to end with the answer-key locator.

It carries PARAM-09..14 (delivery lead time, delay LDs, liability cap, commissioning spares, site
supervision, rated altitude) under equipment class mv_gis_switchgear_ext. The mutation operators written
for it, and the eval table for it, are checked here too.
"""

import json
from pathlib import Path

import pytest

import antova_engine as engine
from antova_db import SessionLocal, TenderRepository
from antova_ingest import clarify, ontology
from antova_ingest import eval as evaluation
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import AnswerKeyLocator
from antova_ingest.verify import compare_to_answer_key
from antova_service import persist
from samples import mutate

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
TENDER_ID = "TENDER-2026-418"
PACK = SAMPLES / TENDER_ID
NEW = ("PARAM-09", "PARAM-10", "PARAM-11", "PARAM-12", "PARAM-13", "PARAM-14", "PARAM-15")
Status = engine.ComplianceStatus


@pytest.fixture(scope="module")
def expected():
    return json.loads((PACK / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result(expected):
    return run_ingest(PACK, locator=AnswerKeyLocator(expected), expected_tender_id=TENDER_ID)


def _cells(tender):
    return {(vid, p.id): cell for p in tender.parameters for vid, cell in p.vendors.items()}


def test_the_pack_runs_under_the_extended_class_and_matches_its_hand_written_key(result, expected):
    assert result.tender.equipment_class == ontology.EXTENDED_EQUIPMENT_CLASS
    assert result.tender.rate_table_version == "mv_gis_switchgear_ext_v1"
    assert [p.id for p in result.tender.parameters] == [p.id for p in ontology.parameters_for(result.tender.equipment_class)]
    assert result.warnings == []
    assert compare_to_answer_key(result.tender, expected, result) == []


def test_the_first_eight_parameters_price_exactly_as_in_tender_402(result):
    cells = _cells(result.tender)
    assert {(v, p): round(c.cost_adder, 2) for (v, p), c in cells.items() if p < "PARAM-09"} == {
        ("vendor-a", f"PARAM-0{i}"): 0.0 for i in range(1, 9)} | {
        ("vendor-b", "PARAM-01"): 18500.0, ("vendor-b", "PARAM-02"): 14200.0, ("vendor-b", "PARAM-03"): 8900.0,
        ("vendor-b", "PARAM-04"): 7400.0, ("vendor-b", "PARAM-05"): 6500.0, ("vendor-b", "PARAM-06"): 6150.0,
        ("vendor-b", "PARAM-07"): 0.0, ("vendor-b", "PARAM-08"): 0.0,
        ("vendor-c", "PARAM-01"): 6200.0, ("vendor-c", "PARAM-02"): 0.0, ("vendor-c", "PARAM-03"): 0.0,
        ("vendor-c", "PARAM-04"): 4100.0, ("vendor-c", "PARAM-05"): 2600.0, ("vendor-c", "PARAM-06"): 2100.0,
        ("vendor-c", "PARAM-07"): 0.0, ("vendor-c", "PARAM-08"): 0.0}


@pytest.mark.parametrize("vendor, param, status, values", [
    ("vendor-a", "PARAM-09", Status.COMPLIANT, "36 weeks"),
    ("vendor-b", "PARAM-09", Status.DEVIATION_DOWNGRADE, "44 weeks"),
    ("vendor-b", "PARAM-10", Status.DEVIATION_DOWNGRADE, "0 %; cap 0 %"),
    ("vendor-c", "PARAM-10", Status.DEVIATION_DOWNGRADE, "0.5 % per week; cap 5 %"),
    ("vendor-b", "PARAM-11", Status.DEVIATION_DOWNGRADE, "20 %"),
    ("vendor-b", "PARAM-12", Status.OMITTED, "commissioning spares not included"),
    ("vendor-b", "PARAM-13", Status.OMITTED, "site supervision not included"),
    ("vendor-b", "PARAM-14", Status.DEVIATION_DOWNGRADE, "rated for 800 m"),
    ("vendor-a", "PARAM-15", Status.COMPLIANT, "IEC 62271-1:2017, IEC 62271-200:2021"),
    ("vendor-b", "PARAM-15", Status.DEVIATION_DOWNGRADE, "IEC 62271-1:2017, IEC 62271-200:2011"),
    ("vendor-c", "PARAM-15", Status.DEVIATION_DOWNGRADE, "IEC 62271-1, IEC 62271-200:2021"),
])
def test_what_each_bid_does_on_the_new_parameters(result, vendor, param, status, values):
    cell = _cells(result.tender)[(vendor, param)]
    assert (cell.status, cell.extracted_text) == (status, values)


def test_a_bid_that_does_not_state_altitude_needs_inspection_and_says_so(result):
    cell = _cells(result.tender)[("vendor-c", "PARAM-14")]
    assert cell.status == Status.MANUAL_INSPECTION_REQUIRED
    assert [f["code"] for f in cell.flags] == ["NOT_STATED"]


def test_a_deviation_in_a_class_the_table_has_no_rate_for_is_unpriced_not_free(result):
    cells = _cells(result.tender)
    unpriced = {key for key, cell in cells.items() if cell.pricing_status == "UNPRICED" and key[1] in NEW}
    # Spares and supervision are priced when a bid excludes them; the rest are exposures or unrated.
    assert unpriced == {("vendor-b", p) for p in ("PARAM-09", "PARAM-10", "PARAM-11", "PARAM-14", "PARAM-15")} | {
        ("vendor-c", "PARAM-10"), ("vendor-c", "PARAM-15")}
    assert all(cells[key].cost_adder == 0.0 for key in unpriced)


def test_the_clarification_register_asks_about_the_new_gaps(result):
    rows = [r for r in clarify.clarifications(result.tender) if r["param_id"] in NEW]
    by = {(r["vendor_id"], r["param_id"], r["kind"]): r["question"] for r in rows}
    assert "44 weeks" in by[("vendor-b", "PARAM-09", "DEVIATION")]
    assert "Confirm the lead time" in by[("vendor-b", "PARAM-09", "DEVIATION")]
    assert "leaves this out of the price" in by[("vendor-b", "PARAM-12", "OMITTED")]
    assert ("vendor-c", "PARAM-14", "MANUAL") in by
    assert not [k for k in by if k[0] == "vendor-a"]


def test_the_new_cells_survive_the_database(result):
    db = SessionLocal()
    try:
        persist(result, db)
        loaded = TenderRepository(db).load_tender(TENDER_ID)
    finally:
        db.close()
    assert loaded.equipment_class == ontology.EXTENDED_EQUIPMENT_CLASS
    assert {(vid, p.id): (c.status, c.extracted_text, c.pricing_status) for p in loaded.parameters
            for vid, c in p.vendors.items()} == {
        key: (c.status, c.extracted_text, c.pricing_status) for key, c in _cells(result.tender).items()}


# --- the mutation operators written for this pack -----------------------------------------------

EXTENDED = sorted(mutate.EXTENDED_OPERATORS)


def _ingest(operators, tmp_path, seed=0):
    target = mutate.generate(operators, tmp_path, seed, base=mutate.EXTENDED_BASE_TENDER_ID)
    key = json.loads((target / "expected.json").read_text(encoding="utf-8"))
    return run_ingest(target, locator=AnswerKeyLocator(key)), key


@pytest.mark.parametrize("name", EXTENDED)
def test_each_extended_operator_gives_a_correct_derived_key(name, tmp_path):
    result, key = _ingest([name], tmp_path)
    assert key["tender_id"].startswith("MUT-418-") and key["mutations"] == [name]
    assert compare_to_answer_key(result.tender, key, result) == []


@pytest.mark.parametrize("name", EXTENDED)
def test_extended_operators_do_not_apply_to_the_first_pack(name, tmp_path):
    with pytest.raises(mutate.MutationError):
        mutate.generate([name], tmp_path)


@pytest.mark.parametrize("name, vendor, param, status", [
    ("lead_time_in_months", "vendor-a", "PARAM-09", Status.COMPLIANT),
    ("lead_time_range", "vendor-c", "PARAM-09", Status.DEVIATION_DOWNGRADE),
    ("ld_per_day", "vendor-a", "PARAM-10", Status.COMPLIANT),
    ("percent_spelled_out", "vendor-b", "PARAM-11", Status.DEVIATION_DOWNGRADE),
    ("altitude_in_feet", "vendor-a", "PARAM-14", Status.COMPLIANT),
    ("spares_silent", "vendor-a", "PARAM-12", Status.MANUAL_INSPECTION_REQUIRED),
    ("supervision_optional", "vendor-c", "PARAM-13", Status.OMITTED),
    ("standard_newer_edition", "vendor-a", "PARAM-15", Status.COMPLIANT),
    ("standard_edition_dropped", "vendor-b", "PARAM-15", Status.DEVIATION_DOWNGRADE),
    ("standard_exception", "vendor-a", "PARAM-15", Status.MANUAL_INSPECTION_REQUIRED),
])
def test_what_each_operator_does_to_its_cell(name, vendor, param, status, tmp_path):
    result, _ = _ingest([name], tmp_path)
    assert _cells(result.tender)[(vendor, param)].status == status


def test_the_generic_operators_also_run_on_the_extended_pack(tmp_path):
    for name in ("european_locale", "hyphen_wrap", "two_column_quote", "running_header", "injection_text"):
        result, key = _ingest([name], tmp_path / name)
        assert compare_to_answer_key(result.tender, key, result) == [], name


def test_a_pack_with_an_unreadable_bid_blanks_the_new_cells_too(tmp_path):
    result, key = _ingest(["corrupt_pdf"], tmp_path)
    vendor = key["mutations"] and next(v for v, cells in key["cells"].items()
                                       if all(c["status"] == "MANUAL_INSPECTION_REQUIRED" for c in cells.values()))
    assert all(_cells(result.tender)[(vendor, p)].status == Status.MANUAL_INSPECTION_REQUIRED for p in NEW)
    assert compare_to_answer_key(result.tender, key, result) == []


# --- the eval table for this pack -----------------------------------------------------------------

def test_eval_reports_the_extended_pack_separately(tmp_path):
    rows = evaluation.run(seeds=1, out_dir=tmp_path, base=mutate.EXTENDED_BASE_TENDER_ID)
    names = [r["operator"] for r in rows]
    assert names == ["(base pack)", *sorted(mutate.operators_for(mutate.EXTENDED_BASE_TENDER_ID)), "TOTAL"]
    assert set(EXTENDED) <= set(names)
    total = rows[-1]
    assert (total["mismatches"], total["rejected"]) == (0, 0)
    assert (total["status"], total["pricing"], total["recall"]) == ("100.0%", "100.0%", "100.0%")


# --- the standards check ---------------------------------------------------------------------------

def test_the_standards_rule_reads_as_a_sentence(result):
    param = next(p for p in result.tender.parameters if p.id == "PARAM-15")
    assert param.pint_rule == "Standards include IEC 62271-1:2017, IEC 62271-200:2021"


def test_an_older_or_unstated_edition_is_asked_about(result):
    rows = {(r["vendor_id"], r["kind"]): r["question"] for r in clarify.clarifications(result.tender)
            if r["param_id"] == "PARAM-15"}
    assert "IEC 62271-200:2011" in rows[("vendor-b", "DEVIATION")]
    assert "IEC 62271-1, IEC 62271-200:2021" in rows[("vendor-c", "DEVIATION")]
    assert "type-test certificates" in rows[("vendor-b", "DEVIATION")]


def test_a_stated_exception_is_flagged_and_asked_about_not_left_silent(tmp_path):
    result, _ = _ingest(["standard_exception"], tmp_path)
    cell = _cells(result.tender)[("vendor-a", "PARAM-15")]
    assert [f["code"] for f in cell.flags] == ["STANDARD_EXCEPTION"]
    questions = [r["question"] for r in clarify.clarifications(result.tender)
                 if r["vendor_id"] == "vendor-a" and r["param_id"] == "PARAM-15"]
    assert any("List each clause you deviate from" in q for q in questions)
