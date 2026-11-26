"""Schema v11: counterparties, projects, contract terms and gates, and their persistence.

All names and numbers here are invented.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import antova_db
from antova_db import (
    SCHEMA_VERSION, ContractGateRecord, ContractTermRecord, TenderRepository, VendorRecord, check_foreign_keys,
    fa_version, init_db, suggest_counterparty,
)
from antova_ingest.artifacts import make_artifact
from antova_ingest.tests.test_db import DOCS, _schema, _tender
from antova_ingest.types import IngestResult, OwnerTerms, SourceDocumentInfo
from antova_service import persist

V11_TABLES = {"counterparties", "projects", "contract_terms", "contract_gates", "finding_dispositions",
              "approver_removals", "deletion_log", "price_history"}
# (table, column) pairs v11 adds to tables that already exist.
V11_COLUMNS = {("tenders", "project_id"), ("vendors", "counterparty_id"), ("source_documents", "counterparty_id"),
               ("source_documents", "amends"), ("source_documents", "dated")}
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)

FA = SourceDocumentInfo("FA-Kestrel.pdf", "framework_agreement", None, "fa-sha-1", 12, [], counterparty_id="CP-KESTREL")
AMENDMENT = SourceDocumentInfo("FA-Kestrel-A1.pdf", "framework_agreement", None, "fa-sha-2", 2, [],
                               counterparty_id="CP-KESTREL", amends="FA-Kestrel.pdf", dated=date(2026, 6, 1))
BID = SourceDocumentInfo("b.pdf", "vendor_bid", "vendor-b", "def", 30, [4], counterparty_id="CP-KESTREL")


@pytest.fixture
def db_engine(tmp_path):
    bind = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(bind)
    return bind


def _split_top_level(body: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return parts + [current]


def _v10(tmp_path):
    """A v10 database holding a saved tender: the v11 schema minus what v11 added, with the rows copied over."""
    source = create_engine(f"sqlite:///{(tmp_path / 'v11.db').as_posix()}")
    init_db(source)
    with Session(source) as session:
        TenderRepository(session).save_tender(_tender(), DOCS, NOW)
    target = create_engine(f"sqlite:///{(tmp_path / 'v10.db').as_posix()}")
    with source.connect() as conn:
        rows = conn.execute(text("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL")).all()
    with target.begin() as conn:
        for kind, name, table, sql in rows:
            if table in V11_TABLES or name == "ix_vendors_counterparty_id":
                continue
            if kind == "table":
                head, body = sql.split("(", 1)
                body = body.rsplit(")", 1)[0]
                kept = [p for p in _split_top_level(body)
                        if not any(p.strip().startswith(f"{col} ") or f"({col})" in p.replace(" ", "")
                                   for tbl, col in V11_COLUMNS if tbl == table)]
                sql = f"{head}({','.join(kept)})"
            conn.execute(text(sql))
        conn.exec_driver_sql(f"ATTACH DATABASE '{(tmp_path / 'v11.db').as_posix()}' AS src")
        for kind, name, table, _sql in rows:
            if kind != "table" or table in V11_TABLES:
                continue
            columns = ", ".join(c["name"] for c in inspect(conn).get_columns(table))
            conn.execute(text(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM src.{table}"))
        conn.execute(text("UPDATE schema_version SET version = 10"))
    with target.begin() as conn:
        conn.exec_driver_sql("DETACH DATABASE src")
    return source, target


def test_v10_upgrades_to_v11_with_data_intact(tmp_path):
    fresh, old = _v10(tmp_path)
    tables = set(inspect(old).get_table_names())
    assert not tables & V11_TABLES
    assert "project_id" not in {c["name"] for c in inspect(old).get_columns("tenders")}
    init_db(old)
    check_foreign_keys(old)
    with old.connect() as conn:
        assert conn.execute(text("SELECT version FROM schema_version")).scalar() == SCHEMA_VERSION == 11
    assert V11_TABLES <= set(inspect(old).get_table_names())
    for table in ("tenders", "vendors", "source_documents"):
        assert _schema(old, table) == _schema(fresh, table)
    with Session(old) as session:
        repo = TenderRepository(session)
        tender = repo.load_tender("TENDER-T1")
        assert [v.id for v in tender.vendors] == ["vendor-b", "vendor-a"]
        assert len(repo.list_documents("TENDER-T1")) == 2
        assert session.query(VendorRecord).filter_by(tender_id="TENDER-T1", id="vendor-b").one().counterparty_id is None


def test_project_owner_terms_inherited(db_engine):
    with Session(db_engine) as session:
        repo = TenderRepository(session)
        repo.put_project("PRJ-HALDEN", "Halden Solar Park", "Halden Energy SPV",
                         OwnerTerms(law="Ruritanian law", warranty_months=24), at=NOW)
        inherits = _tender("TENDER-T1")
        inherits.project = {"project_id": "PRJ-HALDEN"}
        repo.save_tender(inherits, DOCS, NOW)
        own = _tender("TENDER-T2")
        own.project = {"project_id": "PRJ-HALDEN", "owner_terms": {"law": "Freedonian law"}}
        repo.save_tender(own, DOCS, NOW)
        alone = _tender("TENDER-T3")
        repo.save_tender(alone, DOCS, NOW)

        assert repo.owner_terms("TENDER-T1") == OwnerTerms(law="Ruritanian law", warranty_months=24)
        assert repo.owner_terms("TENDER-T2") == OwnerTerms(law="Freedonian law")
        assert repo.owner_terms("TENDER-T3") is None
        assert repo.get_meta("TENDER-T1") is not None
    check_foreign_keys(db_engine)


def _result(tender, documents, contracted):
    stats = {"ingest_mode": "replay", "synthetic": True, "stage_log": [], "run_id": "run-1",
             "run_fingerprint": "fp-1"}
    return IngestResult(tender=tender, documents=documents, stats=stats, warnings=[],
                        artifacts={"contracted": make_artifact("contracted", contracted)},
                        fingerprint={"fingerprint": "fp-1", "parts": {}})


CONTRACTED = {
    "terms": [
        {"param_id": "CT-01", "vendor_id": "vendor-b", "state": "cited", "offered": None, "fa": None, "owner": None,
         "flags": [], "fa_present": True, "ld_exclusion_passages": []},
        {"param_id": "CT-02", "vendor_id": "vendor-b", "state": "missing", "offered": None, "fa": None, "owner": None,
         "flags": [], "fa_present": True, "ld_exclusion_passages": []},
    ],
    "checks": [], "gaps": [], "budget": {},
    "findings": [{"id": "CF-vendor-b-CT-01", "vendor_id": "vendor-b", "param_id": "CT-01",
                  "category": "fa_conformance", "severity": "high"}],
    "not_checked": [],
}


def test_persist_writes_contract_terms_findings_and_draft_gates(db_engine):
    with Session(db_engine) as session:
        persist(_result(_tender(), [*DOCS[:1], BID, FA], CONTRACTED), session)
        terms = session.query(ContractTermRecord).filter_by(tender_id="TENDER-T1").all()
        assert sorted((t.vendor_id, t.param_id) for t in terms) == [("vendor-b", "CT-01"), ("vendor-b", "CT-02")]
        assert all(len(t.evidence_hash) == 64 for t in terms)
        gates = {g.vendor_id: g for g in session.query(ContractGateRecord).filter_by(tender_id="TENDER-T1")}
        assert set(gates) == {"vendor-b", "vendor-a"}  # every vendor with an offer
        assert {g.status for g in gates.values()} == {"DRAFT"}
        assert gates["vendor-b"].fa_version == fa_version(session, "CP-KESTREL")
        assert gates["vendor-a"].fa_version is None  # no counterparty, no agreement
        repo = TenderRepository(session)
        assert repo.contract_findings("TENDER-T1") == CONTRACTED["findings"]
        docs = {d.filename: d for d in session.query(antova_db.SourceDocumentRecord)}
        assert docs["FA-Kestrel.pdf"].counterparty_id == "CP-KESTREL"
        # The tender's TBE status is its own; the gates do not touch it.
        assert repo.get_meta("TENDER-T1").status == "DRAFT"
    check_foreign_keys(db_engine)


def test_persist_without_contract_stage_writes_no_terms(db_engine):
    with Session(db_engine) as session:
        persist(_result(_tender(), DOCS, {"terms": [], "checks": [], "gaps": [], "budget": {},
                                          "findings": [], "not_checked": []}), session)
        assert session.query(ContractTermRecord).count() == 0
        assert session.query(ContractGateRecord).count() == 0  # no contract approval for this tender
        assert TenderRepository(session).contract_findings("TENDER-T1") == []


def test_fa_version_changes_when_an_amendment_is_added(db_engine):
    with Session(db_engine) as session:
        repo = TenderRepository(session)
        repo.save_tender(_tender(), [*DOCS, FA], NOW)
        before = fa_version(session, "CP-KESTREL")
        assert len(before) == 64
        repo.save_tender(_tender(), [*DOCS, FA], NOW)
        assert fa_version(session, "CP-KESTREL") == before  # same documents, same version
        repo.save_tender(_tender(), [*DOCS, FA, AMENDMENT], NOW)
        after = fa_version(session, "CP-KESTREL")
        assert after != before
        stored = session.query(antova_db.SourceDocumentRecord).filter_by(filename="FA-Kestrel-A1.pdf").one()
        assert (stored.amends, stored.dated) == ("FA-Kestrel.pdf", date(2026, 6, 1))


def test_subsidiary_not_auto_linked(db_engine):
    """Review Focus 4: a sister company is suggested, never linked by name."""
    with Session(db_engine) as session:
        repo = TenderRepository(session)
        repo.put_counterparty("CP-KESTREL", "Kestrel Power Conversion AG", aliases=["Kestrel PC AG"], at=NOW)
        repo.put_counterparty("CP-OTHER", "Osprey Grid Systems GmbH", at=NOW)
        tender = _tender()
        tender.vendors[0].name = "Kestrel Power Conversion UK Ltd"
        repo.save_tender(tender, [*DOCS, FA], NOW)

        suggestions = suggest_counterparty(session, "Kestrel Power Conversion UK Ltd")
        assert [c.id for c in suggestions] == ["CP-KESTREL"]
        assert [c.id for c in suggest_counterparty(session, "kestrel pc ag")] == ["CP-KESTREL"]
        assert suggest_counterparty(session, "Unrelated Transformers SA") == []

        vendor = lambda: session.query(VendorRecord).filter_by(tender_id="TENDER-T1", id="vendor-b").one()  # noqa: E731
        assert vendor().counterparty_id is None
        repo.set_vendor_counterparty("TENDER-T1", "vendor-b", "CP-KESTREL")
        assert vendor().counterparty_id == "CP-KESTREL"
        repo.save_tender(tender, [*DOCS, FA], NOW)  # a re-ingest keeps the link a person set
        session.expire_all()
        assert vendor().counterparty_id == "CP-KESTREL"
