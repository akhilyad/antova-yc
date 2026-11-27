"""The contract-terms ontology (CT-01..CT-17) and the prompt that locates it."""

import dataclasses
from decimal import Decimal
from typing import get_args

import pytest

from antova_engine import ComplianceStatus, Criticality
from antova_ingest import artifacts, config, contract_ontology, costs, extract, ontology
from antova_ingest.contract_ontology import (
    CONTRACT_PARAMETERS, CONTRACT_PARAMS_BY_ID, CONTRACT_ROLES, LEGAL_TERMS, TBE_EQUIVALENT,
)
from antova_ingest.tests.fakes import PACK
from antova_ingest.precedence import resolve
from antova_ingest.types import Citation, Evidence, FieldKind, FieldValue
from antova_ingest.values import (
    CITATION_ONLY, NOT_COMPARABLE, check, citation_only, display_text, parse_field, plausible, plausible_bounds,
    value_in_span,
)

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
    ("CT-13", "price_currency"): "The Contract Price is in EUR.",
    ("CT-13", "price_fixed_after_ntp"): "The price is fixed and firm from the issue of the Notice to Proceed.",
    ("CT-13", "price_type"): "The Contract Price is a fixed lump sum.",
    ("CT-14", "law"): "This contract is governed by the laws of England and Wales.",
    ("CT-14", "forum"): "Disputes shall be finally settled by ICC arbitration in London.",
    ("CT-15", "credit_insurer"): "The Supplier holds credit insurance with Northgate Credit Assurance plc.",
    ("CT-15", "credit_secured_value"): "The secured value under the credit insurance is EUR 2,000,000.",
    ("CT-16", "fa_reference"): "This order is subject to the terms of the Framework Agreement dated 10 January 2026.",
    ("CT-17", "order"): "In case of conflict, the documents prevail in the following order: (1) the Purchase "
                        "Order; (2) the Framework Agreement; (3) the Technical Specification; (4) the Offer.",
}


