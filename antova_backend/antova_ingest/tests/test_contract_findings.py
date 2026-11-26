"""The contracted stage: contract terms, their checks, gaps and budget lines, and the contract findings (Task 8)."""

import copy
import json
import shutil
from datetime import date
from decimal import Decimal

import pytest

from antova_ingest import artifacts, pipeline
from antova_ingest.contract_findings import EMPTY_BODY, NO_FA, NO_OWNER, assess, build_contract_findings
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, load_expected
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import BaselineMoney, DocumentEntry, OwnerTerms, ProjectBaseline, TenderManifest
from antova_ingest.values import PriceRead

D = Decimal
MR = DocumentEntry(file="mr.pdf", role="mr_spec")
BID_A = DocumentEntry(file="bid-a.pdf", role="vendor_bid", vendor_id="vendor-a", counterparty_id="cp-a")
BID_B = DocumentEntry(file="bid-b.pdf", role="vendor_bid", vendor_id="vendor-b", counterparty_id="cp-b")
FA_A = DocumentEntry(file="fa-a.pdf", role="framework_agreement", counterparty_id="cp-a", dated=date(2025, 3, 1))
OWNER = DocumentEntry(file="owner.pdf", role="owner_contract")
WARRANTY_60 = "The warranty period is 60 months from delivery."
WARRANTY_24 = "The warranty period is 24 months from delivery."
WARRANTY_36 = "The warranty period is 36 months from delivery."


def _manifest(*docs, project=None):
    return TenderManifest(tender_id="T-1", title="t", client="c", equipment_class="mv_gis_switchgear",
                          rate_table="mv_gis_switchgear_v2", documents=[MR, *docs], engineering_estimates={},
                          synthetic=True, project=project)


def _ev(file, param_id, quote, page=1):
    return evidence(param_id, "commercial", page, quote, filename=file)


def _assess(manifest, evidence_by_file, prices=None, true_job_costs=None):
    return assess(manifest, evidence_by_file, prices or {}, true_job_costs or {})


def _by_id(body):
    found = {f["id"]: f for f in body["findings"]}
    assert len(found) == len(body["findings"]), "finding ids are unique"
    return found


# --- FA conformance -----------------------------------------------------------------------------------

def test_a_failing_fa_check_is_one_finding_with_both_citations():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", WARRANTY_24, 4)],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", WARRANTY_60, 9)]})
    found = [f for f in body["findings"] if f["category"] == "fa_conformance"]
    assert len(found) == 1
    (finding,) = found
    assert finding["id"] == "vendor-a|CT-03|fa|warranty_months"
    assert (finding["vendor_id"], finding["param_id"], finding["severity"]) == ("vendor-a", "CT-03", "critical")
    assert {(c["filename"], c["page"]) for c in finding["citations"]} == {("bid-a.pdf", 4), ("fa-a.pdf", 9)}
    assert finding["message"] == ("Warranty: warranty months offered 24; the framework agreement requires 60.")
    assert finding["money"] == [] and finding["gap"] is None
    assert len(finding["evidence_hash"]) == 64
    assert set(finding) == {"id", "vendor_id", "param_id", "category", "severity", "message", "money", "gap",
                            "citations", "evidence_hash"}


def test_a_passing_fa_check_is_no_finding():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", WARRANTY_60, 4)],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", WARRANTY_36, 9)]})
    assert not [f for f in body["findings"] if f["category"] == "fa_conformance"]
    assert any(c["param_id"] == "CT-03" and c["baseline"] == "fa" and c["passed"] is True for c in body["checks"])


def test_a_citation_only_field_is_no_finding():
    named = "Delivery FCA, named place: Port of Rotterdam."
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-12", "Delivery FCA, named place: Hamburg.")],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-12", named)]})
    assert not [f for f in body["findings"] if f["param_id"] == "CT-12" and "named_place" in f["id"]]


# --- missing terms and baselines ------------------------------------------------------------------------

