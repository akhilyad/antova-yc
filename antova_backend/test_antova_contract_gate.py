"""The per-vendor contract gate: its open items, verify and withdraw, and the contract payload.

All names, numbers and clause wording here are invented.
"""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_briefs_api import load_tender
from antova_contract_api import gate_open_items
from antova_db import ContractGateRecord, SessionLocal, TenderRepository, fa_version, init_db
from antova_ingest.artifacts import make_artifact, sha256_of
from antova_ingest.pipeline import run_ingest
from antova_ingest.tests.fakes import PACK, AnswerKeyLocator, load_expected
from antova_review import CONTRACT_MESSAGES, open_items, review_summary
from antova_service import persist

TENDER_ID = "TENDER-2026-402"
REVIEW = f"/api/review/tenders/{TENDER_ID}"
GATE = f"{REVIEW}/vendors/vendor-b/contract"
FA_ID = "vendor-b|CT-03|fa|warranty_months"
REVIEWERS = {
    "pia": {"name": "Pia Procura", "token": "tok-procurement", "roles": ["procurement"]},
    "lea": {"name": "Lea Legal", "token": "tok-legal", "roles": ["legal"]},
}
PIA = {REVIEW_HEADER: "tok-procurement"}


def _citation(filename, page, quote):
    return {"filename": filename, "page": page, "bbox": [0, 0, 1, 1], "quote": quote, "section": "commercial",
            "paragraph": "", "match": "exact", "rects": []}


def _finding(id_, param_id, category, offered):
    citations = [_citation("Bid_vendor-b_VoltSwitch.pdf", 7, f"Warranty: {offered} months.")]
    return {"id": id_, "vendor_id": "vendor-b", "param_id": param_id, "category": category, "severity": "medium",
            "message": f"The offer gives {offered} months.", "money": [], "gap": None, "citations": citations,
            "evidence_hash": sha256_of({"values": {"offered": offered}, "citations": citations})}


def _term(param_id, state, flags=()):
    return {"param_id": param_id, "vendor_id": "vendor-b", "state": state, "offered": None, "fa": None,
            "owner": None, "flags": list(flags), "fa_present": True, "ld_exclusion_passages": []}


def _contracted(offered=24, category="fa_conformance"):
    return {
        "terms": [_term("CT-17", "cited"), _term("CT-03", "cited"), _term("CT-12", "cited"), _term("CT-02", "conflict"),
                  _term("CT-01", "cited", [{"code": "LOW_CONFIDENCE", "message": "Check the LD rate."}])],
        "checks": [{"param_id": "CT-03", "vendor_id": "vendor-b", "baseline": "fa", "field": "warranty_months",
                    "passed": False, "required": "36", "offered": str(offered), "required_citations": [],
                    "offered_citations": [], "reason": ""}],
        "gaps": [], "budget": {},
        "findings": [_finding(FA_ID, "CT-03", category, offered)],
        "not_checked": [{"vendor_id": "vendor-b", "baseline": "owner", "reason": "no owner contract"}],
    }


@pytest.fixture(scope="module")
def ingested():
    return run_ingest(PACK, locator=AnswerKeyLocator(load_expected()))


