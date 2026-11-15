"""The evaluated parameters, evidence tiers and section headings.

Adding a parameter means adding a ParameterDef here (plus a parser in
values.py if it needs a new field kind).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from antova_engine import Criticality
from antova_ingest.types import FieldKind, Section


@dataclass(frozen=True)
class FieldDef:
    name: str
    kind: FieldKind
    # "ge": at least (quantities, IP codes). "eq": exactly. "ord": at least, in the declared
    # order of ORDINALS for this field's kind (a better Incoterm or arc class is compliant).
    # "le": at most (a lead time). "all": every required standard is offered, in the same or a newer edition.
    operator: Literal["ge", "le", "eq", "ord", "all"]
    label: str
    # (low, high), written like values in a bid. A parse outside it is not believed: the value
    # is dropped and the cell flagged. Ranges are for this equipment class (MV GIS switchgear).
    plausible: tuple[str, str] | None = None


@dataclass(frozen=True)
class ParameterDef:
    id: str
    name: str
    category: str
    criticality: Criticality
    fields: tuple[FieldDef, ...]
    display_template: str
    locate_hint: str
    scope_field: str | None = None
    # Regular expressions (case-insensitive) that name this parameter in running text. The recall
    # sweep (sweep.py) keeps a sentence that matches one of them and carries a value for a field.
    sweep_terms: tuple[str, ...] = ()


PARAMETERS: tuple[ParameterDef, ...] = (
    ParameterDef(
        "PARAM-01", "Rated Short-Time Withstand Current (Ik)", "Electrical Ratings (IEC 62271-200)",
        Criticality.MANDATORY,
        (FieldDef("ik", "current", "ge", "Ik", plausible=("10 kA", "100 kA")),
         FieldDef("ik_duration", "duration", "ge", "t", plausible=("0.5 s", "5 s"))),
        "{ik} ({ik_duration})",
        "Rated short-time withstand current Ik of the switchgear and its duration in seconds. "
        "Not the internal arc (IAC) test current or duration.",
        # The last term is a rating written with no label at all: "rated 31.5kA 1s".
        sweep_terms=(r"short[- ]time", r"\bIk\b", r"withstand current",
                     r"\d\s*kA\s*(?:/|for|at|,)?\s*\d+(?:[.,]\d+)?\s*s(?:ec(?:ond)?s?)?\b"),
    ),
    ParameterDef(
        "PARAM-02", "Main Busbar Material & Continuous Current", "Primary Conductors & Materials",
        Criticality.MANDATORY,
        (FieldDef("material", "material", "eq", "Material"), FieldDef("rated_current", "current", "ge", "Ir", plausible=("200 A", "6300 A"))),
        "{material}, {rated_current}",
        "Main busbar conductor material (copper or aluminium) and the busbar's rated continuous current.",
        sweep_terms=(r"busbar",),
    ),
    ParameterDef(
        "PARAM-03", "Enclosure Ingress & Internal Arc Rating", "Mechanical & Safety Protection",
        Criticality.HIGH,
        (FieldDef("ip", "ip", "ge", "IP"), FieldDef("iac_class", "iac", "ord", "IAC")),
        "{ip} / IAC {iac_class}",
        "Enclosure degree of protection (IP code) and internal arc classification (IAC class such as AF, AFL or AFLR).",
        sweep_terms=(r"\bIP\s?\d[\dX]\b", r"\bIAC\b", r"internal arc", r"enclosure protection",
                     r"degree of protection"),
    ),
    ParameterDef(
        "PARAM-04", "IED Protection & Redundant IEC 61850 PRP", "Control, SCADA & Protection Relays",
        Criticality.HIGH,
        (FieldDef("protocol", "protocol", "eq", "Protocol"), FieldDef("marshalling", "marshalling", "eq", "Marshalling")),
        "{protocol}; {marshalling}",
        "Protection IED communication (IEC 61850 edition and PRP redundancy, or other ports such as Modbus/RJ45) "
        "and whether RTU/SCADA marshalling is pre-wired or left to the buyer.",
        sweep_terms=(r"61850", r"\bPRP\b", r"marshalling", r"modbus"),
    ),
    ParameterDef(
        "PARAM-05", "Witnessed Factory Acceptance Test (I-FAT)", "Quality Assurance & Testing",
        Criticality.COMMERCIAL,
        (FieldDef("witnessed_fat_days_included", "fat_days", "ge", "Witnessed FAT days", plausible=("0", "30")),),
        "{witnessed_fat_days_included} witnessed FAT days included",
        "Witnessed factory acceptance test: how many client-witnessed test days are included in the price, "
        "or whether witnessed testing is excluded or charged extra.",
        scope_field="witnessed_fat_days_included",
        sweep_terms=(r"witness", r"\bI?-?FAT\b", r"factory acceptance", r"routine test"),
    ),
    ParameterDef(
        "PARAM-06", "Incoterms 2020 & Site Unloading", "Commercial & Logistics Terms",
        Criticality.COMMERCIAL,
        (FieldDef("incoterm", "incoterm", "ord", "Incoterm"), FieldDef("warranty_months", "months", "ge", "Warranty months", plausible=("1", "120"))),
        "{incoterm} / {warranty_months} months",
        "Delivery Incoterm and place, and the warranty or defect liability period in months.",
        sweep_terms=(r"\b(?:EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b", r"ex[- ]works",
                     r"delivered (?:at place|duty paid)", r"free carrier", r"incoterm", r"warranty",
                     r"defect liability", r"guarantee"),
    ),
    ParameterDef(
        "PARAM-07", "Rated Voltage (Ur)", "Electrical Ratings (IEC 62271-200)",
        Criticality.MANDATORY,
        (FieldDef("rated_voltage", "voltage", "ge", "Ur", plausible=("1 kV", "52 kV")),),
        "{rated_voltage}",
        "Rated voltage Ur of the switchgear.",
        sweep_terms=(r"rated voltage", r"\bUr\b"),
    ),
    ParameterDef(
        "PARAM-08", "Max Ambient Temperature (No Derating)", "Thermal & Environmental",
        Criticality.MANDATORY,
        (FieldDef("max_ambient", "temperature", "ge", "Tamb", plausible=("25 °C", "70 °C")),),
        "max ambient {max_ambient}",
        "Maximum ambient air temperature the equipment is rated for without derating.",
        sweep_terms=(r"ambient",),
    ),
)

PARAMS_BY_ID: dict[str, ParameterDef] = {p.id: p for p in PARAMETERS}

# The delivery, contract-risk and scope-boundary parameters added after the first eight. They are a
# separate equipment class so that tenders, saved locator answers and fingerprints made under the
# first eight stay exactly as they were; a manifest opts in with equipment_class "mv_gis_switchgear_ext".
EXTENDED_PARAMETERS: tuple[ParameterDef, ...] = (
    ParameterDef(
        "PARAM-09", "Delivery Lead Time", "Schedule & Delivery", Criticality.HIGH,
        (FieldDef("lead_time", "lead_time", "le", "Lead time", plausible=("1 weeks", "150 weeks")),),
        "{lead_time}",
        "Delivery lead time in weeks and what it is counted from (the purchase order, approved drawings or a "
        "date). Not the offer validity, the drawing submission time, the FAT date or the warranty period.",
        # A period counts only beside delivery wording, and never in a warranty or liability sentence.
        sweep_terms=(r"lead[- ]time",
                     r"(?!.*(?:warranty|liabilit|defect|guarantee))(?:deliver\w*|manufactur\w+|ex[- ]works)"
                     r"[^.;]{0,30}\b(?:weeks?|months?|days?)\b"),
    ),
    ParameterDef(
        "PARAM-10", "Delay Liquidated Damages", "Commercial Risk (Liquidated Damages)", Criticality.HIGH,
        (FieldDef("ld_rate", "percent", "ge", "LD rate", plausible=("0 %", "10 %")),
         FieldDef("ld_cap", "percent", "ge", "LD cap", plausible=("0 %", "100 %"))),
        "{ld_rate}; cap {ld_cap}",
        "Delay liquidated damages: the rate per week or per day of delay as a percentage of the contract price, "
        "and the maximum cap on them. If the text says liquidated damages are excluded or not accepted, quote that.",
        sweep_terms=(r"liquidated damages", r"\bLDs?\b", r"delay damages", r"penalt\w+"),
    ),
    ParameterDef(
        "PARAM-11", "Limitation of Liability", "Commercial Risk (Liability)", Criticality.HIGH,
        (FieldDef("liability_cap", "percent", "ge", "Liability cap", plausible=("1 %", "1000 %")),),
        "{liability_cap}",
        "The limit on the supplier's total liability as a percentage of the contract price. Not the defect "
        "liability period and not liquidated damages.",
        sweep_terms=(r"liabilit\w+",),
    ),
    ParameterDef(
        "PARAM-12", "Commissioning Spares", "Scope Boundary & Site Interface", Criticality.COMMERCIAL,
        (FieldDef("spares_included", "scope", "ge", "Commissioning spares"),),
        "commissioning spares {spares_included}",
        "Whether commissioning spare parts are included in the price, or quoted separately, optional or excluded.",
        scope_field="spares_included",
        sweep_terms=(r"spares?\b", r"spare parts"),
    ),
    ParameterDef(
        "PARAM-13", "Site Supervision", "Scope Boundary & Site Interface", Criticality.COMMERCIAL,
        (FieldDef("supervision_included", "scope", "ge", "Site supervision"),),
        "site supervision {supervision_included}",
        "Whether supervision of erection and commissioning at site is included in the price, or quoted "
        "separately, optional or excluded.",
        scope_field="supervision_included",
        sweep_terms=(r"supervis\w+", r"commissioning engineer"),
    ),
    ParameterDef(
        "PARAM-14", "Rated Altitude", "Thermal & Environmental", Criticality.MANDATORY,
        (FieldDef("site_altitude", "altitude", "ge", "Altitude", plausible=("0 m", "4500 m")),),
        "rated for {site_altitude}",
        "The altitude above sea level the equipment is rated for, or the installation altitude the MR requires, "
        "in metres or feet.",
        sweep_terms=(r"altitude", r"elevation", r"above sea level", r"\bmasl\b"),
    ),
    ParameterDef(
        "PARAM-15", "Applicable Standards & Editions", "Standards & Certification", Criticality.HIGH,
        (FieldDef("standards", "standard", "all", "Standards"),),
        "{standards}",
        "The standards (IEC, IEEE, EN, VDE, ISO) the equipment is designed and type-tested to, with the edition "
        "year where it is written. Not a quality-system certification such as ISO 9001.",
        # Design and test wording only, so a standards list in a codes section or an ISO 9001 line is not read.
        sweep_terms=(r"designed\s+(?:and\s+[\w-]+\s+)?(?:to|in accordance with|according to)", r"type[- ]tested",
                     r"compl(?:y|ies|iant|iance)\s+with"),
    ),
)

# One parameter set per equipment class. PARAMETERS and PARAMS_BY_ID above stay as the
# default class's names; code that holds a manifest asks for its own class instead.
DEFAULT_EQUIPMENT_CLASS = "mv_gis_switchgear"
EXTENDED_EQUIPMENT_CLASS = "mv_gis_switchgear_ext"
PARAMETERS_BY_CLASS: dict[str, tuple[ParameterDef, ...]] = {
    DEFAULT_EQUIPMENT_CLASS: PARAMETERS,
    EXTENDED_EQUIPMENT_CLASS: PARAMETERS + EXTENDED_PARAMETERS,
}

# The classes whose tenders carry a findings register. The first class is frozen as released, so its
# briefs, fixtures and saved answers keep their shape; the register comes with the extended class.
FINDINGS_CLASSES = frozenset({EXTENDED_EQUIPMENT_CLASS})



class UnknownEquipmentClass(ValueError):
    pass


def parameters_for(equipment_class: str = DEFAULT_EQUIPMENT_CLASS) -> tuple[ParameterDef, ...]:
    try:
        return PARAMETERS_BY_CLASS[equipment_class]
    except KeyError:
        known = ", ".join(sorted(PARAMETERS_BY_CLASS))
        raise UnknownEquipmentClass(
            f"no parameters defined for equipment class {equipment_class!r} (known: {known})") from None


def params_by_id_for(equipment_class: str = DEFAULT_EQUIPMENT_CLASS) -> dict[str, ParameterDef]:
    return {p.id: p for p in parameters_for(equipment_class)}

# Declared orders for classed values, weakest first, from the buyer's side. A field with
# operator "ord" passes when the offered value is at or above the required one in its order.
ORDINALS: dict[str, tuple[str, ...]] = {
    # Incoterms 2020: each step moves more cost and risk to the seller.
    "incoterm": ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"),
    # Internal arc classification: accessible sides covered (front; front and lateral; all).
    "iac": ("AF", "AFL", "AFLR"),
}

# Spec §10: lower number = stronger evidence of what will be delivered.
EVIDENCE_TIERS: dict[Section, int] = {
    "drawing": 1, "bom": 1, "datasheet": 1, "test_plan": 1, "commercial": 1,
    "compliance_table": 2,
    "cover_letter": 3, "general": 3, "unlabeled": 3,
    "mr_spec": 0,
}

# Sections whose statements are a vendor's claim, not a document showing the thing itself.
CLAIM_SECTIONS: frozenset[Section] = frozenset({"cover_letter", "compliance_table"})

SECTION_LABELS: dict[Section, str] = {
    "cover_letter": "Cover letter",
    "compliance_table": "Compliance table",
    "datasheet": "Datasheet",
    "drawing": "Drawing",
    "bom": "BOM",
    "test_plan": "Test plan",
    "commercial": "Commercial terms",
    "general": "General section",
    "mr_spec": "MR spec",
    "unlabeled": "Unlabeled",
}

# Spec §6: heading keyword phrases (matched as whole words) -> section.
HEADING_SECTIONS: list[tuple[tuple[str, ...], Section]] = [
    (("COVER LETTER",), "cover_letter"),
    (("COMPLIANCE STATEMENT", "COMPLIANCE TABLE", "SCHEDULE OF DEVIATIONS"), "compliance_table"),
    (("TECHNICAL DATASHEET", "TECHNICAL DATA"), "datasheet"),
    (("SINGLE LINE DIAGRAM", "SLD", "DRAWING"), "drawing"),
    (("BILL OF MATERIALS", "BOM"), "bom"),
    (("INSPECTION AND TEST PLAN", "ITP", "FACTORY ACCEPTANCE TEST"), "test_plan"),
    (("COMMERCIAL TERMS", "PRICE SCHEDULE"), "commercial"),
]