def test_a_missing_term_on_a_high_param_is_a_missing_finding():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [], "fa-a.pdf": []})
    found = _by_id(body)
    for param_id in ("CT-01", "CT-02", "CT-03", "CT-04", "CT-05", "CT-06", "CT-07"):
        finding = found[f"vendor-a|{param_id}|offer|term"]
        assert (finding["category"], finding["severity"]) == ("missing", "critical")
    assert "vendor-a|CT-08|offer|term" not in found  # a commercial term no baseline requires
    assert found["vendor-a|CT-03|offer|term"]["message"] == "Warranty: not stated in the offer."


def test_a_missing_commercial_term_a_baseline_requires_is_a_medium_missing_finding():
    fa = _ev("fa-a.pdf", "CT-14", "This Agreement is governed by the laws of England and Wales.")
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [], "fa-a.pdf": [fa]})
    finding = _by_id(body)["vendor-a|CT-14|offer|term"]
    assert (finding["category"], finding["severity"]) == ("missing", "medium")
    assert [c["filename"] for c in finding["citations"]] == ["fa-a.pdf"]


def test_a_vendor_with_no_fa_is_not_checked_and_has_no_fa_findings():
    body = _assess(_manifest(BID_A, BID_B, FA_A),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", WARRANTY_24)],
                    "bid-b.pdf": [_ev("bid-b.pdf", "CT-03", WARRANTY_24)],
                    "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", WARRANTY_60)]})
    assert {"vendor_id": "vendor-b", "baseline": "fa", "reason": NO_FA} in body["not_checked"]
    assert not any(e["vendor_id"] == "vendor-a" and e["baseline"] == "fa" for e in body["not_checked"])
    assert not [f for f in body["findings"] if f["vendor_id"] == "vendor-b" and f["category"] == "fa_conformance"]
    assert not [c for c in body["checks"] if c["vendor_id"] == "vendor-b" and c["baseline"] == "fa"]
    # Neither vendor has owner terms.
    assert {"vendor_id": "vendor-a", "baseline": "owner", "reason": NO_OWNER} in body["not_checked"]


def test_an_fa_not_read_is_not_checked_with_its_reason_not_as_missing():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", WARRANTY_24)]})
    (entry,) = [e for e in body["not_checked"] if e["baseline"] == "fa"]
    assert entry == {"vendor_id": "vendor-a", "baseline": "fa",
                     "reason": "the framework agreement was not read (fa-a.pdf)"}


# --- back-to-back -----------------------------------------------------------------------------------------

def _owner_project(**extra):
    owner = OwnerTerms(contract_value=BaselineMoney(amount=D("20000000"), currency="EUR"), ld_rate=D("0.1"),
                       ld_rate_basis="percent_per_day", ld_cap=D("10"), ld_cap_basis="percent",
                       package_ld_share_pct=D("10"), warranty_months=36, warranty_start="delivery")
    return ProjectBaseline(owner_terms=owner, delay_scenario_weeks={"value": "4"}, **extra)


LD_RATE_CAP = "LDs are 0.5% per day, capped at 10% of the order value."
PRICE = {"vendor-a": (PriceRead(D("2000000"), "EUR", None), None)}


def test_a_back_to_back_ld_gap_is_a_back_to_back_finding_with_its_money():
    body = _assess(_manifest(BID_A, project=_owner_project()),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-01", "LDs are 0.05% per day, capped at 5% of the order value."),
                                  _ev("bid-a.pdf", "CT-03", WARRANTY_24)]}, prices=PRICE)
    found = _by_id(body)
    ld = found["vendor-a|CT-01|owner|back_to_back_ld"]
    assert (ld["category"], ld["severity"]) == ("back_to_back", "critical")
    (money,) = ld["money"]
    # owed: min(EUR 20,000 per day × 28 days, EUR 2,000,000 cap) × 10% = EUR 56,000;
    # paid back: min(0.05% × 28 days, 5%) × EUR 2,000,000 = EUR 28,000.
    assert (money["basis"], money["kind"], money["amount"], money["currency"]) == (
        "exposure", "back_to_back_ld", "28000.00", "EUR")
    assert "4 weeks × 7 = 28 days of delay" in money["formula"]
    assert ld["message"] == "Delay LDs: the vendor carries EUR 28,000.00 less than the owner contract requires."
    warranty = found["vendor-a|CT-03|owner|warranty_months"]
    assert warranty["category"] == "back_to_back"
    assert warranty["gap"]["kind"] == "warranty" and warranty["gap"]["value"] == "12"
    assert not any(f["id"].startswith("vendor-a|CT-01|owner|ld_") for f in body["findings"])  # compared in money


