"""Content of the synthetic TENDER-2026-418 pack, written as data.

The extended-class sample: the TENDER-2026-402 scenario with PARAM-09..15 added (delivery lead
time, delay liquidated damages, liability cap, commissioning spares, site supervision, rated
altitude). It is evaluated under equipment class mv_gis_switchgear_ext.

What the three bids do on the new parameters:

- vendor-a meets all six.
- vendor-b is late (44 weeks against 40), excludes liquidated damages, caps liability at 20%, quotes
  spares separately, leaves site supervision to others and rates the equipment for 800 m.
- vendor-c caps liquidated damages at 5% against 10% and says nothing about altitude, which must
  show as needing inspection.

The answer key is written by hand from the evidence sentences. It is never derived from the
pipeline's output. All names are fictional.
"""

from __future__ import annotations

import copy

from samples import scenario
from samples.scenario import (
    BID_SECTIONS, FILLER, FOOTER,  # noqa: F401  (FOOTER is part of the pack interface)
    MR_PRECEDENCE, MR_SECTIONS, VENDORS, DocumentSpec, Ev, _build, _compliance_rows,
)

TENDER_ID = "TENDER-2026-418"
TITLE = "220 MWp Solar Substation — 33kV GIS Switchgear Package, Delivery and Contract Risk Audit"
CLIENT = "Brandt Solar Netz GmbH (fictional)"
MR_FILE = scenario.MR_FILE
EQUIPMENT_CLASS = "mv_gis_switchgear_ext"
RATE_TABLE = "mv_gis_switchgear_ext_v1"
BAY_COUNT = scenario.BAY_COUNT
ENGINEERING_ESTIMATES = dict(scenario.ENGINEERING_ESTIMATES)

# The first scenario's filler, with the two sentences that would now read as evidence replaced: the
# site-services line (it says supervision is offered separately) and the spares line (it says a list is
# "included"). Filler must state nothing a parser can read as a value.
FILLER_418 = copy.deepcopy(FILLER)
FILLER_418["site"] = [
    "Site erection and commissioning shall follow the agreed construction programme.",
    "The supplier's site staff shall attend the purchaser's safety induction.",
    "Commissioning shall follow a procedure agreed with the purchaser.",
    "Site acceptance tests shall be recorded on agreed test sheets.",
]
FILLER_418["spares"] = [
    "A list of recommended spare parts shall accompany the offer.",
    "Spare parts shall be interchangeable with the corresponding original parts.",
    "Special tools required for maintenance shall be supplied with the equipment.",
    "Spare part prices shall remain valid as stated in the commercial terms.",
]

# --- Evidence -----------------------------------------------------------------

MR_EVIDENCE = copy.deepcopy(scenario.MR_EVIDENCE) + [
    Ev("mr.p15", 5, "The switchgear shall be designed and type-tested to IEC 62271-200:2021 and IEC 62271-1:2017.", "PARAM-15"),
    Ev("mr.p14", 7, "Equipment shall be rated for installation at an altitude of up to 1000 m above sea level.", "PARAM-14"),
    Ev("mr.p13", 12, "Supervision of erection and commissioning at site shall be included in the contract price.", "PARAM-13"),
    Ev("mr.p09", 14, "Delivery shall be completed within 40 weeks from the purchase order.", "PARAM-09"),
    Ev("mr.p10", 14, "Delay liquidated damages shall be 0.5% per week of the contract price, capped at 10% of the contract price.", "PARAM-10"),
    Ev("mr.p11", 14, "The supplier's total liability shall be limited to 100% of the contract price.", "PARAM-11"),
    Ev("mr.p12", 15, "Two years of commissioning spares shall be included in the contract price.", "PARAM-12"),
]

