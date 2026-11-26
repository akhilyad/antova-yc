"""Resolving each contract term across the offer, the framework agreement and the owner contract (Task 5)."""

from datetime import date
from decimal import Decimal

from antova_ingest.contract_ontology import CONTRACT_PARAMETERS
from antova_ingest.contract_terms import contract_evidence, resolve_contract_terms
from antova_ingest.tests.helpers import evidence
from antova_ingest.types import Citation, DocumentEntry, OwnerTerms, ProjectBaseline, TenderManifest

MR = DocumentEntry(file="mr.pdf", role="mr_spec")
BID = DocumentEntry(file="bid-a.pdf", role="vendor_bid", vendor_id="vendor-a", counterparty_id="cp-a")
FA = DocumentEntry(file="fa-a.pdf", role="framework_agreement", counterparty_id="cp-a", dated=date(2026, 1, 10))
AMENDMENT = DocumentEntry(file="fa-a-amd1.pdf", role="framework_agreement", counterparty_id="cp-a",
                          amends="fa-a.pdf", dated=date(2026, 3, 2))
OWNER = DocumentEntry(file="owner.pdf", role="owner_contract")

WARRANTY_60 = "The warranty period is 60 months from provisional acceptance."
WARRANTY_66 = "The warranty period is amended to 66 months from provisional acceptance."
WARRANTY_24 = "The warranty period is 24 months from delivery."
TITLE_ON_DELIVERY = "Title to the Equipment passes to the Buyer on delivery."
INCORPORATION = "The Framework Agreement dated 10 January 2026 is incorporated into and forms part of this offer."


def _manifest(documents, owner_terms=None):
    project = ProjectBaseline(owner_terms=owner_terms) if owner_terms is not None else None
    return TenderManifest(tender_id="T-1", title="t", client="c", equipment_class="mv_gis_switchgear",
                          rate_table="mv_gis_switchgear_v2", documents=[MR, *documents],
                          engineering_estimates={}, synthetic=True, project=project)


def _ev(file, param_id, page, quote, section="commercial"):
    return evidence(param_id, section, page, quote, filename=file)


def test_every_vendor_gets_every_contract_term():
    terms = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": []})
    assert set(terms) == {("vendor-a", p.id) for p in CONTRACT_PARAMETERS}
    term = terms[("vendor-a", "CT-03")]
    assert (term.param_id, term.vendor_id) == ("CT-03", "vendor-a")


def test_offer_cites_its_own_term():
    offer = _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)
    terms = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [offer],
                                                         "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)]})
    term = terms[("vendor-a", "CT-03")]
    assert term.state == "cited"
    assert term.offered.kind == "document"
    assert term.offered.values["warranty_start"].value == "delivery"
    assert term.offered.citations == (offer.citation,)
    assert term.fa.values["warranty_months"].value == 60  # the FA is kept apart, as the baseline
    assert term.flags == ()


def test_amendment_after_fa_governs():
    fa = _ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)
    amendment = _ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)
    terms = resolve_contract_terms(_manifest([BID, FA, AMENDMENT]),
                                   {"bid-a.pdf": [], "fa-a.pdf": [fa], "fa-a-amd1.pdf": [amendment]})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa.values["warranty_months"].value == 66
    assert term.fa.citations == (fa.citation, amendment.citation)
    assert all(f["code"] != "amendment_order_unknown" for f in term.flags)


def test_amendment_governs_only_the_fields_it_states():
    fa = _ev("fa-a.pdf", "CT-01", 5, "Liquidated damages for delay are 0.5% of the contract price per day, "
                                     "capped at 10% of the contract price.")
    amendment = _ev("fa-a-amd1.pdf", "CT-01", 1, "The cap on liquidated damages is amended to 15% of the contract price.")
    assert set(amendment.values) == {"ld_cap"}
    terms = resolve_contract_terms(_manifest([BID, FA, AMENDMENT]),
                                   {"bid-a.pdf": [], "fa-a.pdf": [fa], "fa-a-amd1.pdf": [amendment]})
    fa_source = terms[("vendor-a", "CT-01")].fa
    assert fa_source.values["ld_cap"].value == 15.0
    assert fa_source.values["ld_rate"].value == (Decimal("0.5"), "day")
    assert fa_source.citations == (fa.citation, amendment.citation)


