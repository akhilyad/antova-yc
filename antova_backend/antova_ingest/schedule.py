"""Schedule float: does a vendor's stated lead time land before the date the site needs the equipment?

Pure code, no model and no I/O. The inputs are values the pipeline already holds: the buyer's project
dates (types.ProjectBaseline, stored as a dict on the tender), the vendor's lead time (a `lead_time`
FieldValue, in weeks, with what it is counted from), and, where the lead time needs them, the MR's
drawing-approval period and a transit time.

Float is required on-site date minus projected arrival, in calendar days. Negative float is a
critical-path breach. Every other case that cannot be computed for certain is MANUAL with the reason:
this module never assumes a date, an anchor, a transit time or a delivery point.

Calendar rule, stated once: all periods are calendar time. A week is 7 days, there is no working-day or
holiday adjustment, and a fractional number of days rounds up (the buyer plans for the later date).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from antova_ingest.types import FieldValue

Outcome = Literal["float_ok", "critical_path_breach", "manual"]

# Incoterms 2020 where the seller's delivery obligation ends at the buyer's site, so a stated lead time
# is an arrival time. Under every other term delivery happens earlier (at the factory, the carrier or a
# port) and transit still has to be added.
SITE_DELIVERY_TERMS = frozenset({"DAP", "DPU", "DDP"})
ARRIVAL_BASES = ("site", "before_site")


@dataclass(frozen=True)
class ScheduleResult:
    outcome: Outcome
    # The date the equipment is projected to arrive on site; None when it could not be computed.
    projected_arrival: date | None
    required_on_site: date | None
    # Calendar days. Positive: arrives early. Zero: arrives on the day. Negative: late.
    float_days: int | None
    # Why the result is manual, or one line on the outcome.
    reason: str
    # The calculation, one line per step, for the reviewer.
    steps: list[str] = field(default_factory=list)


def arrival_basis_for_incoterm(incoterm: str | None) -> Literal["site", "before_site"] | None:
    """"site" when the term delivers to the buyer's site (DAP, DPU, DDP), "before_site" for every other
    Incoterm, None when the term is unknown."""
    if not incoterm:
        return None
    return "site" if incoterm in SITE_DELIVERY_TERMS else "before_site"


def _date(project: Mapping[str, Any], name: str) -> date | None:
    entry = project.get(name)
    if not entry:
        return None
    raw = entry.get("value") if isinstance(entry, Mapping) else entry
    return raw if isinstance(raw, date) else date.fromisoformat(str(raw))


def _days(weeks: float) -> int:
    """Whole calendar days in `weeks`, rounded up (a tolerance keeps 5.2 weeks from becoming 37 days)."""
    return math.ceil(round(weeks * 7, 6))


def _manual(reason: str, steps: list[str], on_site: date | None = None) -> ScheduleResult:
    return ScheduleResult("manual", None, on_site, None, reason, steps)


def schedule_float(
    project: Mapping[str, Any] | None,
    lead_time: FieldValue | None,
    *,
    drawing_approval_weeks: float | None = None,
    arrival_basis: Literal["site", "before_site"] | None = None,
    transit_days: int | None = None,
    minimum_float_days: int = 0,
) -> ScheduleResult:
    """Float of one vendor's delivery against the project's required on-site date.

    project: the stored baseline ({"po_award_date": {"value": "2027-01-15"}, ...}), or None.
    lead_time: the vendor's `lead_time` value; `.value` is weeks and `.qualifier` what they count from
        ("po", "approved_drawings" or "unspecified").
    drawing_approval_weeks: weeks from PO award to approved drawings, from the MR. Needed when the lead time
        counts from approved drawings.
    arrival_basis: "site" if the lead time is already an arrival at site, "before_site" if it ends earlier
        (see arrival_basis_for_incoterm). Unknown means manual.
    transit_days: calendar days from the point the lead time ends to the site. Needed for "before_site".
    minimum_float_days: float below this is a breach (0: arriving on the required day is acceptable).
    """
    steps: list[str] = []
    baseline = project or {}
    po, required = _date(baseline, "po_award_date"), _date(baseline, "required_on_site_date")

    if lead_time is None:
        return _manual("the vendor states no lead time", steps, required)
    if po is None:
        return _manual("the project has no PO award date", steps, required)
    if required is None:
        return _manual("the project has no required on-site date", steps)
    if lead_time.value is None or lead_time.value <= 0:
        return _manual(f"the stated lead time ({lead_time.display}) is not a positive number of weeks", steps, required)

    anchor = lead_time.qualifier or "unspecified"
    steps.append(f"PO award {po.isoformat()}")
    if anchor == "po":
        start = po
    elif anchor == "approved_drawings":
        if drawing_approval_weeks is None:
            return _manual("the lead time counts from approved drawings and the MR does not state how long "
                           "drawing approval takes", steps, required)
        start = po + timedelta(days=_days(drawing_approval_weeks))
        steps.append(f"+ {drawing_approval_weeks:g} weeks drawing approval = drawings approved {start.isoformat()}")
    else:
        return _manual("the lead time does not say what it is counted from (PO or approved drawings)", steps, required)

    ready = start + timedelta(days=_days(lead_time.value))
    steps.append(f"+ {lead_time.value:g} weeks lead time ({lead_time.display}) = {ready.isoformat()}")

    if arrival_basis not in ARRIVAL_BASES:
        return _manual("it is not known whether the lead time ends at site or before it (no Incoterm read)",
                       steps, required)
    arrival = ready
    if arrival_basis == "before_site":
        if transit_days is None:
            return _manual("the lead time ends before the site (not DAP, DPU or DDP) and no transit time is given",
                           steps, required)
        arrival = ready + timedelta(days=transit_days)
        steps.append(f"+ {transit_days} days transit = arrival {arrival.isoformat()}")

    float_days = (required - arrival).days
    steps.append(f"required on site {required.isoformat()}; float {float_days:+d} days")
    if float_days < minimum_float_days:
        late = f"{-float_days} days late" if float_days < 0 else f"{float_days} days of float, under the {minimum_float_days} required"
        return ScheduleResult("critical_path_breach", arrival, required, float_days,
                              f"arrives {arrival.isoformat()}, {late}", steps)
    return ScheduleResult("float_ok", arrival, required, float_days,
                          f"arrives {arrival.isoformat()}, {float_days} days before it is needed", steps)