def test_every_field_reads_from_a_usual_wording():
    # The citation-only fields (ruling R18) are recorded as quoted; they have their own tests below.
    fields = {(p.id, f.name): f for p in CONTRACT_PARAMETERS for f in p.fields if f.name not in CITATION_ONLY}
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
    ("price_fixed_after_ntp", "The price is fixed from NTP and shall not be subject to escalation.", 1),
    ("price_type", "The Contract Price is a fixed lump sum and shall not be subject to adjustment.", "fixed_lump_sum"),
    ("price_type", "The Contract Price is a fixed lump sum, not subject to escalation.", "fixed_lump_sum"),
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
    ("design_life_months", "The service period is 5 years from PAC.", None),
    ("design_life_months", "The design period is 3 months.", None),
    ("design_life_months", "Spare parts shall be available for a service period of 10 years.", None),
    ("design_life_months", "The equipment has a design life of 25 (twenty-five) years.", 300),
    ("design_life_months", "a 30-year design life", 360),
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
    # Review round 3 (the reviewer's cases.txt and cases2.txt).
    ('design_life_months', 'The minimum operating period is 20 years.', 240),
    ('design_life_months', 'Design life: 25 years.', 300),
    ('design_life_months', 'The service life of the equipment shall be 30 (thirty) years.', 360),
    ('design_life_months', 'The design life is twenty-five (25) years.', 300),
    ('design_life_months', 'Operating period of 20 years at full load; design life of 25 years.', 300),
    ('design_life_months', 'Design life of 25 years (see Annex 3).', 300),
    ('design_life_months', 'The equipment design life of 20 (and in no case less than 15) years.', None),
    ('ntp_deadline_months', 'The Buyer shall issue the NTP within 8 months from the Purchase Order.', 8),
    ('ntp_deadline_months', 'The NTP shall be issued no later than 60 days after signature of the Contract.', Decimal('1.972602739726027397260273973')),
    ('ntp_deadline_months', 'The NTP shall be issued no later than 60 days from the date of the Purchase Order.', Decimal('1.972602739726027397260273973')),
    ('ntp_deadline_months', 'The NTP shall be issued no later than 60 days from receipt of the Purchase Order.', None),
    ('ntp_deadline_months', 'The Buyer may issue the NTP within 6 months.', 6),
    ('ntp_deadline_months', 'The Buyer may issue the NTP within 6 months of the Order.', 6),
    ('ntp_deadline_months', 'The Buyer may issue the NTP within 6 months of contract award.', 6),
    ('ntp_deadline_months', 'The Buyer may issue the NTP within 6 months from the Effective Date.', None),
    ('ntp_deadline_months', 'Delivery 12 months from issue of NTP; the Buyer shall issue the NTP within 3 months from PO.', 3),
    ('ntp_deadline_months', 'The Buyer may issue the NTP within 6 months after the Order, failing which the Supplier may terminate.', 6),
    ('ntp_deadline_months', 'The NTP shall be issued within 90 days of the PO date.', Decimal('2.958904109589041095890410959')),
    ('ntp_deadline_months', 'The NTP shall be issued within 90 days following the order acknowledgement.', None),
    ('price_type', 'The price is a fixed lump sum, not subject to escalation.', 'fixed_lump_sum'),
    ('price_type', 'Without prejudice to Clause 5, the price is a fixed lump sum.', 'fixed_lump_sum'),
    ('price_type', 'The price is a fixed lump sum. No escalation applies.', 'fixed_lump_sum'),
    ('price_type', 'The price is a lump sum subject to escalation.', 'partly_variable'),
    ('price_type', 'The price is not a lump sum and is subject to escalation.', None),
    ('price_type', 'The price is variable.', 'variable'),
    ('price_type', 'Neither party may change the price, which is a fixed lump sum.', 'fixed_lump_sum'),
    ('price_type', 'The price shall not be variable and is a fixed lump sum', None),
    ('price_fixed_after_ntp', 'The price is fixed after NTP and not subject to escalation.', 1),
    ('price_fixed_after_ntp', 'The price is not fixed after NTP.', 0),
    ('price_fixed_after_ntp', 'The price is fixed after NTP, without any escalation.', 1),
    ('price_fixed_after_ntp', 'The price is subject to escalation until NTP and fixed thereafter.', 1),
    ('price_fixed_after_ntp', 'No escalation; the price is fixed after NTP.', 1),
    ('price_fixed_after_ntp', 'The price, which is not variable, is fixed after NTP.', 1),
    ('warranty_on_site', 'Warranty period 24 months. Goods delivered ex works.', None),
    ('warranty_on_site', 'Defective parts are returned to the factory, but repairs are made on site.', None),
    ('warranty_on_site', "Warranty: the Supplier shall repair defects. Repairs are carried out at the Supplier's factory.", 0),
    ('warranty_on_site', 'The Supplier shall remedy defects on site', 1),
    ('warranty_on_site', 'Warranty repairs: on site. Delivery: ex works.', 1),
    ('warranty_coverage', 'The warranty covers all components unless otherwise agreed.', None),
    ('warranty_coverage', 'The warranty covers all components.', 1),
    ('warranty_coverage', 'The warranty covers only the components listed in Annex 2.', 0),
    ('performance_bond_pct', 'Performance bond (see Clause 5) of 10% of the Contract Price.', 10.0),
    ('performance_bond_pct', 'Performance bond (and advance payment bond) 10%.', None),
    ('performance_bond_pct', 'Advance payment bond (and performance bond) 10%.', None),
    ('advance_bond_pct', 'Advance payment bond (10%) and performance bond (5%).', 10.0),
    ('performance_bond_pct', 'Advance payment bond (10%) and performance bond (5%).', 5.0),
    ('warranty_bond_pct', 'Warranty bond (5%) of the Contract Price.', 5.0),
    ('performance_bond_pct', 'Performance bond (in the amount of 10%) of the Contract Price.', 10.0),
]


