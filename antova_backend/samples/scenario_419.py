"""Content of the synthetic TENDER-2026-419 pack, written as data: a call-off under framework agreements.

The contract sample. The EPC, Brightmere Renewables EPC Ltd, calls off 33kV GIS switchgear from two vendors
it holds framework agreements with, against the TENDER-2026-402 Material Requisition (same MR text):

- Harlow Energy Systems Ltd (counterparty cp-harlow): an offer that conforms to its framework agreement.
- Kestrel Power Conversion UK Ltd (counterparty cp-kestrel): its framework agreement is with Kestrel Power
  Conversion AG (the counterparty links them, never the name), amended later (warranty 60 -> 66 months).
  The offer deliberately differs: an LD cap of 5% against the agreement's 10%; a warranty from delivery
  where the owner contract runs it from PAC; silent on title, which it inherits through its incorporation
  clause (CT-16); and a liability cap in GBP where the agreement states EUR.

On the equipment side both bids are the 402 scenario's fully compliant bid, so the TBE has no deviation
and every contract difference comes from the contract terms alone.

The answer key is written by hand from the evidence sentences. It is never derived from the pipeline's
output. All names are invented.
"""

from __future__ import annotations

import copy

from samples import scenario
from samples.scenario import (
    BID_SECTIONS, COMPLIANCE_TOPICS, FOOTER,  # noqa: F401  (FOOTER is part of the pack interface)
    MR_EVIDENCE, MR_PRECEDENCE, MR_SECTIONS, DocumentSpec, Ev, Page, Vendor, _build,
)

TENDER_ID = "TENDER-2026-419"
TITLE = "Call-off under framework agreements — 33kV GIS Switchgear Package, Contract Approval"
CLIENT = "Brightmere Renewables EPC Ltd (fictional)"
EPC = "Brightmere Renewables EPC Ltd"
OWNER = "Ardley Fen Solar Ltd"
MR_FILE = scenario.MR_FILE
EQUIPMENT_CLASS = "mv_gis_switchgear"
RATE_TABLE = "mv_gis_switchgear_v2"
BAY_COUNT = scenario.BAY_COUNT
ENGINEERING_ESTIMATES = dict(scenario.ENGINEERING_ESTIMATES)

HARLOW = Vendor("vendor-a", "Harlow Energy Systems Ltd", "United Kingdom", "HES-36 GIS",
                "Offer_cp-harlow_Harlow.pdf", "452000.00", "2. COMPLIANCE STATEMENT")
KESTREL = Vendor("vendor-b", "Kestrel Power Conversion UK Ltd", "United Kingdom", "KPC-36 GIS",
                 "Offer_cp-kestrel_Kestrel.pdf", "441000.00", "2. COMPLIANCE STATEMENT")
VENDORS = [HARLOW, KESTREL]
COUNTERPARTY = {"vendor-a": "cp-harlow", "vendor-b": "cp-kestrel"}

FA_HARLOW = "FA_cp-harlow_FA-HES-2025-03.pdf"
FA_KESTREL = "FA_cp-kestrel_FA-KPC-2025-07.pdf"
FA_KESTREL_AMENDMENT = "FA_cp-kestrel_FA-KPC-2025-07_Amendment-1.pdf"
OWNER_FILE = "Owner-Contract-Excerpt.pdf"

# Contract documents' filler: plain obligations a contract reader can read no term from.
CONTRACT_FILLER = [
    "The parties shall act in good faith in the performance of this document.",
    "Notices shall be given in writing to the addresses stated on the first page.",
    "Headings are for convenience only and do not affect the interpretation of the clauses.",
    "Capitalised words have the meaning given to them in the definitions schedule.",
]

# --- Evidence: equipment ------------------------------------------------------------------------

def _equipment(prefix: str, price: str) -> list[Ev]:
    """The 402 scenario's fully compliant bid, under this vendor's key prefix and price."""
    evidence = []
    for e in scenario.VENDOR_EVIDENCE["vendor-a"]:
        text = f"Total lump-sum price for the complete scope: {price}" if e.key == "a.price" else e.text
        evidence.append(Ev(prefix + e.key[1:], e.page, text, e.param_id))
    return evidence


