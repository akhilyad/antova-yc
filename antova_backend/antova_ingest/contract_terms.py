"""Each contract term (CT-01 to CT-17) for each vendor, across its offer, its framework agreement and the
owner contract (spec 4.3).

Three sources per term, kept apart so the checks can compare them:

- offered: what the vendor's offer states. An offer silent on a term inherits the framework agreement's
  values only when an incorporation clause (CT-16) in the offer or the agreement makes the agreement part
  of the offer; the citations are then the incorporation clause and the agreement's clause.
- fa: the framework agreement with the vendor's counterparty, linked by the manifest's `counterparty_id`
  on both entries, never by name. None when there is no agreement for that counterparty.
- owner: the owner contract's documents, or else the owner terms the buyer typed in (`buyer_entered`).

Within one source, the strongest evidence tier governs (precedence._tier); two passages on the same tier
that disagree are a conflict. An amendment dated after the document it amends governs the fields it
states and the earlier document the rest, with both citations kept. When the order is not known (no
date, or dated on or before the amended document) and the two disagree, the field is a conflict: no
value is picked. A conflict anywhere in a term makes the term's state "conflict".
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from itertools import groupby
from typing import Literal

from antova_ingest.contract_ontology import CONTRACT_PARAMETERS, CONTRACT_PARAMS_BY_ID
from antova_ingest.ontology import ParameterDef
from antova_ingest.precedence import _tier, add_flag, flag
from antova_ingest.types import Citation, DocumentEntry, Evidence, FieldValue, OwnerTerms, TenderManifest
from antova_ingest.values import EVENT_LABELS, _dec_text, _warranty_basis, parse_field

State = Literal["cited", "inherited", "missing", "conflict"]


@dataclass(frozen=True)
class TermSource:
    kind: Literal["document", "buyer_entered", "inherited"]
    values: dict[str, FieldValue]
    citations: tuple[Citation, ...]  # inherited: (incorporation clause, FA clause)


@dataclass(frozen=True)
class ContractTerm:
    param_id: str
    vendor_id: str
    offered: TermSource | None
    fa: TermSource | None
    owner: TermSource | None
    state: State
    flags: tuple[dict, ...]  # precedence.flag(code, message) dicts


# --- one field, while its documents are combined -------------------------------------------------

@dataclass
class _Slot:
    value: FieldValue | None  # None: unresolved (see conflicts)
    citations: list[Citation]
    conflicts: list[dict] = field(default_factory=list)


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
        slots[f.name] = _Slot(None, [e.citation for e in distinct], [flag(
            "passages_disagree",
            f"{param.id} {f.label}: {said}. Neither outranks the other, so a person decides which governs.")])
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
            state[name] = _Slot(None, prior.citations + new.citations, [*prior.conflicts, *new.conflicts, flag(
                "amendment_order_unknown",
                f"{param.id} {labels[name]}: {reason}, so it is not known whether {_shown(prior)} "
                f"or {_shown(new)} governs.")])


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


def _resolve_documents(param: ParameterDef, entries: list[DocumentEntry], by_file: Mapping[str, DocumentEntry],
                       evidence: Mapping[str, list[Evidence]]) -> _Resolved:
    """One source (an offer, an agreement with its amendments, the owner contract) for one term."""
    read = [e for e in entries if e.file in evidence]
    state: dict[str, _Slot] = {}
    _override(state, _layer(param, _passages([e.file for e in read if e.amends is None], evidence)))
    amendments = [e for e in read if e.amends is not None]
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
    return _Resolved(TermSource("document", values, citations), conflicts)


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

    def unmapped(param_id: str, message: str) -> None:
        flags.setdefault(param_id, []).append(flag("owner_value_not_mapped", message))

    if terms.ld_rate is not None:
        unit, period = terms.ld_rate_basis.split("_per_")
        if unit == "percent":
            put("CT-01", "ld_rate", FieldValue("rate_per_period", (terms.ld_rate, period),
                                               f"{_dec_text(terms.ld_rate)}% per {period}"))
        else:
            unmapped("CT-01", f"The owner LD rate is entered as an amount per {period}, not a percentage of the "
                              f"price; it is used for the back-to-back LD gap, not compared as a rate.")
    if terms.ld_cap is not None:
        if terms.ld_cap_basis == "percent":
            put("CT-01", "ld_cap", FieldValue("percent", float(terms.ld_cap), f"{float(terms.ld_cap):g} %",
                                              qualifier="contract"))
        else:
            unmapped("CT-01", "The owner LD cap is entered as an amount, not a percentage of the price; it is "
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
            unmapped("CT-14", f"The owner governing law \"{terms.law}\" is not a law Antova reads; compare it by hand.")
    return values, flags


# --- the terms ---------------------------------------------------------------------------------------

def _incorporation(source: TermSource | None) -> tuple[Citation, ...]:
    return source.citations if source is not None and "fa_reference" in source.values else ()


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
                add_flag(vendor_flags, "document_not_read",
                         f"{d.file} was not read, so the terms it states are unknown.")
        fa_read = any(d.file in evidence for d in fa_docs)

        incorporation = _incorporation(_resolve_documents(CONTRACT_PARAMS_BY_ID["CT-16"], bids, by_file, evidence).source)
        if not incorporation and fa_read:
            incorporation = _incorporation(
                _resolve_documents(CONTRACT_PARAMS_BY_ID["CT-16"], fa_docs, by_file, evidence).source)

        for param in CONTRACT_PARAMETERS:
            flags = list(vendor_flags)
            offer = _resolve_documents(param, bids, by_file, evidence)
            fa = _resolve_documents(param, fa_docs, by_file, evidence) if fa_read else _Resolved(None, [])
            owner = _resolve_documents(param, owner_docs, by_file, evidence)
            owner_source = owner.source
            if owner_source is None and param.id in form_values:
                owner_source = TermSource("buyer_entered", dict(form_values[param.id]), ())
            if owner.source is None:
                for f in form_flags.get(param.id, []):
                    add_flag(flags, f["code"], f["message"])

            if offer.source is not None:
                offered, state = offer.source, "cited"
            elif incorporation and fa.source is not None:
                citations = tuple(dict.fromkeys((*incorporation, *fa.source.citations)))  # CT-16 itself: one clause
                offered = TermSource("inherited", dict(fa.source.values), citations)
                state = "inherited"
            else:
                offered, state = None, "missing"
                if incorporation and not fa_docs:
                    add_flag(flags, "fa_not_linked",
                             "The offer incorporates a framework agreement, but none is linked to this vendor's "
                             "counterparty in the manifest, so the term cannot be inherited.")
            for conflict in [*offer.conflicts, *fa.conflicts, *owner.conflicts]:
                add_flag(flags, conflict["code"], conflict["message"])
            if offer.conflicts or fa.conflicts or owner.conflicts:
                state = "conflict"
            terms[(vendor_id, param.id)] = ContractTerm(param.id, vendor_id, offered, fa.source, owner_source,
                                                        state, tuple(flags))
    return terms


# --- from the verified stage ---------------------------------------------------------------------------

def contract_evidence(verified: Mapping, manifest: TenderManifest,
                      failed_vendors: Collection[str] = ()) -> tuple[dict[str, list[Evidence]], list[dict]]:
    """The verified stage's contract citations as Evidence per file, with their values read from the quote.

    A bid of a failed vendor is left out (not read). A citation repeated in the stage body counts once; a
    passage with a parameter id that is not a contract term, or that states no value, is not evidence.
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
            if values:
                items.append(Evidence(row["param_id"], citation, values, source=row.get("source", "locator")))
        found[file] = items
    return found, notes