def test_amendment_order_unknown():
    # A manifest amendment always carries a date (types.DocumentEntry); one that reaches here without it is not trusted.
    manifest = _manifest([BID, FA, AMENDMENT])
    manifest.documents[-1] = AMENDMENT.model_copy(update={"dated": None})
    fa = _ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)
    amendment = _ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)
    terms = resolve_contract_terms(manifest, {"bid-a.pdf": [], "fa-a.pdf": [fa], "fa-a-amd1.pdf": [amendment]})
    term = terms[("vendor-a", "CT-03")]
    assert term.state == "conflict"
    assert "amendment_order_unknown" in [f["code"] for f in term.flags]
    assert "warranty_months" not in term.fa.values  # no silent pick between 60 and 66
    assert term.fa.values["warranty_start"].value == "pac"  # both say PAC: the order does not matter
    assert term.fa.citations == (fa.citation, amendment.citation)


def test_amendment_dated_on_or_before_the_fa_is_order_unknown():
    for dated in (date(2026, 1, 10), date(2025, 12, 1)):
        early = AMENDMENT.model_copy(update={"dated": dated})
        terms = resolve_contract_terms(_manifest([BID, FA, early]), {
            "bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)],
            "fa-a-amd1.pdf": [_ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)]})
        term = terms[("vendor-a", "CT-03")]
        assert term.state == "conflict"
        assert "amendment_order_unknown" in [f["code"] for f in term.flags]


def test_amendment_to_an_undated_fa_is_order_unknown():
    undated_fa = FA.model_copy(update={"dated": None})
    terms = resolve_contract_terms(_manifest([BID, undated_fa, AMENDMENT]), {
        "bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)],
        "fa-a-amd1.pdf": [_ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)]})
    assert terms[("vendor-a", "CT-03")].state == "conflict"


def test_inherited_needs_incorporation_clause():
    fa_title = _ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)
    incorporation = _ev("bid-a.pdf", "CT-16", 20, INCORPORATION)
    assert "fa_reference" in incorporation.values

    with_clause = resolve_contract_terms(_manifest([BID, FA]),
                                         {"bid-a.pdf": [incorporation], "fa-a.pdf": [fa_title]})
    term = with_clause[("vendor-a", "CT-09")]
    assert term.state == "inherited"
    assert term.offered.kind == "inherited"
    assert term.offered.values["title_event"].value == "delivery"
    assert term.offered.citations == (incorporation.citation, fa_title.citation)
    assert term.offered.inherited_fields == {"title_event"}

    without = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [], "fa-a.pdf": [fa_title]})
    term = without[("vendor-a", "CT-09")]
    assert term.state == "missing"
    assert term.offered is None
    assert term.fa.values["title_event"].value == "delivery"


LD_RATE_ONLY = "Liquidated damages for delay are 0.5% of the contract price per day."
LD_RATE_AND_CAP = ("Liquidated damages for delay are 0.5% of the contract price per day, "
                   "capped at 10% of the contract price.")


def test_inheritance_is_per_field():
    # Ruling R25: the offer states the LD rate and is silent on the cap; the cap comes from the FA.
    offer_ld = _ev("bid-a.pdf", "CT-01", 21, LD_RATE_ONLY)
    incorporation = _ev("bid-a.pdf", "CT-16", 20, INCORPORATION)
    fa_ld = _ev("fa-a.pdf", "CT-01", 5, LD_RATE_AND_CAP)
    assert set(offer_ld.values) == {"ld_rate"}
    term = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [incorporation, offer_ld], "fa-a.pdf": [fa_ld]})[("vendor-a", "CT-01")]
    assert term.state == "inherited"
    assert term.offered.kind == "document"  # the offer states part of the term itself
    assert term.offered.values["ld_rate"].value == (Decimal("0.5"), "day")
    assert term.offered.values["ld_cap"].value == 10.0
    assert term.offered.inherited_fields == {"ld_cap"}
    assert term.offered.citations == (offer_ld.citation, incorporation.citation, fa_ld.citation)
    assert term.flags == ()


def test_partial_offer_without_incorporation_stays_cited():
    offer_ld = _ev("bid-a.pdf", "CT-01", 21, LD_RATE_ONLY)
    term = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [offer_ld], "fa-a.pdf": [_ev("fa-a.pdf", "CT-01", 5, LD_RATE_AND_CAP)]})[("vendor-a", "CT-01")]
    assert term.state == "cited"
    assert set(term.offered.values) == {"ld_rate"}
    assert term.offered.inherited_fields == frozenset()
    assert term.offered.citations == (offer_ld.citation,)


