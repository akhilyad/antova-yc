"""The ontology is a registry of parameter sets keyed by equipment class."""

import pytest

from antova_ingest import ontology
from antova_ingest.pipeline import IngestFailed, stage_parsed
from antova_ingest.types import TenderManifest


def test_default_class_is_the_module_constants():
    assert ontology.parameters_for() is ontology.PARAMETERS
    assert ontology.parameters_for("mv_gis_switchgear") is ontology.PARAMETERS
    assert ontology.params_by_id_for() == ontology.PARAMS_BY_ID


def test_unknown_class_names_the_known_ones():
    with pytest.raises(ontology.UnknownEquipmentClass, match="mv_gis_switchgear"):
        ontology.parameters_for("hv_transformer")


def test_unknown_class_fails_the_ingest_at_intake(tmp_path):
    manifest = TenderManifest(tender_id="T", title="t", client="c", equipment_class="hv_transformer",
                              rate_table="mv_gis_switchgear_v2", documents=[], engineering_estimates={})
    with pytest.raises(IngestFailed, match="hv_transformer"):
        stage_parsed(tmp_path, manifest)
