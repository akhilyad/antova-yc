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


# One usual contract wording per field (ruling R14): a field no wording can fill is a term that is
# always "missing", so its checks never run.
CANONICAL = {
    ("CT-01", "ld_rate"): "Liquidated damages of 0.5% of the Contract Price per day of delay.",
    ("CT-01", "ld_cap"): "Liquidated damages are capped at 10 % of the Contract Price.",
    ("CT-01", "ld_triggers"): "LDs apply to late delivery and/or any late Cold Commissioning and/or Hot Commissioning.",
    ("CT-02", "liability_cap"): "The Supplier's total liability is limited to 100% of the Contract Price.",
    ("CT-02", "liability_cap_money"): "The Supplier's total liability is limited to EUR 500,000.",
    ("CT-02", "liability_exclusions"): "The limitation of liability shall not apply in case of gross negligence, "
                                       "wilful misconduct or fraud.",
    ("CT-03", "warranty_months"): "The warranty period is 24 months from PAC.",
    ("CT-03", "warranty_start"): "The warranty period starts from Provisional Acceptance (PAC).",
    ("CT-03", "warranty_on_site"): "Defective parts shall be repaired or replaced on site at the Seller's cost.",
    ("CT-03", "warranty_coverage"): "The warranty covers all components of the supply.",
    ("CT-04", "warranty_bond_pct"): "The Supplier provides a warranty bond of 5% of the Contract Price.",
    ("CT-04", "warranty_bond_months"): "The warranty bond is valid for 24 months.",
    ("CT-05", "performance_bond_pct"): "A performance bond equal to ten percent (10%) of the Price.",
    ("CT-05", "performance_bond_release"): "The performance bond is released at Provisional Acceptance.",
    ("CT-06", "advance_bond_pct"): "Against an advance payment bond of 10%.",
    ("CT-07", "milestones"): "90% within thirty (30) days after delivery; 10% at PAC against a warranty bond.",
    ("CT-08", "termination_fee_pct"): "The Buyer pays a termination fee of 5% of the price of the affected Goods.",
    ("CT-09", "title_event"): "Title to the Goods passes to the Buyer on delivery.",
    ("CT-10", "ntp_deadline_months"): "The Buyer may issue the NTP within 8 months from the Project Purchase Order.",
    ("CT-11", "design_life_months"): "The equipment has a minimum operating period of 35 years.",
    ("CT-12", "incoterm"): "Delivery DAP Site Rotterdam (Incoterms 2020).",
    ("CT-12", "named_place"): "Delivery DAP Site Rotterdam (Incoterms 2020).",
    ("CT-13", "price_currency"): "The Contract Price is in EUR.",
    ("CT-13", "price_fixed_after_ntp"): "The price is fixed and firm from the issue of the Notice to Proceed.",
    ("CT-13", "price_type"): "The Contract Price is a fixed lump sum.",
    ("CT-13", "escalation_formula"): "The price is not subject to any escalation.",
    ("CT-14", "law"): "This contract is governed by the laws of England and Wales.",
    ("CT-14", "forum"): "Disputes shall be finally settled by ICC arbitration in London.",
    ("CT-15", "credit_insurer"): "The Supplier holds credit insurance with Northgate Credit Assurance plc.",
    ("CT-15", "credit_secured_value"): "The secured value under the credit insurance is EUR 2,000,000.",
    ("CT-16", "fa_reference"): "This order is subject to the terms of the Framework Agreement dated 10 January 2026.",
    ("CT-17", "order"): "In case of conflict, the documents prevail in the following order: (1) the Purchase "
                        "Order; (2) the Framework Agreement; (3) the Technical Specification; (4) the Offer.",
}


def test_every_field_reads_from_a_usual_wording():
    fields = {(p.id, f.name): f for p in CONTRACT_PARAMETERS for f in p.fields}
    assert set(CANONICAL) == set(fields)
    for key, quote in CANONICAL.items():
        f = fields[key]
        value = parse_field(f.kind, quote, f.name)
        assert value is not None, key
        assert value.kind == f.kind, key
        assert plausible(f, value), key


