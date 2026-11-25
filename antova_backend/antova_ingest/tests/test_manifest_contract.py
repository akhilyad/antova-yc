"""Contract document roles in the manifest, and the owner/budget baseline the buyer enters."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from antova_ingest.types import (
    BaselineFx, BaselineMoney, OwnerTerms, ProjectBaseline, TenderManifest,
)
from antova_review import project_field_hash, project_field_state

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

BASE = {
    "tender_id": "T-1", "title": "t", "client": "c", "equipment_class": "mv_gis_switchgear",
    "rate_table": "mv_gis_switchgear_v2", "engineering_estimates": {}, "synthetic": True,
}
MR = {"file": "mr.pdf", "role": "mr_spec"}
BID = {"file": "bid-a.pdf", "role": "vendor_bid", "vendor_id": "vendor-a"}
FA = {"file": "fa-a.pdf", "role": "framework_agreement", "counterparty_id": "vendor-a", "dated": "2025-03-01"}
FA_AMENDMENT = {"file": "fa-a-amd1.pdf", "role": "framework_agreement", "counterparty_id": "vendor-a",
                "amends": "fa-a.pdf", "dated": "2026-02-10"}
OWNER = {"file": "owner.pdf", "role": "owner_contract", "dated": "2026-06-30"}

OWNER_TERMS = {
    "contract_value": {"amount": "48000000.00", "currency": "EUR"},
    "ld_rate": "0.5", "ld_rate_basis": "percent_per_week",
    "ld_cap": "10", "ld_cap_basis": "percent",
    "package_ld_share_pct": "12.5",
    "liability_cap": {"amount": "48000000.00", "currency": "EUR", "note": "100% of contract value"},
    "warranty_months": 24, "warranty_start": "pac", "law": "England and Wales",
}
FX_USD_EUR = {"from_currency": "USD", "to_currency": "EUR", "rate": "0.9210", "dated": "2026-11-01"}
PROJECT = {
    "po_award_date": {"value": "2027-01-15"},
    "frozen_budget": {"amount": "3150000.00", "currency": "EUR"},
    "fx": [FX_USD_EUR, {"from_currency": "GBP", "to_currency": "EUR", "rate": "1.17", "dated": "2026-11-01"}],
    "delay_scenario_weeks": {"value": "8"},
    "owner_terms": OWNER_TERMS,
    "procurement_negotiated": True,
    "signing_before_ntp": False,
    "project_id": "PRJ-SOLAR-350",
}


def _names(field, excinfo):
    """True when an error's message or location names the field (not just the echoed input)."""
    return any(field in e["msg"] or field in [str(part) for part in e["loc"]] for e in excinfo.value.errors())


def _manifest(documents, project=None):
    data = {**BASE, "documents": documents}
    if project is not None:
        data["project"] = project
    return TenderManifest.model_validate(data)


def test_a_manifest_with_all_four_roles_and_the_full_baseline_loads():
    manifest = _manifest([MR, BID, FA, FA_AMENDMENT, OWNER], PROJECT)
    roles = [d.role for d in manifest.documents]
    assert roles == ["mr_spec", "vendor_bid", "framework_agreement", "framework_agreement", "owner_contract"]
    amendment = manifest.documents[3]
    assert (amendment.counterparty_id, amendment.amends, str(amendment.dated)) == ("vendor-a", "fa-a.pdf", "2026-02-10")
    project = manifest.project
    assert project.frozen_budget.amount == Decimal("3150000.00") and project.frozen_budget.currency == "EUR"
    assert project.frozen_budget.source == "buyer_entered"
    assert project.fx[0].rate == Decimal("0.9210") and project.fx[0].source == "buyer_entered"
    assert project.delay_scenario_weeks.value == Decimal("8")
    terms = project.owner_terms
    assert terms.source == "buyer_entered" and terms.warranty_start == "pac"
    assert terms.ld_rate == Decimal("0.5") and terms.package_ld_share_pct == Decimal("12.5")
    assert terms.contract_value.amount == Decimal("48000000.00")
    assert (project.procurement_negotiated, project.signing_before_ntp, project.project_id) == (
        True, False, "PRJ-SOLAR-350")