EQUIPMENT_EVIDENCE = {"vendor-a": _equipment("h", "€452,000"), "vendor-b": _equipment("k", "€441,000")}
_DELIVERY = next(e.text for e in scenario.VENDOR_EVIDENCE["vendor-a"] if e.key == "a.p06")

# --- Evidence: contract terms -----------------------------------------------------------------------
# One Ev per sentence; a term's sentences share a page. Ev.key is "<doc>.<term>.<n>".

_LD_RATE = "Liquidated damages of 0.5% of the Contract Price per week of delay."
_LD_CAP_10 = "Liquidated damages are capped at 10 % of the Contract Price."
_LD_TRIGGERS = "LDs apply to late delivery and/or any late Cold Commissioning and/or Hot Commissioning."
_ORDER = ("In case of conflict, the documents prevail in the following order: (1) the Purchase Order; "
          "(2) the Framework Agreement; (3) the Technical Specification; (4) the Offer.")


def _fa(key: str, *, liability: str, warranty_months: int, insurer: str) -> list[Ev]:
    """A framework agreement's clauses: every term but CT-16 (it is the agreement incorporated)."""
    clauses = [
        (2, "CT-17", _ORDER),
        (3, "CT-12", "Delivery DDP project site Brandenburg (Incoterms 2020)."),
        (3, "CT-10", "The Buyer may issue the NTP within 8 months from the Project Purchase Order."),
        (3, "CT-01", _LD_RATE), (3, "CT-01", _LD_CAP_10), (3, "CT-01", _LD_TRIGGERS),
        (4, "CT-13", "The Contract Price is in EUR."),
        (4, "CT-13", "The price is fixed and firm from the issue of the Notice to Proceed."),
        (4, "CT-13", "The Contract Price is a fixed lump sum."),
        (4, "CT-07", "Payment terms: 90% within thirty (30) days after delivery; 10% at PAC against a warranty bond."),
        (4, "CT-06", "The advance payment is made against an advance payment bond of 10%."),
        (5, "CT-04", "The Supplier provides a warranty bond of 5% of the Contract Price."),
        (5, "CT-04", "The warranty bond is valid for 24 months."),
        (5, "CT-05", "A performance bond equal to ten percent (10%) of the Price."),
        (5, "CT-05", "The performance bond is released at Provisional Acceptance."),
        (5, "CT-15", f"The Supplier holds credit insurance with {insurer}."),
        (5, "CT-15", "The secured value under the credit insurance is EUR 2,000,000."),
        (6, "CT-03", f"The warranty period is {warranty_months} months from PAC."),
        (6, "CT-03", "Defective parts shall be repaired or replaced on site at the Seller's cost."),
        (6, "CT-03", "The warranty covers all components of the supply."),
        (6, "CT-09", "Title to the Goods passes to the Buyer on delivery."),
        (6, "CT-02", liability),
        (6, "CT-11", "The equipment has a minimum operating period of 35 years."),
        (7, "CT-08", "The Buyer pays a termination fee of 5% of the price of the affected Goods."),
        (7, "CT-14", "This contract is governed by the laws of England and Wales."),
        (7, "CT-14", "Disputes shall be finally settled by ICC arbitration in London."),
    ]
    return _keyed(key, clauses)


def _keyed(key: str, clauses) -> list[Ev]:
    counts: dict[str, int] = {}
    evidence = []
    for page, param_id, text in clauses:
        counts[param_id] = counts.get(param_id, 0) + 1
        evidence.append(Ev(f"{key}.{param_id}.{counts[param_id]}", page, text, param_id))
    return evidence