def test_a_gap_that_is_not_known_is_a_finding_never_zero():
    body = _assess(_manifest(BID_A, project=_owner_project()),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-01", "LDs are 0.1% per day, capped at 5% of the order value.")]})
    ld = _by_id(body)["vendor-a|CT-01|owner|back_to_back_ld"]
    (money,) = ld["money"]
    assert money["amount"] is None and money["reason"] == "not known: the order value"
    assert ld["message"] == "Delay LDs: the back-to-back gap cannot be worked out: not known: the order value."


def test_a_coordinated_ld_exclusion_beside_a_rate_gives_no_number():
    # Ruling R31 through the stage: the exclusion passage is kept, and the gap is not computed.
    exclusion = _ev("bid-a.pdf", "CT-01", "We exclude consequential losses and liquidated damages.", 12)
    body = _assess(_manifest(BID_A, project=_owner_project()),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-01", LD_RATE_CAP, 9), exclusion]}, prices=PRICE)
    (term,) = [t for t in body["terms"] if t["param_id"] == "CT-01"]
    assert [c["quote"] for c in term["ld_exclusion_passages"]] == [exclusion.citation.quote]
    (money,) = _by_id(body)["vendor-a|CT-01|owner|back_to_back_ld"]["money"]
    assert money["amount"] is None and "check the scope by hand" in money["reason"]


def test_a_liability_cap_short_of_the_owners_is_one_back_to_back_finding_in_the_order_currency():
    owner = OwnerTerms(liability_cap=BaselineMoney(amount=D("2000000"), currency="EUR"))
    project = ProjectBaseline(owner_terms=owner, fx=[{"from_currency": "GBP", "to_currency": "EUR", "rate": "1.17",
                                                      "dated": "2026-11-01"}])
    body = _assess(_manifest(BID_A, project=project),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-02",
                                      "The Supplier's total liability is limited to 50% of the Contract Price.")]},
                   prices={"vendor-a": (PriceRead(D("2000000"), "GBP", None), None)})
    found = [f for f in body["findings"] if f["param_id"] == "CT-02"]
    assert [f["id"] for f in found] == ["vendor-a|CT-02|owner|back_to_back_liability"]  # no check finding beside it
    (money,) = found[0]["money"]
    # EUR 2,000,000 ÷ 1.17 = GBP 1,709,401.71, less 50 % × GBP 2,000,000.
    assert (money["amount"], money["currency"]) == ("709401.71", "GBP")
    assert "÷ 1.17 (2026-11-01, GBP→EUR)" in money["formula"]


def test_an_owner_contract_read_that_states_none_of_the_terms_says_so():
    # Ruling R35: read and silent is not "no owner contract terms provided".
    body = _assess(_manifest(BID_A, OWNER), {"bid-a.pdf": [], "owner.pdf": []})
    owner = [e for e in body["not_checked"] if e["baseline"] == "owner"]
    assert owner == [{"vendor_id": "vendor-a", "baseline": "owner",
                      "reason": "the owner contract states none of these terms"}]
    # With no owner contract and no owner terms, nothing was provided.
    body = _assess(_manifest(BID_A), {"bid-a.pdf": []})
    assert [e for e in body["not_checked"] if e["baseline"] == "owner"] == [
        {"vendor_id": "vendor-a", "baseline": "owner", "reason": NO_OWNER}]


def test_an_owner_contract_not_read_is_not_checked_with_its_reason():
    body = _assess(_manifest(BID_A, OWNER), {"bid-a.pdf": []})
    assert {"vendor_id": "vendor-a", "baseline": "owner",
            "reason": "the owner contract was not read (owner.pdf)"} in body["not_checked"]