_NEW_VENDOR_EVIDENCE = {
    "vendor-a": [
        Ev("a.p15", 6, "Designed and type-tested to IEC 62271-200:2021 and IEC 62271-1:2017.", "PARAM-15"),
        Ev("a.p14", 7, "Rated for installation at altitude up to 1000 m above sea level.", "PARAM-14"),
        Ev("a.p09", 21, "Delivery 36 weeks from purchase order.", "PARAM-09"),
        Ev("a.p10", 21, "Delay liquidated damages are accepted at 0.5% per week of the contract price, capped at 10% of the contract price.", "PARAM-10"),
        Ev("a.p11", 21, "Total liability is limited to 100% of the contract price.", "PARAM-11"),
        Ev("a.p13", 21, "Supervision of erection and commissioning is included in the price.", "PARAM-13"),
        Ev("a.p12", 26, "Two years of commissioning spares are included in the price.", "PARAM-12"),
    ],
    "vendor-b": [
        Ev("b.p15", 6, "Designed and type-tested to IEC 62271-200:2011 and IEC 62271-1:2017.", "PARAM-15"),
        Ev("b.p14", 7, "Rated for installation at altitude up to 800 m above sea level.", "PARAM-14"),
        Ev("b.p09", 21, "Delivery 44 weeks from purchase order.", "PARAM-09"),
        Ev("b.p10", 21, "Liquidated damages for delay are excluded.", "PARAM-10"),
        Ev("b.p11", 21, "Total liability is limited to 20% of the contract price.", "PARAM-11"),
        Ev("b.p13", 21, "Supervision of erection and commissioning is by others.", "PARAM-13"),
        Ev("b.p12", 26, "Commissioning spares are quoted separately.", "PARAM-12"),
    ],
    "vendor-c": [
        Ev("c.p15", 6, "Designed and type-tested to IEC 62271-200:2021 and IEC 62271-1.", "PARAM-15"),
        Ev("c.p09", 21, "Delivery 38 weeks from purchase order.", "PARAM-09"),
        Ev("c.p10", 21, "Delay liquidated damages of 0.5% per week, capped at 5% of the contract price.", "PARAM-10"),
        Ev("c.p11", 21, "Total liability is limited to 100% of the contract price.", "PARAM-11"),
        Ev("c.p13", 21, "Supervision of erection and commissioning is included in the price.", "PARAM-13"),
        Ev("c.p12", 26, "Commissioning spares for two years are included in the price.", "PARAM-12"),
    ],
}
VENDOR_EVIDENCE = {
    vendor_id: copy.deepcopy(scenario.VENDOR_EVIDENCE[vendor_id]) + _NEW_VENDOR_EVIDENCE[vendor_id]
    for vendor_id in scenario.VENDOR_EVIDENCE
}

_EV_BY_KEY = {e.key: e for e in MR_EVIDENCE}
for _evs in VENDOR_EVIDENCE.values():
    _EV_BY_KEY.update({e.key: e for e in _evs})

# --- Answer key (literal values) ----------------------------------------------

REQUIREMENTS = {
    **scenario.REQUIREMENTS,
    "PARAM-09": (["mr.p09"], {"lead_time": "40 weeks"}),
    "PARAM-10": (["mr.p10"], {"ld_rate": "0.5 % per week", "ld_cap": "10 %"}),
    "PARAM-11": (["mr.p11"], {"liability_cap": "100 %"}),
    "PARAM-12": (["mr.p12"], {"spares_included": "included"}),
    "PARAM-13": (["mr.p13"], {"supervision_included": "included"}),
    "PARAM-14": (["mr.p14"], {"site_altitude": "1000 m"}),
    "PARAM-15": (["mr.p15"], {"standards": "IEC 62271-1:2017, IEC 62271-200:2021"}),
}

