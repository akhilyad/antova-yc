"""Deterministic value parsers and checks (spec §9).

Each parser reads one verified quote and returns a typed value or None.
Pint handles every physical unit; nothing here is guessed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pint

from antova_ingest.ontology import ORDINALS, FieldDef, ParameterDef
from antova_ingest.types import FieldKind, FieldValue

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity


@dataclass(frozen=True)
class IPCode:
    solid: int
    liquid: int

    @property
    def display(self) -> str:
        return f"IP{self.solid}{self.liquid}"


CARVE_OUT_PHRASES: tuple[str, ...] = (
    "excluded", "not included", "charged extra", "at extra cost", "billed per tariff",
    "by others", "optional", "supplied loose", "on request",
)
_UPGRADE_EXTRA = re.compile(r"\bupgrade\b.*\bextra\b", re.IGNORECASE)

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_LEAD = r"(?<![\w.\-])"
_QUANTITY_PATTERNS = {
    "current": re.compile(_LEAD + _NUM + r"\s*(kA|A)\b"),
    "duration": re.compile(_LEAD + _NUM + r"\s*(seconds|second|secs|sec|s)\b"),
    "voltage": re.compile(_LEAD + _NUM + r"\s*(kV|V)\b"),
}
_TEMPERATURE = re.compile(r"([+-]?)" + _NUM + r"\s*°\s*([CF])\b|" + _LEAD + _NUM + r"\s*K\b")
_PINT_UNITS = {
    "kA": ureg.kiloampere, "A": ureg.ampere, "kV": ureg.kilovolt, "V": ureg.volt,
    "s": ureg.second, "°C": ureg.degC, "°F": ureg.degF, "K": ureg.kelvin, "m": ureg.meter, "ft": ureg.foot,
}
PINT_UNITS = _PINT_UNITS  # public: the codec rebuilds quantities from a unit label
_DIMENSIONS = {"current": "[current]", "duration": "[time]", "voltage": "[length] ** 2 * [mass] / [current] / [time] ** 3"}
# Ordered preferences when a quote holds several values of one kind.
_PREFERRED_CONTEXT = {
    "ik": [re.compile(r"short-time|\bik\b", re.IGNORECASE), re.compile(r"withstand", re.IGNORECASE)],
    "ik_duration": [re.compile(r"short-time|\bik\b", re.IGNORECASE), re.compile(r"withstand", re.IGNORECASE)],
    "rated_current": [re.compile(r"continuous|rated current|busbar", re.IGNORECASE)],
}
# Clauses describing a different rating than Ik: arc tests, peak and making currents.
_NOT_IK = re.compile(r"\bIAC\b|internal arc|arc classification|\bpeak\b|\bIp\b|\bmaking\b", re.IGNORECASE)


def _clauses(sentence: str) -> list[str]:
    """Split on ', ' (never inside numbers such as 50,000)."""
    return [c for c in re.split(r",\s+", sentence) if c]


clauses = _clauses
# A fault or arc rating is not the busbar's continuous current.
_NOT_CONTINUOUS = re.compile(r"short[- ]time|withstand|\bpeak\b|\bmaking\b|\bIk\b|\bIp\b|\bIAC\b|internal arc",
                             re.IGNORECASE)
# Per field: clauses matching this describe some other rating and are never read for the field.
NEGATIVE_CONTEXT = {"ik": _NOT_IK, "ik_duration": _NOT_IK, "rated_current": _NOT_CONTINUOUS}


def _normalize_text(text: str) -> str:
    return text.replace("−", "-").replace("–", "-").replace("—", "-")


# Abbreviations whose full stop never ends a sentence, and ones that do not when a number or a
# lower-case word follows ("Ed. 2", "Pos. 44", "No. 7"; but "No. Routine tests only." is two sentences).
_ABBREVIATIONS_ALWAYS = frozenset({"e.g", "i.e", "approx", "ca", "cf", "vs", "incl", "excl", "resp", "bzw", "z.b"})
_ABBREVIATIONS_BEFORE_VALUE = frozenset({
    "ed", "pos", "fig", "cl", "para", "rev", "ref", "no", "nos", "nr", "max", "min", "sec", "art", "item",
    "typ", "std", "dwg", "doc", "vol", "pt", "abs", "ziff",
})
_CLOSERS = "\"')]"


def is_abbreviation(token: str, next_token: str | None) -> bool:
    """True when `token` ends in an abbreviation's full stop, so the sentence goes on with `next_token`."""
    stripped = token.rstrip(_CLOSERS)
    if not stripped.endswith(".") or not next_token:
        return False
    core = stripped.rstrip(".").lstrip("([\"'").lower()
    if core in _ABBREVIATIONS_ALWAYS:
        return True
    first = next_token.lstrip("([\"'")[:1]
    return core in _ABBREVIATIONS_BEFORE_VALUE and (first.isdigit() or first.islower())


def split_sentences(text: str) -> list[str]:
    parts = [p for p in re.split(r"(?<=[.;!?])\s+", _normalize_text(text).strip()) if p]
    sentences: list[str] = []
    for part in parts:
        if sentences and is_abbreviation(sentences[-1].split()[-1], part.split()[0]):
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences


# --- Number locale -------------------------------------------------------------
#
# "2,500" is two and a half in Hamburg and two thousand five hundred in Houston. A token that
# can only be read one way ("31,5", "418.000,00", "1,250,000") is read that way. A token that
# can be read both ways takes the document's locale, which is detected from the unambiguous
# value tokens in that document. With no such evidence it is not read at all.