# --- budget ------------------------------------------------------------------------------------------------

def test_a_budget_overrun_is_a_high_budget_finding():
    project = ProjectBaseline(frozen_budget=BaselineMoney(amount=D("1900000"), currency="EUR"))
    body = _assess(_manifest(BID_A, project=project), {"bid-a.pdf": []}, prices=PRICE,
                   true_job_costs={"vendor-a": (D("1850000"), "EUR")})
    finding = _by_id(body)["vendor-a|budget|variance"]
    assert (finding["category"], finding["severity"], finding["param_id"]) == ("budget", "high", None)
    (money,) = finding["money"]
    assert (money["amount"], money["currency"]) == ("100000.00", "EUR")
    assert finding["message"] == "The offered price is EUR 100,000.00 over the frozen budget."
    assert "vendor-a|budget|variance_true_job_cost" not in _by_id(body)  # under budget
    assert [line["label"] for line in body["budget"]["vendor-a"]] == ["offered", "variance", "variance_true_job_cost"]


def test_no_frozen_budget_is_a_budget_finding():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [], "fa-a.pdf": []}, prices=PRICE)
    finding = _by_id(body)["vendor-a|budget|variance"]
    assert (finding["category"], finding["severity"]) == ("budget", "medium")
    assert finding["money"][0]["amount"] is None
    assert finding["message"] == "Budget variance not known: frozen budget not entered."


# --- ids, hashes, a single offer ---------------------------------------------------------------------------

def _two_vendor_case(warranty_offered=WARRANTY_24):
    return _assess(_manifest(BID_A, BID_B, FA_A, project=_owner_project()),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", warranty_offered, 4)],
                    "bid-b.pdf": [_ev("bid-b.pdf", "CT-03", "The warranty period is 30 months from delivery.", 4)],
                    "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", WARRANTY_60, 9)]}, prices=PRICE)


def test_ids_and_hashes_are_stable_across_runs():
    first, again = _two_vendor_case(), _two_vendor_case()
    assert first == again
    assert artifacts.canonical_json(first) == artifacts.canonical_json(again)
    assert [f["id"] for f in first["findings"]] == [f["id"] for f in again["findings"]]


def test_the_evidence_hash_changes_when_a_cited_value_changes():
    before = _by_id(_two_vendor_case(WARRANTY_24))
    after = _by_id(_two_vendor_case("The warranty period is 30 months from delivery."))
    key = "vendor-a|CT-03|fa|warranty_months"
    assert before[key]["evidence_hash"] != after[key]["evidence_hash"]
    other = "vendor-b|CT-03|owner|warranty_months"  # vendor-b's evidence did not change
    assert before[other]["category"] == "back_to_back"  # 30 months against the owner's 36
    assert before[other]["evidence_hash"] == after[other]["evidence_hash"]


def test_a_single_offer_call_off_is_assessed_on_its_own():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", WARRANTY_24)],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", WARRANTY_60)]}, prices=PRICE)
    assert {t["vendor_id"] for t in body["terms"]} == {"vendor-a"}
    assert len(body["terms"]) == 17
    assert body["checks"] and {c["vendor_id"] for c in body["checks"]} == {"vendor-a"}
    assert body["findings"] and {f["vendor_id"] for f in body["findings"]} == {"vendor-a"}
    assert set(body["budget"]) == {"vendor-a"}


def test_build_contract_findings_on_nothing_is_empty():
    assert build_contract_findings({}, [], {}, [], {}) == ([], [])


# --- the staged run ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def expected():
    return load_expected()


@pytest.fixture(scope="module")
def run_402(expected):
    return run_ingest(PACK, locator=AnswerKeyLocator(expected))


def test_a_pack_without_contract_documents_has_an_empty_contracted_body(run_402):
    assert list(run_402.artifacts) == list(artifacts.STAGE_NAMES)
    assert json.loads(run_402.artifacts["contracted"].body) == EMPTY_BODY
    assert EMPTY_BODY == {"terms": [], "checks": [], "gaps": [], "budget": {}, "findings": [], "not_checked": []}