def test_a_field_the_offer_states_in_conflict_is_not_filled_from_the_fa():
    one = _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)
    other = _ev("bid-a.pdf", "CT-03", 22, "The warranty period is 36 months from delivery.")
    term = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [_ev("bid-a.pdf", "CT-16", 20, INCORPORATION), one, other],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)]})[("vendor-a", "CT-03")]
    assert term.state == "conflict"
    assert "warranty_months" not in term.offered.values
    assert term.offered.inherited_fields == frozenset()


def test_fa_present_even_when_the_fa_is_silent_on_the_term():
    # Ruling R26: fa is None for a silent FA, but fa_present says an FA is linked.
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    silent = terms[("vendor-a", "CT-03")]
    assert silent.fa is None and silent.fa_present is True
    assert terms[("vendor-a", "CT-09")].fa_present is True

    none = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": []})[("vendor-a", "CT-03")]
    assert none.fa is None and none.fa_present is False
    unlinked = resolve_contract_terms(_manifest([BID.model_copy(update={"counterparty_id": None}), FA]),
                                      {"bid-a.pdf": [], "fa-a.pdf": []})[("vendor-a", "CT-03")]
    assert unlinked.fa_present is False


def test_incorporation_clause_in_the_fa_also_counts():
    fa_title = _ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)
    fa_clause = _ev("fa-a.pdf", "CT-16", 1, "This Framework Agreement forms part of every order placed under it.")
    assert "fa_reference" in fa_clause.values
    terms = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [], "fa-a.pdf": [fa_clause, fa_title]})
    term = terms[("vendor-a", "CT-09")]
    assert term.state == "inherited"
    assert term.offered.citations == (fa_clause.citation, fa_title.citation)


def test_incorporation_clause_inherited_from_the_fa_is_cited_once():
    fa_clause = _ev("fa-a.pdf", "CT-16", 1, "This Framework Agreement forms part of every order placed under it.")
    term = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [], "fa-a.pdf": [fa_clause]})[("vendor-a", "CT-16")]
    assert term.state == "inherited"
    assert term.offered.citations == (fa_clause.citation,)


