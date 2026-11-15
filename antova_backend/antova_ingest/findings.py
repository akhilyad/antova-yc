"""The findings register: what is wrong, or not known, in each vendor's bid, with the money behind it.

Built from what the pipeline already holds (the evaluated cells, their typed values and citations, the
rate table's cost lines, the buyer's project dates and estimates). Nothing here reads a document or calls
a model, and nothing is scored: a finding has a severity taken from the parameter's criticality, and the
register is the list, not a number.

Every finding cites at least one passage, and every euro figure shows where it comes from:

- an `adder` is the cell's rate-table cost lines, shown line by line, and is part of the True Job Cost;
- an `exposure` is money at risk (exposure.py), with the formula and each input named with its source.
  It is never added to the True Job Cost.

A figure that cannot be computed is None with the reason. It is never shown as zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

import antova_engine as engine
from antova_ingest import exposure, schedule
from antova_ingest.exposure import Exposure

Severity = Literal["critical", "major", "minor"]
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}
# From the parameter's criticality; no cell, flag or amount moves it.
SEVERITY_OF = {
    engine.Criticality.MANDATORY: "critical",
    engine.Criticality.HIGH: "major",
    engine.Criticality.MEDIUM: "minor",
    engine.Criticality.COMMERCIAL: "minor",
}
CENTS = Decimal("0.01")
_OPERATOR_WORDS = {"ge": "at least", "le": "at most", "eq": "exactly", "ord": "at least", "all": "including"}


@dataclass(frozen=True)
class Money:
    basis: Literal["adder", "exposure"]
    kind: str
    # Euros as a string with two decimals, or None when it cannot be computed.
    amount: str | None
    formula: str = ""
    reason: str = ""
    # The rate-table lines an adder is made of: [{"label", "qty", "unit", "rate", "driver"}].
    lines: tuple[dict, ...] = ()
    # What an exposure was computed from: [{"name", "value", "source"}].
    inputs: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Finding:
    vendor_id: str
    param_id: str
    kind: str
    severity: Severity
    message: str
    citations: tuple[dict, ...]
    money: tuple[Money, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["citations"] = [dict(c) for c in self.citations]
        return data


def _citations(*items: dict | None) -> tuple[dict, ...]:
    seen, out = set(), []
    for c in items:
        if c and (c["filename"], c["page"], c["quote"]) not in seen:
            seen.add((c["filename"], c["page"], c["quote"]))
            out.append(c)
    return tuple(out)


def _line(line: engine.CostLine) -> dict:
    return {"label": line.label, "qty": str(line.qty), "unit": line.unit, "rate": str(line.rate), "driver": line.driver}


def _adder(cell: engine.ExtractedParameter) -> Money | None:
    if cell.pricing_status != "PRICED" or not cell.cost_lines:
        return None
    total = sum((line.qty * line.rate for line in cell.cost_lines), Decimal("0")).quantize(CENTS, rounding=ROUND_HALF_UP)
    return Money("adder", "rate_table", f"{total:.2f}", cell.adder_formula, lines=tuple(_line(l) for l in cell.cost_lines))


def _from_exposure(e: Exposure, inputs: list[dict]) -> Money:
    return Money("exposure", e.kind, None if e.amount is None else f"{e.amount:.2f}", e.formula, e.reason,
                 inputs=tuple(inputs))


def _where(citation: dict | None, what: str) -> str:
    return f"{what} p.{citation['page']}" if citation else what


def _message(cell: engine.ExtractedParameter, param: engine.EngineeringParameter) -> str:
    labels = {s["field"]: s["label"] for s in param.checks_spec}
    failing = [c for c in cell.checks if not c["passed"]]
    if failing:
        return "; ".join(f"{labels.get(c['field'], c['field'])} {c['offered']}, required {c['required']}" for c in failing)
    return cell.extracted_text


def build_findings(
    tender: engine.TenderPackage,
    resolutions: Mapping[str, Any],
    requirements: Mapping[str, Mapping[str, Any]],
    *,
    project: Mapping[str, Any] | None = None,
    estimates: Mapping[str, Decimal] | None = None,
    key=lambda vendor_id, param_id: f"{vendor_id}|{param_id}",
) -> list[dict]:
    """The register as a flat list, ordered by vendor, then severity (critical first), then parameter."""
    estimates = estimates or {}
    found: list[tuple[int, int, int, Finding]] = []
    for v_index, vendor in enumerate(tender.vendors):
        value = Decimal(str(vendor.headline_price)) if vendor.headline_price else None
        contract = [{"name": "contract value", "value": f"{value:.2f}" if value else "not found",
                     "source": _where(vendor.headline_citation, "bid headline price")}]
        for p_index, param in enumerate(tender.parameters):
            cell = param.vendors.get(vendor.id)
            if cell is None:
                continue
            resolution = resolutions.get(key(vendor.id, param.id))
            mr = param.requirement_citation
            money: list[Money] = []
            adder = _adder(cell)
            if adder:
                money.append(adder)
            severity = SEVERITY_OF[param.criticality]
            finding: Finding | None = None

            if cell.status in (engine.ComplianceStatus.DEVIATION_DOWNGRADE, engine.ComplianceStatus.CONFLICT_DOWNGRADE,
                               engine.ComplianceStatus.OMITTED):
                kind = {"DEVIATION_DOWNGRADE": "deviation", "CONFLICT_DOWNGRADE": "conflict", "OMITTED": "omitted"}[
                    cell.status.value]
                if cell.pricing_status == "UNPRICED" and not adder:
                    money.append(Money("adder", "rate_table", None, reason="The rate table has no price for this gap."))
                money.extend(_exposures(param, resolution, requirements.get(param.id, {}), value, contract, estimates))
                finding = Finding(vendor.id, param.id, kind, severity, _message(cell, param),
                                  _citations(cell.citation, cell.secondary_citation, mr), tuple(money))
            elif cell.status == engine.ComplianceStatus.MANUAL_INSPECTION_REQUIRED:
                finding = Finding(vendor.id, param.id, "unstated", severity,
                                  f"{param.name} is not clear from the bid. Ask the vendor.",
                                  _citations(cell.citation, mr), ())
            elif cell.evidence == "claim_only":
                finding = Finding(vendor.id, param.id, "claim_only", "minor",
                                  f"{param.name} appears only in a cover letter or compliance table, not shown "
                                  f"in a datasheet, drawing or test report.", _citations(cell.citation), ())
            if finding:
                found.append((v_index, SEVERITY_RANK[finding.severity], p_index, finding))

            if param.id == "PARAM-09" and resolution is not None:
                extra = _schedule_finding(vendor.id, param, resolution, resolutions.get(key(vendor.id, "PARAM-06")),
                                          project, estimates)
                if extra:
                    found.append((v_index, SEVERITY_RANK[extra.severity], p_index, extra))
    found.sort(key=lambda item: item[:3])
    return [f.to_dict() for *_, f in found]


def _exposures(param, resolution, requirement, contract_value, contract_inputs, estimates):
    """The exposures that belong to a deviation in this parameter."""
    if resolution is None:
        return []
    mr = param.requirement_citation

    def inputs_of(field_name):
        if field_name not in requirement or field_name not in resolution.governing:
            return []
        value, citation = resolution.governing[field_name]
        return [{"name": f"MR {field_name}", "value": requirement[field_name].display, "source": _where(mr, "MR")},
                {"name": f"bid {field_name}", "value": value.display, "source": _where(citation.to_dict(), "bid")}]

    def number(field_name):
        got = resolution.governing.get(field_name)
        need = requirement.get(field_name)
        return (None if need is None else need.value, None if got is None else got[0].value)

    if param.id == "PARAM-10":
        req_rate, off_rate = number("ld_rate")
        req_cap, off_cap = number("ld_cap")
        weeks = estimates.get("ld_delay_weeks")
        e = exposure.ld_recovery_shortfall(req_rate, req_cap, off_rate, off_cap, weeks, contract_value)
        inputs = inputs_of("ld_rate") + inputs_of("ld_cap") + contract_inputs + (
            [{"name": "delay to assume (weeks)", "value": f"{weeks:g}", "source": "buyer-entered estimate"}]
            if weeks is not None else [])
        return [_from_exposure(e, inputs)]
    if param.id == "PARAM-11":
        req, off = number("liability_cap")
        e = exposure.liability_shortfall(req, off, contract_value)
        return [_from_exposure(e, inputs_of("liability_cap") + contract_inputs)]
    return []


def _schedule_finding(vendor_id, param, resolution, incoterm_resolution, project, estimates) -> Finding | None:
    lead = resolution.governing.get("lead_time")
    if lead is None:
        return None
    incoterm = incoterm_resolution.governing.get("incoterm") if incoterm_resolution else None
    basis = schedule.arrival_basis_for_incoterm(incoterm[0].value if incoterm else None)
    result = schedule.schedule_float(project, lead[0], arrival_basis=basis)
    citations = _citations(lead[1].to_dict(), param.requirement_citation)
    if result.outcome == "float_ok":
        return None
    if result.outcome == "manual":
        return Finding(vendor_id, param.id, "schedule_unassessed", "minor",
                       f"Delivery date not checked: {result.reason}", citations, ())
    weeks_late = Decimal(-result.float_days) / 7
    cost = estimates.get("delay_cost_per_week")
    e = exposure.delay_cost(weeks_late, cost)
    inputs = [{"name": "weeks late", "value": f"{weeks_late:.1f}", "source": "schedule float: " + "; ".join(result.steps)}]
    if cost is not None:
        inputs.append({"name": "cost of a week of delay", "value": f"{cost:.2f}", "source": "buyer-entered estimate"})
    return Finding(vendor_id, param.id, "critical_path_breach", "critical", f"Delivery {result.reason}", citations,
                   (_from_exposure(e, inputs),))