CONTRACT_EVIDENCE: dict[str, list[Ev]] = {
    FA_HARLOW: _fa("fah", liability="The Supplier's total liability is limited to 100% of the Contract Price.",
                   warranty_months=36, insurer="Calder Mutual Credit Ltd"),
    FA_KESTREL: _fa("fak", liability="The Supplier's total liability is limited to EUR 450,000.",
                    warranty_months=60, insurer="Fenwold Credit Assurance plc"),
    FA_KESTREL_AMENDMENT: _keyed("amd", [(2, "CT-03", "The warranty period is 66 months from PAC.")]),
    OWNER_FILE: _keyed("own", [
        (2, "CT-01", _LD_RATE), (2, "CT-01", _LD_CAP_10),
        (3, "CT-02", "The Contractor's total liability is limited to 100% of the Contract Price."),
        (3, "CT-03", "The warranty period is 24 months from PAC."),
        (3, "CT-09", "Title to the Goods passes to the Buyer on delivery."),
        (4, "CT-14", "This contract is governed by the laws of England and Wales."),
        (4, "CT-14", "Disputes shall be finally settled by ICC arbitration in London."),
    ]),
    # The offers' commercial terms (bid pages 20 to 22). CT-12 cites the delivery sentence the equipment
    # pass cites for PARAM-06: one sentence, two readings.
    HARLOW.file: _keyed("h", [
        (20, "CT-16", "This offer is subject to the terms of the Framework Agreement dated 1 April 2025."),
        (20, "CT-13", "The Contract Price is in EUR."),
        (21, "CT-12", _DELIVERY),
        (21, "CT-01", "Liquidated damages of 0.5% of the Contract Price per week of delay, capped at 10% of "
                      "the Contract Price."),
        (21, "CT-02", "The Supplier's total liability is limited to 100% of the Contract Price."),
        (21, "CT-03", "The warranty period is 36 months from PAC."),
        (21, "CT-09", "Title to the Goods passes to the Buyer on delivery."),
    ]),
    # Kestrel: LD cap 5%, warranty from delivery, liability in GBP, and nothing on title.
    KESTREL.file: _keyed("k", [
        (20, "CT-16", "This offer is subject to the terms of the Framework Agreement dated 12 May 2025."),
        (20, "CT-13", "The Contract Price is in EUR."),
        (21, "CT-12", _DELIVERY),
        (21, "CT-01", "Liquidated damages of 0.5% of the Contract Price per week of delay, capped at 5% of "
                      "the Contract Price."),
        (21, "CT-02", "The Supplier's total liability is limited to GBP 400,000."),
        (21, "CT-03", "The warranty period is 36 months from delivery."),
    ]),
}

# --- Answer key: contract values, per document and term (literal, by hand) -------------------------

_FA_COMMON = {
    "CT-01": {"ld_rate": "0.5% per week", "ld_cap": "10 %",
              "ld_triggers": "delivery, cold commissioning, hot commissioning"},
    "CT-04": {"warranty_bond_pct": "5 %", "warranty_bond_months": "24"},
    "CT-05": {"performance_bond_pct": "10 %", "performance_bond_release": "PAC"},
    "CT-06": {"advance_bond_pct": "10 %"},
    "CT-07": {"milestones": "90% 30 days after delivery; 10% at PAC against warranty bond"},
    "CT-08": {"termination_fee_pct": "5 %"},
    "CT-09": {"title_event": "delivery"},
    "CT-10": {"ntp_deadline_months": "8"},
    "CT-11": {"design_life_months": "35 years (420 months)"},
    "CT-12": {"incoterm": "DDP", "named_place": "Delivery DDP project site Brandenburg (Incoterms 2020)."},
    "CT-13": {"price_currency": "EUR", "price_fixed_after_ntp": "fixed after NTP", "price_type": "fixed lump sum"},
    "CT-14": {"law": "English law", "forum": "ICC arbitration"},
    "CT-17": {"order": "Purchase Order > Framework Agreement > Technical Specification > Offer"},
}
_WARRANTY_TERMS = {"warranty_on_site": "on site", "warranty_coverage": "all components"}
_DELIVERY_PLACE = {"incoterm": "DDP", "named_place": _DELIVERY}