_NUMBER_TOKEN = re.compile(r"(?<![\w.,])\d[\d.,]*\d(?!\d)|(?<![\w.,])\d")
_CURRENCY = r"€|\$|£|EUR|USD|GBP|CHF|TRY|SEK|NOK|DKK|PLN"
_MONEY_BEFORE = re.compile(r"(?:" + _CURRENCY + r")\s?$")
_MONEY_AFTER = re.compile(r"\s?(?:" + _CURRENCY + r")(?![A-Za-z])")
_UNIT_AFTER = re.compile(
    r"\s*(?:kVA|MVA|kV|kA|kW|MW|kg|mm|cm|ms|Hz|bar|secs?|seconds?|hours?|hrs?|days?|months?|years?|"
    r"°\s*[CF]|[AVKsmth%])(?![A-Za-z])")
AMBIGUOUS_MARK = "[?]"


def _classify_number(token: str) -> str:
    """'plain', 'en' (dot decimal), 'eu' (comma decimal), 'ambiguous' or 'other' (not a number we read)."""
    if "," not in token and "." not in token:
        return "plain"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", token):
        return "en"
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", token):
        return "eu"
    if "," in token and "." in token:
        return "other"
    separator = "," if "," in token else "."
    as_decimal, as_grouping = ("eu", "en") if separator == "," else ("en", "eu")
    head, *groups = token.split(separator)
    grouped = len(head) <= 3 and not head.startswith("0") and all(len(g) == 3 for g in groups)
    if len(groups) >= 2:
        return as_grouping if grouped else "other"  # a decimal mark cannot repeat
    return "ambiguous" if grouped else as_decimal


def _grouping_locale(token: str) -> str:
    """The locale in which this token's separator groups thousands."""
    return "en" if "," in token else "eu"


def _is_money(text: str, match: re.Match) -> bool:
    return bool(_MONEY_BEFORE.search(text[:match.start()]) or _MONEY_AFTER.match(text, match.end()))


def _to_plain_number(token: str, locale: str) -> str:
    if locale == "en":
        return token.replace(",", "")
    return token.replace(".", "").replace(",", ".")


def detect_locale(texts) -> str:
    """'en', 'eu', 'mixed' (evidence for both) or 'unknown' (none), from a document's page texts.

    Only tokens that carry a unit or a currency count, so a clause number ("4.2") or a
    material code is not evidence. Money has at most two decimals, so a price with a
    three-digit group ("418.000") shows which separator groups thousands.
    """
    seen = set()
    for text in texts:
        text = _normalize_text(text)
        for match in _NUMBER_TOKEN.finditer(text):
            kind = _classify_number(match.group(0))
            money = _is_money(text, match)
            if kind == "ambiguous" and money:
                kind = _grouping_locale(match.group(0))
            if kind in ("en", "eu") and (money or _UNIT_AFTER.match(text, match.end())):
                seen.add(kind)
    if not seen:
        return "unknown"
    return seen.pop() if len(seen) == 1 else "mixed"


def normalize_numbers(text: str, locale: str) -> tuple[str, list[str]]:
    """Rewrite every number in `text` with a dot decimal and no grouping, for the parsers.

    Returns the text and the tokens that could not be read: ambiguous ones in a document
    with no single locale. Those are replaced by a mark no parser reads, never guessed.
    """
    ambiguous: list[str] = []

    def rewrite(match: re.Match) -> str:
        token = match.group(0)
        kind = _classify_number(token)
        if kind in ("plain", "other"):
            return token
        if kind == "ambiguous":
            if _is_money(text, match):
                kind = _grouping_locale(token)
            elif locale in ("en", "eu"):
                kind = locale
            else:
                ambiguous.append(token)
                return AMBIGUOUS_MARK
        return _to_plain_number(token, kind)

    return _NUMBER_TOKEN.sub(rewrite, text), ambiguous


def _has_carve_out(sentence: str) -> bool:
    lower = sentence.lower()
    return any(phrase in lower for phrase in CARVE_OUT_PHRASES) or bool(_UPGRADE_EXTRA.search(sentence))


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _fmt(magnitude: float) -> str:
    return f"{magnitude:g}"


def _parse_quantity(kind: str, quote: str, field_name: str) -> FieldValue | None:
    pattern = _QUANTITY_PATTERNS[kind]
    found: list[tuple[str, re.Match]] = []
    for sentence in split_sentences(quote):
        if _has_carve_out(sentence):
            continue
        negative = NEGATIVE_CONTEXT.get(field_name)
        parts = _clauses(sentence) if negative else [sentence]
        for part in parts:
            if negative and negative.search(part):
                continue
            found.extend((part, m) for m in pattern.finditer(part))
    if not found:
        return None
    if len(found) > 1:
        for preferred in _PREFERRED_CONTEXT.get(field_name, []):
            in_context = [item for item in found if preferred.search(item[0])]
            if in_context:
                found = in_context
                break
    _, match = found[0]
    magnitude = _number(match.group(1))
    unit = "s" if kind == "duration" else match.group(2)
    quantity = Q_(magnitude, _PINT_UNITS[unit])
    if not quantity.check(_DIMENSIONS[kind]):
        return None
    return FieldValue(kind, quantity, f"{_fmt(magnitude)} {unit}")  # type: ignore[arg-type]


_WITH_DERATING = re.compile(r"\bwith\b[^;,()]*derat", re.IGNORECASE)