@pytest.mark.parametrize("name, quote, expected", NOT_A_VALUE)
def test_a_reader_does_not_guess(name, quote, expected):
    f = next(f for p in CONTRACT_PARAMETERS for f in p.fields if f.name == name)
    value = parse_field(f.kind, quote, f.name)
    if expected is None:
        assert value is None, value
    else:
        assert value is not None and value.value == expected, value


# --- citation-only fields (ruling R18) -------------------------------------------------------------
# Named place, escalation formula and liability exclusions are not normalized or compared in v1: the
# reader records the clause as quoted (whitespace collapsed) and a reviewer compares the quoted clauses.

def test_the_citation_only_fields():
    names = {f.name for p in CONTRACT_PARAMETERS for f in p.fields}
    assert CITATION_ONLY == {"named_place", "escalation_formula", "liability_exclusions"} and CITATION_ONLY <= names
    assert citation_only(field("CT-12", "named_place")) and not citation_only(field("CT-12", "incoterm"))
    # price_type and price_fixed_after_ntp keep their readers.
    assert not citation_only(field("CT-13", "price_type")) and not citation_only(field("CT-13", "price_fixed_after_ntp"))


CITED = [
    (("CT-12", "named_place"), "Delivery DAP Site Rotterdam\n(Incoterms 2020).",
     "Delivery DAP Site Rotterdam (Incoterms 2020)."),
    (("CT-13", "escalation_formula"), "Price adjustment formula:  P = P0 x (0.15 + 0.85 x Cu/Cu0).",
     "Price adjustment formula: P = P0 x (0.15 + 0.85 x Cu/Cu0)."),
    (("CT-13", "escalation_formula"), "The price is not subject to any escalation.",
     "The price is not subject to any escalation."),
    (("CT-02", "liability_exclusions"), "  The limitation of liability shall not apply in case of gross negligence,\n"
                                        "wilful misconduct or fraud. ",
     "The limitation of liability shall not apply in case of gross negligence, wilful misconduct or fraud."),
]


@pytest.mark.parametrize("key, quote, clause", CITED)
def test_a_citation_only_clause_is_captured_verbatim(key, quote, clause):
    f = field(*key)
    value = parse_field(f.kind, quote, f.name)
    assert value == FieldValue("scope", clause, clause)
    assert plausible(f, value) and value_in_span(value, quote)


def test_a_quote_not_about_the_field_records_nothing():
    assert parse_field("scope", "Total liability is limited to 100% of the price.", "liability_exclusions") is None
    assert parse_field("scope", "Delivery to the Buyer's site in Rotterdam.", "named_place") is None
    assert parse_field("scope", "The Contract Price is in EUR.", "escalation_formula") is None
    assert parse_field("scope", "  ", "named_place") is None


# The reviewer's round-4 findings: each was read to a wrong value by the old inference readers.
NB = [
    ("named_place", "DAP Rotterdam and the risk passes to the Buyer on arrival."),  # NB1
    ("named_place", "DAP Rotterdam for delivery of the Goods."),  # NB1
    ("named_place", "DAP Rotterdam to be delivered by truck."),  # NB1
    ("named_place", "DAP Rotterdam at the latest by 1 May 2027."),  # NB1
    ("escalation_formula", "The Contract Price is firm and not subject to escalation, indexation or any adjustment "
                           "formula."),  # NB2
    ("escalation_formula", "The escalation formula excludes transport costs."),  # NB3
    ("escalation_formula", "The escalation formula is waived until NTP."),  # NB3
    ("liability_exclusions", "Total liability is capped at 100% of the Contract Price. The LD cap of 10% shall not "
                             "apply in case of wilful misconduct."),  # NB4
    ("liability_exclusions", "The Supplier's aggregate liability shall be limited to 100% of the Contract Price. "
                             "The cap on LDs shall not apply in case of fraud."),  # NB4
]


@pytest.mark.parametrize("name, quote", NB)
def test_the_round_4_findings_are_captured_verbatim(name, quote):
    value = parse_field("scope", quote, name)
    assert value == FieldValue("scope", quote, quote)


