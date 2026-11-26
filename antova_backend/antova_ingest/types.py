"""Shared data types for the ingestion pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_serializer, model_validator,
)

from antova_engine import ComplianceStatus, Criticality, TenderPackage  # noqa: F401

Section = Literal[
    "mr_spec", "cover_letter", "compliance_table", "datasheet", "drawing",
    "bom", "test_plan", "commercial", "general", "unlabeled",
]
FieldKind = Literal[
    "current", "duration", "voltage", "temperature", "material", "ip", "iac",
    "protocol", "marshalling", "fat_days", "incoterm", "months",
    "lead_time", "percent", "money_cap", "altitude", "standard", "scope",
    # Contract terms: an LD-style rate per day or week, a project event, payment milestones, law, forum.
    "rate_per_period", "event", "milestone_list", "law", "forum",
]


DocumentRole = Literal["mr_spec", "vendor_bid", "framework_agreement", "owner_contract"]
# The same two roles as contract_ontology.CONTRACT_ROLES (kept here: that module imports ontology, which imports this one).
_CONTRACT_ROLES = ("framework_agreement", "owner_contract")
# Fields added with the contract roles: left out of a dump when unset, so a stored manifest that
# predates them is stored exactly as before.
_CONTRACT_ENTRY_FIELDS = ("counterparty_id", "amends", "dated")


class DocumentEntry(BaseModel):
    file: str
    role: DocumentRole
    vendor_id: str | None = None
    vendor_name: str | None = None
    country: str | None = None
    # The vendor a framework agreement is with (a framework agreement has no vendor_id: it is not a bid).
    counterparty_id: str | None = None
    # The file name of the entry this one amends (same role and counterparty); the later one governs
    # for the clauses it covers.
    amends: str | None = None
    dated: date | None = None

    @model_validator(mode="after")
    def _contract_role_owners(self):
        if self.amends is not None:
            if self.role not in _CONTRACT_ROLES:
                raise ValueError(f"{self.file}: only a framework_agreement or owner_contract amends another; "
                                 f"a {self.role} has no amends")
            if self.dated is None:
                raise ValueError(f"{self.file}: an entry that amends another needs dated (the later one governs)")
        if self.role == "framework_agreement":
            if not self.counterparty_id:
                raise ValueError(f"{self.file}: a framework_agreement needs counterparty_id")
            if self.vendor_id is not None:
                raise ValueError(f"{self.file}: a framework_agreement takes counterparty_id, not vendor_id")
        if self.role == "owner_contract":
            for name in ("vendor_id", "counterparty_id"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{self.file}: an owner_contract is the project's; it has no {name}")
        return self

    @model_serializer(mode="wrap")
    def _omit_unset_contract_fields(self, handler):
        data = handler(self)
        for name in _CONTRACT_ENTRY_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data


class BaselineDate(BaseModel):
    """A project date the buyer typed in. It is a claim about the project, not read from a document,
    so it is shown at the review gate until a person confirms it."""
    model_config = ConfigDict(extra="forbid")
    value: date
    source: Literal["buyer_entered"] = "buyer_entered"
    note: str | None = None


class BaselineNumber(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Decimal
    source: Literal["buyer_entered"] = "buyer_entered"
    note: str | None = None


Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]  # ISO 4217 code, upper case


class BaselineMoney(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(ge=0)
    currency: Currency
    source: Literal["buyer_entered"] = "buyer_entered"
    note: str | None = None


class BaselineFx(BaseModel):
    """A conversion rate the buyer entered, with its date: 1 from_currency = rate to_currency.
    Antova never fetches or assumes a rate."""
    model_config = ConfigDict(extra="forbid")
    from_currency: Currency
    to_currency: Currency
    rate: Decimal = Field(gt=0)
    dated: date
    source: Literal["buyer_entered"] = "buyer_entered"


class OwnerTerms(BaseModel):
    """The owner contract's pass-down terms, entered by the buyer instead of read from a PDF.
    Each one unknown is None (never 0)."""
    model_config = ConfigDict(extra="forbid")
    contract_value: BaselineMoney | None = None
    ld_rate: Decimal | None = Field(default=None, ge=0)
    ld_rate_basis: Literal["percent_per_day", "percent_per_week", "money_per_day", "money_per_week"] | None = None
    ld_cap: Decimal | None = Field(default=None, ge=0)
    ld_cap_basis: Literal["percent", "money"] | None = None
    # The share of the owner's LDs this package carries, in percent.
    package_ld_share_pct: Decimal | None = Field(default=None, ge=0, le=100)
    liability_cap: BaselineMoney | None = None
    warranty_months: int | None = Field(default=None, ge=0)
    warranty_start: str | None = None  # a project event, ontology.ORDINALS["event"]
    law: str | None = None
    source: Literal["buyer_entered"] = "buyer_entered"

    @field_validator("warranty_start")
    @classmethod
    def _known_event(cls, value):
        from antova_ingest.ontology import ORDINALS  # ontology imports this module
        if value is not None and value not in ORDINALS["event"]:
            raise ValueError(f"warranty_start must be one of {', '.join(ORDINALS['event'])}")
        return value

    @model_validator(mode="after")
    def _numbers_carry_their_basis(self):
        # A rate or cap without its basis cannot be computed with (per day or per week, percent or money).
        for number, basis in (("ld_rate", "ld_rate_basis"), ("ld_cap", "ld_cap_basis")):
            if (getattr(self, number) is None) != (getattr(self, basis) is None):
                raise ValueError(f"{number} and {basis} are entered together or not at all")
        return self


class ProjectBaseline(BaseModel):
    """What the project needs, as the buyer states it. Every field is optional: an audit that
    needs one it does not have says so (it never assumes a date)."""
    model_config = ConfigDict(extra="forbid")
    po_award_date: BaselineDate | None = None
    required_on_site_date: BaselineDate | None = None
    energization_date: BaselineDate | None = None
    site_altitude_m: BaselineNumber | None = None
    site_max_ambient_c: BaselineNumber | None = None
    frozen_budget: BaselineMoney | None = None
    fx: list[BaselineFx] = []
    # The delay scenario for LD comparisons, in weeks (spec 4.1); a formula needing days shows the x7.
    delay_scenario_weeks: BaselineNumber | None = None
    owner_terms: OwnerTerms | None = None
    procurement_negotiated: bool | None = None
    signing_before_ntp: bool | None = None
    project_id: str | None = None

    @model_validator(mode="after")
    def _dates_in_order(self):
        award, site = self.po_award_date, self.required_on_site_date
        if award and site and site.value < award.value:
            raise ValueError("required_on_site_date is before po_award_date")
        if self.site_altitude_m is not None and not (-500 <= self.site_altitude_m.value <= 6000):
            raise ValueError("site_altitude_m must be between -500 and 6000")
        return self

    @model_validator(mode="after")
    def _one_fx_rate_per_pair(self):
        seen: set[frozenset[str]] = set()
        for rate in self.fx:
            if rate.from_currency == rate.to_currency:
                raise ValueError(f"fx: from_currency and to_currency are both {rate.from_currency}")
            # USD->EUR and EUR->USD are one pair: two entered rates could disagree.
            pair = frozenset((rate.from_currency, rate.to_currency))
            if pair in seen:
                raise ValueError(f"fx: more than one rate for {rate.from_currency}/{rate.to_currency}; "
                                 f"one rate per currency pair")
            seen.add(pair)
        return self

    @model_serializer(mode="wrap")
    def _omit_empty_fx(self, handler):
        # No rates is left out, as an unset field is, so a baseline with only the old fields
        # dumps as before and gets no review item for an empty list.
        data = handler(self)
        if not data.get("fx", True):
            del data["fx"]
        return data


PROJECT_FIELDS = ("po_award_date", "required_on_site_date", "energization_date",
                  "site_altitude_m", "site_max_ambient_c", "frozen_budget", "fx", "delay_scenario_weeks",
                  "owner_terms", "procurement_negotiated", "signing_before_ntp", "project_id")


class TenderManifest(BaseModel):
    tender_id: str
    title: str
    client: str
    equipment_class: str
    rate_table: str
    documents: list[DocumentEntry]
    engineering_estimates: dict[str, Decimal]
    synthetic: bool = False
    project: ProjectBaseline | None = None

    @model_validator(mode="after")
    def _amendments_name_their_document(self):
        by_file = {d.file: d for d in self.documents}
        for d in self.documents:
            if d.amends is None:
                continue
            target = by_file.get(d.amends)
            if target is None or target is d:
                raise ValueError(f"{d.file}: amends {d.amends!r}, which is not another document in the manifest")
            if (target.role, target.counterparty_id) != (d.role, d.counterparty_id):
                raise ValueError(f"{d.file}: amends {d.amends!r}, which is not a {d.role} with the same counterparty")
        for d in self.documents:
            chain, current = {d.file}, d
            while current.amends is not None and current.amends in by_file:
                if current.amends in chain:
                    raise ValueError(f"{d.file}: amends leads back to itself ({current.amends!r})")
                chain.add(current.amends)
                current = by_file[current.amends]
        return self


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int = 0  # PyMuPDF text block: one paragraph or table row
    line: int = 0  # line number inside the block


@dataclass
class PageText:
    number: int
    text: str
    words: list[Word]
    section: Section = "unlabeled"
    has_text: bool = True
    width: float = 0.0  # page size in PDF points; 0 when unknown (a page built from text alone)
    height: float = 0.0


@dataclass
class ParsedDocument:
    entry: DocumentEntry
    sha256: str
    pages: list[PageText]


@dataclass(frozen=True)
class Citation:
    """Where evidence sits. `quote` is the page's own full sentence (or table row)
    containing the matched text; `paragraph` is the whole text block around it."""

    filename: str
    page: int
    bbox: tuple[float, float, float, float]
    quote: str
    section: Section
    paragraph: str = ""
    # How the quote was found: "exact", or "dehyphenated" when it only matched with a
    # line-end hyphen removed ("switch-" + "gear" for "switchgear").
    match: str = "exact"
    # One box per text line of the quote (a sentence that wraps is not one rectangle).
    # For drawing the highlight only; `bbox` is their outer box.
    rects: tuple[tuple[float, float, float, float], ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        data["rects"] = [list(r) for r in self.rects]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Citation":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        values["bbox"] = tuple(values["bbox"])
        values["rects"] = tuple(tuple(r) for r in values.get("rects", ()))
        return cls(**values)


@dataclass(frozen=True)
class FieldValue:
    kind: FieldKind
    value: Any
    display: str
    # What the value is measured from, where that matters (a warranty's start point).
    qualifier: str | None = None


@dataclass
class Evidence:
    param_id: str
    citation: Citation
    values: dict[str, FieldValue]
    # "locator": a passage the locator returned. "sweep": found by the recall sweep (sweep.py),
    # which may only make a cell worse or flagged, never compliant.
    source: str = "locator"


@dataclass
class CheckResult:
    field: str
    operator: str
    required: str
    offered: str
    offered_canonical: str
    passed: bool
    citation_ref: str
    # Where each side measures from, for a warranty: dispatch, delivery, commissioning or unspecified.
    required_basis: str | None = None
    offered_basis: str | None = None
    # False when the two sides start from different points and nothing in the MR says how far apart.
    comparable: bool = True
    # The offered months restated on the required basis, when the MR's own duration allowed it.
    offered_equivalent: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Resolution:
    param_id: str
    vendor_id: str
    status: ComplianceStatus
    governing: dict[str, tuple[FieldValue, Citation]]
    checks: list[CheckResult]
    primary: Citation | None
    secondary: Citation | None
    conflict_note: str | None
    extracted_text: str
    confidence: float
    # Things a person must look at: [{"code": ..., "message": ...}]
    flags: list[dict] = field(default_factory=list)
    # For a COMPLIANT cell: "documentary" or "claim_only" (cover letter or compliance table only).
    evidence: str | None = None
    # One entry per value shown in extracted_text: {"field", "label", "text", "citation"}.
    segments: list[dict] = field(default_factory=list)
    # "assembled from p.11 and p.3" when the values shown come from more than one page.
    assembled_from: str | None = None


@dataclass
class SourceDocumentInfo:
    filename: str
    role: str
    vendor_id: str | None
    sha256: str
    page_count: int
    pages_without_text: list[int] = field(default_factory=list)
    # From the manifest entry (contract roles): the counterparty, the file it amends, its date.
    counterparty_id: str | None = None
    amends: str | None = None
    dated: date | None = None


@dataclass
class IngestResult:
    tender: TenderPackage
    documents: list[SourceDocumentInfo]
    stats: dict
    warnings: list[str]
    # stage name -> StageArtifact (antova_ingest.artifacts), in stage order
    artifacts: dict = field(default_factory=dict)
    # {"fingerprint": sha256, "parts": {...}} (antova_ingest.artifacts.run_fingerprint)
    fingerprint: dict = field(default_factory=dict)