CONTRACT_VALUES: dict[str, dict[str, dict[str, str]]] = {
    FA_HARLOW: {**_FA_COMMON,
                "CT-02": {"liability_cap": "100 %"},
                "CT-03": {"warranty_months": "36", "warranty_start": "PAC", **_WARRANTY_TERMS},
                "CT-15": {"credit_insurer": "Calder Mutual Credit Ltd", "credit_secured_value": "EUR 2,000,000"}},
    FA_KESTREL: {**_FA_COMMON,
                 "CT-02": {"liability_cap_money": "EUR 450,000"},
                 "CT-03": {"warranty_months": "60", "warranty_start": "PAC", **_WARRANTY_TERMS},
                 "CT-15": {"credit_insurer": "Fenwold Credit Assurance plc", "credit_secured_value": "EUR 2,000,000"}},
    FA_KESTREL_AMENDMENT: {"CT-03": {"warranty_months": "66", "warranty_start": "PAC"}},
    OWNER_FILE: {
        "CT-01": {"ld_rate": "0.5% per week", "ld_cap": "10 %"},
        "CT-02": {"liability_cap": "100 %"},
        "CT-03": {"warranty_months": "24", "warranty_start": "PAC"},
        "CT-09": {"title_event": "delivery"},
        "CT-14": {"law": "English law", "forum": "ICC arbitration"},
    },
    HARLOW.file: {
        "CT-16": {"fa_reference": "Framework Agreement dated 1 April 2025"},
        "CT-13": {"price_currency": "EUR"},
        "CT-12": _DELIVERY_PLACE,
        "CT-01": {"ld_rate": "0.5% per week", "ld_cap": "10 %"},
        "CT-02": {"liability_cap": "100 %"},
        "CT-03": {"warranty_months": "36", "warranty_start": "PAC"},
        "CT-09": {"title_event": "delivery"},
    },
    KESTREL.file: {
        "CT-16": {"fa_reference": "Framework Agreement dated 12 May 2025"},
        "CT-13": {"price_currency": "EUR"},
        "CT-12": _DELIVERY_PLACE,
        "CT-01": {"ld_rate": "0.5% per week", "ld_cap": "5 %"},
        "CT-02": {"liability_cap_money": "GBP 400,000"},
        "CT-03": {"warranty_months": "36", "warranty_start": "delivery"},
    },
}

# --- Answer key: the TBE (both bids are 402's compliant bid) ---------------------------------------

REQUIREMENTS = copy.deepcopy(scenario.REQUIREMENTS)
CELLS = {
    vendor_id: {param_id: (status, prefix + key[1:], values, secondary, adder)
                for param_id, (status, key, values, secondary, adder) in scenario.CELLS["vendor-a"].items()}
    for vendor_id, prefix in (("vendor-a", "h"), ("vendor-b", "k"))
}
VENDOR_TOTALS = {"vendor-a": ("0.00", "452000.00"), "vendor-b": ("0.00", "441000.00")}
# The lowest true evaluated price; neither bid has a deviation, so nothing is clawed back.
NEGOTIATING_VENDOR = "vendor-b"
CLAWBACK = {"round_2": "0.00", "round_3": "0.00"}

# The project baseline the buyer enters: the owner contract value and this package's share of its LDs (the
# owner contract's LD percentages are read from its excerpt), the GBP rate for Kestrel's liability cap, and
# the delay scenario.
PROJECT = {
    "po_award_date": {"value": "2027-01-15"},
    "frozen_budget": {"amount": "480000.00", "currency": "EUR"},
    "fx": [{"from_currency": "GBP", "to_currency": "EUR", "rate": "1.17", "dated": "2026-11-01"}],
    "delay_scenario_weeks": {"value": "8"},
    "owner_terms": {"contract_value": {"amount": "48000000.00", "currency": "EUR"}, "package_ld_share_pct": "2.5"},
}

_EV_BY_KEY = {e.key: e for e in MR_EVIDENCE}
for _evs in [*EQUIPMENT_EVIDENCE.values(), *CONTRACT_EVIDENCE.values()]:
    _EV_BY_KEY.update({e.key: e for e in _evs})


# --- Documents ---------------------------------------------------------------------------------------

