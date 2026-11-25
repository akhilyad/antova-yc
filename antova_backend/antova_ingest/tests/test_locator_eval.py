"""The locator scorecard: recall against the answer key, rejected quotes, and the pipeline on stored answers."""

import copy
import json
import shutil
from decimal import Decimal

import pytest

from antova_ingest import locator_eval
from antova_ingest.extract import LocateOutcome, LocateResponse, ResponseStore
from antova_ingest.parse import load_manifest, parse_document
from antova_ingest.tests.fakes import PACK, load_expected

VOLTSWITCH = "Bid_vendor-b_VoltSwitch.pdf"


def _pack(tmp_path):
    folder = tmp_path / PACK.name
    shutil.copytree(PACK, folder, ignore=shutil.ignore_patterns("runs", "saved_responses"))
    return folder


def _store(folder, answers: dict[str, dict]):
    """Save `answers` (file -> locate response) as if the model had given them."""
    store = ResponseStore(folder / "saved_responses")
    for entry in load_manifest(folder).documents:
        if entry.file not in answers:
            continue
        doc = parse_document(folder, entry)
        response = LocateResponse.model_validate(answers[entry.file])
        store.save(store.key_for(doc), LocateOutcome(response, 12_000, 3_000, False, "test-model"), doc)


@pytest.fixture
def ideal():
    return copy.deepcopy(load_expected()["ideal_locate"])


def test_the_answer_keys_own_answers_score_perfectly(tmp_path, ideal):
    folder = _pack(tmp_path)
    _store(folder, ideal)
    pack = locator_eval.score_pack(folder, locator_eval.stored_locator(folder, live=False))
    assert pack["missing"] == []
    assert len(pack["documents"]) == 4
    for d in pack["documents"]:
        assert (d["rejected"], d["extra"]) == (0, 0), d["file"]
        assert d["recall"] in (1.0, None), d["file"]
        assert d["headline_ok"] in (True, None) and d["cover_ok"] in (True, None)
    assert all(row["matched"] == row["expected"] for row in pack["per_param"].values())
    assert pack["pipeline"]["ran"] and pack["pipeline"]["mismatches"] == []
    assert pack["pipeline"]["status_ok"] == pack["pipeline"]["cells"]
    # 4 documents, each recorded at 12,000 input and 3,000 output tokens ($4 / $20 per million).
    assert Decimal(pack["cost_usd"]) == Decimal("0.4320")


def test_a_missed_and_a_paraphrased_quote_show_up(tmp_path, ideal):
    folder = _pack(tmp_path)
    bid = ideal[VOLTSWITCH]
    dropped = next(c for c in bid["candidates"] if c["param_id"] == "PARAM-01")
    bid["candidates"].remove(dropped)
    paraphrased = next(c for c in bid["candidates"] if c["param_id"] != "PARAM-01")
    paraphrased["quote"] = "The vendor says it is fine, more or less."
    _store(folder, ideal)
    pack = locator_eval.score_pack(folder, locator_eval.stored_locator(folder, live=False))
    volt = next(d for d in pack["documents"] if d["file"] == VOLTSWITCH)
    assert volt["rejected"] == 1
    assert volt["matched"] == volt["expected"] - 2
    assert volt["recall"] < 1.0
    param01 = pack["per_param"]["PARAM-01"]
    assert param01["matched"] == param01["expected"] - 1
    others = [d for d in pack["documents"] if d["file"] != VOLTSWITCH]
    assert all(d["recall"] in (1.0, None) for d in others)
    assert pack["pipeline"]["ran"] and pack["pipeline"]["mismatches"]  # the miss reaches the result


def test_a_parameter_with_neither_a_candidate_nor_not_stated_is_reported_silent(tmp_path, ideal):
    folder = _pack(tmp_path)
    bid = ideal[VOLTSWITCH]
    bid["candidates"] = [c for c in bid["candidates"] if c["param_id"] != "PARAM-03"]
    bid["not_stated"] = []  # the contract says PARAM-03 needs one or the other
    _store(folder, ideal)
    pack = locator_eval.score_pack(folder, locator_eval.stored_locator(folder, live=False))
    volt = next(d for d in pack["documents"] if d["file"] == VOLTSWITCH)
    assert "PARAM-03" in volt["silent_params"]


def test_a_tender_without_saved_answers_is_reported_not_scored(tmp_path, capsys):
    folder = _pack(tmp_path)
    assert locator_eval.main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert out.count("not scored") == 4
    assert "Pipeline not run" in out


