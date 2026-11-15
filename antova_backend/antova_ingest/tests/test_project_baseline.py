"""The buyer-entered project baseline: schema, storage and the review gate."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from antova_db import TenderRepository, init_db
from antova_ingest.tests.helpers import sample_tender
from antova_ingest.types import TenderManifest
from antova_review import (
    open_items, project_field_hash, project_field_state, project_review_key,
)

BASE = {
    "tender_id": "T-1", "title": "t", "client": "c", "equipment_class": "mv_gis_switchgear",
    "rate_table": "mv_gis_switchgear_v2", "engineering_estimates": {},
    "documents": [{"file": "mr.pdf", "role": "mr_spec"}],
}
PROJECT = {
    "po_award_date": {"value": "2027-01-15"},
    "required_on_site_date": {"value": "2027-09-30"},
    "site_altitude_m": {"value": "1200"},
}


def test_a_manifest_without_a_project_still_loads():
    manifest = TenderManifest.model_validate(BASE)
    assert manifest.project is None


def test_project_fields_are_buyer_entered_and_optional():
    manifest = TenderManifest.model_validate({**BASE, "project": PROJECT})
    assert manifest.project.po_award_date.source == "buyer_entered"
    assert manifest.project.energization_date is None


@pytest.mark.parametrize("project", [
    {"po_award_date": {"value": "2027-10-01"}, "required_on_site_date": {"value": "2027-09-30"}},
    {"site_altitude_m": {"value": "9000"}},
    {"po_award_date": {"value": "not a date"}},
    {"po_award_date": {"value": "2027-01-15", "source": "mr_cited"}},
    {"unknown_field": {"value": "1"}},
])
def test_bad_baselines_are_rejected(project):
    with pytest.raises(ValidationError):
        TenderManifest.model_validate({**BASE, "project": project})


def _with_project():
    tender = sample_tender(rate_table="mv_gis_switchgear_v1")
    tender.project = json.loads(TenderManifest.model_validate({**BASE, "project": PROJECT})
                                .project.model_dump_json(exclude_none=True))
    return tender


def test_each_buyer_entered_field_is_one_gate_item():
    base = open_items(sample_tender(rate_table="mv_gis_switchgear_v1"), {})
    items = [i for i in open_items(_with_project(), {}) if i.kind == "project_field"]
    assert sorted(i.param_id for i in items) == [
        "PROJECT.po_award_date", "PROJECT.required_on_site_date", "PROJECT.site_altitude_m"]
    assert len(open_items(_with_project(), {})) == len(base) + 3


def _review(value, action="approve", hash_of=None):
    return {"action": action, "evidence_hash": project_field_hash(hash_of or value)}


def test_confirming_a_value_clears_its_item_until_the_value_changes():
    tender = _with_project()
    key = project_review_key("po_award_date")
    value = tender.project["po_award_date"]
    assert project_field_state(value, _review(value)) == "reviewed"
    reviews = {key: _review(value)}
    assert "PROJECT.po_award_date" not in [i.param_id for i in open_items(tender, {}, reviews)]
    tender.project["po_award_date"] = {**value, "value": "2027-02-01"}
    assert "PROJECT.po_award_date" in [i.param_id for i in open_items(tender, {}, reviews)]
    assert project_field_state(tender.project["po_award_date"], reviews[key]) == "stale"


def test_a_rejected_value_stays_open():
    value = _with_project().project["po_award_date"]
    assert project_field_state(value, _review(value, "reject")) == "rejected"


def test_the_baseline_survives_a_save_and_load():
    engine = create_engine("sqlite://")
    init_db(engine)
    tender = _with_project()
    with Session(engine) as session:
        repo = TenderRepository(session)
        repo.save_tender(tender, ingested_at=datetime.now(timezone.utc), documents=[])
        assert repo.load_tender(tender.id).project == tender.project
        tender.project = None
        repo.save_tender(tender, ingested_at=datetime.now(timezone.utc), documents=[])
        assert repo.load_tender(tender.id).project is None
