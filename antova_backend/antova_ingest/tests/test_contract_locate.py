"""Contract documents are located with the contract prompt, behind the zero-retention guard."""

import copy
import json
import shutil

import pytest

from antova_ingest import artifacts, config, extract
from antova_ingest.contract_ontology import CONTRACT_PARAMETERS
from antova_ingest.extract import ModelLocator, ResponseStore
from antova_ingest.parse import load_manifest, parse_document
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, FakeAnthropic, fake_message, load_expected

FA = "FA-vendor-a.pdf"
NORDWERK = "Bid_vendor-a_Nordwerk.pdf"
MR = "MR-33kV-GIS-Rev4.pdf"
BIDS = {NORDWERK, "Bid_vendor-b_VoltSwitch.pdf", "Bid_vendor-c_Deltaport.pdf"}
REFUSED = (f"{FA}: not read. Contract documents go only to a model provider with a zero-retention "
           f"agreement; set ANTOVA_ZERO_RETENTION_REF.")
CT_IDS = {p.id for p in CONTRACT_PARAMETERS}


class Recording:
    """A locator that sends text to a provider (as far as the guard knows), answering from the answer key."""

    sends_text = True
    model = "recording-model"
    ingest_mode_label = "answer_key"  # the located artifact then matches the answer key's own run

    def __init__(self, expected):
        self.inner = AnswerKeyLocator(expected)
        self.calls: list[tuple[str, bool]] = []

    def locate(self, doc, *, contract=False):
        self.calls.append((doc.entry.file, contract))
        return self.inner.locate(doc, contract=contract)


@pytest.fixture
def expected():
    return load_expected()


@pytest.fixture(autouse=True)
def no_reference(monkeypatch):
    monkeypatch.delenv("ANTOVA_ZERO_RETENTION_REF", raising=False)


@pytest.fixture(scope="module")
def baseline():
    return run_ingest(PACK, locator=AnswerKeyLocator(load_expected()))


def _pack(tmp_path, synthetic=False):
    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    shutil.copy(folder / NORDWERK, folder / FA)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["synthetic"] = synthetic
    manifest["documents"].append(
        {"file": FA, "role": "framework_agreement", "counterparty_id": "vendor-a", "dated": "2025-03-01"})
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _statuses(run):
    return {(vid, p.id): cell.status for p in run.tender.parameters for vid, cell in p.vendors.items()}


def test_zero_retention_reference_reads_the_environment(monkeypatch):
    assert config.zero_retention_reference() is None
    monkeypatch.setenv("ANTOVA_ZERO_RETENTION_REF", "   ")
    assert config.zero_retention_reference() is None
    monkeypatch.setenv("ANTOVA_ZERO_RETENTION_REF", "  DPA-2026-11 ")
    assert config.zero_retention_reference() == "DPA-2026-11"


def test_contract_docs_refused_without_reference(tmp_path, expected, baseline):
    locator = Recording(expected)
    run = run_ingest(_pack(tmp_path), locator=locator)
    assert FA not in {file for file, _ in locator.calls}
    assert run.warnings.count(REFUSED) == 1
    # Bids and the MR are still located with the equipment prompt; no contract text goes anywhere.
    assert {file for file, contract in locator.calls if not contract} == BIDS | {MR}
    assert not any(contract for _, contract in locator.calls)
    for bid in BIDS:
        assert any(w.startswith(f"{bid}: contract terms not read.") for w in run.warnings)
    located = json.loads(run.artifacts["located"].body)
    assert FA not in located["outcomes"] and "contract_outcomes" not in located
    assert _statuses(run) == _statuses(baseline)
    assert run.fingerprint["parts"]["zero_retention_ref"] == ""


