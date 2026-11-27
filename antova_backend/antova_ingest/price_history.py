"""The buyer's own past offers (spec 7): a CSV import and the CAS comparison against them.

CSV columns, exactly and in this order (CSV_COLUMNS):
    project,country,capacity_mwp,capacity_mva,dc_ac_ratio,equipment,manufacturer,offer_date,price,currency,option_lines
`project`, `equipment`, `offer_date` (ISO YYYY-MM-DD), `price` and `currency` (three letters) are required; the
capacities and the DC/AC ratio are optional positive decimals; `option_lines` is `label=amount;label=amount` or
blank. A row that fails is reported with its line number (the header is line 1) and not imported; the others are.

Money is Decimal throughout, stored as Decimal strings; no float ever touches a price.

price_comparison: the price per Wp (capacity_mwp) or per kVA (capacity_mva) against the median of the same
equipment over the four complete calendar quarters before the quarter of `offer_date`, same currency only.
Rows in another currency are excluded and counted; fewer than MIN_ROWS comparable rows gives None.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Literal

from sqlalchemy.orm import Session

from antova_db import PriceHistoryRecord

CSV_COLUMNS = ("project", "country", "capacity_mwp", "capacity_mva", "dc_ac_ratio", "equipment", "manufacturer",
               "offer_date", "price", "currency", "option_lines")
MIN_ROWS = 3
QUARTERS = 4
_CURRENCY = re.compile(r"^[A-Z]{3}$")
# Units per basis: a MWp is 1,000,000 Wp, a MVA is 1,000 kVA.
BASIS_UNITS = {"per_wp": ("capacity_mwp", Decimal(1_000_000), "Wp"),
               "per_kva": ("capacity_mva", Decimal(1_000), "kVA")}


MAX_ROWS = 50_000
MAX_PRICE = Decimal("1E+12")
# Column widths of price_history; a longer cell is a rejected line, not a database error.
WIDTHS = {"project": 255, "country": 64, "equipment": 128, "manufacturer": 255, "capacity_mwp": 32,
          "capacity_mva": 32, "dc_ac_ratio": 32, "price": 32}
_FORMULA_START = ("=", "+", "-", "@")


class HeaderError(ValueError):
    """The CSV header is not exactly CSV_COLUMNS."""


class TooManyRows(ValueError):
    """The CSV has more than MAX_ROWS data rows."""


@dataclass(frozen=True)
class ImportResult:
    imported: int
    rejected: list[dict]


def _decimal(raw: str, name: str, *, required: bool = False, positive: bool = False) -> Decimal | None:
    raw = raw.strip()
    if not raw:
        if required:
            raise ValueError(f"{name} is required")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{name} {raw!r} is not a number") from None
    if not value.is_finite():
        raise ValueError(f"{name} {raw!r} is not a number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    if abs(value) >= MAX_PRICE:
        raise ValueError(f"{name} {raw!r} is too large")
    return value


def _option_lines(raw: str) -> list[dict]:
    lines = []
    for part in (p.strip() for p in raw.split(";")):
        if not part:
            continue
        label, sep, amount = part.partition("=")
        if not sep or not label.strip():
            raise ValueError(f"option line {part!r} is not label=amount")
        lines.append({"label": label.strip(), "amount": str(_decimal(amount, f"option line {label.strip()!r}",
                                                                    required=True))})
    return lines


def _parse_row(row: dict) -> dict:
    for name, width in WIDTHS.items():
        if len(row[name].strip()) > width:
            raise ValueError(f"{name} is longer than {width} characters")
    for name in ("project", "equipment", "manufacturer"):
        if row[name].strip().startswith(_FORMULA_START):
            raise ValueError(f"{name} must not start with = + - or @")
    project = row["project"].strip()
    equipment = row["equipment"].strip()
    if not project:
        raise ValueError("project is required")
    if not equipment:
        raise ValueError("equipment is required")
    raw_date = row["offer_date"].strip()
    try:
        offer_date = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError(f"offer_date {raw_date!r} is not a date (YYYY-MM-DD)") from None
    currency = row["currency"].strip().upper()
    if not _CURRENCY.match(currency):
        raise ValueError(f"currency {row['currency'].strip()!r} is not a three-letter code")
    values = {name: _decimal(row[name], name, positive=True) for name in ("capacity_mwp", "capacity_mva", "dc_ac_ratio")}
    price = _decimal(row["price"], "price", required=True, positive=True)
    return {"project": project, "country": row["country"].strip() or None, "equipment": equipment,
            "manufacturer": row["manufacturer"].strip() or None, "offer_date": offer_date, "price": str(price),
            "currency": currency, "option_lines": json.dumps(_option_lines(row["option_lines"])),
            **{k: (None if v is None else str(v)) for k, v in values.items()}}


def import_csv(db: Session, content: str, imported_by: str) -> ImportResult:
    """Import the valid rows of a price history CSV; report the others by line number. Commits."""
    reader = csv.reader(io.StringIO(content.lstrip("﻿")))
    header = next(reader, None)
    if header is None or tuple(h.strip() for h in header) != CSV_COLUMNS:
        raise HeaderError(f"the CSV header must be exactly: {','.join(CSV_COLUMNS)}")
    now = datetime.now(timezone.utc)
    imported, rejected, pending = 0, [], []
    for cells in reader:
        line = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        if imported + len(rejected) >= MAX_ROWS:
            db.rollback()
            raise TooManyRows(f"the CSV has more than {MAX_ROWS:,} rows; split it into smaller files")
        if len(cells) != len(CSV_COLUMNS):
            rejected.append({"line": line, "reason": f"expected {len(CSV_COLUMNS)} columns, found {len(cells)}"})
            continue
        try:
            fields = _parse_row(dict(zip(CSV_COLUMNS, cells)))
        except ValueError as exc:
            rejected.append({"line": line, "reason": str(exc)})
            continue
        pending.append(PriceHistoryRecord(**fields, imported_at=now, imported_by=imported_by))
        imported += 1
    db.add_all(pending)
    db.commit()
    return ImportResult(imported, rejected)


# --- comparison -------------------------------------------------------------------------------------------

def _quarter_index(day: date) -> int:
    return day.year * 4 + (day.month - 1) // 3


def _quarter_label(index: int) -> str:
    return f"{index // 4}-Q{index % 4 + 1}"


def unit_price(price: Decimal, capacity: Decimal, basis: str) -> Decimal:
    """The price per Wp or per kVA of a price over a capacity in MWp or MVA."""
    return price / (capacity * BASIS_UNITS[basis][1])


NO_CAPACITY = "the project capacity (MWp or MVA) not entered"
NO_PRICE = "the offered price is not known"
FEWER = f"fewer than {MIN_ROWS} comparable past offers"


def offer_basis(capacity_mwp: Decimal | None, capacity_mva: Decimal | None, amount: Decimal | None,
                currency: str | None) -> tuple[str, Decimal, str] | str:
    """(basis, unit price, currency) of an offer, per Wp when the MWp is entered, else per kVA; or the reason."""
    if amount is None or not currency:
        return NO_PRICE
    for basis, capacity in (("per_wp", capacity_mwp), ("per_kva", capacity_mva)):
        if capacity is not None and capacity > 0:
            return basis, unit_price(amount, capacity, basis), currency
    return NO_CAPACITY


def price_comparison(db: Session, *, equipment: str, basis: Literal["per_wp", "per_kva"], value: Decimal,
                     currency: str, offer_date: date) -> dict | None:
    """`value` (per Wp or per kVA, in `currency`) against the median of the same equipment over the four
    calendar quarters before the quarter of `offer_date`. None with fewer than MIN_ROWS comparable rows."""
    if basis not in BASIS_UNITS:
        raise ValueError(f"unknown basis {basis!r}")
    capacity_column = BASIS_UNITS[basis][0]
    current = _quarter_index(offer_date)
    first, last = current - QUARTERS, current - 1
    key = equipment.strip().lower()
    rows, other_currency, no_capacity = [], 0, 0
    for record in db.query(PriceHistoryRecord).order_by(PriceHistoryRecord.offer_date, PriceHistoryRecord.id):
        if record.equipment.strip().lower() != key or record.offer_date is None:
            continue
        if not first <= _quarter_index(record.offer_date) <= last:
            continue
        capacity = getattr(record, capacity_column)
        if record.price is None:
            continue
        if capacity is None:
            no_capacity += 1
            continue
        if (record.currency or "").upper() != currency.upper():
            other_currency += 1
            continue
        rows.append({"project": record.project, "manufacturer": record.manufacturer,
                     "offer_date": record.offer_date.isoformat(),
                     "unit_price": unit_price(Decimal(record.price), Decimal(capacity), basis)})
    if len(rows) < MIN_ROWS:
        return None
    mid = median(r["unit_price"] for r in rows)  # Decimal in, Decimal out
    return {"basis": basis, "unit": BASIS_UNITS[basis][2], "currency": currency.upper(), "value": value,
            "median": mid, "count": len(rows), "excluded_other_currency": other_currency,
            "excluded_no_capacity": no_capacity,
            "delta_pct": (value - mid) / mid * 100,
            "window": {"from": _quarter_label(first), "to": _quarter_label(last)}, "rows": rows}
