"""Each contract term (CT-01 to CT-17) for each vendor, across its offer, its framework agreement and the
owner contract (spec 4.3).

Three sources per term, kept apart so the checks can compare them:

- offered: what the vendor's offer states. An offer silent on a term inherits the framework agreement's
  values only when an incorporation clause (CT-16) in the offer or the agreement makes the agreement part
  of the offer; the citations are then the incorporation clause and the agreement's clause.
- fa: the framework agreement with the vendor's counterparty, linked by the manifest's `counterparty_id`
  on both entries, never by name. None when there is no agreement for that counterparty.
- owner: the owner contract's documents, or else the owner terms the buyer typed in (`buyer_entered`).

Inheritance is per field (ruling R25): with an incorporation clause, each field the offer is silent on takes
the agreement's value. Every incorporating passage counts, however worded; a cited CT-16 passage that does
not read as incorporating (a disclaimer) blocks inheritance, and the offer's clauses decide before the
agreement's. A source with a document that was not read has no values at all, and gives and takes nothing
by inheritance: unknown is never shown as inherited.

Within one source, the strongest evidence tier governs (precedence._tier); two passages on the same tier
that disagree are a conflict. An amendment dated after the document it amends governs the fields it
states and the earlier document the rest, with both citations kept. When the order is not known (no
date, or dated on or before the amended document) and the two disagree, the field is a conflict: no
value is picked. A conflict anywhere in a term makes the term's state "conflict".

A CT-01 passage that excludes or waives LDs (values.NO_LD) is kept apart in `ld_exclusion_passages`, with or
without a value, so the back-to-back LD check can read its scope (ruling R30). It takes no part in the
resolution: it never makes a term cited, inherited or a conflict.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from itertools import groupby
from typing import Literal

from antova_ingest.contract_ontology import CONTRACT_PARAMETERS, CONTRACT_PARAMS_BY_ID, MANUAL_REASON, is_manual
from antova_ingest.ontology import ParameterDef
from antova_ingest.precedence import _tier, add_flag, flag
from antova_ingest.types import Citation, DocumentEntry, Evidence, FieldValue, OwnerTerms, TenderManifest
from antova_ingest.values import EVENT_LABELS, NO_LD, _dec_text, _warranty_basis, parse_field

State = Literal["cited", "inherited", "missing", "conflict"]


@dataclass(frozen=True)
class TermSource:
    kind: Literal["document", "buyer_entered", "inherited"]
    values: dict[str, FieldValue]
    citations: tuple[Citation, ...]  # inherited: (incorporation clause, FA clause)
    # The fields whose values come from the framework agreement through an incorporation clause (ruling R25):
    # all of them for kind "inherited", the ones the offer is silent on for an offer that states the rest.
    inherited_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ContractTerm:
    param_id: str
    vendor_id: str
    offered: TermSource | None
    fa: TermSource | None
    owner: TermSource | None
    state: State
    # precedence.flag(code, message) dicts. A flag about one field also carries "field" (the field name) and
    # "source" ("offered", "fa" or "owner"), so a reader matches on those keys, never on the message (ruling R27).
    # A "document_not_read" flag carries "file" and "role" (the manifest role of the document not read).
    flags: tuple[dict, ...]
    # An FA (as amended) is linked to the vendor's counterparty, even if it is silent on this term or was
    # not read (ruling R26). False: no framework agreement for this vendor.
    fa_present: bool
    # CT-01 only: the offer's cited passages that exclude or waive LDs (values.NO_LD) and are not among
    # `offered.citations`, a passage that states no value included; and the agreement's, when an LD rate or cap
    # is inherited from it. They are read for their scope only (contract_checks.back_to_back_ld, ruling R30).
    ld_exclusion_passages: tuple[Citation, ...] = ()


# --- one field, while its documents are combined -------------------------------------------------

@dataclass
class _Slot:
    value: FieldValue | None  # None: unresolved (see conflicts)
    citations: list[Citation]
    conflicts: list[dict] = field(default_factory=list)


def _field_flag(code: str, message: str, field_name: str, source: str | None = None) -> dict:
    """A flag about one field. `source` is filled in when the term is assembled (resolve_contract_terms)."""
    entry = {**flag(code, message), "field": field_name}
    return entry if source is None else {**entry, "source": source}


def _add(flags: list[dict], entry: dict) -> None:
    if entry not in flags:
        flags.append(entry)


def _same(a: FieldValue | None, b: FieldValue | None) -> bool:
    return a is not None and b is not None and (a.kind, a.value, a.qualifier) == (b.kind, b.value, b.qualifier)


def _where(c: Citation) -> str:
    return f"{c.filename} p.{c.page}"


def _shown(slot: _Slot) -> str:
    return slot.value.display if slot.value is not None else "an unresolved value"


def _layer(param: ParameterDef, passages: list[Evidence]) -> dict[str, _Slot]:
    """The fields one set of documents states for a term: the strongest tier governs, and two passages
    on that tier stating different values are a conflict."""
    slots: dict[str, _Slot] = {}
    for f in param.fields:
        candidates = [e for e in passages if e.param_id == param.id and f.name in e.values]
        if not candidates:
            continue
        best = min(_tier(e) for e in candidates)
        distinct: list[Evidence] = []  # the first passage per distinct value, in page order
        for e in (e for e in candidates if _tier(e) == best):
            if not any(_same(e.values[f.name], d.values[f.name]) for d in distinct):
                distinct.append(e)
        if len(distinct) == 1:
            slots[f.name] = _Slot(distinct[0].values[f.name], [distinct[0].citation])
            continue
        said = "; ".join(f"{_where(e.citation)} says {e.values[f.name].display}" for e in distinct)
        slots[f.name] = _Slot(None, [e.citation for e in distinct], [_field_flag(
            "passages_disagree",
            f"{param.id} {f.label}: {said}. Neither outranks the other, so a person decides which governs.", f.name)])
    return slots


def _override(state: dict[str, _Slot], layer: dict[str, _Slot]) -> None:
    """A later document governs the fields it states; the earlier citation is kept."""
    for name, new in layer.items():
        prior = state.get(name)
        state[name] = new if prior is None else _Slot(new.value, prior.citations + new.citations, list(new.conflicts))


def _unordered(state: dict[str, _Slot], layer: dict[str, _Slot], param: ParameterDef, reason: str) -> None:
    """A document whose order against the others is not known: where it disagrees, nothing is picked."""
    labels = {f.name: f.label for f in param.fields}
    for name, new in layer.items():
        prior = state.get(name)
        if prior is None:
            state[name] = new  # nothing else states it: the order does not matter
        elif not prior.conflicts and not new.conflicts and _same(prior.value, new.value):
            prior.citations.extend(new.citations)
        else:
            state[name] = _Slot(None, prior.citations + new.citations, [*prior.conflicts, *new.conflicts, _field_flag(
                "amendment_order_unknown",
                f"{param.id} {labels[name]}: {reason}, so it is not known whether {_shown(prior)} "
                f"or {_shown(new)} governs.", name)])


def _order_reason(entry: DocumentEntry, by_file: Mapping[str, DocumentEntry]) -> str | None:
    """None when every amendment in the entry's chain is dated after the document it amends; otherwise why not."""
    current = entry
    while current.amends is not None:
        target = by_file[current.amends]
        if current.dated is None:
            return f"{current.file} amends {target.file} but has no date"
        if target.dated is None:
            return f"{current.file} amends {target.file}, which has no date"
        if current.dated <= target.dated:
            return (f"{current.file} amends {target.file} but is dated {current.dated.isoformat()}, "
                    f"not after {target.dated.isoformat()}")
        current = target
    return None