# Every wording the earlier inference readers were probed with (review rounds 1 to 4): the reader gives the
# quote as written, or nothing; never a slug, "none" / "formula" or a tuple of exclusion codes.
CITATION_PROBES = [
    ('named_place', 'FCA, Incoterms 2020, named place Hamburg.'),
    ('named_place', 'Delivery DAP Incoterms 2020 Rotterdam.'),
    ('named_place', 'Delivery terms: DAP Rotterdam, Incoterms 2020, including unloading.'),
    ('named_place', 'DAP Rotterdam Incoterms 2020 and the risk passes on delivery.'),
    ('named_place', 'DAP Site Rotterdam (Incoterms 2020).'),
    ('named_place', 'FCA Hamburg Incoterms 2020.'),
    ('named_place', 'DDP Site, Incoterms 2020, with the Seller bearing all duties.'),
    ('named_place', 'DAP Rotterdam incl. unloading.'),
    ('named_place', 'DAP Bosnia and Herzegovina, Incoterms 2020.'),
    ('named_place', 'DAP Site to be named by the Buyer.'),
    ('named_place', 'DAP Site, as defined in Clause 1.'),
    ('named_place', 'FCA Hamburg, Germany, Incoterms 2020.'),
    ('named_place', 'DAP Incoterms 2020 Rotterdam.'),
    ('named_place', 'FCA, Incoterms 2020, named place Hamburg.'),
    ('named_place', "Delivery DAP Buyer's warehouse at the port of Antwerp."),
    ('named_place', 'Delivery: DDP Project Site Hornsea for the Wind Farm.'),
    ('named_place', 'DAP Rotterdam in accordance with Incoterms 2020.'),
    ('named_place', 'DAP Rotterdam per ICC Incoterms® 2020.'),
    ('named_place', 'Delivery DAP Rotterdam where unloading is by the Buyer.'),
    ('named_place', 'CIF Port of Antwerp, Incoterms 2010'),
    ('named_place', 'DAP Rotterdam, Netherlands; risk passes at delivery.'),
    ('named_place', 'DAP Rotterdam AND unloading'),
    ('named_place', 'DAP Rotterdam, Incoterms 2020'),
    ('named_place', 'Delivery terms DAP Rotterdam Incoterms'),
    ('named_place', "DAP Site at the Buyer's plant in Rotterdam, Incoterms 2020."),
    ('named_place', "DAP Buyer's warehouse at the port of Hamburg (Incoterms 2020)."),
    ('named_place', 'DAP Site for Project Hornsea, Incoterms 2020.'),
    ('named_place', 'DAP Trinidad and Tobago (Incoterms 2020).'),
    ('named_place', "DAP Rotterdam at the Seller's risk."),
    ('named_place', 'DAP Site to be confirmed by the Buyer (Incoterms 2020).'),
    ('named_place', 'DAP Site with offloading by the Buyer.'),
    ('named_place', 'DDP Port of Spain, Incoterms 2020.'),
    ('named_place', "FCA Seller's works with loading"),
    ('named_place', 'DAP Rotterdam and the risk passes to the Buyer on arrival.'),
    ('named_place', 'DAP Rotterdam and risk of loss passes on delivery.'),
    ('named_place', 'DAP Rotterdam for delivery of the Goods.'),
    ('named_place', 'DAP Rotterdam to be delivered by truck.'),
    ('named_place', 'DAP Rotterdam at the latest by 1 May 2027.'),
    ('named_place', 'DAP Rotterdam by 31 March 2027.'),
    ('named_place', 'Delivery DAP Rotterdam within 12 months of NTP.'),
    ('named_place', 'DAP Rotterdam shall apply.'),
    ('named_place', 'DAP Saint Vincent and the Grenadines, Incoterms 2020.'),
    ('named_place', 'DAP Rotterdam, Netherlands, with unloading by the Buyer.'),
    ('named_place', 'DAP Rotterdam, and the Seller bears the cost of unloading.'),
    ('named_place', 'DAP Rotterdam and DDP Hamburg.'),
    ('named_place', 'DAP Rotterdam inclusive of unloading.'),
    ('named_place', 'DAP Rotterdam excl. duties.'),
    ('named_place', "DAP Site, which is the Buyer's plant."),
    ('named_place', 'DAP Rotterdam, to be confirmed.'),
    ('named_place', 'DAP Site (to be confirmed).'),
    ('named_place', "DAP Rotterdam or Antwerp at the Buyer's option."),
    ('named_place', 'DAP Site as defined in Clause 1.'),
    ('escalation_formula', 'No escalation applies before NTP; thereafter the price is adjusted per the formula P = P0 x Cu/Cu0.'),
    ('escalation_formula', 'No escalation applies before NTP.'),
    ('escalation_formula', 'No escalation formula applies.'),
    ('escalation_formula', 'Prices are firm and not subject to any escalation or adjustment formula.'),
    ('escalation_formula', 'There shall be no escalation or adjustment formula.'),
    ('escalation_formula', 'The price is not subject to escalation. Price basis: steel index of January 2026.'),
    ('escalation_formula', 'No price adjustment applies.'),
    ('escalation_formula', 'The price is subject to escalation per the formula P = P0 x (0.15 + 0.85 Cu/Cu0).'),
    ('escalation_formula', 'The price is subject to escalation; the formula is given in Annex 3.'),
    ('escalation_formula', 'Escalation formula: P = P0 (0.2 + 0.8 I/I0), where I is the index not older than 3 months.'),
    ('escalation_formula', 'Price adjustment per the formula in Annex No. 3.'),
    ('escalation_formula', 'Escalation formula: N/A.'),
    ('escalation_formula', 'Prices are firm, excluding any escalation formula.'),
    ('escalation_formula', 'Prices are firm and free of any escalation formula.'),
    ('escalation_formula', 'The escalation formula of the Framework Agreement is deleted.'),
    ('escalation_formula', 'No escalation applies before NTP. After NTP, the price is adjusted per the formula in Annex 4.'),
    ('escalation_formula', 'No escalation applies before NTP; after NTP the price is adjusted per the formula P = P0 x I/I0.'),
    ('escalation_formula', 'Prices are subject to adjustment according to the steel index published by Eurostat.'),
    ('escalation_formula', 'Price escalation: indexed to LME copper, formula P = P0 x Cu/Cu0.'),
    ('escalation_formula', 'The price is not fixed: it is adjusted per the formula P = P0 x Cu/Cu0.'),
    ('escalation_formula', 'Without prejudice to Clause 5, the price is adjusted per formula P = P0 x I/I0.'),
    ('escalation_formula', 'Prices are fixed; the escalation formula of the Framework Agreement does not apply.'),
    ('escalation_formula', 'Price escalation shall apply. The formula is set out in Annex 3.'),
    ('escalation_formula', 'Escalation formula per Annex No. 3: P = P0 x I/I0.'),
    ('escalation_formula', 'The price is subject to escalation per the formula; it shall not exceed 10%.'),
    ('escalation_formula', 'The Contract Price is firm and not subject to escalation, indexation or any adjustment formula.'),
    ('escalation_formula', 'The price shall not be subject to escalation, adjustment or any formula.'),
    ('escalation_formula', 'The escalation formula excludes transport costs.'),
    ('escalation_formula', 'The escalation formula applies to the steel content only and excludes the labour portion.'),
    ('escalation_formula', 'Escalation applies per the formula excluding freight costs.'),
    ('escalation_formula', 'The escalation formula shall not apply to spare parts.'),
    ('escalation_formula', 'The escalation formula does not apply before NTP.'),
    ('escalation_formula', 'The escalation formula is waived until NTP.'),
    ('escalation_formula', 'Escalation formula: not applicable.'),
    ('escalation_formula', 'Price adjustment formula: see Annex 3.'),
    ('escalation_formula', 'Escalation, if any, per formula P = P0 x I/I0.'),
    ('escalation_formula', 'The price is not, under any circumstances, adjusted per the formula.'),
    ('escalation_formula', 'The price is subject to escalation, the formula being P = P0 x I/I0.'),
    ('escalation_formula', 'Price escalation per formula P = P0 (a + b I/I0) where the fixed portion a is nil.'),
    ('escalation_formula', 'Prices are firm until NTP; thereafter the escalation formula in Annex 3 applies.'),
    ('escalation_formula', 'The escalation formula in Annex 3 replaces the deleted Clause 7.'),
    ('escalation_formula', 'No price escalation, adjustment formula or indexation shall apply.'),
    ('escalation_formula', 'Prices are fixed and firm, without escalation or adjustment formula.'),
    ('escalation_formula', 'The Supplier waives any escalation, adjustment formula or price review.'),
    ('escalation_formula', 'The price is not subject to escalation, indexation, or adjustment formula of any kind.'),
    ('liability_exclusions', 'The Supplier excludes all liability for third-party claims.'),
    ('liability_exclusions', 'The limitation of liability shall not apply in case of gross negligence or fraud. The Supplier shall not be liable for indirect loss.'),
    ('liability_exclusions', 'The limitation of liability shall not apply in case of gross negligence or fraud; the Supplier shall not be liable for indirect loss.'),
    ('liability_exclusions', "The Supplier's aggregate liability shall be limited to 100% of the Contract Price; this limitation shall not apply in case of gross negligence or wilful misconduct."),
    ('liability_exclusions', 'The total liability of the Supplier is capped at the Contract Price, save in case of gross negligence or fraud.'),
    ('liability_exclusions', 'The Supplier shall not be liable for indirect loss except in case of fraud. The limitation of liability shall not apply in case of gross negligence.'),
    ('liability_exclusions', 'The Supplier excludes all liability for indirect loss.'),
    ('liability_exclusions', 'The limitation of liability shall not apply in case of gross negligence, wilful misconduct or fraud.'),
    ('liability_exclusions', 'Total liability is capped at 100% of the Contract Price. The LD cap of 10% shall not apply in case of wilful misconduct.'),
    ('liability_exclusions', "The Supplier's liability is limited as set out in Clause 20. LDs are capped at 10%, except in case of gross negligence."),
    ('liability_exclusions', 'The limitation of liability shall not apply in case of gross negligence; the LD cap applies except in case of fraud.'),
    ('liability_exclusions', "The Supplier's aggregate liability shall be limited to 100% of the Contract Price. The cap on LDs shall not apply in case of fraud."),
]