def test_contract_docs_allowed_with_reference(tmp_path, expected, baseline, monkeypatch):
    monkeypatch.setenv("ANTOVA_ZERO_RETENTION_REF", "DPA-2026-11")
    locator = Recording(expected)
    run = run_ingest(_pack(tmp_path), locator=locator)
    assert (FA, True) in locator.calls and (FA, False) not in locator.calls
    for bid in BIDS:  # a bid is located twice: equipment terms, then contract terms
        assert (bid, False) in locator.calls and (bid, True) in locator.calls
    assert (MR, True) not in locator.calls
    located = json.loads(run.artifacts["located"].body)
    assert set(located["contract_outcomes"]) == BIDS | {FA}
    assert FA not in located["outcomes"]
    assert set(located["contract_outcomes"][FA]["param_ids"]) == CT_IDS
    assert not any("not read" in w for w in run.warnings)
    assert run.fingerprint["parts"]["zero_retention_ref"] == "DPA-2026-11"
    assert _statuses(run) == _statuses(baseline)


def test_synthetic_manifest_bypasses_guard(tmp_path, expected):
    locator = Recording(expected)
    run = run_ingest(_pack(tmp_path, synthetic=True), locator=locator)
    assert (FA, True) in locator.calls
    assert REFUSED not in run.warnings


def test_answer_key_locator_never_guarded(tmp_path, expected):
    locator = AnswerKeyLocator(expected)
    assert locator.sends_text is False
    run = run_ingest(_pack(tmp_path), locator=locator)
    assert FA in locator.contract_calls and FA not in locator.calls
    assert REFUSED not in run.warnings


def test_a_locator_that_does_not_say_is_treated_as_sending_text(tmp_path, expected):
    # Fail closed: a locator without sends_text could be calling a provider.
    class Quiet:
        def __init__(self):
            self.inner = AnswerKeyLocator(expected)

        def locate(self, doc, *, contract=False):
            assert doc.entry.file != FA
            return self.inner.locate(doc, contract=contract)

    run = run_ingest(_pack(tmp_path), locator=Quiet())
    assert REFUSED in run.warnings


def test_contract_evidence_never_mixes_with_equipment_evidence(tmp_path, expected, baseline):
    bid_quote = expected["ideal_locate"][NORDWERK]["candidates"][0]
    expected = copy.deepcopy(expected)
    expected["ideal_contract_locate"] = {NORDWERK: {
        "page_sections": [], "tender_facts": [], "headline_price": None, "cover_claim": None,
        "candidates": [
            {"param_id": "CT-03", "page": bid_quote["page"], "quote": bid_quote["quote"]},
            # An equipment id in the contract pass is not that pass's to answer: dropped.
            {"param_id": "PARAM-01", "page": bid_quote["page"], "quote": bid_quote["quote"]},
        ]}}
    run = run_ingest(_pack(tmp_path, synthetic=True), locator=AnswerKeyLocator(expected))
    verified = json.loads(run.artifacts["verified"].body)
    contract_rows = verified["contract"]["citations"][NORDWERK]
    assert [row["param_id"] for row in contract_rows] == ["CT-03"]
    assert verified["contract"]["rejected"] == 1
    for rows in verified["citations"].values():
        assert not any(row["param_id"].startswith("CT-") for row in rows)
    assert FA not in verified["citations"] and FA not in verified["sweep_hits"]
    resolved = json.loads(run.artifacts["resolved"].body)
    for items in resolved["evidence"].values():
        assert not any(e["param_id"].startswith("CT-") for e in items)
    assert not any(key.split("|")[1].startswith("CT-") for key in json.loads(run.artifacts["checked"].body)["resolutions"])
    assert _statuses(run) == _statuses(baseline)


def test_a_pack_without_contract_documents_has_no_contract_pass(expected, baseline):
    locator = Recording(expected)
    run = run_ingest(PACK, locator=locator)
    assert not any(contract for _, contract in locator.calls)
    assert {s: a.sha256 for s, a in run.artifacts.items()} == {s: a.sha256 for s, a in baseline.artifacts.items()}