@dataclass
class _Resolved:
    source: TermSource | None
    conflicts: list[dict]
    # The fields the documents state (a field in conflict included), and each one's citations.
    field_citations: dict[str, tuple[Citation, ...]] = field(default_factory=dict)
    # False when a document of the source was not read: what it states is unknown, so the source has no
    # values (not the values of the documents that were read) and neither gives nor takes inherited values.
    complete: bool = True


def _resolve_documents(param: ParameterDef, entries: list[DocumentEntry], by_file: Mapping[str, DocumentEntry],
                       evidence: Mapping[str, list[Evidence]]) -> _Resolved:
    """One source (an offer, an agreement with its amendments, the owner contract) for one term."""
    if any(e.file not in evidence for e in entries):
        # An unread amendment may change what the read documents say; an unread offer is not a silent one.
        return _Resolved(None, [], complete=False)
    state: dict[str, _Slot] = {}
    _override(state, _layer(param, _passages([e.file for e in entries if e.amends is None], evidence)))
    amendments = [e for e in entries if e.amends is not None]
    ordered = [e for e in amendments if _order_reason(e, by_file) is None]
    for dated, group in groupby(sorted(ordered, key=lambda e: e.dated), key=lambda e: e.dated):
        # Amendments of one date are ordered against what they amend, not against each other.
        same_day: dict[str, _Slot] = {}
        for entry in group:
            _unordered(same_day, _layer(param, _passages([entry.file], evidence)), param,
                       f"{entry.file} is dated {dated.isoformat()}, the same day as another amendment")
        _override(state, same_day)
    for entry in amendments:
        reason = _order_reason(entry, by_file)
        if reason is not None:
            _unordered(state, _layer(param, _passages([entry.file], evidence)), param, reason)
    if not state:
        return _Resolved(None, [])
    values = {name: slot.value for name, slot in state.items() if slot.value is not None}
    citations = tuple(dict.fromkeys(c for f in param.fields if f.name in state for c in state[f.name].citations))
    conflicts = [c for f in param.fields if f.name in state for c in state[f.name].conflicts]
    stated = {f.name: tuple(state[f.name].citations) for f in param.fields if f.name in state}
    return _Resolved(TermSource("document", values, citations), conflicts, stated)


