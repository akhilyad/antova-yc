"""The extended equipment class: PARAM-09..14, its rate table, its locator prompt and the scope parser."""

import json
import shutil

import pytest

from antova_ingest import config, costs, extract, ontology
from antova_ingest.artifacts import run_fingerprint
from antova_ingest.pipeline import IngestFailed, stage_parsed
from antova_ingest.tests.fakes import PACK
from antova_ingest.types import TenderManifest
from antova_ingest.values import parse_field

EXT = ontology.EXTENDED_EQUIPMENT_CLASS
BASE = ontology.DEFAULT_EQUIPMENT_CLASS


def test_the_extended_class_is_the_first_eight_plus_seven():
    base, ext = ontology.parameters_for(BASE), ontology.parameters_for(EXT)
    assert [p.id for p in base] == [f"PARAM-0{i}" for i in range(1, 9)]
    assert ext[:8] == base
    assert [p.id for p in ext[8:]] == ["PARAM-09", "PARAM-10", "PARAM-11", "PARAM-12", "PARAM-13", "PARAM-14",
                                       "PARAM-15"]


def test_every_new_field_has_a_parser_a_sweep_signal_and_a_deviation_class():
    config.validate_config()  # raises ConfigError naming the first gap
    for param in ontology.EXTENDED_PARAMETERS:
        for f in param.fields:
            assert costs.classes_for_field(param, f)


def test_the_first_class_keeps_its_prompt_and_so_its_saved_locator_answers():
    # The recorded answers under samples/ are keyed by this hash.
    assert extract.prompt_fingerprint() == extract.prompt_fingerprint(BASE) == "801b179cc4d5"
    assert extract.prompt_fingerprint(EXT) != extract.prompt_fingerprint(BASE)


def test_the_extended_prompt_lists_the_new_parameters_and_the_first_one_does_not():
    assert "PARAM-14" in extract.build_system_prompt(EXT) and "PARAM-09" in extract.build_system_prompt(EXT)
    assert "PARAM-09" not in extract.build_system_prompt(BASE)


def test_a_store_for_the_extended_class_never_replays_the_first_class_answers(tmp_path):
    class Doc:
        sha256 = "abc"

    first = extract.ResponseStore(tmp_path).key_for(Doc())
    extended = extract.ResponseStore(tmp_path, equipment_class=EXT).key_for(Doc())
    assert first != extended


def test_the_locator_reads_the_class_from_the_manifest(tmp_path):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"equipment_class": EXT}), encoding="utf-8")
    assert extract.manifest_equipment_class(folder) == EXT
    assert extract.make_locator(folder, "replay").store.equipment_class == EXT
    assert extract.manifest_equipment_class(tmp_path / "missing") == BASE


def test_the_fingerprint_follows_the_class(tmp_path):
    base = run_fingerprint(PACK, "replay")["parts"]
    folder = tmp_path / "ext"
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["equipment_class"], manifest["rate_table"] = EXT, "mv_gis_switchgear_ext_v1"
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ext = run_fingerprint(folder, "replay")["parts"]
    assert ext["ontology"] != base["ontology"] and ext["prompt"] != base["prompt"]


# --- rate tables belong to a class ---------------------------------------------------------------------

def test_a_table_without_a_class_prices_the_first_class_and_the_new_table_the_extended_one():
    assert costs.load_rate_table("mv_gis_switchgear_v2").equipment_class == BASE
    table = costs.load_rate_table("mv_gis_switchgear_ext_v1")
    assert table.equipment_class == EXT
    # Delivery, LD, liability and altitude are unrated (the first three are exposures, not adders);
    # the two scope exclusions are priced.
    assert {"PARAM-09.lead_time_long", "PARAM-10.ld_cap_short", "PARAM-14.altitude_short"} <= table.unpriced_classes
    assert not {"PARAM-12.spares_missing", "PARAM-13.supervision_missing"} & table.unpriced_classes
    assert {"PARAM-12.spares_missing", "PARAM-13.supervision_missing"} <= set(table.deviation_rates)


def test_the_extended_table_prices_the_first_eight_exactly_as_v2_does():
    old, new = costs.load_rate_table("mv_gis_switchgear_v2"), costs.load_rate_table("mv_gis_switchgear_ext_v1")
    assert {k: v for k, v in new.deviation_rates.items() if k < "PARAM-09"} == old.deviation_rates
    assert new.base_labour_rate == old.base_labour_rate


def test_a_manifest_whose_table_prices_another_class_fails_at_intake(tmp_path):
    manifest = TenderManifest(tender_id="T", title="t", client="c", equipment_class=EXT,
                              rate_table="mv_gis_switchgear_v2", documents=[], engineering_estimates={})
    with pytest.raises(IngestFailed, match="prices equipment class"):
        stage_parsed(tmp_path, manifest)


# --- the scope parser ------------------------------------------------------------------------------

def scope(quote, name):
    value = parse_field("scope", quote, name)
    return None if value is None else (value.value, value.display)


@pytest.mark.parametrize("quote, name, expected", [
    ("Two years of commissioning spares are included in the price.", "spares_included", (1, "included")),
    ("The price includes a set of commissioning spares.", "spares_included", (1, "included")),
    ("Commissioning spares are quoted separately.", "spares_included", (0, "not included")),
    ("Commissioning spares are excluded from the price.", "spares_included", (0, "not included")),
    ("Spare parts are optional and priced on request.", "spares_included", (0, "not included")),
    ("Site supervision for erection and commissioning is included.", "supervision_included", (1, "included")),
    ("Supervision of erection is by others.", "supervision_included", (0, "not included")),
    ("Site supervision shall be offered separately.", "supervision_included", (0, "not included")),
    # An exclusion anywhere beats an inclusion.
    ("Site supervision of erection is included; commissioning supervision is excluded.",
     "supervision_included", (0, "not included")),
    # Silent: nothing about the subject, or the subject without a statement.
    ("The panels are painted grey.", "spares_included", None),
    ("A list of recommended spare parts shall accompany the offer.", "spares_included", None),
    ("Supervision follows the agreed procedure.", "supervision_included", None),
    # Another field's subject is not read.
    ("Commissioning spares are included in the price.", "supervision_included", None),
])
def test_scope(quote, name, expected):
    assert scope(quote, name) == expected


def test_an_unknown_scope_field_reads_nothing():
    assert scope("Everything is included.", "anything_else") is None


def test_the_standards_sweep_reads_design_and_test_wording_only():
    from antova_ingest import sweep

    standards = ontology.params_by_id_for(EXT)["PARAM-15"]
    assert sweep.names_parameter(standards, "Designed and type-tested to IEC 62271-200:2021 and IEC 62271-1:2017.")
    assert sweep.names_parameter(standards, "The switchgear complies with IEC 62271-200.")
    assert not sweep.names_parameter(standards, "IEC 62271-200: Metal-enclosed switchgear and controlgear.")
    assert not sweep.names_parameter(standards, "The supplier operates a quality system certified to ISO 9001.")
    assert not sweep.names_parameter(standards, "Our offer is 100% compliant with MR-33kV-GIS-Rev4.")


@pytest.mark.parametrize("quote, expected", [
    ("Designed to IEC 62271-200:2021, except clause 6.2.", True),
    ("Partially compliant with IEC 61850.", True),
    ("Compliance with IEC 62271-200 is optional.", True),
    ("Designed to IEC 62271-200:2021.", False),
    ("Delivery is excluded.", False),  # an exception, but no standard named
])
def test_a_standard_exception_is_recognised(quote, expected):
    from antova_ingest.values import standard_exception

    assert standard_exception(quote) is expected