def _temperature_value(m: re.Match) -> FieldValue:
    if m.group(2) is not None:
        magnitude = _number(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        unit = "°" + m.group(3)
    else:
        magnitude = _number(m.group(4))
        unit = "K"
    return FieldValue("temperature", Q_(magnitude, _PINT_UNITS[unit]), f"{_fmt(magnitude)} {unit}")


def _parse_temperature(quote: str) -> FieldValue | None:
    """The rated maximum: the upper end of an explicit 'X to Y' range, else the first value.

    Clauses that describe operation *with* derating are ignored.
    """
    text = _normalize_text(quote)
    kept = " ; ".join(c for c in re.split(r"[;()]|,\s+", text) if c.strip() and not _WITH_DERATING.search(c))
    matches = list(_TEMPERATURE.finditer(kept))
    if not matches:
        return None
    for first, second in zip(matches, matches[1:]):
        if re.fullmatch(r"\s*(?:to|up to|-)\s*", kept[first.end():second.start()], re.IGNORECASE):
            a, b = _temperature_value(first), _temperature_value(second)
            return max((a, b), key=lambda v: v.value.to(ureg.kelvin).magnitude)
    return _temperature_value(matches[0])


_COPPER = re.compile(r"copper|cu-etp|\bhdhc\b", re.IGNORECASE)
_ALUMINIUM = re.compile(r"alumin(?:i)?um|almgsi", re.IGNORECASE)


def _parse_material(quote: str) -> FieldValue | None:
    copper, aluminium = bool(_COPPER.search(quote)), bool(_ALUMINIUM.search(quote))
    if copper == aluminium:
        return None
    code = "Cu-ETP" if copper else "Al"
    return FieldValue("material", code, code)


def _parse_ip(quote: str) -> FieldValue | None:
    m = re.search(r"\bIP\s?([0-6])([0-9])\b", quote)
    if not m:
        return None
    code = IPCode(int(m.group(1)), int(m.group(2)))
    return FieldValue("ip", code, code.display)


def _parse_iac(quote: str) -> FieldValue | None:
    m = re.search(r"\b(AFLR|AFL|AF)\b", quote)
    return FieldValue("iac", m.group(1), m.group(1)) if m else None


_PRP_NEGATED = re.compile(r"\b(?:without|no)\s+PRP\b|\bPRP\b[^.;]*\bnot\b", re.IGNORECASE)


# What a buyer reads for each code. The code stays the value the checks compare.
_PROTOCOL_DISPLAY = {
    "IEC61850_ED2_PRP": "IEC 61850 Ed. 2 with PRP",
    "IEC61850": "IEC 61850, not Ed. 2 with PRP",
    "OTHER": "not IEC 61850",
}


def _parse_protocol(quote: str) -> FieldValue | None:
    # Only what is offered counts: clauses that carve something out are ignored,
    # so "IEC 61850 relays, PRP not included" still offers IEC 61850.
    offered = [c for s in split_sentences(quote) for c in _clauses(s) if not _has_carve_out(c)]
    text = " ".join(offered)
    if "61850" in text:
        prp = any(re.search(r"\bPRP\b", s) and not _PRP_NEGATED.search(s) for s in offered)
        edition_1 = re.search(r"\bEd(?:ition)?\.?\s?1\b", text, re.IGNORECASE)
        code = "IEC61850_ED2_PRP" if prp and not edition_1 else "IEC61850"
    elif re.search(r"modbus|rj45|rs485", text, re.IGNORECASE):
        code = "OTHER"
    else:
        return None
    return FieldValue("protocol", code, _PROTOCOL_DISPLAY[code])


_MARSHALLING_CONTEXT = re.compile(r"marshalling|terminal|wiring", re.IGNORECASE)
_NOT_PREWIRED = re.compile(r"loose|excluded|by others|field termination|customer (?:scada )?wiring", re.IGNORECASE)
_PREWIRED = re.compile(r"pre-?wired|factory wired", re.IGNORECASE)


def _parse_marshalling(quote: str) -> FieldValue | None:
    sentences = [s for s in split_sentences(quote) if _MARSHALLING_CONTEXT.search(s)]
    if any(_NOT_PREWIRED.search(s) for s in sentences):
        return FieldValue("marshalling", "NOT_PREWIRED", "not pre-wired")
    if any(_PREWIRED.search(s) for s in sentences):
        return FieldValue("marshalling", "PREWIRED", "pre-wired")
    return None


_DAYS = re.compile(r"(?<![\w/])(\d+)\s*-?\s*days?\b", re.IGNORECASE)


def _parse_fat_days(quote: str) -> FieldValue | None:
    sentences = split_sentences(quote)
    for s in sentences:
        lower = s.lower()
        if "witness" in lower and not _has_carve_out(s) and not lower.startswith("no "):
            m = _DAYS.search(s)
            if m:
                days = int(m.group(1))
                return FieldValue("fat_days", days, str(days))
    for s in sentences:
        lower = s.lower()
        excluded_witnessing = "witness" in lower and (_has_carve_out(s) or "no witness" in lower)
        routine_only = "routine" in lower and "only" in lower
        if excluded_witnessing or routine_only:
            return FieldValue("fat_days", 0, "0")
    return None


_INCOTERM_CODES = re.compile(r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b")
_INCOTERM_WORDS = [
    (re.compile(r"ex[- ]works", re.IGNORECASE), "EXW"),
    (re.compile(r"delivered at place unloaded", re.IGNORECASE), "DPU"),
    (re.compile(r"delivered at place", re.IGNORECASE), "DAP"),
    (re.compile(r"delivered duty paid", re.IGNORECASE), "DDP"),
    (re.compile(r"free carrier", re.IGNORECASE), "FCA"),
]


def _parse_incoterm(quote: str) -> FieldValue | None:
    for sentence in split_sentences(quote):
        if _has_carve_out(sentence):
            continue  # e.g. "DDP delivery available on request"
        m = _INCOTERM_CODES.search(sentence)
        if m:
            return FieldValue("incoterm", m.group(1), m.group(1))
        for pattern, code in _INCOTERM_WORDS:
            if pattern.search(sentence):
                return FieldValue("incoterm", code, code)
    return None


_MONTHS = re.compile(r"(?<![\w.])(\d+)\s*-?\s*months?\b", re.IGNORECASE)
_WARRANTY_CONTEXT = re.compile(r"warranty|defect liability|guarantee", re.IGNORECASE)


# Where a warranty period starts. Later is better for the buyer: dispatch < delivery < commissioning.
WARRANTY_BASIS_ORDER = {"dispatch": 0, "delivery": 1, "commissioning": 2}
_START = r"(?:from|after|following|as of|starting (?:from|at|on)|commencing (?:from|at|on))\s+(?:the\s+)?(?:date\s+of\s+)?"
_BASIS_WORDS = [
    ("dispatch", r"dispatch|shipment|shipping|ex[- ]works|leaving the factory"),
    ("delivery", r"delivery|arrival"),
    ("commissioning", r"commissioning|taking[- ]over|take[- ]over|energi[sz]ation|hand[- ]?over|"
                      r"provisional acceptance|site acceptance|\bPAC\b|\bSAT\b"),
]


def _warranty_basis(sentence: str) -> str:
    for basis, words in _BASIS_WORDS:
        if re.search(_START + r"(?:" + words + r")", sentence, re.IGNORECASE):
            return basis
    return "unspecified"


def _parse_months(quote: str) -> FieldValue | None:
    for s in split_sentences(quote):
        if _WARRANTY_CONTEXT.search(s) and not _has_carve_out(s):
            m = _MONTHS.search(s)
            if m:
                months = int(m.group(1))
                return FieldValue("months", months, str(months), qualifier=_warranty_basis(s))
    return None


_LAG_AFTER = re.compile(
    r"\bcommission\w*\b[^.;]*?(?<![\w.])(\d+)\s*-?\s*months?\s+(?:after|from|following)\s+(?:the\s+)?"
    r"(?:date\s+of\s+)?(delivery|dispatch|shipment)", re.IGNORECASE)
_LAG_BETWEEN = re.compile(
    r"(?<![\w.])(\d+)\s*-?\s*months?\s+(?:between|from)\s+(?:the\s+)?(delivery|dispatch|shipment)\s+"
    r"(?:and|to|until|up to)\s+(?:the\s+)?commission", re.IGNORECASE)


def parse_commissioning_lag(text: str) -> tuple[int, str] | None:
    """(months, "delivery" | "dispatch") when a sentence states how long after delivery or dispatch
    commissioning takes place. This is the only thing that lets two warranty start points be compared."""
    for sentence in split_sentences(text):
        m = _LAG_AFTER.search(sentence) or _LAG_BETWEEN.search(sentence)
        if m:
            start = m.group(2).lower()
            return int(m.group(1)), "dispatch" if start == "shipment" else start
    return None


# --- Schedule, contract-risk, site and standards values -------------------------------------------
#
# Lead time (weeks), percentages (LD rate, LD cap, liability cap), money caps, site altitude and
# cited standards. Every parser is deterministic: a quote it cannot read for certain is None, and
# the cell is flagged downstream. A parser reads only what is offered: a sentence that carves
# something out is skipped, never half-read.

_LEAD_TIME = re.compile(
    _LEAD + r"(\d+(?:\.\d+)?)(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?))?\s*"
    r"((?:working\s+|business\s+|calendar\s+)?(?:weeks?|wks?|months?|days?))\b", re.IGNORECASE)
_DELIVERY_CONTEXT = re.compile(
    r"deliver|lead[- ]time|ex[- ]works|shipment|shipping|dispatch|manufactur|ready for|site arrival|"
    r"\b(?:EXW|CIF|DAP|DDP|DPU|FCA)\b", re.IGNORECASE)
# Periods that are not the delivery lead time: validity, warranty, payment terms, drawing submission, FAT.
_NOT_LEAD_TIME = re.compile(
    r"validity|valid for|warranty|guarantee|liabilit|defect|payment|invoice|drawing submission|submission of|submit|"
    r"review period|\bFAT\b|witness|commissioning|installation", re.IGNORECASE)
# What the weeks are counted from; the later start wins when a sentence names both.
_LEAD_ANCHORS = (
    ("approved_drawings", re.compile(
        r"approv\w*\s+(?:of\s+)?(?:the\s+)?(?:final\s+)?drawings?|drawings?\s+approval|\bAFC\b", re.IGNORECASE)),
    ("po", re.compile(
        r"purchase\s+order|\bPO\b|order\s+(?:placement|confirmation|date)|receipt\s+of\s+(?:the\s+)?order|"
        r"contract\s+(?:award|signature|signing|date)|letter\s+of\s+(?:award|intent)|\bLOA\b|\bLOI\b|"
        r"notice\s+to\s+proceed|\bNTP\b|effective\s+date", re.IGNORECASE)),
)


def _weeks(number: float, unit: str) -> float:
    unit = unit.lower()
    if "month" in unit:
        weeks = number * 52 / 12
    elif "day" in unit:
        weeks = number / 7
        if unit.startswith(("working", "business")):
            weeks *= 7 / 5  # five working days are one calendar week
    else:
        weeks = number
    return round(weeks, 6)


def _lead_anchor(text: str) -> str:
    for anchor, pattern in _LEAD_ANCHORS:
        if pattern.search(text):
            return anchor
    return "unspecified"


def _parse_lead_time(quote: str, require_context: bool = True) -> FieldValue | None:
    """Delivery lead time in weeks, with what it is counted from (`qualifier`: "po",
    "approved_drawings" or "unspecified"). A range gives its upper end: the buyer plans for the worst case."""
    for sentence in split_sentences(quote):
        if _has_carve_out(sentence):
            continue
        for clause in _clauses(sentence):
            if require_context and (not _DELIVERY_CONTEXT.search(clause) or _NOT_LEAD_TIME.search(clause)):
                continue
            m = _LEAD_TIME.search(clause)
            if not m:
                continue
            number = float(m.group(2) or m.group(1))
            weeks = _weeks(number, m.group(3))
            shown = f"{_fmt(weeks)} weeks"
            if not re.match(r"(?:\w+\s+)?(?:weeks?|wks?)$", m.group(3), re.IGNORECASE):
                shown += f" ({_fmt(number)} {m.group(3).lower()})"
            return FieldValue("lead_time", weeks, shown, qualifier=_lead_anchor(sentence))
    return None


_PERCENT = re.compile(_LEAD + r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b)", re.IGNORECASE)
_RATE_PERIOD = re.compile(
    r"^\s*(?:of\s+(?:the\s+)?[^.;,]{0,40}?\s+)?(?:per|/|each|every|for each)\s+"
    r"(?:(?:complete|full|started|commenced|whole)\s+)?(week|day|month)\b", re.IGNORECASE)
_RATE_WEEKLY = re.compile(r"^\s*(?:of\s+[^.;,]{0,40}?\s+)?(weekly|daily)\b", re.IGNORECASE)
_CAP_BEFORE = re.compile(r"max\w*|\bcap(?:ped)?\b|not\s+(?:to\s+)?exceed|limited\s+to|up\s+to|ceiling|"
                         r"aggregate|in\s+total", re.IGNORECASE)
_CAP_AFTER = re.compile(r"^\s*(?:of\s+[^.;,]{0,40}?\s+)?(?:maximum|cap\b|in\s+total|in\s+aggregate|at\s+most)",
                        re.IGNORECASE)
_LD_CONTEXT = re.compile(r"liquidated|\bLDs?\b|delay\s+damages|late\s+delivery|penalt", re.IGNORECASE)
_LIABILITY_CONTEXT = re.compile(r"liabilit", re.IGNORECASE)
NO_LD = re.compile(
    r"\b(?:no|without|excludes?|excluded|waive[sd]?|not\s+(?:accept\w*|applicable|agree\w*))\b[^.;]{0,40}liquidated|"
    r"liquidated[^.;]{0,60}\b(?:excluded|waived|not\s+(?:accepted|applicable|agreed)|do(?:es)?\s+not\s+apply)|"
    r"\bno\s+LDs?\b|\bLDs?\s+(?:excluded|waived|not\s+applicable)", re.IGNORECASE)
_PER_WEEK_FACTOR = {"week": 1.0, "day": 7.0, "month": 12 / 52}
_CAP_BASIS = (("delayed_portion", re.compile(r"delayed|undelivered|late(?:ly)?\s+delivered|affected", re.IGNORECASE)),
              ("contract", re.compile(r"contract|order|total\s+(?:price|value)", re.IGNORECASE)))


def _percent_value(m: re.Match, factor: float = 1.0, shown: str = "", qualifier: str | None = None) -> FieldValue:
    number = float(m.group(1))
    return FieldValue("percent", round(number * factor, 9), shown or f"{_fmt(number)} %", qualifier=qualifier)


def _cap_basis(clause: str) -> str:
    return next((basis for basis, pattern in _CAP_BASIS if pattern.search(clause)), "unspecified")


def _parse_percent(quote: str, field_name: str, require_context: bool = True) -> FieldValue | None:
    """One percentage, chosen by the field: `ld_rate` (per week; per day or month is converted),
    `ld_cap` (a maximum on liquidated damages) or `liability_cap` (a limit on total liability).
    "No liquidated damages" reads as 0 for both LD fields."""
    if not require_context:
        m = _PERCENT.search(quote)
        return _percent_value(m) if m else None
    sentences = split_sentences(quote)
    if field_name in ("ld_rate", "ld_cap") and any(NO_LD.search(s) for s in sentences):
        return FieldValue("percent", 0.0, "0 %", qualifier="none")
    for sentence in sentences:
        for clause in _clauses(sentence):
            for m in _PERCENT.finditer(clause):
                before, after = clause[:m.start()], clause[m.end():]
                period, weekly = _RATE_PERIOD.match(after), _RATE_WEEKLY.match(after)
                per = bool(period or weekly)
                if field_name == "ld_rate" and per and _LD_CONTEXT.search(sentence):
                    unit = period.group(1).lower() if period else ("day" if weekly.group(1).lower() == "daily" else "week")
                    return _percent_value(m, _PER_WEEK_FACTOR[unit], f"{_fmt(float(m.group(1)))} % per {unit}", "per_week")
                is_cap = not per and bool(_CAP_BEFORE.search(before[-60:]) or _CAP_AFTER.match(after))
                if field_name == "ld_cap" and is_cap and _LD_CONTEXT.search(sentence):
                    return _percent_value(m, qualifier=_cap_basis(clause))
                if (field_name == "liability_cap" and is_cap and _LIABILITY_CONTEXT.search(clause)
                        and not _LD_CONTEXT.search(clause)):
                    return _percent_value(m, qualifier=_cap_basis(clause))
    return None


_MONEY_CAP_CONTEXT = re.compile(r"max\w*|\bcap(?:ped)?\b|not\s+(?:to\s+)?exceed|limited\s+to|up\s+to|ceiling|"
                                r"aggregate|liabilit|liquidated|\bLDs?\b", re.IGNORECASE)


def _money_in(sentence: str) -> tuple[Decimal, str] | None:
    before, after = _PRICE_BEFORE.search(sentence), _PRICE_AFTER.search(sentence)
    if before and (not after or before.start() <= after.start()):
        raw, token, end = before.group(2), before.group(1), before.end()
    elif after:
        raw, token, end = after.group(1), after.group(2), after.end()
    else:
        return None
    if _ABBREVIATED.match(sentence, end):
        return None  # "EUR 1m" is not read to the euro
    amount = _amount(raw)
    if amount is None:
        return None
    return amount, _CURRENCY_CODES.get(token, token.upper())


def _parse_money_cap(quote: str, require_context: bool = True) -> FieldValue | None:
    """A capped amount of money with its currency (never assumed): "capped at EUR 500,000"."""
    for sentence in split_sentences(quote):
        if _has_carve_out(sentence) or (require_context and not _MONEY_CAP_CONTEXT.search(sentence)):
            continue
        found = _money_in(sentence)
        if found:
            amount, code = found
            shown = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
            return FieldValue("money_cap", amount, f"{code} {shown}", qualifier=code)
    return None


_ALTITUDE = re.compile(_LEAD + _NUM + r"\s*(masl|amsl|m|ft)\b(?!\s*(?:/|²|³))")


def _altitude_unit(token: str) -> str:
    return "ft" if token == "ft" else "m"  # masl and amsl are metres above sea level
_ALTITUDE_CONTEXT = re.compile(r"altitude|elevation|a\.?s\.?l\.?|above\s+(?:mean\s+)?sea\s+level|\bmasl\b|\bamsl\b",
                               re.IGNORECASE)


def _parse_altitude(quote: str, require_context: bool = True) -> FieldValue | None:
    """Site altitude, or the altitude an item is rated for, in metres or feet as stated."""
    for sentence in split_sentences(_normalize_text(quote)):
        if _has_carve_out(sentence) or (require_context and not _ALTITUDE_CONTEXT.search(sentence)):
            continue
        for clause in (c for c in re.split(r"[;()]|,\s+", sentence) if c.strip() and not _WITH_DERATING.search(c)):
            m = _ALTITUDE.search(clause)
            if m:
                magnitude = _number(m.group(1))
                unit = _altitude_unit(m.group(2))
                return FieldValue("altitude", Q_(magnitude, _PINT_UNITS[unit]), f"{_fmt(magnitude)} {unit}")
    return None


# Standards bodies and reference numbers: "IEC 62271-200:2011", "IEEE C37.20.2", "EN 50160".
_STANDARD = re.compile(
    r"\b(IEC|IEEE|ANSI|EN|ISO|DIN|VDE|BS|UL|NEMA)\s*(?:[-/]\s*)?"
    r"((?:[A-Z]\d{2}[.\d]*|\d{2,6})(?:-\d+)*(?:\.\d+)*)"
    r"(?:\s*:\s*(\d{4})|\s*\((\d{4})\))?")
# A sentence that states a departure from a standard does not offer it.
_STANDARD_NEGATED = re.compile(
    r"\bnot\s+(?:in\s+accordance|compliant|certified|tested|designed|complying|comply|conform\w*)|non-?compl\w+|"
    r"deviat\w+|except(?:ion|ions)?\b|\bwithout\b|partial(?:ly)?\s+compl\w+", re.IGNORECASE)


def _parse_standard(quote: str) -> FieldValue | None:
    """The standards a passage cites, as a sorted tuple such as ("IEC 61850", "IEC 62271-200:2011").
    An edition is kept only when written as :YYYY or (YYYY). A sentence that carves out or negates
    compliance cites nothing."""
    refs: dict[str, str | None] = {}
    for sentence in split_sentences(_normalize_text(quote).replace("‑", "-")):
        if _has_carve_out(sentence) or _STANDARD_NEGATED.search(sentence):
            continue
        for m in _STANDARD.finditer(sentence):
            base = f"{m.group(1)} {m.group(2).rstrip('.-')}"
            year = m.group(3) or m.group(4)
            if year and (refs.get(base) is None or year > refs[base]):
                refs[base] = year
            else:
                refs.setdefault(base, None)
    if not refs:
        return None
    items = tuple(sorted(f"{base}:{year}" if year else base for base, year in refs.items()))
    return FieldValue("standard", items, ", ".join(items))


def standard_exception(quote: str) -> bool:
    """True when a sentence cites a standard and also states an exception, deviation or partial compliance."""
    for sentence in split_sentences(_normalize_text(quote).replace("‑", "-")):
        if _STANDARD.search(sentence) and (_STANDARD_NEGATED.search(sentence) or _has_carve_out(sentence)):
            return True
    return False


def _split_standard(item: str) -> tuple[str, str | None]:
    base, _, year = item.partition(":")
    return base, year or None


def standards_satisfied(required: tuple, offered: tuple) -> bool:
    """Every required standard is offered, and an edition the requirement names is met by the same or a newer one."""
    have = dict(_split_standard(item) for item in offered)
    for item in required:
        base, year = _split_standard(item)
        if base not in have or (year and (have[base] is None or have[base] < year)):
            return False
    return True


QUANTITY_KINDS = tuple(_QUANTITY_PATTERNS)
FIELD_PARSERS = {
    "temperature": _parse_temperature,
    "material": _parse_material,
    "ip": _parse_ip,
    "iac": _parse_iac,
    "protocol": _parse_protocol,
    "marshalling": _parse_marshalling,
    "fat_days": _parse_fat_days,
    "incoterm": _parse_incoterm,
    "months": _parse_months,
    "lead_time": _parse_lead_time,
    "money_cap": _parse_money_cap,
    "altitude": _parse_altitude,
    "standard": _parse_standard,
}
# Whether something is part of the price: spares, site supervision. Read per field from the clauses that
# name it. An exclusion anywhere beats an inclusion: the buyer is told the bad news.
_SCOPE_SUBJECT = {
    "spares_included": re.compile(r"spares?\b|spare parts", re.IGNORECASE),
    "supervision_included": re.compile(r"supervis\w+|commissioning engineer", re.IGNORECASE),
}
_SCOPE_IN = re.compile(r"\b(?:included|includes|including|incl\.?)\b|within (?:the )?(?:price|scope)|"
                       r"part of (?:the )?(?:price|scope|supply)|in the (?:contract )?price", re.IGNORECASE)
_SCOPE_OUT = re.compile(r"\b(?:excluded|exclude[sd]?|not included|not part of|not offered|not covered|"
                        r"quoted separately|priced separately|offered separately|charged (?:extra|separately)|"
                        r"by others|by the (?:buyer|purchaser)|optional|on request|at extra cost|extra cost)\b|"
                        r"billed per|supplied loose", re.IGNORECASE)


def _parse_scope(quote: str, field_name: str) -> FieldValue | None:
    """1 ("included") or 0 ("not included") for what the field names, or None when the text does not say."""
    subject = _SCOPE_SUBJECT.get(field_name)
    if subject is None:
        return None
    included = False
    for sentence in split_sentences(quote):
        for clause in _clauses(sentence):
            if not subject.search(clause):
                continue
            if _SCOPE_OUT.search(clause):
                return FieldValue("scope", 0, "not included")
            if _SCOPE_IN.search(clause):
                included = True
    return FieldValue("scope", 1, "included") if included else None


# One-argument parsers by kind, and ones that need, an LD cap and a liability cap
# are all percentages).
NAME_PARSERS = {"percent": _parse_percent, "scope": _parse_scope}
# Context-free readers for the ontology's plausible bounds, which are bare values such as "4 weeks".
_BOUND_PARSERS = {
    "lead_time": lambda text, name: _parse_lead_time(text, require_context=False),
    "percent": lambda text, name: _parse_percent(text, name, require_context=False),
    "money_cap": lambda text, name: _parse_money_cap(text, require_context=False),
    "altitude": lambda text, name: _parse_altitude(text, require_context=False),
}


def parse_field(kind: FieldKind, quote: str, field_name: str) -> FieldValue | None:
    if kind in _QUANTITY_PATTERNS:
        return _parse_quantity(kind, quote, field_name)
    if kind in NAME_PARSERS:
        return NAME_PARSERS[kind](quote, field_name)
    return FIELD_PARSERS[kind](quote)


# Digits with optional thousands grouping by comma, dot, space, no-break or narrow no-break space.
_PRICE = re.compile(r"(?:€|EUR)\s?(\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)")
_ABBREVIATED = re.compile(r"\s?(?:k|m|mio|mn|million|thousand)\b", re.IGNORECASE)


def parse_price(quote: str) -> Decimal | None:
    m = _PRICE.search(quote)
    if not m or _ABBREVIATED.match(quote, m.end()):
        return None  # "€452k" is not a price we can trust to the euro
    raw = re.sub(r"[   ]", "", m.group(1)).rstrip(".,")
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        raw = raw.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in raw:
        raw = raw.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw) else raw.replace(",", ".")
    elif "." in raw and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


_AMOUNT = r"\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_CURRENCY_CODES = {"€": "EUR", "$": "USD", "£": "GBP"}
_CURRENCY_TOKEN = r"€|\$|£|\b(?:EUR|USD|GBP|CHF|TRY|SEK|NOK|DKK|PLN|JPY|CNY|INR|AED|SAR)\b"
_PRICE_BEFORE = re.compile(r"(" + _CURRENCY_TOKEN + r")\s?(" + _AMOUNT + r")")
_PRICE_AFTER = re.compile(r"(?<![\w.,])(" + _AMOUNT + r")\s?(" + _CURRENCY_TOKEN + r")")
_PRICE_BARE = re.compile(r"\b(?:price|total|amount|sum)\b[^0-9€$£]*?(?<![\w.,])(" + _AMOUNT + r")(?![\d.,]*\d)",
                         re.IGNORECASE)
_PER_UNIT = re.compile(r"\bper\s+(?:bay|panel|unit|cubicle|feeder|set|piece|item)\b|\beach\b|"
                       r"/\s*(?:bay|panel|unit|cubicle|feeder)\b", re.IGNORECASE)


@dataclass(frozen=True)
class PriceRead:
    amount: Decimal
    currency: str | None  # ISO code; None when the passage names no currency
    per_unit: str | None  # "per bay", "each"...: the figure may be a unit rate, not the total


def _amount(raw: str) -> Decimal | None:
    raw = re.sub(r"[   ]", "", raw).rstrip(".,")
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        raw = raw.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in raw:
        raw = raw.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw) else raw.replace(",", ".")
    elif "." in raw and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def read_price(quote: str) -> PriceRead | None:
    """The price a passage states: its amount, its currency and whether it is worded per unit.

    The currency is read, never assumed. An amount in shorthand ("€452k") is not read at all.
    """
    before, after = _PRICE_BEFORE.search(quote), _PRICE_AFTER.search(quote)
    if before and (not after or before.start() <= after.start()):
        raw, token, end = before.group(2), before.group(1), before.end()
    elif after:
        raw, token, end = after.group(1), after.group(2), after.start(2)
    else:
        bare = _PRICE_BARE.search(quote)
        if not bare:
            return None
        raw, token, end = bare.group(1), None, bare.end()
    if _ABBREVIATED.match(quote, end if token is None or before else after.end(1)):
        return None  # "€452k" is not a price we can trust to the euro
    amount = _amount(raw)
    if amount is None:
        return None
    currency = _CURRENCY_CODES.get(token, token.upper()) if token else None
    unit = _PER_UNIT.search(quote)
    return PriceRead(amount, currency, " ".join(unit.group(0).lower().replace("/", "per ").split()) if unit else None)