def _contract_document(filename: str, title: str, heading_lines: list[str], sections: dict[int, str],
                       page_count: int) -> DocumentSpec:
    evidence = CONTRACT_EVIDENCE[filename]
    pages = [Page([("title", title), *(("p", line) for line in heading_lines)])]
    for number in range(2, page_count + 1):
        here = [e for e in evidence if e.page == number]
        filler = [("p", CONTRACT_FILLER[(number + i) % len(CONTRACT_FILLER)]) for i in range(2)]
        blocks = [("h", sections[number])] if number in sections else []
        pages.append(Page(blocks + filler[:1] + [("p", e.text) for e in here] + filler[1:]))
    return DocumentSpec(filename, title, pages)


_FA_SECTIONS = {2: "1. CONTRACT STRUCTURE", 3: "2. DELIVERY AND SCHEDULE", 4: "3. PRICE AND PAYMENT",
                5: "4. SECURITIES AND INSURANCE", 6: "5. WARRANTY, TITLE AND LIABILITY",
                7: "6. TERMINATION, LAW AND DISPUTES"}


def documents() -> list[DocumentSpec]:
    mr_title = [
        ("title", EPC),
        ("p", "Material Requisition MR-33kV-GIS-Rev4"),
        ("p", TITLE),
        ("p", f"Tender reference {TENDER_ID}"),
    ]
    mr_pages = _build(26, MR_SECTIONS, list(MR_EVIDENCE), mr_title)
    mr_pages[1].blocks.insert(1, ("p", MR_PRECEDENCE))
    docs = [DocumentSpec(MR_FILE, "Material Requisition MR-33kV-GIS-Rev4", mr_pages)]
    for vendor in VENDORS:
        title = [
            ("title", vendor.name),
            ("p", "Technical and Commercial Offer"),
            ("p", f"Product: {vendor.product}"),
            ("p", "Submitted in response to Material Requisition MR-33kV-GIS-Rev4"),
        ]
        equipment = EQUIPMENT_EVIDENCE[vendor.id]
        shown = {e.text for e in equipment}
        placed = equipment + [e for e in CONTRACT_EVIDENCE[vendor.file] if e.text not in shown]
        pages = _build(
            30, BID_SECTIONS, placed, title,
            compliance_heading=vendor.compliance_heading,
            compliance_rows=[("row", f"{topic} - Complied, see datasheet") for topic in COMPLIANCE_TOPICS],
            drawing_code=f"{vendor.product.split()[0]}-SLD",
        )
        pages[1].blocks.append(("p", vendor.name))
        docs.append(DocumentSpec(vendor.file, f"Offer — {vendor.name}", pages))
    docs.append(_contract_document(
        FA_HARLOW, "Framework Agreement FA-HES-2025-03",
        [f"between {EPC} (the Buyer)", "and Harlow Energy Systems Ltd (the Supplier)", "Dated 1 April 2025"],
        _FA_SECTIONS, 7))
    docs.append(_contract_document(
        FA_KESTREL, "Framework Agreement FA-KPC-2025-07",
        [f"between {EPC} (the Buyer)", "and Kestrel Power Conversion AG (the Supplier)", "Dated 12 May 2025"],
        _FA_SECTIONS, 7))
    docs.append(_contract_document(
        FA_KESTREL_AMENDMENT, "Amendment No. 1 to Framework Agreement FA-KPC-2025-07",
        [f"between {EPC} (the Buyer)", "and Kestrel Power Conversion AG (the Supplier)", "Dated 2 March 2026"],
        {2: "1. AMENDED CLAUSE"}, 2))
    docs.append(_contract_document(
        OWNER_FILE, "EPC Contract — Excerpt of the Pass-Down Terms",
        [f"between {OWNER} (the Employer)", f"and {EPC} (the Contractor)", "Dated 30 June 2026"],
        {2: "1. DELAY DAMAGES", 3: "2. LIABILITY, WARRANTY AND TITLE", 4: "3. LAW AND DISPUTES"}, 4))
    return docs


