"""The contract approval sheet (CAS) as DOCX (python-docx) and PDF (reportlab).

Rendered from the dict antova_cas.build_cas returns, never re-derived: sections 1-10 in the spec's order (4.5),
then 11, the evidence appendix with every citation (file, page, verbatim quote). Every page carries a footer
with the run fingerprint, the rate table version, the contract-ontology hash and the zero-retention reference
("none" when there is none). A draft is watermarked "DRAFT: n findings unreviewed".
"""

from __future__ import annotations

import io
import re
from typing import Iterable
from xml.sax.saxutils import escape

SECTIONS = (
    ("project", "1. Project and contract data"),
    ("law", "2. Law and jurisdiction"),
    ("price", "3. Price"),
    ("delivery", "4. Incoterms and delivery"),
    ("payment_bonds", "5. Payment and bonds"),
    ("lds", "6. Liquidated damages"),
    ("warranty", "7. Warranty"),
    ("risks", "8. Risks"),
    ("technical", "9. Technical evaluation"),
    ("approvers", "10. Approvers"),
)
APPENDIX = "11. Evidence appendix"
RISK_TITLES = {"technical": "Technical", "fa_conformance": "Framework agreement conformance",
               "back_to_back": "Back-to-back", "budget": "Budget", "missing": "Missing"}
TITLE = "Contract approval sheet"
BASELINES = {"fa": "framework agreement", "owner": "owner contract", "offer": "offer", "budget": "budget",
             "tbe": "material requisition"}
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(value) -> str:
    """The text without the control characters XML cannot hold (PDF text carries form feeds and NULs)."""
    return _ILLEGAL.sub("", "" if value is None else str(value))


def watermark(cas: dict) -> str:
    return f"DRAFT: {cas['meta']['unreviewed_count']} findings unreviewed"


def footer(cas: dict) -> str:
    meta = cas.get("meta") or {}
    return (f"Run {meta.get('run_fingerprint') or 'none'} · rate table {meta.get('rate_table_version') or 'none'}"
            f" · contract ontology {meta.get('contract_ontology_hash') or 'none'}"
            f" · zero retention {meta.get('zero_retention_ref') or 'none'}")


def _cite(c: dict) -> str:
    page, name = c.get("page"), c.get("filename") or "unknown file"
    return f"{name}, p. {page}" if page is not None else name


def _field_lines(field: dict) -> tuple[str, str]:
    """(label, the text shown beside it)."""
    text = field.get("display") or "not known"
    if field.get("state") == "computed" and field.get("formula"):
        text += f" (computed: {field['formula']})"
    elif field.get("state") == "missing":
        text += f" ({field.get('reason')})"
    elif field.get("state") == "inherited":
        text += " (inherited from the framework agreement)"
    if field.get("citations"):
        text += " [" + "; ".join(_cite(c) for c in field["citations"]) + "]"
    return field.get("label") or "", text


def _risk_detail(r: dict) -> str:
    """The baseline, money and days of a risk line (spec 4.5 item 8), when the finding has them."""
    parts = []
    segments = str(r.get("finding_id") or "").split("|")
    baseline = BASELINES.get(segments[1] if len(segments) > 1 and segments[1] == "budget" else
                             segments[2] if len(segments) > 2 else "")
    if baseline:
        parts.append(f"baseline: {baseline}")
    for m in r.get("money") or []:
        amount = m.get("amount")
        parts.append(f"{m.get('kind') or 'amount'}: {amount} {m.get('currency') or ''}".rstrip()
                     if amount is not None else f"{m.get('kind') or 'amount'}: not known ({m.get('reason')})")
    gap = r.get("gap")
    if gap:
        parts.append(f"gap: {gap['value']} {gap.get('unit') or ''}".rstrip() if gap.get("value") is not None
                     else f"gap: not known ({gap.get('reason')})")
    return f" ({'; '.join(parts)})" if parts else ""


