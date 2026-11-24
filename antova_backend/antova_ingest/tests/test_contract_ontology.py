"""The contract-terms ontology (CT-01..CT-17) and the prompt that locates it."""

import dataclasses
from typing import get_args

import pytest

from antova_engine import Criticality
from antova_ingest import artifacts, config, contract_ontology, extract, ontology
from antova_ingest.contract_ontology import (
    CONTRACT_PARAMETERS, CONTRACT_PARAMS_BY_ID, CONTRACT_ROLES, LEGAL_TERMS, TBE_EQUIVALENT,
)
from antova_ingest.tests.fakes import PACK
from antova_ingest.types import FieldKind
from antova_ingest.values import parse_field, plausible, plausible_bounds

CLASSES = tuple(ontology.PARAMETERS_BY_CLASS)
EQUIPMENT_PARAMETERS = {p.id: p for params in ontology.PARAMETERS_BY_CLASS.values() for p in params}


def field(param_id, name):
    return next(f for f in CONTRACT_PARAMS_BY_ID[param_id].fields if f.name == name)


def test_ids_run_ct01_to_ct17_in_order():
    assert [p.id for p in CONTRACT_PARAMETERS] == [f"CT-{n:02d}" for n in range(1, 18)]
    assert list(CONTRACT_PARAMS_BY_ID) == [p.id for p in CONTRACT_PARAMETERS]


def test_names_follow_the_spec_table():
    names = {p.id: p.name for p in CONTRACT_PARAMETERS}
    assert names == {
        "CT-01": "Delay LDs", "CT-02": "Limitation of liability", "CT-03": "Warranty",
        "CT-04": "Warranty bond", "CT-05": "Performance bond", "CT-06": "Advance payment bond",
        "CT-07": "Payment milestones", "CT-08": "Termination for convenience fees", "CT-09": "Passing of title",
        "CT-10": "NTP deadline", "CT-11": "Design life / minimum operating period",
        "CT-12": "Incoterms and named place", "CT-13": "Price basis", "CT-14": "Governing law and disputes",
        "CT-15": "Supplier credit insurance", "CT-16": "Incorporation of the framework agreement",
        "CT-17": "Order of precedence",
    }


def test_field_names_and_kinds():
    shape = {p.id: tuple((f.name, f.kind) for f in p.fields) for p in CONTRACT_PARAMETERS}
    assert tuple(f.name for f in CONTRACT_PARAMS_BY_ID["CT-01"].fields) == ("ld_rate", "ld_cap", "ld_triggers")
    assert shape["CT-01"] == (("ld_rate", "rate_per_period"), ("ld_cap", "percent"), ("ld_triggers", "scope"))
    assert shape["CT-03"][:2] == (("warranty_months", "months"), ("warranty_start", "event"))
    assert shape["CT-07"] == (("milestones", "milestone_list"),)
    assert shape["CT-12"][0] == ("incoterm", "incoterm")
    assert shape["CT-14"] == (("law", "law"), ("forum", "forum"))
    assert shape["CT-16"] == (("fa_reference", "scope"),)
    assert shape["CT-17"] == (("order", "scope"),)


def test_every_field_is_well_formed():
    kinds = set(get_args(FieldKind))
    for p in CONTRACT_PARAMETERS:
        names = [f.name for f in p.fields]
        assert names and len(names) == len(set(names)), p.id
        assert p.sweep_terms and p.locate_hint and p.category, p.id
        p.display_template.format(**{name: "" for name in names})
        for f in p.fields:
            assert f.kind in kinds, (p.id, f.name)
            assert f.kind in config.PARSED_KINDS, (p.id, f.name)
            assert f.operator in config.OPERATORS, (p.id, f.name)
            if f.operator == "ord" or (f.operator == "le" and f.kind == "event"):
                assert f.kind in ontology.ORDINALS, (p.id, f.name)
            plausible_bounds(f)  # raises if a bound cannot be read


def test_criticality():
    high = {"CT-01", "CT-02", "CT-03", "CT-04", "CT-05", "CT-06", "CT-07", "CT-16"}
    for p in CONTRACT_PARAMETERS:
        assert p.criticality == (Criticality.HIGH if p.id in high else Criticality.COMMERCIAL), p.id


def test_the_shared_constants():
    assert CONTRACT_ROLES == frozenset({"framework_agreement", "owner_contract"})
    assert TBE_EQUIVALENT == {"CT-01": "PARAM-10", "CT-02": "PARAM-11", "CT-12": "PARAM-06"}
    assert set(TBE_EQUIVALENT) <= set(CONTRACT_PARAMS_BY_ID)
    assert set(TBE_EQUIVALENT.values()) <= set(EQUIPMENT_PARAMETERS)
    assert LEGAL_TERMS == frozenset({"CT-02", "CT-08", "CT-09", "CT-14"})