def manifest() -> dict:
    bids = [{"file": v.file, "role": "vendor_bid", "vendor_id": v.id, "vendor_name": v.name,
             "country": v.country, "counterparty_id": COUNTERPARTY[v.id]} for v in VENDORS]
    contracts = [
        {"file": FA_HARLOW, "role": "framework_agreement", "counterparty_id": "cp-harlow", "dated": "2025-04-01"},
        {"file": FA_KESTREL, "role": "framework_agreement", "counterparty_id": "cp-kestrel", "dated": "2025-05-12"},
        {"file": FA_KESTREL_AMENDMENT, "role": "framework_agreement", "counterparty_id": "cp-kestrel",
         "amends": FA_KESTREL, "dated": "2026-03-02"},
        {"file": OWNER_FILE, "role": "owner_contract", "dated": "2026-06-30"},
    ]
    return {
        "tender_id": TENDER_ID,
        "title": TITLE,
        "client": CLIENT,
        "equipment_class": EQUIPMENT_CLASS,
        "rate_table": RATE_TABLE,
        "documents": [{"file": MR_FILE, "role": "mr_spec"}, *bids, *contracts],
        "engineering_estimates": dict(ENGINEERING_ESTIMATES),
        "project": copy.deepcopy(PROJECT),
        "synthetic": True,
    }


def _candidates(evidence: list[Ev]) -> list[dict]:
    return [{"param_id": e.param_id, "page": e.page, "quote": e.text} for e in evidence if e.param_id]


def _quote_ref(key: str) -> dict:
    e = _EV_BY_KEY[key]
    return {"page": e.page, "quote": e.text}


def _contract_block() -> dict:
    block: dict = {}
    for filename, evidence in CONTRACT_EVIDENCE.items():
        terms: dict = {}
        for e in evidence:
            term = terms.setdefault(e.param_id, {"page": e.page, "quotes": [],
                                                 "values": CONTRACT_VALUES[filename][e.param_id]})
            assert term["page"] == e.page, (filename, e.param_id)  # a term's sentences share a page
            term["quotes"].append(e.text)
        assert set(terms) == set(CONTRACT_VALUES[filename]), filename
        block[filename] = terms
    return block


def expected() -> dict:
    requirements = {}
    for param_id, (keys, values) in REQUIREMENTS.items():
        evs = [_EV_BY_KEY[k] for k in keys]
        requirements[param_id] = {"page": evs[0].page, "quotes": [e.text for e in evs], "values": values}
    cells = {}
    for vendor_id, by_param in CELLS.items():
        cells[vendor_id] = {}
        for param_id, (status, key, values, secondary, adder) in by_param.items():
            e = _EV_BY_KEY[key]
            cells[vendor_id][param_id] = {
                "status": status, "page": e.page, "quote": e.text, "values": values,
                "secondary_page": secondary, "adder": adder,
            }
    vendors = {
        v.id: {"name": v.name, "headline_price": v.headline,
               "total_adders": VENDOR_TOTALS[v.id][0], "true_evaluated_price": VENDOR_TOTALS[v.id][1]}
        for v in VENDORS
    }
    ideal = {
        MR_FILE: {
            "page_sections": [],
            "candidates": _candidates(MR_EVIDENCE),
            "tender_facts": [{"fact": "bay_count", **_quote_ref("mr.bays")}],
            "headline_price": None,
            "cover_claim": None,
        }
    }
    for v in VENDORS:
        prefix = "h" if v is HARLOW else "k"
        ideal[v.file] = {
            "page_sections": [{"page": 1, "section": "general"}],
            "candidates": _candidates(EQUIPMENT_EVIDENCE[v.id]),
            "tender_facts": [],
            "headline_price": _quote_ref(f"{prefix}.price"),
            "cover_claim": _quote_ref(f"{prefix}.cover"),
        }
    ideal_contract = {
        filename: {"page_sections": [], "candidates": _candidates(evidence), "tender_facts": [],
                   "headline_price": None, "cover_claim": None}
        for filename, evidence in CONTRACT_EVIDENCE.items()
    }
    return {
        "tender_id": TENDER_ID,
        "equipment_class": EQUIPMENT_CLASS,
        "bay_count": BAY_COUNT,
        "requirements": requirements,
        "cells": cells,
        "vendors": vendors,
        "negotiating_vendor": NEGOTIATING_VENDOR,
        "clawback": dict(CLAWBACK),
        "ideal_locate": ideal,
        "ideal_contract_locate": ideal_contract,
        "contract": _contract_block(),
    }