@pytest.mark.parametrize("name, quote", CITATION_PROBES)
def test_a_citation_only_reader_never_infers(name, quote):
    value = parse_field("scope", quote, name)
    clause = " ".join(quote.split())
    assert value is None or value == FieldValue("scope", clause, clause), value


def _cited(param_id, name, quote, page):
    f = field(param_id, name)
    return f, parse_field(f.kind, quote, f.name), Evidence(
        param_id, Citation("bid.pdf", page, (0.0, 0.0, 1.0, 1.0), quote, "commercial"),
        {name: parse_field(f.kind, quote, f.name)})


@pytest.mark.parametrize("param_id, name, required, offered", [
    ("CT-12", "named_place", "Delivery DAP Site Rotterdam (Incoterms 2020).", "DAP Rotterdam and the risk passes."),
    ("CT-12", "named_place", "DAP Rotterdam, Incoterms 2020.", "DAP Rotterdam, Incoterms 2020."),
    ("CT-13", "escalation_formula", "The price is not subject to any escalation.",
     "The price is subject to escalation per the formula P = P0 x I/I0."),
    ("CT-02", "liability_exclusions", "The limitation of liability shall not apply in case of gross negligence.",
     "The limitation of liability shall not apply in case of fraud."),
])
def test_a_citation_only_field_is_never_compared(param_id, name, required, offered):
    f, req, _ = _cited(param_id, name, required, 1)
    _, off, evidence = _cited(param_id, name, offered, 2)
    # values.check: not passed, and the text says to compare by hand (equal clauses too: no comparison runs).
    assert check(f, req, off) == (False, NOT_COMPARABLE)
    # The resolution records it as not comparable: not passed, and (ruling R19) not a deviation either. On its
    # own it asks a person to compare the clauses.
    resolution = resolve(CONTRACT_PARAMS_BY_ID[param_id], {name: req}, [evidence], "V1")
    (result,) = [c for c in resolution.checks if c.field == name]
    assert result.comparable is False and result.passed is False
    assert result.offered_canonical == "compare the quoted clauses by hand"
    assert result.offered == off.display
    assert resolution.status == ComplianceStatus.MANUAL_INSPECTION_REQUIRED
    assert resolution.primary == evidence.citation
    # It has no deviation class, so it neither prices nor unprices a cell.
    assert costs.deviation_classes(CONTRACT_PARAMS_BY_ID[param_id], {name}, {name: off}, {name: req},
                                   frozenset({name})) == []