def test_live_needs_spend_to_confirm(tmp_path, capsys):
    folder = _pack(tmp_path)
    assert locator_eval.main([str(folder), "--live"]) == 2
    assert "--spend" in capsys.readouterr().err
    assert not (folder / "saved_responses").exists()  # nothing was called or saved


def test_live_without_credentials_reports_instead_of_calling(tmp_path, capsys):
    folder = _pack(tmp_path)  # conftest.py blanks the API key
    assert locator_eval.main([str(folder), "--live", "--spend"]) == 1
    assert "MissingCredentials" in capsys.readouterr().out


def test_the_report_and_json_forms(tmp_path, ideal, capsys):
    folder = _pack(tmp_path)
    _store(folder, ideal)
    assert locator_eval.main([str(folder)]) == 0
    report = capsys.readouterr().out
    assert "| parameter | matched | expected | recall |" in report
    assert "0 answer-key differences" in report
    assert locator_eval.main([str(folder), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["tender"] == PACK.name and data[0]["pipeline"]["ran"]


def test_an_api_error_is_reported_per_document_not_raised(tmp_path, capsys):
    import anthropic
    import httpx

    class Failing:
        model = "test-model"

        def locate(self, doc):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.test"))

    folder = _pack(tmp_path)
    pack = locator_eval.score_pack(folder, Failing())
    assert len(pack["missing"]) == 4 and "APIConnectionError" in pack["missing"][0]["reason"]
    assert pack["locator"] == "test-model" and pack["pipeline"]["ran"] is False


def _with_contract_documents(folder, synthetic):
    shutil.copy(folder / VOLTSWITCH, folder / "FA-vendor-b.pdf")
    shutil.copy(folder / "MR-33kV-GIS-Rev4.pdf", folder / "Owner-contract.pdf")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["synthetic"] = synthetic
    manifest["documents"] += [
        {"file": "FA-vendor-b.pdf", "role": "framework_agreement", "counterparty_id": "vendor-b", "dated": "2025-03-01"},
        {"file": "Owner-contract.pdf", "role": "owner_contract"},
    ]
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class _Recording:
    def __init__(self, stored):
        self.stored, self.model, self.sent = stored, stored.model, []
        self.sends_text = stored.sends_text

    def locate(self, doc, *, contract=False):
        self.sent.append((doc.entry.file, contract))
        return self.stored.locate(doc, contract=contract)


def test_contract_documents_are_held_back_without_a_zero_retention_reference(tmp_path, ideal, monkeypatch):
    # The scorecard sends contract text to a provider only past the same guard as the pipeline.
    monkeypatch.delenv("ANTOVA_ZERO_RETENTION_REF", raising=False)
    folder = _pack(tmp_path)
    _with_contract_documents(folder, synthetic=False)
    _store(folder, ideal)
    recording = _Recording(locator_eval.stored_locator(folder, live=False))
    pack = locator_eval.score_pack(folder, recording)
    assert {"FA-vendor-b.pdf", "Owner-contract.pdf"}.isdisjoint(file for file, _ in recording.sent)
    assert not any(contract for _, contract in recording.sent)  # nor the bids' contract terms
    assert pack["missing"] == [] and len(pack["documents"]) == 4
    assert pack["held"] == ["FA-vendor-b.pdf", "Owner-contract.pdf"]
    assert pack["pipeline"]["ran"] and pack["pipeline"]["mismatches"] == []
    assert "not located: FA-vendor-b.pdf" in locator_eval.format_report([pack])


def test_contract_documents_are_located_with_a_zero_retention_reference(tmp_path, ideal, monkeypatch):
    monkeypatch.setenv("ANTOVA_ZERO_RETENTION_REF", "DPA-2026-11")
    folder = _pack(tmp_path)
    _with_contract_documents(folder, synthetic=False)
    _store(folder, ideal)
    empty = LocateResponse(page_sections=[], candidates=[], tender_facts=[], headline_price=None, cover_claim=None)
    store = ResponseStore(folder / "saved_responses")
    for entry in load_manifest(folder).documents:
        if entry.role != "mr_spec":
            doc = parse_document(folder, entry)
            store.save(store.key_for(doc, contract=True), LocateOutcome(empty, 1_000, 100, False, "test-model"),
                       doc, contract=True)
    recording = _Recording(locator_eval.stored_locator(folder, live=False))
    pack = locator_eval.score_pack(folder, recording)
    assert ("FA-vendor-b.pdf", True) in recording.sent and ("FA-vendor-b.pdf", False) not in recording.sent
    assert recording.sent.count((VOLTSWITCH, True)) == 1  # remembered: the pipeline run reused it
    assert pack["held"] == [] and pack["missing"] == []
    assert pack["pipeline"]["ran"] and pack["pipeline"]["mismatches"] == []