_BAYS = re.compile(r"(?<![\w.])(\d+)\s*(?:nos?\.?\s*|x\s*)?(?:feeder\s+)?(?:bays|panels|cubicles)\b", re.IGNORECASE)


def parse_bay_count(quote: str) -> int | None:
    m = _BAYS.search(quote)
    return int(m.group(1)) if m else None


def _unit_label(value: FieldValue) -> str:
    return value.display.split(" ", 1)[1]


def check(field: FieldDef, required: FieldValue, offered: FieldValue) -> tuple[bool, str]:
    """Return (passed, offered value expressed in the required unit)."""
    req, off = required.value, offered.value
    if isinstance(req, pint.Quantity) and isinstance(off, pint.Quantity):
        try:
            converted = off.to(req.units).magnitude
            if field.kind == "temperature":
                a, b = off.to(ureg.kelvin).magnitude, req.to(ureg.kelvin).magnitude
            else:
                a, b = converted, req.magnitude
        except pint.DimensionalityError:
            return False, offered.display
        canonical = f"{_fmt(round(converted, 9))} {_unit_label(required)}"
        if field.operator == "ge":
            return (a >= b or math.isclose(a, b, rel_tol=1e-9)), canonical
        if field.operator == "le":
            return (a <= b or math.isclose(a, b, rel_tol=1e-9)), canonical
        return math.isclose(a, b, rel_tol=1e-9), canonical
    if field.operator == "all" and isinstance(req, tuple) and isinstance(off, tuple):
        return standards_satisfied(req, off), offered.display
    if isinstance(req, Decimal) and isinstance(off, Decimal):
        if required.qualifier != offered.qualifier:
            return False, offered.display  # two currencies: not comparable without a dated rate
        passed = {"ge": off >= req, "le": off <= req}.get(field.operator, off == req)
        return passed, offered.display
    if isinstance(req, IPCode) and isinstance(off, IPCode):
        return (off.solid >= req.solid and off.liquid >= req.liquid), offered.display
    if field.operator == "ord":
        # Amendment A1: better than required is compliant. A value outside the declared order fails.
        order = ORDINALS[field.kind]
        if req not in order or off not in order:
            return False, offered.display
        return order.index(off) >= order.index(req), offered.display
    if field.operator == "ge":
        return off >= req, offered.display
    if field.operator == "le":
        return off <= req, offered.display
    return off == req, offered.display


