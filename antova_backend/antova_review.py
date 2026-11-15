"""
Founder review: the open items that block verifying a brief, and the brief payload.

Pure functions over a loaded tender; the HTTP routes fetch the tender, metadata,
documents and overrides and pass them in. The payload's keys and override shape
are a contract with the frontend fixtures.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Optional

from antova_engine import (
    ComplianceStatus,
    Override,
    SensitivitySettings,
    TenderPackage,
    evaluate_vendor_financials,
)

if TYPE_CHECKING:  # type only: importing antova_db at runtime would build its database engine
    from antova_db import TenderMeta

_CENTS = Decimal("0.01")

MESSAGES = {
    "unpriced": "Gap has no price yet. Enter an amount or accept it.",
    "needs_inspection": "Check this quote against its page.",
    "missing_price": "Bid price not found. Correct the bid file and process it again.",
    "review_flag": "Flag not reviewed yet.",
    "project_field": "Confirm the project value you entered.",
}
_REVIEW_MESSAGES = {
    "open": "Flag not reviewed yet.",
    "stale": "Evidence changed since it was approved; review again",
    "rejected": "You rejected this quote. Correct the bid file and process it again.",
}


@dataclass
class OpenItem:
    kind: Literal["unpriced", "needs_inspection", "missing_price", "review_flag", "project_field"]
    vendor_id: str
    param_id: Optional[str]
    message: str


def _item(kind: str, vendor_id: str, param_id: Optional[str] = None) -> OpenItem:
    return OpenItem(kind=kind, vendor_id=vendor_id, param_id=param_id, message=MESSAGES[kind])


# A buyer-entered project value is reviewed through the same record as a cell, under this key:
# param_id "PROJECT.<field>", vendor_id "-".
PROJECT_PREFIX = "PROJECT."
PROJECT_VENDOR = "-"


def project_review_key(field_name: str) -> tuple[str, str]:
    return (PROJECT_PREFIX + field_name, PROJECT_VENDOR)


def project_field_hash(value: Mapping[str, Any]) -> str:
    """SHA-256 of a project value as the reviewer saw it; a changed value makes the confirmation stale."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def project_field_state(value: Mapping[str, Any], review: Optional[Mapping[str, Any]]) -> str:
    """open, reviewed, stale or rejected. A buyer-entered value always needs a person's confirmation."""
    if review is None:
        return "open"
    if review["evidence_hash"] != project_field_hash(value):
        return "stale"
    return "rejected" if review["action"] == "reject" else "reviewed"


