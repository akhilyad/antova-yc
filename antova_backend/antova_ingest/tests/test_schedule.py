"""Schedule float: lead time against the required on-site date."""

from datetime import date

import pytest

from antova_ingest.schedule import arrival_basis_for_incoterm, schedule_float
from antova_ingest.types import FieldValue, ProjectBaseline
from antova_ingest.values import parse_field

PROJECT = {"po_award_date": {"value": "2027-01-15"}, "required_on_site_date": {"value": "2027-10-15"}}


def lead(weeks, anchor="po"):
    return FieldValue("lead_time", float(weeks), f"{weeks:g} weeks", qualifier=anchor)


def run(weeks=38, anchor="po", project=PROJECT, **kwargs):
    kwargs.setdefault("arrival_basis", "site")
    return schedule_float(project, lead(weeks, anchor) if weeks is not None else None, **kwargs)


# PO 2027-01-15 + 38 weeks (266 days) = 2027-10-08, a week before the 2027-10-15 need.

def test_positive_float():
    r = run(38)
    assert (r.outcome, r.projected_arrival, r.float_days) == ("float_ok", date(2027, 10, 8), 7)
    assert r.required_on_site == date(2027, 10, 15)


def test_arriving_on_the_required_day_is_acceptable_by_default():
    r = run(39)
    assert (r.outcome, r.float_days) == ("float_ok", 0)


def test_negative_float_is_a_critical_path_breach():
    r = run(41)
    assert (r.outcome, r.float_days) == ("critical_path_breach", -14)
    assert "14 days late" in r.reason


def test_a_minimum_float_makes_a_thin_margin_a_breach():
    assert run(38, minimum_float_days=14).outcome == "critical_path_breach"
    assert run(38, minimum_float_days=7).outcome == "float_ok"
    assert "under the 14 required" in run(38, minimum_float_days=14).reason


def test_the_calculation_is_shown_step_by_step():
    assert run(38).steps == [
        "PO award 2027-01-15",
        "+ 38 weeks lead time (38 weeks) = 2027-10-08",
        "required on site 2027-10-15; float +7 days",
    ]


# --- anchors ---------------------------------------------------------------------------------------

def test_a_lead_time_from_approved_drawings_adds_the_drawing_approval_period():
    r = run(32, "approved_drawings", drawing_approval_weeks=6)  # 6 + 32 = 38 weeks in all
    assert (r.outcome, r.projected_arrival, r.float_days) == ("float_ok", date(2027, 10, 8), 7)
    assert "drawings approved 2027-02-26" in " ".join(r.steps)


def test_a_drawings_anchor_without_the_mr_approval_period_is_manual():
    r = run(34, "approved_drawings")
    assert r.outcome == "manual" and "drawing approval" in r.reason
    assert r.projected_arrival is None and r.float_days is None


@pytest.mark.parametrize("anchor", ["unspecified", None])
def test_an_unknown_anchor_is_manual_and_never_assumed_to_be_the_po(anchor):
    r = run(10, anchor)
    assert r.outcome == "manual" and "counted from" in r.reason


# --- missing inputs --------------------------------------------------------------------------------

@pytest.mark.parametrize("project, fragment", [
    (None, "PO award date"),
    ({"required_on_site_date": {"value": "2027-10-15"}}, "PO award date"),
    ({"po_award_date": {"value": "2027-01-15"}}, "required on-site date"),
])
def test_missing_project_dates_are_manual(project, fragment):
    r = run(38, project=project)
    assert r.outcome == "manual" and fragment in r.reason


def test_no_lead_time_is_manual():
    r = run(None)
    assert r.outcome == "manual" and "no lead time" in r.reason


def test_a_non_positive_lead_time_is_manual():
    assert run(0).outcome == "manual"


# --- where the lead time ends ----------------------------------------------------------------------

def test_an_unknown_arrival_basis_is_manual():
    r = run(38, arrival_basis=None)
    assert r.outcome == "manual" and "Incoterm" in r.reason


def test_a_lead_time_ending_before_site_needs_transit():
    assert run(38, arrival_basis="before_site").outcome == "manual"
    r = run(36, arrival_basis="before_site", transit_days=14)
    assert (r.projected_arrival, r.float_days) == (date(2027, 10, 8), 7)  # ready 2027-09-24, then 14 days
    late = run(38, arrival_basis="before_site", transit_days=30)
    assert (late.outcome, late.float_days) == ("critical_path_breach", -23)


@pytest.mark.parametrize("term, basis", [
    ("DAP", "site"), ("DPU", "site"), ("DDP", "site"),
    ("EXW", "before_site"), ("FCA", "before_site"), ("CIF", "before_site"), ("CIP", "before_site"),
    (None, None), ("", None),
])
def test_the_incoterm_decides_where_the_lead_time_ends(term, basis):
    assert arrival_basis_for_incoterm(term) == basis


# --- calendar arithmetic ---------------------------------------------------------------------------

def test_a_fractional_week_rounds_up_to_the_later_day():
    # 5.2 weeks = 36.4 days: a vendor ready on day 36.4 is ready on day 37.
    r = schedule_float({"po_award_date": {"value": "2027-01-01"}, "required_on_site_date": {"value": "2027-03-01"}},
                       lead(5.2), arrival_basis="site")
    assert r.projected_arrival == date(2027, 2, 7)


def test_a_whole_number_of_days_is_not_rounded_up_by_float_noise():
    r = schedule_float({"po_award_date": {"value": "2027-01-01"}, "required_on_site_date": {"value": "2027-03-01"}},
                       lead(0.1 * 10), arrival_basis="site")
    assert r.projected_arrival == date(2027, 1, 8)


def test_the_calendar_crosses_a_leap_year_without_a_working_day_adjustment():
    r = schedule_float({"po_award_date": {"value": "2027-12-20"}, "required_on_site_date": {"value": "2028-03-01"}},
                       lead(10), arrival_basis="site")
    assert r.projected_arrival == date(2028, 2, 28)
    assert r.float_days == 2  # 2028-02-29 exists


# --- the real inputs -------------------------------------------------------------------------------

def test_it_reads_the_stored_baseline_and_a_parsed_lead_time():
    baseline = ProjectBaseline.model_validate(
        {"po_award_date": {"value": "2027-01-15"}, "required_on_site_date": {"value": "2027-10-15"}}
    ).model_dump(mode="json", exclude_none=True)
    value = parse_field("lead_time", "Delivery 38 weeks from PO.", "lead")
    r = schedule_float(baseline, value, arrival_basis=arrival_basis_for_incoterm("DAP"))
    assert (r.outcome, r.float_days) == ("float_ok", 7)


def test_a_range_in_the_bid_is_planned_at_its_upper_end():
    value = parse_field("lead_time", "Delivery 36 to 42 weeks from PO.", "lead")
    r = schedule_float(PROJECT, value, arrival_basis="site")
    assert r.outcome == "critical_path_breach"