def test_unknown_owner_terms_stay_none_never_zero():
    terms = OwnerTerms.model_validate({"law": "Spain"})
    assert terms.ld_rate is None and terms.ld_cap is None and terms.contract_value is None
    assert terms.package_ld_share_pct is None and terms.warranty_months is None


@pytest.mark.parametrize("documents, field", [
    # A framework agreement belongs to one counterparty, named as counterparty_id, not vendor_id.
    ([MR, {**FA, "counterparty_id": None}], "counterparty_id"),
    ([MR, {**FA, "vendor_id": "vendor-a"}], "vendor_id"),
    # An owner contract is the project's, not any vendor's.
    ([MR, {**OWNER, "vendor_id": "vendor-a"}], "vendor_id"),
    ([MR, {**OWNER, "counterparty_id": "vendor-a"}], "counterparty_id"),
    # An amendment amends another entry of the same role and counterparty.
    ([MR, FA, {**FA_AMENDMENT, "amends": "missing.pdf"}], "amends"),
    ([MR, FA, {**FA_AMENDMENT, "amends": "fa-a-amd1.pdf"}], "amends"),
    ([MR, FA, OWNER, {**FA_AMENDMENT, "amends": "owner.pdf"}], "amends"),
    ([MR, FA, {**FA, "file": "fa-b.pdf", "counterparty_id": "vendor-b"},
      {**FA_AMENDMENT, "amends": "fa-b.pdf"}], "amends"),
    ([MR, {**FA, "role": "contract_draft"}], "role"),
    # An amendment carries its date: the later document governs for the clauses it covers.
    ([MR, FA, {**FA_AMENDMENT, "dated": None}], "dated"),
    # Only contract documents are amended this way (a revised MR or bid is a new document).
    ([MR, BID, {**BID, "file": "bid-a-rev1.pdf", "vendor_id": "vendor-z", "amends": "bid-a.pdf",
                "dated": "2026-02-10"}], "amends"),
    # Two amendments that amend each other have no original.
    ([MR, {**FA, "amends": "fa-a-amd1.pdf"}, FA_AMENDMENT], "amends"),
])
def test_document_rules_reject_with_the_field_named(documents, field):
    with pytest.raises(ValidationError) as excinfo:
        _manifest(documents)
    assert _names(field, excinfo), excinfo.value


@pytest.mark.parametrize("project, field", [
    ({"frozen_budget": {"amount": "1", "currency": "eur"}}, "currency"),
    ({"frozen_budget": {"amount": "1", "currency": "EURO"}}, "currency"),
    ({"frozen_budget": {"amount": "1", "currency": "EUR", "source": "mr_cited"}}, "source"),
    ({"fx": [{"from_currency": "USD", "to_currency": "EUR", "rate": "0", "dated": "2026-11-01"}]}, "rate"),
    ({"fx": [{"from_currency": "USD", "to_currency": "EUR", "rate": "-1.1", "dated": "2026-11-01"}]}, "rate"),
    ({"fx": [{"from_currency": "US", "to_currency": "EUR", "rate": "1.1", "dated": "2026-11-01"}]}, "from_currency"),
    ({"fx": [{"from_currency": "USD", "to_currency": "EUR", "rate": "1.1"}]}, "dated"),
    ({"owner_terms": {"warranty_start": "first_power"}}, "warranty_start"),
    ({"owner_terms": {"package_ld_share_pct": "100.5"}}, "package_ld_share_pct"),
    ({"owner_terms": {"package_ld_share_pct": "-1"}}, "package_ld_share_pct"),
    ({"owner_terms": {"ld_rate_basis": "percent_per_month"}}, "ld_rate_basis"),
    ({"owner_terms": {"ld_cap_basis": "per_day"}}, "ld_cap_basis"),
    ({"owner_terms": {"liability_cap": {"amount": "1", "currency": "eu"}}}, "currency"),
    # One rate per currency pair, and a pair is two different currencies.
    ({"fx": [{**FX_USD_EUR, "to_currency": "USD"}]}, "fx"),
    ({"fx": [FX_USD_EUR, {**FX_USD_EUR, "rate": "0.95", "dated": "2026-11-20"}]}, "fx"),
    ({"fx": [FX_USD_EUR, {**FX_USD_EUR, "from_currency": "EUR", "to_currency": "USD", "rate": "1.08"}]}, "fx"),
    # A rate or cap is entered with its basis, or neither is.
    ({"owner_terms": {"ld_rate": "0.5"}}, "ld_rate_basis"),
    ({"owner_terms": {"ld_rate_basis": "percent_per_day"}}, "ld_rate"),
    ({"owner_terms": {"ld_cap": "10"}}, "ld_cap_basis"),
    ({"owner_terms": {"ld_cap_basis": "money"}}, "ld_cap"),
    # Amounts, rates, caps and months are never negative.
    ({"frozen_budget": {"amount": "-1", "currency": "EUR"}}, "amount"),
    ({"owner_terms": {"ld_rate": "-0.5", "ld_rate_basis": "percent_per_week"}}, "ld_rate"),
    ({"owner_terms": {"ld_cap": "-10", "ld_cap_basis": "percent"}}, "ld_cap"),
    ({"owner_terms": {"warranty_months": -1}}, "warranty_months"),
])
def test_baseline_rules_reject_with_the_field_named(project, field):
    with pytest.raises(ValidationError) as excinfo:
        _manifest([MR], project)
    assert _names(field, excinfo), excinfo.value


