"""Price history import and the comparison against the buyer's past offers (Task 18).

All projects, names and prices here are invented.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from antova_api import app
from antova_auth import REVIEW_HEADER
from antova_db import PriceHistoryRecord, SessionLocal, init_db
from antova_ingest.price_history import CSV_COLUMNS, HeaderError, import_csv, price_comparison

D = Decimal
HEADER = ",".join(CSV_COLUMNS)
ROUTE = "/api/review/price-history"
REVIEWERS = {
    "pia": {"name": "Pia Procura", "token": "tok-procurement", "roles": ["procurement"]},
    "lea": {"name": "Lea Legal", "token": "tok-legal", "roles": ["legal"]},
}


def _row(project="Solar A", capacity_mwp="10", offer_date="2026-02-10", price="11000000", currency="EUR",
         equipment="pv_inverter", capacity_mva="", options=""):
    return f"{project},DE,{capacity_mwp},{capacity_mva},1.25,{equipment},Inverta,{offer_date},{price},{currency},{options}"


def _csv(*rows):
    return "\n".join([HEADER, *rows]) + "\n"


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    session.query(PriceHistoryRecord).delete()
    session.commit()
    yield session
    session.query(PriceHistoryRecord).delete()
    session.commit()
    session.close()


def _compare(db, **kw):
    args = {"equipment": "pv_inverter", "basis": "per_wp", "value": D("1.30"), "currency": "EUR",
            "offer_date": date(2026, 11, 20), **kw}
    return price_comparison(db, **args)


def test_median_of_three_per_wp(db):
    # 10 MWp at 11.0M, 12.0M, 14.0M EUR -> 1.10, 1.20, 1.40 EUR/Wp
    result = import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000", offer_date="2026-05-02"),
                                 _row(project="C", price="14000000", offer_date="2025-12-31")), "pia")
    assert (result.imported, result.rejected) == (3, [])
    comparison = _compare(db)
    assert comparison["median"] == D("1.2")
    assert isinstance(comparison["median"], Decimal)
    assert comparison["count"] == 3 and comparison["excluded_other_currency"] == 0
    assert comparison["window"] == {"from": "2025-Q4", "to": "2026-Q3"}
    assert [r["project"] for r in comparison["rows"]] == ["C", "Solar A", "B"]
    assert comparison["delta_pct"].quantize(D("0.01")) == D("8.33")


def test_rows_older_than_four_quarters_excluded(db):
    import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000"),
                        _row(project="Old", price="14000000", offer_date="2025-09-30")), "pia")
    assert _compare(db) is None


def test_other_currency_excluded_and_counted(db):
    import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000"),
                        _row(project="C", price="14000000"), _row(project="G", price="9000000", currency="GBP")), "pia")
    comparison = _compare(db)
    assert comparison["median"] == D("1.2") and comparison["excluded_other_currency"] == 1


def test_two_rows_is_none(db):
    import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000")), "pia")
    assert _compare(db) is None


def test_other_equipment_and_current_quarter_not_compared(db):
    import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000"),
                        _row(project="T", price="14000000", equipment="transformer"),
                        _row(project="Now", price="14000000", offer_date="2026-10-01")), "pia")
    assert _compare(db) is None


def test_per_kva_basis(db):
    rows = [_row(project=p, capacity_mwp="", capacity_mva="2", price=price, equipment="transformer")
            for p, price in (("A", "60000"), ("B", "70000"), ("C", "90000"))]
    import_csv(db, _csv(*rows), "pia")
    comparison = _compare(db, equipment="transformer", basis="per_kva", value=D("40"))
    assert comparison["median"] == D("35") and comparison["unit"] == "kVA"


def test_malformed_date_rejected_with_line_number(db):
    result = import_csv(db, _csv(_row(), _row(project="Bad", offer_date="10/02/2026"), _row(price="abc")), "pia")
    assert result.imported == 1
    assert [r["line"] for r in result.rejected] == [3, 4]
    assert "offer_date" in result.rejected[0]["reason"] and "price" in result.rejected[1]["reason"]
    assert db.query(PriceHistoryRecord).count() == 1


def test_option_lines_and_decimal_strings_stored(db):
    import_csv(db, _csv(_row(options="spares=12000.50;training=3000")), "pia")
    record = db.query(PriceHistoryRecord).one()
    assert json.loads(record.option_lines) == [{"label": "spares", "amount": "12000.50"},
                                               {"label": "training", "amount": "3000"}]
    assert (record.price, record.capacity_mwp, record.imported_by) == ("11000000", "10", "pia")
    bad = import_csv(db, _csv(_row(options="spares")), "pia")
    assert bad.rejected[0]["line"] == 2 and "label=amount" in bad.rejected[0]["reason"]


def test_wrong_header_refused(db):
    with pytest.raises(HeaderError):
        import_csv(db, "project,price\nA,1\n", "pia")


# --- route --------------------------------------------------------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("ANTOVA_REVIEWERS", json.dumps(REVIEWERS))
    return TestClient(app)


def _post(client, content, token="tok-procurement"):
    return client.post(ROUTE, files={"file": ("history.csv", content.encode(), "text/csv")},
                       headers={REVIEW_HEADER: token} if token else {})


def test_route_imports_and_reports(client, db):
    response = _post(client, _csv(_row(), _row(offer_date="2026-13-01")))
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "rejected": [{"line": 3, "reason": response.json()["rejected"][0]["reason"]}]}
    assert db.query(PriceHistoryRecord).one().imported_by == "pia"


def test_route_needs_procurement_role_and_token(client):
    assert _post(client, _csv(_row()), token="tok-legal").status_code == 403
    assert _post(client, _csv(_row()), token=None).status_code == 401


def test_route_bad_header_and_encoding(client):
    assert _post(client, "a,b\n1,2\n").status_code == 422
    response = client.post(ROUTE, files={"file": ("h.csv", b"\xff\xfe\x00bad", "text/csv")},
                           headers={REVIEW_HEADER: "tok-procurement"})
    assert response.status_code == 422


# --- review round 1 -----------------------------------------------------------------------------------------

def test_even_count_median_and_no_capacity_counted(db):
    import_csv(db, _csv(_row(price="11000000"), _row(project="B", price="12000000"),
                        _row(project="C", price="13000000"), _row(project="E", price="14000000"),
                        _row(project="N", capacity_mwp="", capacity_mva="5")), "pia")
    comparison = _compare(db)
    assert comparison["median"] == D("1.25") and comparison["count"] == 4
    assert comparison["excluded_no_capacity"] == 1


def test_bom_header_blank_line_and_wrong_column_count(db):
    result = import_csv(db, "\ufeff" + HEADER + "\n" + _row() + "\n\n" + "a,b,c\n", "pia")
    assert result.imported == 1
    assert result.rejected == [{"line": 4, "reason": "expected 11 columns, found 3"}]


@pytest.mark.parametrize("price,word", [("0", "greater than 0"), ("-5", "greater than 0"),
                                        ("1E+15", "too large"), ("Infinity", "not a number"), ("NaN", "not a number")])
def test_bad_prices_rejected(db, price, word):
    result = import_csv(db, _csv(_row(price=price)), "pia")
    assert result.imported == 0 and word in result.rejected[0]["reason"]


def test_long_cells_and_formula_injection_rejected(db):
    result = import_csv(db, _csv(_row(project="P" * 256), _row(equipment="E" * 129), _row(project="=HYPERLINK(1)"),
                                 _row(project="@cmd"), _row()), "pia")
    assert result.imported == 1
    reasons = [r["reason"] for r in result.rejected]
    assert "longer than 255" in reasons[0] and "longer than 128" in reasons[1]
    assert all("must not start with" in r for r in reasons[2:])


def test_row_cap(db, monkeypatch):
    from antova_ingest import price_history
    from antova_ingest.price_history import TooManyRows
    monkeypatch.setattr(price_history, "MAX_ROWS", 2)
    with pytest.raises(TooManyRows):
        import_csv(db, _csv(_row(), _row(), _row()), "pia")
    assert db.query(PriceHistoryRecord).count() == 0


def test_route_size_and_row_caps(client, db, monkeypatch):
    import antova_contract_api
    from antova_ingest import price_history
    monkeypatch.setattr(antova_contract_api, "PRICE_HISTORY_MAX_BYTES", 100)
    assert _post(client, _csv(_row(), _row())).status_code == 413
    monkeypatch.setattr(antova_contract_api, "PRICE_HISTORY_MAX_BYTES", 5 * 1024 * 1024)
    monkeypatch.setattr(price_history, "MAX_ROWS", 1)
    response = _post(client, _csv(_row(), _row()))
    assert response.status_code == 413 and "more than" in response.json()["detail"]
    assert db.query(PriceHistoryRecord).count() == 0