def test_the_pinned_plausible_ranges():
    pinned = {
        ("CT-01", "ld_rate"): ("0.01% per day", "5% per day"),
        ("CT-01", "ld_cap"): ("1 %", "100 %"),
        ("CT-03", "warranty_months"): ("6", "120"),
        ("CT-04", "warranty_bond_pct"): ("1 %", "30 %"),
        ("CT-05", "performance_bond_pct"): ("1 %", "30 %"),
        ("CT-06", "advance_bond_pct"): ("1 %", "30 %"),
        ("CT-11", "design_life_months"): ("120", "600"),  # 10 to 50 years
        ("CT-10", "ntp_deadline_months"): ("1", "60"),
    }
    for (param_id, name), bounds in pinned.items():
        assert field(param_id, name).plausible == bounds, (param_id, name)


def test_an_ld_rate_outside_the_range_is_not_plausible():
    rate = field("CT-01", "ld_rate")
    assert not plausible(rate, parse_field("rate_per_period", "LDs of 0.001% per day of delay", "ld_rate"))
    assert plausible(rate, parse_field("rate_per_period", "LDs of 0.5% per day of delay", "ld_rate"))
    assert plausible(rate, parse_field("rate_per_period", "LDs of 1% per week of delay", "ld_rate"))
    assert not plausible(rate, parse_field("rate_per_period", "LDs of 50% per week of delay", "ld_rate"))
    cap = field("CT-01", "ld_cap")
    assert plausible(cap, parse_field("percent", "LDs capped at 10 % of the Contract Price", "ld_cap"))
    assert not plausible(cap, parse_field("percent", "LDs capped at 150 % of the Contract Price", "ld_cap"))
    warranty = field("CT-03", "warranty_months")
    assert not plausible(warranty, parse_field("months", "warranty of 3 months", "warranty_months"))
    assert plausible(warranty, parse_field("months", "warranty of 24 months", "warranty_months"))


def test_better_is_the_buyers_side():
    # Title passing earlier and lower termination fees are better: both are "at most" checks.
    assert field("CT-09", "title_event").operator == "le"
    assert field("CT-08", "termination_fee_pct").operator == "le"
    # A later warranty start, a later bond release and a later NTP deadline are better.
    assert field("CT-03", "warranty_start").operator == "ord"
    assert field("CT-05", "performance_bond_release").operator == "ord"
    assert field("CT-10", "ntp_deadline_months").operator == "ge"
    assert field("CT-12", "incoterm").operator == "ord"
    assert field("CT-14", "law").operator == "eq" and field("CT-14", "forum").operator == "eq"


# --- the prompt ----------------------------------------------------------------------------------

@pytest.mark.parametrize("role", sorted(CONTRACT_ROLES))
@pytest.mark.parametrize("equipment_class", CLASSES)
def test_the_contract_prompt_names_every_term_and_no_equipment_parameter(role, equipment_class):
    prompt = extract.system_prompt_for(role, equipment_class)
    for p in CONTRACT_PARAMETERS:
        assert f"{p.id} ({p.name})" in prompt
        assert p.locate_hint in prompt
    for p in EQUIPMENT_PARAMETERS.values():
        assert p.name not in prompt, p.id
        assert p.locate_hint not in prompt, p.id
    assert "PARAM-" not in prompt
    assert prompt == extract.build_contract_prompt()


@pytest.mark.parametrize("equipment_class", CLASSES)
def test_other_roles_keep_the_equipment_prompt(equipment_class):
    expected = extract.build_system_prompt(equipment_class)
    assert extract.system_prompt_for("mr_spec", equipment_class) == expected
    assert extract.system_prompt_for("vendor_bid", equipment_class) == expected


@pytest.mark.parametrize("equipment_class", CLASSES)
def test_a_bids_contract_pass_gets_the_contract_prompt(equipment_class):
    prompt = extract.system_prompt_for("vendor_bid", equipment_class, contract=True)
    assert prompt == extract.build_contract_prompt()
    assert prompt != extract.build_system_prompt(equipment_class)


def test_the_equipment_prompt_is_unchanged():
    # Saved locator answers are keyed by this prompt's hash; the contract terms must not touch it.
    prompt = extract.build_system_prompt()
    assert "CT-" not in prompt
    assert extract.prompt_fingerprint() == extract.prompt_fingerprint(ontology.DEFAULT_EQUIPMENT_CLASS)


# --- the fingerprint -----------------------------------------------------------------------------

def test_the_contract_ontology_hash_is_stable_and_in_the_fingerprint():
    digest = artifacts.contract_ontology_hash()
    assert len(digest) == 64 and digest == artifacts.contract_ontology_hash()
    assert artifacts.run_fingerprint(PACK, "replay")["parts"]["contract_ontology"] == digest


def test_the_contract_ontology_hash_follows_the_terms(monkeypatch):
    before = artifacts.contract_ontology_hash()
    first = CONTRACT_PARAMETERS[0]
    changed = dataclasses.replace(first, locate_hint=first.locate_hint + " Changed.")
    monkeypatch.setattr(contract_ontology, "CONTRACT_PARAMETERS", (changed,) + CONTRACT_PARAMETERS[1:])
    assert artifacts.contract_ontology_hash() != before


def test_the_contract_ontology_hash_follows_the_event_order(monkeypatch):
    before = artifacts.contract_ontology_hash()
    monkeypatch.setitem(ontology.ORDINALS, "event", tuple(reversed(ontology.ORDINALS["event"])))
    assert artifacts.contract_ontology_hash() != before