def test_the_contracted_stage_leaves_every_earlier_stage_hash_unchanged(run_402, expected, monkeypatch):
    # The run as it was before the stage existed: the same stages, stopping at leveled.
    before = tuple(s for s in artifacts.STAGE_NAMES if s != "contracted")
    monkeypatch.setattr(artifacts, "STAGE_NAMES", before)
    monkeypatch.setattr(pipeline, "STAGE_NAMES", before)
    old = run_ingest(PACK, locator=AnswerKeyLocator(expected))
    assert list(old.artifacts) == list(before)
    assert {s: a.sha256 for s, a in old.artifacts.items()} == {
        s: a.sha256 for s, a in run_402.artifacts.items() if s != "contracted"}


FA_FILE = "FA-vendor-c.pdf"
DELTAPORT = "Bid_vendor-c_Deltaport.pdf"


def test_a_staged_run_with_a_framework_agreement_raises_its_finding(tmp_path, expected):
    # The FA is a copy of the MR, whose defect liability period is 36 months from commissioning; Deltaport
    # offers 24 months from commissioning.
    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    shutil.copy(folder / "MR-33kV-GIS-Rev4.pdf", folder / FA_FILE)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["synthetic"] = True
    for doc in manifest["documents"]:
        if doc["file"] == DELTAPORT:
            doc["counterparty_id"] = "cp-deltaport"
    manifest["documents"].append({"file": FA_FILE, "role": "framework_agreement",
                                  "counterparty_id": "cp-deltaport", "dated": "2025-03-01"})
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    key = copy.deepcopy(expected)
    empty = {"page_sections": [], "tender_facts": [], "headline_price": None, "cover_claim": None}
    key["ideal_contract_locate"] = {
        FA_FILE: {**empty, "candidates": [
            {"param_id": "CT-03", "page": 14, "quote": "The defect liability period shall be 36 months from commissioning."}]},
        DELTAPORT: {**empty, "candidates": [
            {"param_id": "CT-03", "page": 21,
             "quote": "Delivered At Place (DAP) project site; warranty 24 months from commissioning."}]},
    }
    run = run_ingest(folder, locator=AnswerKeyLocator(key))
    body = json.loads(run.artifacts["contracted"].body)
    found = _by_id(body)
    finding = found["vendor-c|CT-03|fa|warranty_months"]
    assert finding["category"] == "fa_conformance"
    assert {(c["filename"], c["page"]) for c in finding["citations"]} == {(DELTAPORT, 21), (FA_FILE, 14)}
    assert {"vendor_id": "vendor-a", "baseline": "fa", "reason": NO_FA} in body["not_checked"]
    assert found["vendor-c|budget|variance"]["message"] == "Budget variance not known: frozen budget not entered."
    offered = next(line for line in body["budget"]["vendor-c"] if line["label"] == "offered")
    assert offered["amount"] == "439000.00" and offered["currency"] == "EUR"
    tjc = next(line for line in body["budget"]["vendor-c"] if line["label"] == "variance_true_job_cost")
    assert tjc["amount"] is None
    # Rerunning from a stored stage reproduces the contracted body.
    again = pipeline.rerun_from("verified", run.artifacts, run.fingerprint)
    assert again.artifacts["contracted"].sha256 == run.artifacts["contracted"].sha256


# --- review round 1 -------------------------------------------------------------------------------------------

ESCALATION = "The price is subject to escalation by the formula P = P0 x (0.3 + 0.7 x I/I0)."
OWNER_LIABILITY_PCT = "The Contractor's total liability is limited to 20% of the Contract Price."


def test_a_citation_only_field_the_offer_does_not_state_is_a_finding():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-13", "The price is variable.")],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-13", ESCALATION)]})
    finding = _by_id(body)["vendor-a|CT-13|fa|escalation_formula"]
    assert finding["category"] == "missing"
    assert {c["filename"] for c in finding["citations"]} == {"bid-a.pdf", "fa-a.pdf"}


