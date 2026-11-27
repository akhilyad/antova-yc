"""CAS export: DOCX and PDF, gated on the TBE and the contract gate, a watermarked draft otherwise."""
from __future__ import annotations

import io

import pytest
from docx import Document
from sqlalchemy import text

from antova_db import SessionLocal
from test_antova_cas import CAS, PIA, TENDER_ID, cas, client, ingested  # noqa: F401  (fixtures)

TBE_NOT_VERIFIED = "Sign off the technical evaluation before you download the approval sheet."
GATE_NOT_VERIFIED = "Sign off this vendor's contract terms before you download the approval sheet."
FA_CHANGED = ("The framework agreement for this vendor changed after sign-off. "
              "Withdraw and sign off again before you download the approval sheet.")


def _sql(statement, **params):
    db = SessionLocal()
    try:
        db.execute(text(statement), {"id": TENDER_ID, **params})
        db.commit()
    finally:
        db.close()


def _verify_tbe():
    _sql("UPDATE tenders SET status = 'VERIFIED' WHERE id = :id")


def _verify_gate():
    _sql("UPDATE contract_gates SET status = 'VERIFIED' WHERE tender_id = :id AND vendor_id = 'vendor-b'")


def _docx_text(body: bytes) -> str:
    doc = Document(io.BytesIO(body))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(c.text for c in row.cells)
    for section in doc.sections:
        parts.extend(p.text for p in section.footer.paragraphs)
        parts.extend(p.text for p in section.header.paragraphs)
    return "\n".join(parts)


@pytest.mark.parametrize("ext", ["docx", "pdf"])
def test_final_export_needs_the_tbe_signed_off(client, ext):
    response = client.get(f"{CAS}.{ext}", headers=PIA)
    assert response.status_code == 409
    assert response.json()["detail"] == TBE_NOT_VERIFIED


@pytest.mark.parametrize("ext", ["docx", "pdf"])
def test_final_export_needs_the_contract_gate_signed_off(client, ext):
    _verify_tbe()
    response = client.get(f"{CAS}.{ext}", headers=PIA)
    assert response.status_code == 409
    assert response.json()["detail"] == GATE_NOT_VERIFIED


@pytest.mark.parametrize("ext", ["docx", "pdf"])
def test_final_export_refused_when_the_fa_changed_after_sign_off(client, ext):
    _verify_tbe()
    _verify_gate()
    _sql("UPDATE contract_gates SET fa_version = 'an-older-agreement' WHERE tender_id = :id AND vendor_id = 'vendor-b'")
    response = client.get(f"{CAS}.{ext}", headers=PIA)
    assert response.status_code == 409
    assert response.json()["detail"] == FA_CHANGED
    assert client.get(f"{CAS}.{ext}?draft=true", headers=PIA).status_code == 200


def test_draft_docx_is_watermarked_and_lists_every_risk_and_citation(client, cas):
    response = client.get(f"{CAS}.docx?draft=true", headers=PIA)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = _docx_text(response.content)
    assert f"DRAFT: {cas['meta']['unreviewed_count']} findings unreviewed" in body
    risks = [r for group in cas["risks"].values() for r in group]
    assert risks
    for risk in risks:
        assert risk["message"] in body
    files = {c["filename"] for group in cas["risks"].values() for r in group for c in r["citations"]}
    for section in cas.values():
        if isinstance(section, dict):
            for field in section.values():
                if isinstance(field, dict):
                    files |= {c["filename"] for c in field.get("citations") or []}
    assert files
    for name in files:
        assert name in body
    assert cas["meta"]["run_fingerprint"] in body
    assert "not known" in body  # a missing field shows as not known, with its reason


def test_final_docx_has_no_watermark(client):
    _verify_tbe()
    _verify_gate()
    response = client.get(f"{CAS}.docx", headers=PIA)
    assert response.status_code == 200
    assert "DRAFT:" not in _docx_text(response.content)


def test_pdf_export_is_a_pdf(client):
    response = client.get(f"{CAS}.pdf?draft=true", headers=PIA)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF") and len(response.content) > 1000
    _verify_tbe()
    _verify_gate()
    final = client.get(f"{CAS}.pdf", headers=PIA)
    assert final.status_code == 200 and final.content.startswith(b"%PDF")


def test_export_needs_the_review_token(client):
    assert client.get(f"{CAS}.docx?draft=true").status_code in (401, 403)


def _pdf_text(body: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)


def test_pdf_carries_the_watermark_and_the_footer_on_every_page(client, cas):
    from antova_cas_export import footer
    from pypdf import PdfReader
    response = client.get(f"{CAS}.pdf?draft=true", headers=PIA)
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(response.content)).pages]
    mark = f"DRAFT: {cas['meta']['unreviewed_count']} findings unreviewed"
    for page in pages:
        assert mark in page
        assert cas["meta"]["run_fingerprint"] in page.replace("\n", "")
    assert footer(cas)[:40] in pages[0].replace("\n", "")
    _verify_tbe()
    _verify_gate()
    assert "DRAFT:" not in _pdf_text(client.get(f"{CAS}.pdf", headers=PIA).content)


def test_control_characters_from_pdf_text_do_not_break_either_format(cas):
    from antova_cas_export import cas_docx, cas_pdf
    risk = next(r for group in cas["risks"].values() for r in group)
    risk["message"] = "page\x0cbreak and a\x00nul"
    risk["citations"] = [{"filename": "odd\x0b.pdf", "page": 3, "quote": "form\x0cfeed\x00 quote"}]
    body = _docx_text(cas_docx(cas, draft=True))
    assert "pagebreak and anul" in body and "formfeed quote" in body
    assert "formfeed quote" in _pdf_text(cas_pdf(cas, draft=True)).replace("\n", " ")


def test_a_risk_line_shows_its_baseline_money_and_gap(cas):
    from antova_cas_export import cas_docx
    risk = next(r for group in cas["risks"].values() for r in group)
    risk["finding_id"] = "vendor-b|CT-03|owner|warranty_months"
    risk["money"] = [{"kind": "exposure", "amount": "12345.00", "currency": "EUR", "reason": ""}]
    risk["gap"] = {"value": "6", "unit": "months", "reason": ""}
    risk["citations"] = [{"filename": None, "page": None, "quote": "q"}]
    body = _docx_text(cas_docx(cas, draft=True))
    assert "baseline: owner contract" in body
    assert "exposure: 12345.00 EUR" in body and "gap: 6 months" in body
    assert "unknown file" in body and "None" not in body


def test_docx_sections_are_in_the_spec_order(client):
    body = _docx_text(client.get(f"{CAS}.docx?draft=true", headers=PIA).content)
    positions = [body.index(f"{n}. ") for n in range(1, 12)]
    assert positions == sorted(positions)


def test_content_disposition_filename_is_quoted(client):
    header = client.get(f"{CAS}.docx?draft=true", headers=PIA).headers["content-disposition"]
    assert header == f'attachment; filename="CAS_{TENDER_ID}_vendor-b_DRAFT.docx"'


def test_vendor_without_a_counterparty_exports_final_when_the_gate_has_no_fa_version(client):
    _verify_tbe()
    _verify_gate()
    _sql("UPDATE vendors SET counterparty_id = NULL WHERE tender_id = :id")
    _sql("UPDATE source_documents SET counterparty_id = NULL WHERE tender_id = :id")
    _sql("UPDATE contract_gates SET fa_version = NULL WHERE tender_id = :id AND vendor_id = 'vendor-b'")
    for ext in ("docx", "pdf"):
        assert client.get(f"{CAS}.{ext}", headers=PIA).status_code == 200