_MANUAL = ("MANUAL_INSPECTION_REQUIRED", None, {}, None, "0.00")
CELLS = copy.deepcopy(scenario.CELLS)
CELLS["vendor-a"].update({
    "PARAM-09": ("COMPLIANT", "a.p09", {"lead_time": "36 weeks"}, None, "0.00"),
    "PARAM-10": ("COMPLIANT", "a.p10", {"ld_rate": "0.5 % per week", "ld_cap": "10 %"}, None, "0.00"),
    "PARAM-11": ("COMPLIANT", "a.p11", {"liability_cap": "100 %"}, None, "0.00"),
    "PARAM-12": ("COMPLIANT", "a.p12", {"spares_included": "included"}, None, "0.00"),
    "PARAM-13": ("COMPLIANT", "a.p13", {"supervision_included": "included"}, None, "0.00"),
    "PARAM-14": ("COMPLIANT", "a.p14", {"site_altitude": "1000 m"}, None, "0.00"),
    "PARAM-15": ("COMPLIANT", "a.p15", {"standards": "IEC 62271-1:2017, IEC 62271-200:2021"}, None, "0.00"),
})
CELLS["vendor-b"].update({
    "PARAM-09": ("DEVIATION_DOWNGRADE", "b.p09", {"lead_time": "44 weeks"}, None, "0.00"),
    "PARAM-10": ("DEVIATION_DOWNGRADE", "b.p10", {"ld_rate": "0 %", "ld_cap": "0 %"}, None, "0.00"),
    "PARAM-11": ("DEVIATION_DOWNGRADE", "b.p11", {"liability_cap": "20 %"}, None, "0.00"),
    "PARAM-12": ("OMITTED", "b.p12", {"spares_included": "not included"}, None, "14000.00"),
    "PARAM-13": ("OMITTED", "b.p13", {"supervision_included": "not included"}, None, "9600.00"),
    "PARAM-14": ("DEVIATION_DOWNGRADE", "b.p14", {"site_altitude": "800 m"}, None, "0.00"),
    # An older edition of IEC 62271-200 than the MR names.
    "PARAM-15": ("DEVIATION_DOWNGRADE", "b.p15", {"standards": "IEC 62271-1:2017, IEC 62271-200:2011"}, None, "0.00"),
})
CELLS["vendor-c"].update({
    "PARAM-09": ("COMPLIANT", "c.p09", {"lead_time": "38 weeks"}, None, "0.00"),
    "PARAM-10": ("DEVIATION_DOWNGRADE", "c.p10", {"ld_rate": "0.5 % per week", "ld_cap": "5 %"}, None, "0.00"),
    "PARAM-11": ("COMPLIANT", "c.p11", {"liability_cap": "100 %"}, None, "0.00"),
    "PARAM-12": ("COMPLIANT", "c.p12", {"spares_included": "included"}, None, "0.00"),
    "PARAM-13": ("COMPLIANT", "c.p13", {"supervision_included": "included"}, None, "0.00"),
    "PARAM-14": _MANUAL,
    # IEC 62271-1 with no edition: compliance with the 2017 edition cannot be shown.
    "PARAM-15": ("DEVIATION_DOWNGRADE", "c.p15", {"standards": "IEC 62271-1, IEC 62271-200:2021"}, None, "0.00"),
})

# Table ext_v1 prices a bid that excludes commissioning spares (14,000.00, one lot) or site supervision
# (120 h x 60.00 + 2,400.00 = 9,600.00). Delivery, liquidated damages, liability and altitude are knowingly
# unpriced: the first three are exposures (exposure.py), not adders.
VENDOR_TOTALS = {**scenario.VENDOR_TOTALS, "vendor-b": ("85250.00", "503250.00")}
NEGOTIATING_VENDOR = scenario.NEGOTIATING_VENDOR
CLAWBACK = dict(scenario.CLAWBACK)
CELL_EXTRAS = copy.deepcopy(scenario.CELL_EXTRAS)
for _vendor, _params in (("vendor-b", ("PARAM-09", "PARAM-10", "PARAM-11", "PARAM-14", "PARAM-15")),
                         ("vendor-c", ("PARAM-10", "PARAM-15"))):
    for _param in _params:
        CELL_EXTRAS[(_vendor, _param)] = {"pricing_status": "UNPRICED"}
VENDOR_PRICE_STATUS = {"vendor-b": "UNPRICED_ITEMS", "vendor-c": "UNPRICED_ITEMS"}