def test_several_quoted_clauses_of_a_citation_only_field_in_the_baseline_are_no_conflict():
    # Ruling R40: never compared (R18), so two different quoted clauses are both kept, not a conflict.
    other = "The price is subject to escalation by the formula P = P0 x (0.5 + 0.5 x I/I0)."
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-13", ESCALATION)],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-13", ESCALATION, 3),
                                                         _ev("fa-a.pdf", "CT-13", other, 5)]})
    assert not any(f["param_id"] == "CT-13" and "a person decides" in f["message"] for f in body["findings"])
    (term,) = [t for t in body["terms"] if t["vendor_id"] == "vendor-a" and t["param_id"] == "CT-13"]
    assert term["state"] != "conflict"
    quoted = term["fa"]["values"]["escalation_formula"]["display"]
    assert ESCALATION in quoted and other in quoted


def test_both_sides_quoting_a_citation_only_clause_is_no_finding():
    body = _assess(_manifest(BID_A, FA_A), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-13", ESCALATION)],
                                            "fa-a.pdf": [_ev("fa-a.pdf", "CT-13", ESCALATION)]})
    assert "vendor-a|CT-13|fa|escalation_formula" not in _by_id(body)


def _owner_value_project(**extra):
    return ProjectBaseline(owner_terms=OwnerTerms(contract_value=BaselineMoney(amount=D("20000000"), currency="EUR")),
                           **extra)


def test_an_owner_liability_percentage_against_an_offer_amount_is_compared_in_money():
    body = _assess(_manifest(BID_A, OWNER, project=_owner_value_project()),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-02", "The Supplier's total liability is limited to EUR 500,000.")],
                    "owner.pdf": [_ev("owner.pdf", "CT-02", OWNER_LIABILITY_PCT)]}, prices=PRICE)
    found = [f for f in body["findings"] if f["param_id"] == "CT-02"]
    assert [f["id"] for f in found] == ["vendor-a|CT-02|owner|back_to_back_liability"]  # never "not stated"
    (money,) = found[0]["money"]
    assert (money["amount"], money["currency"]) == ("3500000.00", "EUR")
    assert "owner liability cap 20% × EUR 20,000,000.00 = EUR 4,000,000.00" in money["formula"]


def test_an_owner_liability_percentage_against_an_offer_amount_in_another_currency_is_converted():
    fx = [{"from_currency": "GBP", "to_currency": "EUR", "rate": "1.25", "dated": "2026-11-01"}]
    body = _assess(_manifest(BID_A, OWNER, project=_owner_value_project(fx=fx)),
                   {"bid-a.pdf": [_ev("bid-a.pdf", "CT-02", "The Supplier's total liability is limited to GBP 500,000.")],
                    "owner.pdf": [_ev("owner.pdf", "CT-02", OWNER_LIABILITY_PCT)]})
    (money,) = _by_id(body)["vendor-a|CT-02|owner|back_to_back_liability"]["money"]
    assert (money["amount"], money["currency"]) == ("2700000.00", "GBP")  # EUR 4,000,000 ÷ 1.25 − GBP 500,000
    assert "÷ 1.25 (2026-11-01, GBP→EUR)" in money["formula"]


def test_an_unread_owner_contract_beside_buyer_entered_terms_is_not_checked():
    project = ProjectBaseline(owner_terms=OwnerTerms(warranty_months=36, warranty_start="delivery"))
    body = _assess(_manifest(BID_A, OWNER, project=project), {"bid-a.pdf": []})
    assert {"vendor_id": "vendor-a", "baseline": "owner",
            "reason": "the owner contract was not read (owner.pdf)"} in body["not_checked"]


def test_duplicate_finding_ids_raise(monkeypatch):
    from antova_ingest import contract_findings
    from antova_ingest.contract_terms import resolve_contract_terms
    monkeypatch.setattr(contract_findings, "_missing_finding",
                        lambda term, required, gap: {"id": "vendor-a|CT-01|offer|term", "vendor_id": term.vendor_id})
    resolved = resolve_contract_terms(_manifest(BID_A), {"bid-a.pdf": []})
    with pytest.raises(ValueError, match="duplicate finding ids"):
        build_contract_findings(resolved, [], {}, [], {})