def test_negated_incorporation_is_not_an_incorporation_clause():
    negated = _ev("bid-a.pdf", "CT-16", 20, "This offer is not subject to the Framework Agreement.")
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [negated], "fa-a.pdf": [_ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    assert terms[("vendor-a", "CT-09")].state == "missing"


def test_owner_terms_from_form():
    owner_terms = OwnerTerms(warranty_months=60, warranty_start="pac")
    terms = resolve_contract_terms(_manifest([BID], owner_terms), {"bid-a.pdf": []})
    owner = terms[("vendor-a", "CT-03")].owner
    assert owner.kind == "buyer_entered"
    assert owner.values["warranty_months"].value == 60
    assert owner.values["warranty_months"].qualifier == "commissioning"  # PAC, read as the parser reads it
    assert owner.values["warranty_start"].value == "pac"
    assert owner.citations == ()
    assert terms[("vendor-a", "CT-09")].owner is None  # the form says nothing about title


def test_owner_terms_map_lds_liability_and_law():
    owner_terms = OwnerTerms.model_validate({
        "ld_rate": "0.5", "ld_rate_basis": "percent_per_week", "ld_cap": "10", "ld_cap_basis": "percent",
        "liability_cap": {"amount": "48000000.00", "currency": "EUR"}, "law": "England and Wales",
    })
    terms = resolve_contract_terms(_manifest([BID], owner_terms), {"bid-a.pdf": []})
    lds = terms[("vendor-a", "CT-01")].owner.values
    assert lds["ld_rate"].value == (Decimal("0.5"), "week")
    assert lds["ld_rate"].display == "0.5% per week"
    assert lds["ld_cap"].value == 10.0 and lds["ld_cap"].qualifier == "contract"
    cap = terms[("vendor-a", "CT-02")].owner.values["liability_cap_money"]
    assert (cap.value, cap.qualifier, cap.display) == (Decimal("48000000.00"), "EUR", "EUR 48,000,000")
    law = terms[("vendor-a", "CT-14")].owner.values["law"]
    assert (law.value, law.display) == ("english", "English law")
    for typed, code in (("German law", "german"), ("new_york", "new_york")):
        law = resolve_contract_terms(_manifest([BID], OwnerTerms(law=typed)), {"bid-a.pdf": []})[("vendor-a", "CT-14")]
        assert law.owner.values["law"].value == code
    unread = resolve_contract_terms(_manifest([BID], OwnerTerms(law="Atlantis")), {"bid-a.pdf": []})[("vendor-a", "CT-14")]
    assert unread.owner is None
    assert "owner_value_not_mapped" in [f["code"] for f in unread.flags]


def test_owner_ld_in_money_is_not_mapped_as_a_percent():
    owner_terms = OwnerTerms.model_validate({"ld_rate": "10000", "ld_rate_basis": "money_per_day"})
    term = resolve_contract_terms(_manifest([BID], owner_terms), {"bid-a.pdf": []})[("vendor-a", "CT-01")]
    assert term.owner is None
    assert "owner_value_not_mapped" in [f["code"] for f in term.flags]


def test_owner_contract_document_wins_over_the_form():
    owner_doc = _ev("owner.pdf", "CT-03", 12, "The warranty period is 66 months from provisional acceptance.")
    terms = resolve_contract_terms(_manifest([BID, OWNER], OwnerTerms(warranty_months=60, warranty_start="pac")),
                                   {"bid-a.pdf": [], "owner.pdf": [owner_doc]})
    owner = terms[("vendor-a", "CT-03")].owner
    assert owner.kind == "document"
    assert owner.values["warranty_months"].value == 66
    assert owner.citations == (owner_doc.citation,)


def test_no_fa_means_fa_none():
    terms = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": [_ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)]})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa is None
    assert term.state == "cited"
    assert terms[("vendor-a", "CT-09")].state == "missing"
    assert terms[("vendor-a", "CT-09")].fa is None


def test_vendor_without_counterparty_has_no_fa():
    # An FA whose counterparty id happens to equal the vendor id is still not linked: only counterparty_id links.
    bid = BID.model_copy(update={"counterparty_id": None})
    fa = FA.model_copy(update={"counterparty_id": "vendor-a"})
    terms = resolve_contract_terms(_manifest([bid, fa]), {
        "bid-a.pdf": [_ev("bid-a.pdf", "CT-16", 20, INCORPORATION)],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    term = terms[("vendor-a", "CT-09")]
    assert term.fa is None
    assert term.state == "missing"
    assert "fa_not_linked" in [f["code"] for f in term.flags]


def test_fa_of_another_counterparty_is_not_used():
    other = FA.model_copy(update={"counterparty_id": "cp-b"})
    terms = resolve_contract_terms(_manifest([BID, other]), {
        "bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)]})
    assert terms[("vendor-a", "CT-03")].fa is None


def test_offer_passages_use_the_tier_order():
    datasheet = _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24, section="commercial")
    cover = _ev("bid-a.pdf", "CT-03", 2, WARRANTY_60, section="cover_letter")
    terms = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": [cover, datasheet]})
    term = terms[("vendor-a", "CT-03")]
    assert term.state == "cited"
    assert term.offered.values["warranty_start"].value == "delivery"
    assert term.offered.citations == (datasheet.citation,)


def test_offer_passages_disagreeing_on_one_tier_are_a_conflict():
    one = _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)
    other = _ev("bid-a.pdf", "CT-03", 22, "The warranty period is 36 months from delivery.")
    terms = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": [one, other]})
    term = terms[("vendor-a", "CT-03")]
    assert term.state == "conflict"
    assert "passages_disagree" in [f["code"] for f in term.flags]
    assert "warranty_months" not in term.offered.values
    assert term.offered.values["warranty_start"].value == "delivery"  # both say delivery
    assert term.offered.citations == (one.citation, other.citation)


def test_a_document_not_read_is_flagged_not_silent():
    terms = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": []})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa is None
    assert any(f["code"] == "document_not_read" and "fa-a.pdf" in f["message"] for f in term.flags)


def _verified(rows_by_file, locales=None):
    return {"locales": locales or {f: "en" for f in rows_by_file},
            "contract": {"citations": rows_by_file, "accepted": 0, "rejected": 0, "not_stated": {}}}


def _row(param_id, file, page, quote):
    citation = Citation(file, page, (1.0, 2.0, 3.0, 4.0), quote, "commercial")
    return {"param_id": param_id, "citation": citation.to_dict(), "source": "locator"}


def test_contract_evidence_reads_values_dedupes_and_drops_failed_vendors():
    other_bid = DocumentEntry(file="bid-b.pdf", role="vendor_bid", vendor_id="vendor-b")
    manifest = _manifest([BID, other_bid, FA])
    verified = _verified({
        "bid-a.pdf": [_row("CT-03", "bid-a.pdf", 21, WARRANTY_24), _row("CT-03", "bid-a.pdf", 21, WARRANTY_24),
                      _row("PARAM-01", "bid-a.pdf", 5, "Rated current 1250 A.")],
        "bid-b.pdf": [_row("CT-03", "bid-b.pdf", 9, WARRANTY_60)],
        "fa-a.pdf": [_row("CT-09", "fa-a.pdf", 7, TITLE_ON_DELIVERY),
                     _row("CT-03", "fa-a.pdf", 8, "The warranty clause is set out in Annex 4.")],
    })
    found, notes = contract_evidence(verified, manifest, failed_vendors={"vendor-b"})
    assert set(found) == {"bid-a.pdf", "fa-a.pdf"}
    assert [(e.param_id, e.citation.page) for e in found["bid-a.pdf"]] == [("CT-03", 21)]
    assert found["bid-a.pdf"][0].values["warranty_months"].value == 24
    assert [e.param_id for e in found["fa-a.pdf"]] == ["CT-09"]  # a passage stating no value is not evidence
    assert notes == []


def test_contract_evidence_without_a_contract_pass_is_empty():
    assert contract_evidence({"locales": {}}, _manifest([BID]), failed_vendors=()) == ({}, [])


# --- review round 1 (task-5-findings-r1) ------------------------------------------------------------------

FA_CALL_OFF = "This Framework Agreement forms part of every order placed under it."
NEGATED = "This offer is not subject to the Framework Agreement."


def _codes(term):
    return [f["code"] for f in term.flags]


def test_an_unread_offer_inherits_nothing_from_the_fa_call_off_clause():
    # Critical 1a: the offer was not read, so it is not silent; its terms are unknown.
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-16", 1, FA_CALL_OFF), _ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    term = terms[("vendor-a", "CT-09")]
    assert term.state == "missing"
    assert term.offered is None
    assert term.fa.values["title_event"].value == "delivery"  # the FA itself was read
    assert any(f["code"] == "document_not_read" and "bid-a.pdf" in f["message"] for f in term.flags)


def test_a_failed_vendor_inherits_nothing():
    # Critical 1b: contract_evidence leaves a failed vendor's bid out, so it is unread.
    verified = _verified({"bid-a.pdf": [_row("CT-16", "bid-a.pdf", 20, INCORPORATION)],
                          "fa-a.pdf": [_row("CT-16", "fa-a.pdf", 1, FA_CALL_OFF),
                                       _row("CT-09", "fa-a.pdf", 7, TITLE_ON_DELIVERY)]})
    manifest = _manifest([BID, FA])
    found, _ = contract_evidence(verified, manifest, failed_vendors={"vendor-a"})
    assert "bid-a.pdf" not in found
    terms = resolve_contract_terms(manifest, found)
    assert all(t.state == "missing" and t.offered is None for (v, _p), t in terms.items() if v == "vendor-a")


def test_an_unread_amendment_leaves_the_fa_unknown():
    # Critical 1c: the base FA's 60 months may have been amended; it is not shown as governing or inherited.
    terms = resolve_contract_terms(_manifest([BID, FA, AMENDMENT]), {
        "bid-a.pdf": [_ev("bid-a.pdf", "CT-16", 20, INCORPORATION)],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)]})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa is None and term.fa_present is True
    assert term.offered is None and term.state == "missing"
    assert any(f["code"] == "document_not_read" and "fa-a-amd1.pdf" in f["message"] for f in term.flags)


def test_an_offer_that_refuses_the_fa_does_not_inherit_through_the_fa_clause():
    # Critical 2: the FA says it forms part of every order; the offer says it is not subject to it.
    negated = _ev("bid-a.pdf", "CT-16", 20, NEGATED)
    assert negated.values == {}
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [negated],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-16", 1, FA_CALL_OFF), _ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    term = terms[("vendor-a", "CT-09")]
    assert term.state == "missing" and term.offered is None
    assert "incorporation_unclear" in _codes(term)


def test_a_disclaimer_beside_an_incorporation_clause_blocks_inheritance():
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [_ev("bid-a.pdf", "CT-16", 2, INCORPORATION), _ev("bid-a.pdf", "CT-16", 20, NEGATED)],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)]})
    term = terms[("vendor-a", "CT-09")]
    assert term.state == "missing"
    assert "incorporation_unclear" in _codes(term)