_NUMERIC_KINDS = frozenset({"current", "duration", "voltage", "temperature", "fat_days", "months"})


def _comparable(value: FieldValue) -> float:
    raw = value.value
    if isinstance(raw, pint.Quantity):
        return raw.to(ureg.kelvin).magnitude if value.kind == "temperature" else raw.to_base_units().magnitude
    return float(raw)


def plausible_bounds(field: FieldDef) -> tuple[FieldValue, FieldValue] | None:
    """The field's plausible range as typed values; raises ValueError if the ontology's bounds cannot be read."""
    if field.plausible is None:
        return None
    bounds = []
    for text in field.plausible:
        if field.kind in ("fat_days", "months"):
            bounds.append(FieldValue(field.kind, int(text), text))
            continue
        read_bound = _BOUND_PARSERS.get(field.kind)
        value = read_bound(text, field.name) if read_bound else parse_field(field.kind, text, field.name)
        if value is None:
            raise ValueError(f"plausible bound {text!r} of field {field.name!r} is not a {field.kind}")
        bounds.append(value)
    low, high = bounds
    if _comparable(low) > _comparable(high):
        raise ValueError(f"plausible range of field {field.name!r} is reversed")
    return low, high


def plausible(field: FieldDef, value: FieldValue) -> bool:
    """False when a parsed value lies outside the range the ontology declares for the field.

    An Ik of 315 kA is a dropped decimal point or the wrong number, whatever the locale said.
    """
    bounds = plausible_bounds(field)
    if bounds is None:
        return True
    low, high = bounds
    return _comparable(low) <= _comparable(value) <= _comparable(high) or any(
        math.isclose(_comparable(value), _comparable(b), rel_tol=1e-9) for b in bounds)