def _passages(files: list[str], evidence: Mapping[str, list[Evidence]]) -> list[Evidence]:
    return sorted((e for file in files for e in evidence[file]), key=lambda e: e.citation.page)


# --- the owner terms the buyer typed in ------------------------------------------------------------

def owner_term_values(terms: OwnerTerms) -> tuple[dict[str, dict[str, FieldValue]], dict[str, list[dict]]]:
    """The buyer-entered owner terms as CT field values (CT-01, CT-02, CT-03, CT-14), shaped as the parsers
    shape them, and a flag for each entered value that has no such field."""
    values: dict[str, dict[str, FieldValue]] = {}
    flags: dict[str, list[dict]] = {}

    def put(param_id: str, name: str, value: FieldValue) -> None:
        values.setdefault(param_id, {})[name] = value

    def unmapped(param_id: str, name: str, message: str) -> None:
        flags.setdefault(param_id, []).append(_field_flag("owner_value_not_mapped", message, name, "owner"))

    if terms.ld_rate is not None:
        unit, period = terms.ld_rate_basis.split("_per_")
        if unit == "percent":
            put("CT-01", "ld_rate", FieldValue("rate_per_period", (terms.ld_rate, period),
                                               f"{_dec_text(terms.ld_rate)}% per {period}"))
        else:
            unmapped("CT-01", "ld_rate", f"The owner LD rate is entered as an amount per {period}, not a percentage of the "
                              f"price; it is used for the back-to-back LD gap, not compared as a rate.")
    if terms.ld_cap is not None:
        if terms.ld_cap_basis == "percent":
            put("CT-01", "ld_cap", FieldValue("percent", float(terms.ld_cap), f"{float(terms.ld_cap):g} %",
                                              qualifier="contract"))
        else:
            unmapped("CT-01", "ld_cap", "The owner LD cap is entered as an amount, not a percentage of the price; it is "
                              "used for the back-to-back LD gap, not compared as a cap.")
    if terms.liability_cap is not None:
        cap = terms.liability_cap
        shown = f"{cap.amount:,.0f}" if cap.amount == cap.amount.to_integral_value() else f"{cap.amount:,.2f}"
        put("CT-02", "liability_cap_money", FieldValue("money_cap", cap.amount, f"{cap.currency} {shown}",
                                                       qualifier=cap.currency))
    if terms.warranty_months is not None:
        # The months carry the start point as a warranty basis, read with the parser's own words.
        basis = _warranty_basis(f"from {EVENT_LABELS[terms.warranty_start]}") if terms.warranty_start else "unspecified"
        put("CT-03", "warranty_months", FieldValue("months", terms.warranty_months, str(terms.warranty_months),
                                                   qualifier=basis))
    if terms.warranty_start is not None:
        put("CT-03", "warranty_start", FieldValue("event", terms.warranty_start, EVENT_LABELS[terms.warranty_start]))
    if terms.law is not None:
        # As typed ("English law"), as a country ("England and Wales"), or as a code ("english", "new_york").
        law = next((v for v in (parse_field("law", text, "law") for text in (
            terms.law, f"the laws of {terms.law}", f"{terms.law.replace('_', ' ').title()} law")) if v), None)
        if law is not None:
            put("CT-14", "law", law)
        else:
            unmapped("CT-14", "law", f"The owner governing law \"{terms.law}\" is not a law Antova reads; compare it by hand.")
    return values, flags