def test_contract_evidence_keeps_a_disclaimer_of_the_fa():
    verified = _verified({"bid-a.pdf": [_row("CT-16", "bid-a.pdf", 20, NEGATED)]})
    found, _ = contract_evidence(verified, _manifest([BID]), failed_vendors=())
    assert [(e.param_id, e.values) for e in found["bid-a.pdf"]] == [("CT-16", {})]


def test_differently_worded_incorporation_clauses_still_incorporate():
    # Important 3: both passages incorporate the FA; the wording differs, the meaning does not.
    short = _ev("bid-a.pdf", "CT-16", 2, "This offer is subject to the Framework Agreement.")
    long = _ev("bid-a.pdf", "CT-16", 20, INCORPORATION)
    fa_title = _ev("fa-a.pdf", "CT-09", 7, TITLE_ON_DELIVERY)
    terms = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [short, long], "fa-a.pdf": [fa_title]})
    term = terms[("vendor-a", "CT-09")]
    assert term.state == "inherited"
    assert term.offered.citations == (short.citation, long.citation, fa_title.citation)


def test_counterparty_ambiguous():
    bid_b = DocumentEntry(file="bid-a2.pdf", role="vendor_bid", vendor_id="vendor-a", counterparty_id="cp-b")
    fa_b = DocumentEntry(file="fa-b.pdf", role="framework_agreement", counterparty_id="cp-b")
    terms = resolve_contract_terms(_manifest([BID, bid_b, FA, fa_b]), {
        "bid-a.pdf": [], "bid-a2.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)], "fa-b.pdf": []})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa is None and term.fa_present is False
    assert "counterparty_ambiguous" in _codes(term)


def test_two_bids_one_naming_the_counterparty_link_the_fa():
    unnamed = DocumentEntry(file="bid-a2.pdf", role="vendor_bid", vendor_id="vendor-a")
    offer = _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)
    fa_warranty = _ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)
    terms = resolve_contract_terms(_manifest([BID, unnamed, FA]), {
        "bid-a.pdf": [offer], "bid-a2.pdf": [], "fa-a.pdf": [fa_warranty]})
    term = terms[("vendor-a", "CT-03")]
    assert term.fa_present is True and term.fa.values["warranty_months"].value == 60
    assert term.state == "cited" and term.offered.citations == (offer.citation,)
    # One of the two bids not read: the offer is unknown, not the other bid's alone.
    partly = resolve_contract_terms(_manifest([BID, unnamed, FA]), {
        "bid-a.pdf": [offer], "fa-a.pdf": [fa_warranty]})[("vendor-a", "CT-03")]
    assert partly.offered is None and partly.state == "missing"