@pytest.mark.parametrize("tender", ["TENDER-2026-402", "TENDER-2026-417", "TENDER-2026-418"])
def test_the_sample_manifests_load_and_dump_unchanged(tender):
    raw = json.loads((SAMPLES / tender / "manifest.json").read_text(encoding="utf-8"))
    manifest = TenderManifest.model_validate(raw)
    assert [d.role for d in manifest.documents][0] == "mr_spec"
    # The new optional fields are left out of the dump when unset, so a stored manifest is what it was.
    old_keys = {"file", "role", "vendor_id", "vendor_name", "country"}
    for dumped, source in zip(manifest.model_dump(mode="json")["documents"], raw["documents"]):
        assert set(dumped) == old_keys
        assert {k: dumped[k] for k in source} == source


def test_a_baseline_with_only_the_old_fields_dumps_as_before():
    old = {"po_award_date": {"value": "2027-01-15"}, "site_altitude_m": {"value": "1200"}}
    project = ProjectBaseline.model_validate(old)
    assert project.fx == [] and project.owner_terms is None and project.frozen_budget is None
    assert project.model_dump(mode="json", exclude_none=True) == {
        "po_award_date": {"value": "2027-01-15", "source": "buyer_entered"},
        "site_altitude_m": {"value": "1200", "source": "buyer_entered"},
    }


def test_each_new_baseline_field_is_reviewed_like_the_old_ones():
    dumped = _manifest([MR], PROJECT).project.model_dump(mode="json", exclude_none=True)
    assert set(dumped) == set(PROJECT)
    for name, value in dumped.items():
        assert project_field_state(value, None) == "open", name
        review = {"action": "approve", "evidence_hash": project_field_hash(value)}
        assert project_field_state(value, review) == "reviewed", name


def test_money_and_fx_models_stand_alone():
    assert BaselineMoney(amount=Decimal("1.5"), currency="USD").note is None
    fx = BaselineFx(from_currency="USD", to_currency="EUR", rate=Decimal("0.92"), dated="2026-11-01")
    assert str(fx.dated) == "2026-11-01"


def test_a_zero_is_kept_as_a_known_zero():
    terms = OwnerTerms.model_validate({"ld_cap": "0", "ld_cap_basis": "percent", "warranty_months": 0})
    assert terms.ld_cap == Decimal("0") and terms.warranty_months == 0


def test_an_amendment_chain_loads():
    second = {**FA_AMENDMENT, "file": "fa-a-amd2.pdf", "amends": "fa-a-amd1.pdf", "dated": "2026-08-01"}
    manifest = _manifest([MR, FA, FA_AMENDMENT, second])
    assert [d.amends for d in manifest.documents] == [None, None, "fa-a.pdf", "fa-a-amd1.pdf"]