# --- the terms ---------------------------------------------------------------------------------------

def _incorporation(entries: list[DocumentEntry],
                   evidence: Mapping[str, list[Evidence]]) -> tuple[tuple[Citation, ...], dict | None, bool]:
    """The incorporation clauses (CT-16) in a set of documents: (citations, blocking flag, any passage cited).

    Every passage that reads as incorporating the agreement counts, however it is worded. A cited CT-16
    passage that does not read as incorporating it (a disclaimer such as "not subject to the Framework
    Agreement", or wording the reader does not follow) blocks inheritance, even beside one that does.
    """
    passages = sorted((e for d in entries for e in evidence.get(d.file, []) if e.param_id == "CT-16"),
                      key=lambda e: (e.citation.filename, e.citation.page))
    unclear = [e for e in passages if "fa_reference" not in e.values]
    if unclear:
        e = unclear[0]
        return (), flag("incorporation_unclear",
                        f"{_where(e.citation)} cites a clause on the framework agreement that does not read as "
                        f"incorporating it (\"{e.citation.quote}\"), so no term is inherited from the agreement. "
                        f"Check the clause by hand."), True
    return tuple(dict.fromkeys(e.citation for e in passages)), None, bool(passages)


def resolve_contract_terms(manifest: TenderManifest,
                           evidence: Mapping[str, list[Evidence]],  # file name -> verified CT evidence
                           ) -> dict[tuple[str, str], ContractTerm]:
    """Every contract term for every vendor bid, keyed (vendor_id, param_id).

    A document missing from `evidence` was not read (refused by the zero-retention guard, unreadable,
    or its vendor failed); its terms are unknown, and every term says so in a flag.
    """
    by_file = {d.file: d for d in manifest.documents}
    owner_docs = [d for d in manifest.documents if d.role == "owner_contract"]
    owner_terms = manifest.project.owner_terms if manifest.project else None
    form_values, form_flags = owner_term_values(owner_terms) if owner_terms else ({}, {})
    vendors = list(dict.fromkeys(d.vendor_id for d in manifest.documents if d.role == "vendor_bid" and d.vendor_id))

    terms: dict[tuple[str, str], ContractTerm] = {}
    for vendor_id in vendors:
        bids = [d for d in manifest.documents if d.role == "vendor_bid" and d.vendor_id == vendor_id]
        counterparties = {d.counterparty_id for d in bids if d.counterparty_id}
        vendor_flags: list[dict] = []
        fa_docs: list[DocumentEntry] = []
        if len(counterparties) == 1:
            (counterparty,) = counterparties
            fa_docs = [d for d in manifest.documents
                       if d.role == "framework_agreement" and d.counterparty_id == counterparty]
        elif counterparties:
            add_flag(vendor_flags, "counterparty_ambiguous",
                     f"The offers of {vendor_id} name more than one counterparty ({', '.join(sorted(counterparties))}), "
                     f"so no framework agreement is linked.")
        for d in [*bids, *fa_docs, *owner_docs]:
            if d.file not in evidence:
                # "file" and "role" say which document, so a reader matches on keys, never the message (R27).
                _add(vendor_flags, {**flag("document_not_read", f"{d.file} was not read, so the terms it states "
                                                                f"are unknown."), "file": d.file, "role": d.role})
        offer_read = all(d.file in evidence for d in bids)
        fa_read = bool(fa_docs) and all(d.file in evidence for d in fa_docs)
        # The offer's own clause decides; a disclaimer in it wins over the agreement's call-off clause.
        # The agreement's clause counts only when the offer cites none.
        incorporation, blocked = (), None
        if offer_read:
            incorporation, blocked, offer_cites = _incorporation(bids, evidence)
            if not offer_cites and fa_read:
                incorporation, blocked, _ = _incorporation(fa_docs, evidence)

        for param in CONTRACT_PARAMETERS:
            flags = list(vendor_flags)
            if is_manual(param):  # ruling R7: below the recall bar, so a person checks it by hand
                terms[(vendor_id, param.id)] = ContractTerm(
                    param.id, vendor_id, None, None, None, "missing",
                    (*flags, flag("manual_term", MANUAL_REASON)), fa_present=bool(fa_docs))
                continue
            offer = _resolve_documents(param, bids, by_file, evidence)
            fa = _resolve_documents(param, fa_docs, by_file, evidence)
            owner = _resolve_documents(param, owner_docs, by_file, evidence)
            owner_source = owner.source
            if owner_source is None and param.id in form_values:
                owner_source = TermSource("buyer_entered", dict(form_values[param.id]), ())
            if owner.source is None:
                for f in form_flags.get(param.id, []):
                    _add(flags, f)

            # Ruling R25: with an incorporation clause, each field the offer is silent on takes the FA's value.
            # A field the offer states, even in conflict, is never filled from the FA.
            # Both sides must be wholly read: nothing unknown is given or taken.
            silent = ([f.name for f in param.fields
                       if f.name in fa.source.values and f.name not in offer.field_citations]
                      if offer.complete and fa.complete and fa.source is not None else [])
            inherited = silent if incorporation else []
            if silent and blocked:
                add_flag(flags, blocked["code"], blocked["message"])
            if inherited:
                fa_cites = [c for name in inherited for c in fa.field_citations[name]]
                own = offer.source.citations if offer.source is not None else ()
                citations = tuple(dict.fromkeys((*own, *incorporation, *fa_cites)))  # CT-16 itself: one clause
                values = {**(offer.source.values if offer.source is not None else {}),
                          **{name: fa.source.values[name] for name in inherited}}
                values = {f.name: values[f.name] for f in param.fields if f.name in values}  # ontology order
                kind = "document" if offer.source is not None else "inherited"
                offered = TermSource(kind, values, citations, frozenset(inherited))
                state = "inherited"
            elif offer.source is not None:
                offered, state = offer.source, "cited"
            else:
                offered, state = None, "missing"
                if incorporation and not fa_docs:
                    add_flag(flags, "fa_not_linked",
                             "The offer incorporates a framework agreement, but none is linked to this vendor's "
                             "counterparty in the manifest, so the term cannot be inherited.")
            for source, resolved in (("offered", offer), ("fa", fa), ("owner", owner)):
                for conflict in resolved.conflicts:
                    _add(flags, {**conflict, "source": source})
            if offer.conflicts or fa.conflicts or owner.conflicts:
                state = "conflict"
            exclusions: tuple[Citation, ...] = ()
            if param.id == "CT-01" and offer.complete:  # an unread bid may state anything
                docs = [*bids, *(fa_docs if {"ld_rate", "ld_cap"} & set(inherited) else [])]
                exclusions = _ld_exclusion_passages(docs, evidence, offered.citations if offered else ())
            terms[(vendor_id, param.id)] = ContractTerm(param.id, vendor_id, offered, fa.source, owner_source,
                                                        state, tuple(flags), fa_present=bool(fa_docs),
                                                        ld_exclusion_passages=exclusions)
    return terms