def _persist(ingested, clear=True, **contracted):
    result = dataclasses.replace(ingested, artifacts={
        **ingested.artifacts, "contracted": make_artifact("contracted", _contracted(**contracted))})
    db = SessionLocal()
    try:
        persist(result, db)
        if clear:
            for table in ("cell_reviews", "adder_overrides", "finding_dispositions"):
                db.execute(text(f"DELETE FROM {table} WHERE tender_id = :id"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client(ingested, monkeypatch):
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    _persist(ingested)
    yield TestClient(app)
    _persist(ingested)


def _run_id(client):
    return client.get(GATE, headers=PIA).json()["run_id"]


def _verify(client, run_id=None):
    return client.post(f"{GATE}/verify", json={"run_id": run_id or _run_id(client)}, headers=PIA)


def _review(client, param_id, action="approve"):
    return client.put(f"{REVIEW}/reviews/{param_id}/vendor-b", json={"action": action, "note": "checked"},
                      headers=PIA)


def _accept(client, token="tok-procurement"):
    return client.put(f"{GATE.rsplit('/', 1)[0]}/findings/{FA_ID}/disposition",
                      json={"disposition": "accept", "reason": "matches the FA"}, headers={REVIEW_HEADER: token})


def _items(response):
    assert response.status_code == 409
    return [(i["kind"], i["param_id"], i.get("finding_id")) for i in response.json()["open_items"]]


def test_contract_payload_rows_mr_checks_and_gate(client):
    body = client.get(GATE, headers=PIA).json()
    assert [r["param_id"] for r in body["rows"]] == ["CT-01", "CT-02", "CT-03", "CT-12", "CT-17"]  # ontology order
    row = {r["param_id"]: r for r in body["rows"]}
    assert set(row["CT-01"]) >= {"param_id", "name", "offered", "fa", "owner", "mr", "state", "checks"}
    assert row["CT-12"]["mr"]["param_id"] == "PARAM-06"  # TBE_EQUIVALENT
    assert row["CT-01"]["mr"] is None  # its equivalent, PARAM-10, is not in this tender
    assert row["CT-17"]["mr"] is None
    assert [c["field"] for c in row["CT-03"]["checks"]] == ["warranty_months"]
    assert (row["CT-02"]["state"], row["CT-02"]["review"]) == ("conflict", "open")
    assert body["not_checked"] == [{"vendor_id": "vendor-b", "baseline": "owner", "reason": "no owner contract"}]
    assert body["gate"]["status"] == "DRAFT" and len(body["gate"]["open_items"]) == 4


def test_verify_refused_with_unreviewed_cells_and_undecided_finding(client):
    response = _verify(client)
    assert response.json()["detail"] == "Resolve the open items before you sign off."
    assert _items(response) == [("contract_cell", "CT-01", None), ("contract_cell", "CT-02", None),
                                ("contract_cell", "CT-17", None), ("finding", "CT-03", FA_ID)]
    messages = {i["param_id"]: i["message"] for i in response.json()["open_items"] if i["kind"] == "contract_cell"}
    assert messages["CT-17"] == CONTRACT_MESSAGES["precedence"]  # the reviewer confirms the precedence order
    assert messages["CT-02"] == CONTRACT_MESSAGES["conflict"]


def _resolve_all(client):
    for param_id in ("CT-01", "CT-02", "CT-17"):
        assert _review(client, param_id).status_code == 200
    assert _accept(client).status_code == 200


def test_unreviewed_ct17_alone_blocks_verify(client):
    _resolve_all(client)
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM cell_reviews WHERE tender_id = :id AND param_id = 'CT-17'"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()
    assert _items(_verify(client)) == [("contract_cell", "CT-17", None)]


def test_rejected_cell_blocks_verify(client):
    _resolve_all(client)
    assert _review(client, "CT-02", action="reject").json()["state"] == "rejected"
    assert _items(_verify(client)) == [("contract_cell", "CT-02", None)]


def test_stale_disposition_blocks_verify(client, ingested):
    _resolve_all(client)
    _persist(ingested, clear=False, offered=18)  # the finding's evidence changed; reviews and decisions kept
    kinds = [k for k, _, _ in _items(_verify(client))]
    assert "stale_disposition" in kinds and "finding" not in kinds


def test_fa_change_since_ingest_blocks_verify(client):
    _resolve_all(client)
    db = SessionLocal()
    try:
        db.execute(text("UPDATE contract_gates SET fa_version = 'an-older-agreement' "
                        "WHERE tender_id = :id AND vendor_id = 'vendor-b'"), {"id": TENDER_ID})
        db.commit()
    finally:
        db.close()
    response = _verify(client)
    assert _items(response) == [("fa_changed", None, None)]
    assert response.json()["open_items"][0]["message"] == (
        "The framework agreement for this vendor changed in another tender. Re-run this tender.")


def test_verify_succeeds_once_resolved_and_leaves_tbe_status(client):
    _resolve_all(client)
    response = _verify(client)
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["verified_by"]) == ("VERIFIED", "pia")
    db = SessionLocal()
    try:
        assert TenderRepository(db).get_meta(TENDER_ID).status == "DRAFT"  # the TBE status is its own
    finally:
        db.close()
    assert _review(client, "CT-02").status_code == 409  # frozen while VERIFIED
    assert client.post(f"{GATE}/withdraw", headers=PIA).json() == {"status": "DRAFT"}
    assert _review(client, "CT-02").status_code == 200


def test_verify_with_an_old_run_is_refused(client):
    _resolve_all(client)
    response = _verify(client, run_id="an-old-run")
    assert response.status_code == 409 and response.json()["code"] == "run_changed"


def test_ct_review_leaves_tbe_open_items_and_summary(client):
    def tbe():
        db = SessionLocal()
        try:
            loaded = load_tender(db, TENDER_ID)
            return (len(open_items(loaded.tender, loaded.overrides, loaded.reviews)),
                    review_summary(loaded.tender, loaded.reviews))
        finally:
            db.close()

    before = tbe()
    _resolve_all(client)
    assert _verify(client).status_code == 200
    assert tbe() == before


def test_unknown_finding_category_is_a_clear_error(client, ingested):
    _persist(ingested, category="a-category-no-rule-names")
    response = _accept(client)
    assert response.status_code == 422
    assert response.json() == {"detail": "No approver rule names who may accept this finding. "
                                         "Add its category to the approver rules."}


@pytest.mark.parametrize("method,path", [
    ("get", GATE), ("post", f"{GATE}/verify"), ("post", f"{GATE}/withdraw"),
    ("get", f"{REVIEW}/vendors/vendor-b/dispositions"),
    ("put", f"{REVIEW}/vendors/vendor-b/findings/{FA_ID}/disposition"),
])
def test_contract_routes_need_the_review_token(client, method, path):
    kwargs = {"json": {"run_id": "x", "disposition": "accept", "reason": "r"}} if method != "get" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    assert getattr(client, method)(path, headers={REVIEW_HEADER: "not-a-token"}, **kwargs).status_code == 401


# --- the framework agreement changed in another tender -----------------------------------------

@pytest.fixture
def db_engine(tmp_path):
    from sqlalchemy import create_engine
    bind = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(bind)
    return bind


def test_fa_change_in_tender_b_makes_tender_a_stale(db_engine):
    from test_antova_contract_db import AMENDMENT, BID, CONTRACTED, FA, _result
    from antova_ingest.tests.test_db import DOCS, _tender

    with Session(db_engine) as session:
        persist(_result(_tender(), [*DOCS[:1], BID, FA], CONTRACTED), session)
        assert not [i for i in gate_open_items(session, "TENDER-T1", "vendor-b") if i.kind == "fa_changed"]
        other = _tender().model_copy(update={"id": "TENDER-T2"})
        TenderRepository(session).save_tender(other, [FA, AMENDMENT], _tender_time())
        stale = [i for i in gate_open_items(session, "TENDER-T1", "vendor-b") if i.kind == "fa_changed"]
        assert [i.message for i in stale] == [CONTRACT_MESSAGES["fa_changed"]]
        # vendor-a has no agreement, at ingest or now: None against None is unchanged.
        assert session.get(ContractGateRecord, ("TENDER-T1", "vendor-a")).fa_version is None
        assert not [i for i in gate_open_items(session, "TENDER-T1", "vendor-a") if i.kind == "fa_changed"]


def _tender_time():
    from datetime import datetime, timezone
    return datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)


def test_fa_version_is_none_without_agreement(db_engine):
    with Session(db_engine) as session:
        assert fa_version(session, "CP-NOBODY") is None