def test_same_day_amendments():
    second = DocumentEntry(file="fa-a-amd2.pdf", role="framework_agreement", counterparty_id="cp-a",
                           amends="fa-a.pdf", dated=AMENDMENT.dated)
    base = {"bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)],
            "fa-a-amd1.pdf": [_ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)]}
    seventy_two = "The warranty period is amended to 72 months from provisional acceptance."
    disagree = resolve_contract_terms(_manifest([BID, FA, AMENDMENT, second]), {
        **base, "fa-a-amd2.pdf": [_ev("fa-a-amd2.pdf", "CT-03", 1, seventy_two)]})
    term = disagree[("vendor-a", "CT-03")]
    assert term.state == "conflict"
    assert "amendment_order_unknown" in _codes(term)
    assert "warranty_months" not in term.fa.values
    agree = resolve_contract_terms(_manifest([BID, FA, AMENDMENT, second]), {
        **base, "fa-a-amd2.pdf": [_ev("fa-a-amd2.pdf", "CT-03", 2, WARRANTY_66)]})
    term = agree[("vendor-a", "CT-03")]
    assert term.state != "conflict"
    assert term.fa.values["warranty_months"].value == 66
    assert len(term.fa.citations) == 3


def test_owner_contract_amendments():
    owner = OWNER.model_copy(update={"dated": date(2026, 1, 1)})
    owner_amd = DocumentEntry(file="owner-amd1.pdf", role="owner_contract", amends="owner.pdf",
                              dated=date(2026, 5, 1))
    base = _ev("owner.pdf", "CT-03", 12, WARRANTY_60)
    later = _ev("owner-amd1.pdf", "CT-03", 1, WARRANTY_66)
    evidence_by_file = {"bid-a.pdf": [], "owner.pdf": [base], "owner-amd1.pdf": [later]}
    term = resolve_contract_terms(_manifest([BID, owner, owner_amd]), evidence_by_file)[("vendor-a", "CT-03")]
    assert term.owner.values["warranty_months"].value == 66
    assert term.owner.citations == (base.citation, later.citation)
    undated = resolve_contract_terms(_manifest([BID, OWNER, owner_amd]), evidence_by_file)[("vendor-a", "CT-03")]
    assert undated.state == "conflict"
    assert "amendment_order_unknown" in _codes(undated)


# --- ruling R27: a flag about one field names the field and its source ---------------------------------

def _keyed(term, code):
    return [(f["field"], f["source"]) for f in term.flags if f["code"] == code]