def evidence_hash(cell) -> str:
    """SHA-256 of what a reviewer sees in a cell: its status, values, citations, checks, flags and price.

    Stored with every review. If a re-ingest changes any of it, the stored hash no longer
    matches and the review is stale.
    """
    seen = {
        "status": cell.status.value if hasattr(cell.status, "value") else cell.status,
        "extracted_text": cell.extracted_text,
        "citation": cell.citation,
        "secondary_citation": cell.secondary_citation,
        "checks": cell.checks,
        "flags": cell.flags,
        "pricing_status": cell.pricing_status,
        "cost_adder": cell.cost_adder,
        "cost_lines": [line.model_dump(mode="json") for line in cell.cost_lines],
    }
    return hashlib.sha256(json.dumps(seen, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def review_state(cell, review: Optional[Mapping[str, Any]]) -> str:
    """none (nothing to review), open, reviewed, stale or rejected. A person approves every flag."""
    if not cell.flags:
        return "none"
    if review is None:
        return "open"
    if review["evidence_hash"] != evidence_hash(cell):
        return "stale"
    return "rejected" if review["action"] == "reject" else "reviewed"


def review_summary(tender: TenderPackage, reviews: Mapping[tuple[str, str], Mapping[str, Any]],
                   status: str = "DRAFT", blocking: int = 0) -> dict[str, Any]:
    """Counts of flagged cells by review state, and whether the result may be presented as final."""
    counts = {"open": 0, "stale": 0, "rejected": 0, "reviewed": 0}
    for p in tender.parameters:
        for vendor_id, cell in p.vendors.items():
            state = review_state(cell, reviews.get((p.id, vendor_id)))
            if state in counts:
                counts[state] += 1
    unresolved = counts["open"] + counts["stale"] + counts["rejected"]
    return {"open_flags": counts["open"], "stale": counts["stale"], "rejected": counts["rejected"],
            "reviewed": counts["reviewed"], "final": status == "VERIFIED" and unresolved == 0 and blocking == 0}


def unresolved_flags(tender: TenderPackage, reviews: Mapping[tuple[str, str], Mapping[str, Any]]) -> int:
    summary = review_summary(tender, reviews)
    return summary["open_flags"] + summary["stale"] + summary["rejected"]


def open_items(tender: TenderPackage, overrides: Mapping[tuple[str, str], Override],
               reviews: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> list[OpenItem]:
    """What still blocks verification, in vendor order then parameter order.

    A vendor with no headline price is one item (listed before its cells). A cell
    that is unpriced or needs inspection is an item unless it has an override. A cell
    with a review flag is an item until a person approves it; an adder override does
    not stand in for that, and an approval goes stale when the evidence changes.
    """
    reviews = reviews or {}
    items = []
    for name, value in (tender.project or {}).items():
        state = project_field_state(value, reviews.get(project_review_key(name)))
        if state in _REVIEW_MESSAGES:
            items.append(OpenItem(kind="project_field", vendor_id=PROJECT_VENDOR, param_id=PROJECT_PREFIX + name,
                                  message=_REVIEW_MESSAGES[state].replace("Review flag", "Buyer-entered value")))
    for v in tender.vendors:
        if not v.headline_price:
            items.append(_item("missing_price", v.id))
        for p in tender.parameters:
            cell = p.vendors.get(v.id)
            if cell is None:
                continue
            state = review_state(cell, reviews.get((p.id, v.id)))
            if state in _REVIEW_MESSAGES:
                items.append(OpenItem(kind="review_flag", vendor_id=v.id, param_id=p.id,
                                      message=_REVIEW_MESSAGES[state]))
            if (p.id, v.id) in overrides:
                continue
            # An exposure gap waits on the review flag above (a person accepts it), not on an adder.
            exposure_gap = any(f.get("code") == "EXPOSURE_GAP" for f in cell.flags)
            if cell.pricing_status == "UNPRICED" and not exposure_gap:
                items.append(_item("unpriced", v.id, p.id))
            if cell.status == ComplianceStatus.MANUAL_INSPECTION_REQUIRED:
                items.append(_item("needs_inspection", v.id, p.id))
    return items


def _override_entries(overrides: Mapping[tuple[str, str], Override]) -> list[dict[str, str]]:
    return [
        {"param_id": param_id, "vendor_id": vendor_id,
         "adder_eur": str(o.adder_eur.quantize(_CENTS)), "note": o.note}
        for (param_id, vendor_id), o in sorted(overrides.items())
    ]


def brief_payload(
    tender: TenderPackage,
    meta: TenderMeta,
    documents: list[dict],
    overrides: Mapping[tuple[str, str], Override],
    sensitivity: SensitivitySettings,
    reviews: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The JSON body the public brief page and the founder review page both render."""
    reviews = reviews or {}
    items = open_items(tender, overrides, reviews)
    tender_json = tender.model_dump(mode="json")
    for optional in ("project", "findings"):
        if tender_json.get(optional) is None:
            del tender_json[optional]  # absent: the payload is what it was before the field existed
    return {
        "share_id": meta.share_id,
        "status": meta.status,
        "verified_at": meta.verified_at.isoformat() if meta.verified_at is not None else None,
        "synthetic": meta.synthetic,
        "ingest_mode": meta.ingest_mode,
        "ingest_stats": meta.ingest_stats,
        "stage_log": meta.stage_log,
        "documents": documents,
        "tender": tender_json,
        "financials": evaluate_vendor_financials(tender, sensitivity, [], overrides=overrides),
        "overrides": _override_entries(overrides),
        "open_items": [asdict(item) for item in items],
        # Nothing is presented as final while a flag is open or the brief is unverified.
        "review": review_summary(tender, reviews, meta.status, len(items)),
    }


# What the run cost Antova. The founder sees it on the review payload; the public brief never does.
_RUN_COST_STATS = ("api_cost_usd", "cogs_usd")
_RUN_COST_TENDER = ("compute_cogs",)


def public_brief_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of a `brief_payload` result with Antova's run cost removed, for GET /api/briefs/{share_id}."""
    return {
        **payload,
        "ingest_stats": {k: v for k, v in payload["ingest_stats"].items() if k not in _RUN_COST_STATS},
        "tender": {k: v for k, v in payload["tender"].items() if k not in _RUN_COST_TENDER},
    }