def value_in_span(value: FieldValue, text: str) -> bool:
    """Whether the value's number, with its unit, appears as a token in `text`.

    `text` is the cited span with its numbers normalized (normalize_numbers). A value
    read from wording alone (0 witnessed days from "charged extra") has no number to find.
    """
    text = _normalize_text(text)
    kind = value.kind
    if kind in _QUANTITY_PATTERNS:
        magnitude, unit = value.display.split(" ", 1)
        for m in _QUANTITY_PATTERNS[kind].finditer(text):
            found_unit = "s" if kind == "duration" else m.group(2)
            if found_unit == unit and math.isclose(_number(m.group(1)), float(magnitude), rel_tol=1e-9):
                return True
        return False
    if kind == "temperature":
        return any(_temperature_value(m).display == value.display for m in _TEMPERATURE.finditer(text))
    if kind == "fat_days":
        return value.value == 0 or any(int(m.group(1)) == value.value for m in _DAYS.finditer(text))
    if kind == "months":
        return any(int(m.group(1)) == value.value for m in _MONTHS.finditer(text))
    if kind == "lead_time":
        return any(math.isclose(_weeks(float(m.group(2) or m.group(1)), m.group(3)), value.value, rel_tol=1e-9)
                   for m in _LEAD_TIME.finditer(text))
    if kind == "percent":
        if value.qualifier == "none":
            return True  # "no liquidated damages": read from wording, no number to find
        shown = float(value.display.split(" ", 1)[0])
        return any(math.isclose(float(m.group(1)), shown, rel_tol=1e-9) for m in _PERCENT.finditer(text))
    if kind == "money_cap":
        return any((found := _money_in(sentence)) and found[0] == value.value for sentence in split_sentences(text))
    if kind == "altitude":
        return any(f"{_fmt(_number(m.group(1)))} {_altitude_unit(m.group(2))}" == value.display for m in _ALTITUDE.finditer(text))
    if kind == "standard":
        flat = _normalize_text(text).replace("‑", "-")
        return all(re.search(re.escape(base.split(" ", 1)[0]) + r"\s*(?:[-/]\s*)?" + re.escape(base.split(" ", 1)[1]), flat)
                   for base, _ in map(_split_standard, value.value))
    if kind == "ip":
        return bool(re.search(rf"\bIP\s?{value.value.solid}{value.value.liquid}\b", text))
    if kind == "iac":
        return bool(re.search(rf"\b{re.escape(value.value)}\b", text))
    if kind == "incoterm":
        return bool(re.search(rf"\b{re.escape(value.value)}\b", text)) or any(
            code == value.value and pattern.search(text) for pattern, code in _INCOTERM_WORDS)
    return True  # material, protocol, marshalling: read from words, no number involved


def rule_text(param: ParameterDef, requirement: dict[str, FieldValue]) -> str:
    parts = []
    for f in param.fields:
        if f.name in requirement:
            if f.kind == "scope":
                parts.append(f"{f.label} {requirement[f.name].display}")
                continue
            symbol = {"eq": "=", "le": "≤", "all": "include"}.get(f.operator, "≥")
            parts.append(f"{f.label} {symbol} {requirement[f.name].display}")
    return " AND ".join(parts)


def display_text(param: ParameterDef, values: dict[str, FieldValue]) -> str:
    return param.display_template.format(
        **{f.name: values[f.name].display if f.name in values else "—" for f in param.fields}
    )