def _section_rows(key: str, section) -> list[tuple[str, str]]:
    """Rows of (label, text) of a section."""
    if key == "risks":
        rows = []
        for group, items in section.items():
            for r in items:
                decision = r.get("disposition") or r.get("disposition_state") or "undecided"
                cites = "; ".join(_cite(c) for c in r.get("citations") or [])
                rows.append((RISK_TITLES.get(group, group),
                             f"{r['message']}{_risk_detail(r)} — decision: {decision}"
                             + (f" [{cites}]" if cites else "")))
        return rows or [("Risks", "none")]
    if key == "approvers":
        rows = [(a["role"], a["reason"]) for a in section.get("suggested") or []]
        rows += [(a["role"], f"removed by {a['removed_by']}: {a['reason']}") for a in section.get("removed") or []]
        rows.append(("Rules", section.get("rules") or ""))
        return rows
    rows = []
    for value in section.values():
        if isinstance(value, dict) and "state" in value:
            rows.append(_field_lines(value))
        elif isinstance(value, dict):  # a nested group of fields
            rows += [_field_lines(v) for v in value.values() if isinstance(v, dict) and "state" in v]
    return rows


def _walk_citations(node) -> Iterable[dict]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "citations" and isinstance(v, list):
                yield from (c for c in v if isinstance(c, dict))
            else:
                yield from _walk_citations(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_citations(v)


def evidence(cas: dict) -> list[dict]:
    """Every citation on the sheet once, in order of appearance."""
    seen, out = set(), []
    for c in _walk_citations({k: cas.get(k) for k, _ in SECTIONS}):
        key = (c.get("filename"), c.get("page"), c.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _review_state(cas: dict) -> str:
    meta = cas.get("meta") or {}
    return f"TBE {meta.get('tbe_status')} · contract gate {meta.get('contract_gate_status')}"


# --- DOCX ------------------------------------------------------------------------------------------

def cas_docx(cas: dict, *, draft: bool) -> bytes:
    from docx import Document

    doc = Document()
    section = doc.sections[0]
    section.footer.paragraphs[0].text = clean(footer(cas))
    if draft:
        section.header.paragraphs[0].text = clean(watermark(cas))
    doc.add_heading(TITLE, level=0)
    if draft:
        doc.add_paragraph(clean(watermark(cas)))
    doc.add_paragraph(clean(_review_state(cas)))
    for key, title in SECTIONS:
        doc.add_heading(title, level=1)
        rows = _section_rows(key, cas.get(key) or {})
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        for label, text in rows:
            cells = table.add_row().cells
            cells[0].text, cells[1].text = clean(label), clean(text)
    doc.add_heading(APPENDIX, level=1)
    for c in evidence(cas):
        doc.add_paragraph(clean(_cite(c)), style="List Bullet")
        doc.add_paragraph(clean(f"“{c.get('quote') or ''}”"))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- PDF -------------------------------------------------------------------------------------------

def cas_pdf(cas: dict, *, draft: bool) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    foot, mark = clean(footer(cas)), clean(watermark(cas)) if draft else None

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, foot[:180])
        if mark:
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawString(15 * mm, A4[1] - 12 * mm, mark)
            canvas.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.15))
            canvas.setFont("Helvetica-Bold", 40)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, mark)
        canvas.restoreState()

    def p(text: str, style=cell):
        return Paragraph(escape(clean(text)), style)

    story = [p(TITLE, styles["Title"])]
    if mark:
        story.append(p(mark, styles["Heading3"]))
    story.append(p(_review_state(cas)))
    for key, title in SECTIONS:
        story.append(p(title, styles["Heading2"]))
        rows = [[p(a), p(b)] for a, b in _section_rows(key, cas.get(key) or {})]
        if rows:
            table = Table(rows, colWidths=[50 * mm, 125 * mm])
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(table)
        story.append(Spacer(1, 4 * mm))
    story.append(p(APPENDIX, styles["Heading2"]))
    for c in evidence(cas):
        story.append(p(_cite(c), styles["Heading5"]))
        story.append(p(f"“{c.get('quote') or ''}”"))
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, title=TITLE, topMargin=18 * mm, bottomMargin=18 * mm).build(
        story, onFirstPage=on_page, onLaterPages=on_page)
    return out.getvalue()