def _ct12(quote, page=1):
    values = {f.name: parse_field(f.kind, quote, f.name) for f in CONTRACT_PARAMS_BY_ID["CT-12"].fields}
    return values, Evidence("CT-12", Citation("bid.pdf", page, (0.0, 0.0, 1.0, 1.0), quote, "commercial"), values)


def test_a_citation_only_field_beside_a_passing_field_asks_for_inspection():
    required, _ = _ct12("Delivery DAP Rotterdam, Incoterms 2020.")
    _, evidence = _ct12("Delivery DDP Rotterdam, Incoterms 2020.")
    resolution = resolve(CONTRACT_PARAMS_BY_ID["CT-12"], required, [evidence], "V1")
    by_field = {c.field: c for c in resolution.checks}
    assert by_field["incoterm"].passed and by_field["incoterm"].comparable
    assert resolution.status == ComplianceStatus.MANUAL_INSPECTION_REQUIRED


def test_a_genuine_failure_beside_a_citation_only_field_stays_a_deviation():
    required, _ = _ct12("Delivery DAP Rotterdam, Incoterms 2020.")
    offered, evidence = _ct12("Delivery EXW Seller's works, Incoterms 2020.")
    param = CONTRACT_PARAMS_BY_ID["CT-12"]
    resolution = resolve(param, required, [evidence], "V1")
    by_field = {c.field: c for c in resolution.checks}
    assert not by_field["incoterm"].passed and by_field["incoterm"].comparable
    assert not by_field["named_place"].comparable
    assert resolution.status == ComplianceStatus.DEVIATION_DOWNGRADE
    # Priced by the Incoterm alone; the quoted place adds no class.
    failing = {c.field for c in resolution.checks if not c.passed}
    incomparable = frozenset(c.field for c in resolution.checks if not c.comparable)
    assert costs.deviation_classes(param, failing, offered, required, incomparable) == ["CT-12.incoterm_exw_to_dap"]


def test_the_named_place_display_does_not_repeat_the_incoterm():
    param = CONTRACT_PARAMS_BY_ID["CT-12"]
    values, _ = _ct12("Delivery DAP Rotterdam, Incoterms 2020.")
    assert display_text(param, values) == "Delivery DAP Rotterdam, Incoterms 2020."
    # An Incoterm the clause does not state stays visible ahead of it.
    other = {"incoterm": parse_field("incoterm", "FCA", "incoterm"), "named_place": values["named_place"]}
    assert display_text(param, other) == "FCA Delivery DAP Rotterdam, Incoterms 2020."
    assert display_text(param, {"incoterm": values["incoterm"]}) == "DAP —"
    assert display_text(param, {"named_place": values["named_place"]}) == "— Delivery DAP Rotterdam, Incoterms 2020."


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