def test_field_flags_carry_field_and_source_keys():
    offer = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": [
        _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24),
        _ev("bid-a.pdf", "CT-03", 22, "The warranty period is 36 months from delivery.")]})[("vendor-a", "CT-03")]
    assert _keyed(offer, "passages_disagree") == [("warranty_months", "offered")]

    manifest = _manifest([BID, FA, AMENDMENT])
    manifest.documents[-1] = AMENDMENT.model_copy(update={"dated": None})
    fa = resolve_contract_terms(manifest, {"bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-03", 4, WARRANTY_60)],
                                           "fa-a-amd1.pdf": [_ev("fa-a-amd1.pdf", "CT-03", 1, WARRANTY_66)]})
    assert _keyed(fa[("vendor-a", "CT-03")], "amendment_order_unknown") == [("warranty_months", "fa")]

    owner_terms = OwnerTerms.model_validate({"ld_rate": "10000", "ld_rate_basis": "money_per_day",
                                             "ld_cap": "500000", "ld_cap_basis": "money"})
    owner = resolve_contract_terms(_manifest([BID], owner_terms), {"bid-a.pdf": []})[("vendor-a", "CT-01")]
    assert _keyed(owner, "owner_value_not_mapped") == [("ld_rate", "owner"), ("ld_cap", "owner")]


def test_term_level_flags_name_no_field():
    term = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": []})[("vendor-a", "CT-03")]
    unread = [f for f in term.flags if f["code"] == "document_not_read"]
    assert unread and all("field" not in f and "source" not in f for f in unread)


# --- an LD exclusion passage that states no value (ruling R30) --------------------------------------------

LD_RATE_CAP = "LDs are 0.5% per day, capped at 10% of the order value."
LD_WAIVER = "LDs are waived during the first two weeks."


def test_contract_evidence_keeps_an_ld_exclusion_that_states_no_value():
    verified = _verified({"bid-a.pdf": [_row("CT-01", "bid-a.pdf", 12, LD_WAIVER),
                                        _row("CT-01", "bid-a.pdf", 13, "The LD clause is set out in Annex 2.")]})
    found, _ = contract_evidence(verified, _manifest([BID]), failed_vendors=())
    assert [(e.param_id, e.citation.page, e.values) for e in found["bid-a.pdf"]] == [("CT-01", 12, {})]


def test_an_ld_exclusion_passage_changes_no_resolution():
    rate = _ev("bid-a.pdf", "CT-01", 9, LD_RATE_CAP)
    waiver = _ev("bid-a.pdf", "CT-01", 12, LD_WAIVER)
    assert waiver.values == {}
    for passages in ([rate], []):
        manifest = _manifest([BID])
        before = resolve_contract_terms(manifest, {"bid-a.pdf": passages})
        after = resolve_contract_terms(manifest, {"bid-a.pdf": [*passages, waiver]})
        for key, term in before.items():
            got = after[key]
            assert (got.offered, got.fa, got.owner, got.state, got.flags) == \
                   (term.offered, term.fa, term.owner, term.state, term.flags)
            assert got.ld_exclusion_passages == ((waiver.citation,) if key[1] == "CT-01" else ())
    alone = resolve_contract_terms(_manifest([BID]), {"bid-a.pdf": [waiver]})[("vendor-a", "CT-01")]
    assert alone.state == "missing" and alone.offered is None  # never "cited" on its own


def test_an_ld_exclusion_in_an_unread_offer_is_not_kept():
    other = DocumentEntry(file="bid-a2.pdf", role="vendor_bid", vendor_id="vendor-a", counterparty_id="cp-a")
    waiver = _ev("bid-a.pdf", "CT-01", 12, LD_WAIVER)
    term = resolve_contract_terms(_manifest([BID, other]), {"bid-a.pdf": [waiver]})[("vendor-a", "CT-01")]
    assert term.ld_exclusion_passages == ()