# Near-variants, negations and look-alikes (review round 1). Each reader returns None or the value the
# wording really states, never a plausible wrong one.
NOT_A_VALUE = [
    ("ntp_deadline_months", "Delivery shall take place within 14 months after NTP.", None),
    ("ntp_deadline_months", "Delivery within 10 months from the Purchase Order.", None),
    ("ntp_deadline_months", "The price is fixed for 18 months from contract signature.", None),
    ("ntp_deadline_months", "Payment within 30 days from the order.", None),
    ("ntp_deadline_months", "The Buyer may issue the NTP within 1,5 months from the PO.", None),
    ("ntp_deadline_months", "The Buyer may issue the NTP within eight (8) months from the PO.", 8),
    ("design_life_months", "Spare parts shall be available for 10 years over the design life of 30 years.", 360),
    ("design_life_months", "The design life is thirty (30) years.", 360),
    ("fa_reference", "The Framework Agreement does not apply to this order.", None),
    ("fa_reference", "This offer is not subject to the Framework Agreement.", None),
    ("order", "Clause 2.1: In case of conflict, the Purchase Order prevails over the Offer.", None),
    ("order", "Order of precedence: documents listed in descending order of priority: 1. Purchase Order; 2. Offer.",
     ("purchase_order", "offer")),
    ("warranty_on_site", "The Contractor shall remedy any defect in the Works on site.", 1),
    ("warranty_coverage", "The warranty covers all components, except normal wear and tear.", 1),
    ("warranty_coverage", "The warranty covers all components and shall only start at PAC.", 1),
    ("liability_exclusions", "The Supplier excludes all liability for third-party claims.", None),
    ("named_place", "FCA, Incoterms 2020, named place Hamburg.", "hamburg"),
    ("named_place", "Delivery DAP Incoterms 2020 Rotterdam.", "rotterdam"),
    ("price_fixed_after_ntp", "The price is fixed from NTP and shall not be subject to escalation.", 1),
    ("price_type", "The Contract Price is a fixed lump sum and shall not be subject to adjustment.", "fixed_lump_sum"),
    ("price_type", "The Contract Price is a fixed lump sum, not subject to escalation.", "fixed_lump_sum"),
    ("escalation_formula", "No escalation applies before NTP; thereafter the price is adjusted per the formula "
                           "P = P0 x Cu/Cu0.", "formula"),
    ("escalation_formula", "No escalation applies before NTP.", None),
    ("escalation_formula", "No escalation formula applies.", "none"),
    ("credit_insurer", "The equipment shall be insured by the Supplier until delivery.", None),
    ("credit_secured_value", "The performance security of EUR 500,000 is returned at PAC.", None),
    ("termination_fee_pct", "If the cap of 10% is reached, the Buyer may terminate the Contract.", None),
    ("termination_fee_pct", "On termination, the performance bond of 10% shall be returned.", None),
    ("termination_fee_pct", "On termination for convenience the Buyer pays 10% if notice is given 6 months before "
                            "delivery and 50% thereafter.", None),
    ("termination_fee_pct", "Overdue amounts bear interest at 8% per annum and the Supplier may terminate.", None),
    ("advance_bond_pct", "10% with order against an advance payment guarantee and 5% at PAC.", None),
    ("performance_bond_pct", "Performance guarantee: the losses shall not exceed the guaranteed value by more "
                             "than 5%.", None),
    ("performance_bond_pct", "The performance bond of 10% shall be reduced to 5% at delivery.", 10.0),
    ("ld_triggers", "LDs apply for late delivery; delays to PAC caused by the Buyer are excluded.", ("delivery",)),
    ("ld_triggers", "LDs are not applicable to late delivery.", None),
    # Review round 2.
    ("escalation_formula", "Prices are firm and not subject to any escalation or adjustment formula.", "none"),
    ("escalation_formula", "There shall be no escalation or adjustment formula.", "none"),
    ("escalation_formula", "The price is not subject to escalation. Price basis: steel index of January 2026.", "none"),
    ("escalation_formula", "No price adjustment applies.", "none"),
    ("escalation_formula", "The price is subject to escalation per the formula P = P0 x (0.15 + 0.85 Cu/Cu0).",
     "formula"),
    ("named_place", "Delivery terms: DAP Rotterdam, Incoterms 2020, including unloading.", "rotterdam"),
    ("named_place", "DAP Rotterdam Incoterms 2020 and the risk passes on delivery.", "rotterdam"),
    ("named_place", "DAP Site Rotterdam (Incoterms 2020).", "site rotterdam"),
    ("named_place", "FCA Hamburg Incoterms 2020.", "hamburg"),
    ("named_place", "DDP Site, Incoterms 2020, with the Seller bearing all duties.", "site"),
    ("design_life_months", "The service period is 5 years from PAC.", None),
    ("design_life_months", "The design period is 3 months.", None),
    ("design_life_months", "Spare parts shall be available for a service period of 10 years.", None),
    ("design_life_months", "The equipment has a design life of 25 (twenty-five) years.", 300),
    ("design_life_months", "a 30-year design life", 360),
    ("liability_exclusions", "The limitation of liability shall not apply in case of gross negligence or fraud. "
                             "The Supplier shall not be liable for indirect loss.", ("gross_negligence", "fraud")),
    ("liability_exclusions", "The limitation of liability shall not apply in case of gross negligence or fraud; "
                             "the Supplier shall not be liable for indirect loss.", ("gross_negligence", "fraud")),
    ("ntp_deadline_months", "Notice to proceed no later than 60 days from PO.", None),
    ("ntp_deadline_months", "The Buyer shall issue the NTP within 30 days after receipt of the advance payment "
                            "guarantee.", None),
    ("ntp_deadline_months", "Delivery within 14 months after issue of the NTP no later than 2 months from PO.", None),
    ("fa_reference", "This Order is subject to the Framework Agreement No. FA-2025-017 dated 10 January 2026.",
     "framework agreement no. fa-2025-017 dated 10 january 2026"),
    ("fa_reference", "This Order is subject to the Framework Agreement dated 10.01.2026.",
     "framework agreement dated 10.01.2026"),
    ("fa_reference", "The Framework Agreement shall apply. No changes to the delivery schedule.", "framework agreement"),
    ("termination_fee_pct", "On termination for convenience the Buyer pays 10% of the price of undelivered Goods; "
                            "the LD cap of 15% remains.", 10.0),
    ("termination_fee_pct", "A termination fee of 5% of the price of the affected Goods.", 5.0),
    ("performance_bond_pct", "Performance bond (10%) of the Contract Price.", 10.0),
    ("ld_triggers", "LDs apply for late delivery and late PAC.", ("delivery", "pac")),
    ("ld_triggers", "Delay in delivery: 0.5% per week. Delay in PAC: 1% per week.", ("delivery", "pac")),
    ("warranty_coverage", "The warranty covers all components except as stated in Clause 5.", None),
    ("warranty_on_site", "Delivery ex works; warranty repairs are made on site.", 1),
    ("order", "In case of conflict the following order applies: Purchase Order, General Conditions of Purchase, "
              "Annexes.", ("purchase_order", "general_conditions", "annexes")),
    ("price_fixed_after_ntp", "The price is fixed from NTP; no part of the price is variable.", None),
]


@pytest.mark.parametrize("name, quote, expected", NOT_A_VALUE)
def test_a_reader_does_not_guess(name, quote, expected):
    f = next(f for p in CONTRACT_PARAMETERS for f in p.fields if f.name == name)
    value = parse_field(f.kind, quote, f.name)
    if expected is None:
        assert value is None, value
    else:
        assert value is not None and value.value == expected, value


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