def _ld_exclusion_passages(entries: list[DocumentEntry], evidence: Mapping[str, list[Evidence]],
                           cited: tuple[Citation, ...]) -> tuple[Citation, ...]:
    """The CT-01 passages of these documents that exclude or waive LDs and are not already cited, in page
    order (ruling R30)."""
    passages = sorted((e for d in entries for e in evidence.get(d.file, []) if e.param_id == "CT-01"),
                      key=lambda e: (e.citation.filename, e.citation.page))
    return tuple(dict.fromkeys(e.citation for e in passages
                               if NO_LD.search(e.citation.quote) and e.citation not in cited))


# --- from the verified stage ---------------------------------------------------------------------------

def contract_evidence(verified: Mapping, manifest: TenderManifest,
                      failed_vendors: Collection[str] = ()) -> tuple[dict[str, list[Evidence]], list[dict]]:
    """The verified stage's contract citations as Evidence per file, with their values read from the quote.

    A bid of a failed vendor is left out (not read). A citation repeated in the stage body counts once; a
    passage with a parameter id that is not a contract term, or that states no value, is not evidence,
    except a CT-16 passage (a disclaimer of the agreement blocks inheritance) and a CT-01 passage that
    excludes or waives LDs (its scope decides the back-to-back LD check, ruling R30).
    Notes on values not believed (pipeline.read_values) are returned as {"file", "param_id", "code", "message"}.
    """
    from antova_ingest.pipeline import read_values  # imported here: the pipeline's contract stage imports this module

    rows_by_file = verified.get("contract", {}).get("citations", {})
    locales = verified.get("locales", {})
    entries = {d.file: d for d in manifest.documents}
    found: dict[str, list[Evidence]] = {}
    notes: list[dict] = []
    for file, rows in rows_by_file.items():
        entry = entries.get(file)
        if entry is None or (entry.role == "vendor_bid" and entry.vendor_id in failed_vendors):
            continue
        seen: set[tuple] = set()
        items: list[Evidence] = []
        for row in rows:
            citation = Citation.from_dict(row["citation"])
            key = (row["param_id"], citation.page, citation.quote)
            if row["param_id"] not in CONTRACT_PARAMS_BY_ID or key in seen:
                continue
            seen.add(key)
            values, read_notes = read_values(row["param_id"], citation, locales.get(file, "unknown"),
                                             manifest.equipment_class)
            notes.extend({"file": file, "param_id": row["param_id"], **n} for n in read_notes)
            # A CT-16 passage that does not read as incorporating the agreement is kept: it may be a disclaimer,
            # which blocks inheritance (_incorporation).
            # A CT-01 passage that excludes or waives LDs is kept too: "LDs are waived during the first two
            # weeks" states no value, but it limits what the vendor pays back (ruling R30).
            keep = row["param_id"] == "CT-16" or (row["param_id"] == "CT-01" and NO_LD.search(citation.quote))
            if values or keep:
                items.append(Evidence(row["param_id"], citation, values, source=row.get("source", "locator")))
        found[file] = items
    return found, notes