def documents() -> list[DocumentSpec]:
    mr_title = [
        ("title", "Brandt Solar Netz GmbH"),
        ("p", "Material Requisition MR-33kV-GIS-Rev4"),
        ("p", TITLE),
        ("p", f"Tender reference {TENDER_ID}"),
    ]
    mr_pages = _build(26, MR_SECTIONS, list(MR_EVIDENCE), mr_title, filler=FILLER_418)
    mr_pages[1].blocks.insert(1, ("p", MR_PRECEDENCE))
    docs = [DocumentSpec(MR_FILE, "Material Requisition MR-33kV-GIS-Rev4", mr_pages)]
    for vendor in VENDORS:
        title = [
            ("title", vendor.name),
            ("p", "Technical and Commercial Offer"),
            ("p", f"Product: {vendor.product}"),
            ("p", "Submitted in response to Material Requisition MR-33kV-GIS-Rev4"),
        ]
        pages = _build(
            30, BID_SECTIONS, VENDOR_EVIDENCE[vendor.id], title,
            compliance_heading=vendor.compliance_heading,
            compliance_rows=_compliance_rows(vendor),
            drawing_code=f"{vendor.product.split()[0]}-SLD",
            filler=FILLER_418,
        )
        pages[1].blocks.append(("p", vendor.name))
        docs.append(DocumentSpec(vendor.file, f"Offer — {vendor.name}", pages))
    return docs


def manifest() -> dict:
    return {
        "tender_id": TENDER_ID,
        "title": TITLE,
        "client": CLIENT,
        "equipment_class": EQUIPMENT_CLASS,
        "rate_table": RATE_TABLE,
        "documents": [{"file": MR_FILE, "role": "mr_spec"}] + [
            {"file": v.file, "role": "vendor_bid", "vendor_id": v.id,
             "vendor_name": v.name, "country": v.country}
            for v in VENDORS
        ],
        "engineering_estimates": dict(ENGINEERING_ESTIMATES),
        "synthetic": True,
    }


def _candidates(evidence: list[Ev]) -> list[dict]:
    return [{"param_id": e.param_id, "page": e.page, "quote": e.text} for e in evidence if e.param_id]


def _quote_ref(key: str) -> dict:
    e = _EV_BY_KEY[key]
    return {"page": e.page, "quote": e.text}


def expected() -> dict:
    requirements = {}
    for param_id, (keys, values) in REQUIREMENTS.items():
        evs = [_EV_BY_KEY[k] for k in keys]
        requirements[param_id] = {"page": evs[0].page, "quotes": [e.text for e in evs], "values": values}
    cells = {}
    for vendor_id, by_param in CELLS.items():
        cells[vendor_id] = {}
        for param_id, (status, key, values, secondary, adder) in by_param.items():
            e = _EV_BY_KEY[key] if key else None
            cells[vendor_id][param_id] = {
                "status": status, "page": e.page if e else None, "quote": e.text if e else None,
                "values": values, "secondary_page": secondary, "adder": adder,
            }
            cells[vendor_id][param_id].update(copy.deepcopy(CELL_EXTRAS.get((vendor_id, param_id), {})))
    vendors = {
        v.id: {"name": v.name, "headline_price": v.headline,
               "total_adders": VENDOR_TOTALS[v.id][0], "true_evaluated_price": VENDOR_TOTALS[v.id][1]}
        for v in VENDORS
    }
    for vendor_id, status in VENDOR_PRICE_STATUS.items():
        vendors[vendor_id]["price_status"] = status
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
        prefix = v.id[-1]
        ideal[v.file] = {
            "page_sections": [{"page": 1, "section": "general"}],
            "candidates": _candidates(VENDOR_EVIDENCE[v.id]),
            "tender_facts": [],
            "headline_price": _quote_ref(f"{prefix}.price"),
            "cover_claim": _quote_ref(f"{prefix}.cover"),
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
    }