def test_a_term_marked_manual_is_missing_with_its_reason(monkeypatch):
    """Ruling R7: a term whose locate_hint starts "MANUAL:" (below the recall bar) is never taken from the
    documents; it is missing, with the reason, so a person checks it by hand."""
    import dataclasses

    from antova_ingest import contract_terms
    from antova_ingest.contract_ontology import MANUAL_PREFIX, MANUAL_REASON

    manual = tuple(dataclasses.replace(p, locate_hint=f"{MANUAL_PREFIX} {p.locate_hint}") if p.id == "CT-09" else p
                   for p in CONTRACT_PARAMETERS)
    monkeypatch.setattr(contract_terms, "CONTRACT_PARAMETERS", manual)
    terms = resolve_contract_terms(_manifest([BID, FA]), {
        "bid-a.pdf": [_ev("bid-a.pdf", "CT-09", 21, TITLE_ON_DELIVERY), _ev("bid-a.pdf", "CT-03", 21, WARRANTY_24)],
        "fa-a.pdf": [_ev("fa-a.pdf", "CT-09", 4, TITLE_ON_DELIVERY)]})
    term = terms[("vendor-a", "CT-09")]
    assert (term.state, term.offered, term.fa, term.owner) == ("missing", None, None, None)
    assert [(f["code"], f["message"]) for f in term.flags] == [("manual_term", MANUAL_REASON)]
    assert MANUAL_REASON == "not reliably found; check by hand"
    assert terms[("vendor-a", "CT-03")].state == "cited"  # other terms are read as before


def test_several_quoted_escalation_clauses_in_one_source_are_not_a_conflict():
    """Ruling R40: a citation-only field (R18) is never compared, so two different price-basis sentences in one
    framework agreement are both quoted, not a conflict; the comparable fields alone decide a conflict."""
    fixed = "The price is fixed and firm from the issue of the Notice to Proceed."
    lump = "The Contract Price is a fixed lump sum."
    a, b = _ev("fa-a.pdf", "CT-13", 4, fixed), _ev("fa-a.pdf", "CT-13", 4, lump)
    terms = resolve_contract_terms(_manifest([BID, FA]), {"bid-a.pdf": [], "fa-a.pdf": [a, b]})
    term = terms[("vendor-a", "CT-13")]
    assert term.state != "conflict"
    # R40 amended: an informational flag, so the cell opens for a person to compare the quoted clauses.
    assert [(f["code"], f["message"], f["field"], f["source"]) for f in term.flags] == [
        ("clauses_quoted_together", "several clauses quoted; compare by hand", "escalation_formula", "fa")]
    assert term.fa.values["price_fixed_after_ntp"].display == "fixed after NTP"
    assert term.fa.values["price_type"].display == "fixed lump sum"
    escalation = term.fa.values["escalation_formula"]
    assert fixed in escalation.display and lump in escalation.display
    assert {a.citation, b.citation} <= set(term.fa.citations)


def test_a_manual_term_keeps_its_contract_cell_open_until_reviewed():
    from antova_review import contract_cell_state, contract_open_items
    from antova_ingest.contract_ontology import MANUAL_REASON

    term = {"param_id": "CT-09", "vendor_id": "vendor-a", "state": "missing", "evidence_hash": "h1",
            "flags": [{"code": "manual_term", "message": MANUAL_REASON}]}
    assert contract_cell_state(term, None) == "open"
    items = contract_open_items([term], [], [], {}, None, None)
    assert [(i.kind, i.param_id) for i in items] == [("contract_cell", "CT-09")]
    review = {"evidence_hash": "h1", "action": "approve"}
    assert contract_cell_state(term, review) == "reviewed"
    assert contract_open_items([term], [], [], {("CT-09", "vendor-a"): review}, None, None) == []


def test_quoted_escalation_clauses_of_an_amendment_of_unknown_order_are_quoted_together():
    """R40 amended, the _unordered path: an amendment dated on or before the agreement it amends, quoting another
    escalation clause, is no conflict; both are quoted and the cell opens for review."""
    from antova_review import contract_cell_state

    original = "The price is subject to escalation by the formula P = P0 x (0.5 + 0.5 x I/I0)."
    amended = "The price is subject to escalation by the formula P = P0 x (0.3 + 0.7 x I/I0)."
    early = DocumentEntry(file="fa-a-amd1.pdf", role="framework_agreement", counterparty_id="cp-a",
                          amends="fa-a.pdf", dated=date(2025, 12, 1))
    terms = resolve_contract_terms(_manifest([BID, FA, early]), {
        "bid-a.pdf": [], "fa-a.pdf": [_ev("fa-a.pdf", "CT-13", 4, original)],
        "fa-a-amd1.pdf": [_ev("fa-a-amd1.pdf", "CT-13", 2, amended)]})
    term = terms[("vendor-a", "CT-13")]
    assert term.state != "conflict"
    assert [f["code"] for f in term.flags] == ["clauses_quoted_together"]
    quoted = term.fa.values["escalation_formula"].display
    assert original in quoted and amended in quoted
    stored = {"param_id": "CT-13", "vendor_id": "vendor-a", "state": term.state, "flags": list(term.flags),
              "evidence_hash": "h"}
    assert contract_cell_state(stored, None) == "open"