def test_fingerprint_parts_name_the_provider_and_model(monkeypatch):
    parts = artifacts.run_fingerprint(PACK, "replay")["parts"]
    for name in ("locate_provider", "locate_model", "zero_retention_ref", "contract_ontology"):
        assert name in parts
    assert parts["zero_retention_ref"] == ""
    named = artifacts.run_fingerprint(PACK, "replay", locate_provider="anthropic", locate_model="m-1")["parts"]
    assert (named["locate_provider"], named["locate_model"]) == ("anthropic", "m-1")
    monkeypatch.setenv("ANTOVA_ZERO_RETENTION_REF", "DPA-2026-11")
    assert artifacts.run_fingerprint(PACK, "replay")["parts"]["zero_retention_ref"] == "DPA-2026-11"


def test_run_ingest_records_the_locators_provider(expected):
    run = run_ingest(PACK, locator=AnswerKeyLocator(expected))
    assert run.fingerprint["parts"]["locate_provider"] == "answer_key"


def _doc(name=NORDWERK):
    entry = next(e for e in load_manifest(PACK).documents if e.file == name)
    return parse_document(PACK, entry)


def test_store_keys_differ_by_prompt(tmp_path):
    store = ResponseStore(tmp_path)
    doc = _doc()
    assert store.key_for(doc) != store.key_for(doc, contract=True)
    assert store.key_for(doc, contract=False) == store.key_for(doc)


def test_model_locator_contract_pass_sends_the_contract_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTOVA_LOCATOR_MODEL", "test-model")
    body = json.dumps({"page_sections": [], "candidates": [], "tender_facts": [], "headline_price": None,
                       "cover_claim": None, "not_stated": []})
    client = FakeAnthropic(fake_message(body))
    store = ResponseStore(tmp_path)
    locator = ModelLocator(store, "live", client_factory=lambda: client)
    assert locator.sends_text is True
    doc = _doc()
    locator.locate(doc, contract=True)
    assert client.stream_calls[-1]["system"] == extract.build_contract_prompt()
    saved = json.loads((tmp_path / f"{store.key_for(doc, contract=True)}.json").read_text(encoding="utf-8"))
    assert saved["prompt_fingerprint"] == extract.contract_prompt_fingerprint()
    assert not (tmp_path / f"{store.key_for(doc)}.json").exists()
    # Replayed from its own key; the equipment pass still has no answer.
    replay = ModelLocator(store, "replay")
    assert replay.locate(doc, contract=True).from_store
    with pytest.raises(extract.ReplayMiss):
        replay.locate(doc)


def test_chat_locator_contract_pass_has_its_own_prompt_and_key(tmp_path, monkeypatch):
    from antova_ingest.chat_locator import ChatCompletionsLocator, schema_instruction

    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    locator = ChatCompletionsLocator.from_env(folder, "replay")
    assert locator.sends_text is True and locator.provider == "explabs"
    assert locator._system(contract=True) == extract.build_contract_prompt() + schema_instruction()
    assert locator._system() == extract.build_system_prompt() + schema_instruction()
    doc = _doc()
    assert locator.store.key_for(doc) != locator.store.key_for(doc, contract=True)
    assert locator.store.contract_salt and locator.store.contract_salt != locator.store.salt


def test_a_frozen_budget_alone_triggers_the_bids_contract_pass(tmp_path, expected, baseline):
    # Ruling R23: no FA and no owner terms, but a frozen budget: the buyer wants an approval sheet.
    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["project"] = {"frozen_budget": {"amount": "2500000", "currency": "EUR"}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    locator = Recording(expected)
    run = run_ingest(folder, locator=locator)
    assert {file for file, contract in locator.calls if contract} == BIDS
    located = json.loads(run.artifacts["located"].body)
    assert set(located["contract_outcomes"]) == BIDS
    assert _statuses(run) == _statuses(baseline)


def test_a_frozen_budget_without_a_reference_holds_the_bids_contract_terms_back(tmp_path, expected):
    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["synthetic"] = False
    manifest["project"] = {"frozen_budget": {"amount": "2500000", "currency": "EUR"}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    locator = Recording(expected)
    run = run_ingest(folder, locator=locator)
    assert not any(contract for _, contract in locator.calls)
    assert sum(w.endswith("contract terms not read. Contract documents go only to a model provider with a "
                          "zero-retention agreement; set ANTOVA_ZERO_RETENTION_REF.") for w in run.warnings) == 3
