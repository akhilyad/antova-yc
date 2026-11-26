"""The contract sample pack, TENDER-2026-419: a call-off under framework agreements.

Every contract quote in the answer key is on its stated page word for word and reads, with the
contract readers, as the value the key states; the answer-key locator finds every CT term; and the
equipment side runs clean against its hand-written key.
"""

import json
from pathlib import Path

import fitz
import pytest

from antova_ingest.contract_ontology import CONTRACT_PARAMETERS, CONTRACT_ROLES
from antova_ingest.locator_eval import score_pack
from antova_ingest.parse import load_manifest, parse_document, verify_quote
from antova_ingest.pipeline import read_values, run_ingest
from antova_ingest.tests.fakes import AnswerKeyLocator
from antova_ingest.verify import compare_to_answer_key
from samples import scenario_419
from samples.generate_sample_pack import pack_files

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
TENDER_ID = "TENDER-2026-419"
PACK = SAMPLES / TENDER_ID


@pytest.fixture(scope="module")
def expected():
    return json.loads((PACK / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def documents():
    manifest = load_manifest(PACK)
    return {entry.file: parse_document(PACK, entry) for entry in manifest.documents}


def test_the_manifest_is_synthetic_and_links_vendors_to_their_agreements_by_counterparty():
    raw = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    assert raw["synthetic"] is True  # a literal true: the upload manifest's flag is StrictBool
    manifest = load_manifest(PACK)
    bids = {d.vendor_name: d.counterparty_id for d in manifest.documents if d.role == "vendor_bid"}
    assert bids == {"Harlow Energy Systems Ltd": "cp-harlow", "Kestrel Power Conversion UK Ltd": "cp-kestrel"}
    agreements = [(d.file, d.counterparty_id, d.amends) for d in manifest.documents if d.role == "framework_agreement"]
    assert agreements == [
        (scenario_419.FA_HARLOW, "cp-harlow", None),
        (scenario_419.FA_KESTREL, "cp-kestrel", None),
        (scenario_419.FA_KESTREL_AMENDMENT, "cp-kestrel", scenario_419.FA_KESTREL),
    ]
    amendment = next(d for d in manifest.documents if d.file == scenario_419.FA_KESTREL_AMENDMENT)
    original = next(d for d in manifest.documents if d.file == scenario_419.FA_KESTREL)
    assert amendment.dated > original.dated
    assert [d.role for d in manifest.documents].count("owner_contract") == 1


def test_every_page_carries_the_synthetic_footer():
    for name in pack_files(scenario_419):
        if not name.endswith(".pdf"):
            continue
        with fitz.open(PACK / name) as doc:
            for index, page in enumerate(doc, start=1):
                assert "SYNTHETIC SAMPLE — not a real offer" in page.get_text(), (name, index)


def test_the_kestrel_agreement_is_with_another_entity_of_the_vendor(documents):
    first_page = " ".join(documents[scenario_419.FA_KESTREL].pages[0].text.split())
    assert "Kestrel Power Conversion AG" in first_page


def test_every_contract_quote_is_on_its_page_word_for_word(expected, documents):
    for filename, terms in expected["contract"].items():
        for param_id, term in terms.items():
            for quote in term["quotes"]:
                assert verify_quote(documents[filename], term["page"], quote) is not None, (filename, param_id, quote)


def test_every_contract_quote_reads_as_the_value_the_key_states(expected):
    for filename, terms in expected["contract"].items():
        for param_id, term in terms.items():
            read: dict = {}
            for quote in term["quotes"]:
                citation = verify_quote(parse_document(PACK, _entry(filename)), term["page"], quote)
                values, notes = read_values(param_id, citation, "en")
                assert notes == [], (filename, param_id, notes)
                read.update({name: v.display for name, v in values.items()})
            # A citation-only escalation formula is captured from any price-basis sentence; the key leaves it out.
            read.pop("escalation_formula", None)
            assert read == term["values"], (filename, param_id)


def _entry(filename):
    return next(d for d in load_manifest(PACK).documents if d.file == filename)


def test_the_scenario_differences_task_17_relies_on(expected):
    contract = expected["contract"]
    kestrel, fa, amendment, owner = (contract[scenario_419.KESTREL.file], contract[scenario_419.FA_KESTREL],
                                     contract[scenario_419.FA_KESTREL_AMENDMENT], contract[scenario_419.OWNER_FILE])
    assert (kestrel["CT-01"]["values"]["ld_cap"], fa["CT-01"]["values"]["ld_cap"]) == ("5 %", "10 %")
    assert (kestrel["CT-03"]["values"]["warranty_start"], owner["CT-03"]["values"]["warranty_start"]) == (
        "delivery", "PAC")
    assert (fa["CT-03"]["values"]["warranty_months"], amendment["CT-03"]["values"]["warranty_months"]) == ("60", "66")
    assert "CT-09" not in kestrel and fa["CT-09"]["values"] == {"title_event": "delivery"}
    assert "CT-16" in kestrel
    assert kestrel["CT-02"]["values"]["liability_cap_money"].startswith("GBP")
    assert fa["CT-02"]["values"]["liability_cap_money"].startswith("EUR")


def test_every_contract_term_is_in_the_pack(expected):
    stated = {param_id for terms in expected["contract"].values() for param_id in terms}
    assert stated == {p.id for p in CONTRACT_PARAMETERS}


def test_the_answer_key_locator_finds_every_contract_term():
    scored = score_pack(PACK, AnswerKeyLocator(json.loads((PACK / "expected.json").read_text(encoding="utf-8"))),
                        contract=True)
    assert scored["missing"] == [] and scored["held"] == []
    files = {d["file"] for d in scored["contract_documents"]}
    manifest = load_manifest(PACK)
    assert files == {d.file for d in manifest.documents if d.role in CONTRACT_ROLES or d.role == "vendor_bid"}
    for d in scored["contract_documents"]:
        assert (d["rejected"], d["extra"], d["recall"], d["silent_params"]) == (0, 0, 1.0, []), d["file"]
    per_term = scored["contract_per_param"]
    assert set(per_term) == {p.id for p in CONTRACT_PARAMETERS}
    for param_id, row in per_term.items():
        assert row["expected"] > 0 and row["matched"] == row["expected"], param_id
    assert scored["pipeline"]["ran"] and scored["pipeline"]["mismatches"] == []


def test_the_equipment_side_matches_its_hand_written_key(expected):
    result = run_ingest(PACK, locator=AnswerKeyLocator(expected), expected_tender_id=TENDER_ID)
    assert result.warnings == []
    assert compare_to_answer_key(result.tender, expected, result) == []
