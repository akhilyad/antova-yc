"""Deterministic value parsers and checks (spec §9).

Each parser reads one verified quote and returns a typed value or None.
Pint handles every physical unit; nothing here is guessed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

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


# "24 months", and the figure of "twenty-four (24) months".
_MONTHS = re.compile(r"(?<![\w.])(\d+)\)?\s*-?\s*months?\b", re.IGNORECASE)
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


_YEARS = re.compile(r"(?<![\w.])(\d+)\)?\s*-?\s*years?\b", re.IGNORECASE)
_PERIOD_DAYS = re.compile(r"(?<![\w.])(\d+)\)?\s*(?:calendar\s+)?days?\b", re.IGNORECASE)
_NTP_WORDS = r"(?:NTP|notice\s+to\s+proceed)"
# The period is the deadline for issuing the NTP only when it follows the issuing wording directly:
# "The Buyer may issue the NTP within 8 months from the PO", "The NTP shall be issued no later than 60 days
# after the PO". "Delivery within 14 months after NTP" is a delivery period, not this term.
_NTP_DEADLINE = re.compile(
    r"(?:\b(?:issue|issuance|give|giving|serve)\w*\s+(?:of\s+)?(?:the\s+|an?\s+)?" + _NTP_WORDS + r"|"
    r"\b" + _NTP_WORDS + r"(?:\s*\([^)]{0,10}\))?\s+(?:shall|must|may|will|is\s+to|can)\s+be\s+(?:issued|given|served))"
    r"(?:\s+by\s+the\s+\w+)?\s+(?:within|no\s+later\s+than|not\s+later\s+than|at\s+the\s+latest\s+within|up\s+to)\s+"
    r"(?:[a-z\-]+\s+\(\s*)?(?<![\w.,])(\d+(?:[.,]\d+)?)\)?\s*((?:calendar\s+)?days?|months?)\b", re.IGNORECASE)
# A life, not a period: "a service period of 10 years" (spare parts) or "the design period" is something else.
# What an NTP deadline is counted from, when the text says: it must be the order (or the contract).
_PERIOD_ANCHOR = re.compile(r"\s+(?:after|from|following|of)\s+", re.IGNORECASE)
_ORDER_ANCHOR = re.compile(r"(?:the\s+)?(?:date\s+of\s+(?:the\s+)?)?(?:signature\s+of\s+(?:the\s+)?)?(?:project\s+)?"
                           r"(?:purchase\s+order|(?-i:PO)\b|order\b|contract\b)"
                           r"(?!\s+(?:acknowledg|confirm))", re.IGNORECASE)  # not "the order acknowledgement"
_LIFE = r"(?:design|service|operating|useful)\s+life|lifetime|minimum\s+operating\s+period"
# A design life's own figure: right after the words ("design life of 30 years", "design life is thirty (30)
# years", "25 (twenty-five) years"), with no other number between, or right before them ("a 30-year design life").
_LIFE_AFTER = re.compile(r"(?:" + _LIFE + r")[^.;\d]{0,30}?(?<![\w.,])(\d+)\)?(?:\s*\([^)\d]{1,20}\))?\s*-?\s*"
                         r"(years?|months?)\b", re.IGNORECASE)
_LIFE_BEFORE = re.compile(r"(?<![\w.,])(\d+)\s*-?\s*(years?|months?)\s+(?:minimum\s+)?(?:" + _LIFE + r")",
                          re.IGNORECASE)


def days_to_months(days: int) -> Decimal:
    """Days restated as months for a deadline: a month is 365/12 days, so 60 days is 1.97 months.
    Computed as days x 12 / 365, one division, so it is the same Decimal however it is called."""
    return Decimal(days) * 12 / 365


def _parse_ntp_deadline(quote: str) -> FieldValue | None:
    """How long after the order the NTP may be issued. Months stay whole months; days are restated as
    months (days x 12 / 365), with the days and the conversion shown."""
    for s in split_sentences(quote):
        if _has_carve_out(s):
            continue
        m = _NTP_DEADLINE.search(s)
        if not m:
            continue
        if re.search(r"\b(?:after|from|following|upon|since|of)\s+(?:the\s+)?$", s[:m.start()], re.IGNORECASE):
            return None  # "within 14 months after issue of the NTP ...": the NTP is the start of another period
        anchor = _PERIOD_ANCHOR.match(s, m.end())
        if anchor and not _ORDER_ANCHOR.match(s, anchor.end()):
            return None  # "within 30 days after receipt of the advance payment guarantee": not counted from the order
        if not m.group(1).isdigit():
            return None  # "1,5 months" or "1.5 months": not a whole number this reader turns into months
        number = int(m.group(1))
        if m.group(2).lower().startswith("month"):
            return FieldValue("months", number, str(number), qualifier="months")
        months = days_to_months(number)
        return FieldValue("months", months, f"{number} days ({months:.2f} months at 365/12 days per month)",
                          qualifier="days")
    return None


def _parse_design_life(quote: str) -> FieldValue | None:
    """A design life or minimum operating period, in months: years are restated as years x 12."""
    for s in split_sentences(quote):
        if _has_carve_out(s):
            continue
        m = _LIFE_AFTER.search(s) or _LIFE_BEFORE.search(s)
        if not m:
            continue
        number = int(m.group(1))
        if m.group(2).lower().startswith("year"):
            return FieldValue("months", number * 12, f"{number} years ({number * 12} months)", qualifier="years")
        return FieldValue("months", number, str(number), qualifier="months")
    return None


def _parse_months(quote: str, field_name: str = "") -> FieldValue | None:
    """A number of months. A warranty period by default; the NTP deadline and the design life by name."""
    if field_name == "ntp_deadline_months":
        return _parse_ntp_deadline(quote)
    if field_name == "design_life_months":
        return _parse_design_life(quote)
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
# "Without prejudice to …" is not an exclusion, nor is an "excludes …" whose object is not LDs ("The price
# excludes taxes and liquidated damages are 0.5% per day"; Task 6 review r5): there "liquidated damages" has its
# own predicate. An exclusion coordinated with other losses ("We exclude consequential losses and liquidated
# damages.") is one (ruling R31); ld_exclusion classifies it "other".
_LD_OWN_PREDICATE = (r"(?!\s+damages?\b(?:\s*\(\s*(?:the\s+)?LDs?\s*\))?\s+"
                     r"(?:are|is|shall|will|apply|applies|amount|amounts)\b)")
NO_LD = re.compile(
    r"\b(?:no|without(?!\s+prejudice)|waive[sd]?|not\s+(?:accept\w*|applicable|agree\w*))\b[^.;]{0,40}liquidated|"
    r"\bexclude[sd]?\b[^.;]{0,40}liquidated" + _LD_OWN_PREDICATE + r"|"
    r"liquidated[^.;]{0,60}\b(?:excluded|waived|not\s+(?:accepted|applicable|agreed)|do(?:es)?\s+not\s+apply)|"
    r"\bno\s+LDs?\b|\bLDs?\s+(?:(?:are|is|shall\s+be|will\s+be)\s+)?(?:excluded|waived|not\s+applicable)|"
    r"\bLDs?\s+(?:do(?:es)?|shall|will)\s+not\s+apply", re.IGNORECASE)
# "No LDs" for the whole offer is read only from wording that says nothing more (Task 6 reviews r3, r4; ruling
# R29). After the exclusion, only the rest of its own words ("damages shall apply") and whole-contract wording
# ("under this Contract", "from this offer", "in any case", "whatsoever") may follow, to the end of the clause.
# Before it, only nothing, whole-contract wording, or the bidder as subject ("The Supplier does not accept…",
# "We hereby exclude…"). Anything else ("during the first two weeks", "in case of force majeure", "to the
# order's spare parts") may scope the exclusion to some delays, so it is not read as "no LDs" at all; nothing
# is listed as a scope, everything unlisted is one.
_WHOLE_CONTRACT = (r"(?:(?:under|to|for|in|of|within|from)\s+(?:this|the|our)\s+(?:contract|order|purchase\s+order|"
                   r"offer|agreement|quotation|proposal|tender|scope\s+of\s+supply|supply|bid|project)|"
                   r"in\s+(?:their|its)\s+entirety|in\s+any\s+(?:case|event)|whatsoever|at\s+all)")
# The rest of the exclusion's own words: "[No liquidated] damages (LDs) shall not be applicable".
_NO_LD_WORDS = (r"\s*(?:damages?\b)?(?:\s*\(\s*(?:the\s+)?LDs?\s*\))?"
                r"(?:\s+(?:shall|will|do(?:es)?|are|is|can|may)(?:\s+not)?)?"
                r"(?:\s+(?:be\s+)?(?:apply|applies|applied|applicable|payable|due|charged|levied|accepted|agreed|offered))?")
_NO_LD_TAIL = re.compile(_NO_LD_WORDS + r"(?:\s*,?\s*" + _WHOLE_CONTRACT + r")*\s*[.!]?\s*", re.IGNORECASE)
# The bidder as the subject of the exclusion, with its verb's auxiliary: "The Supplier does", "We hereby".
_LD_SUBJECT = (r"(?:(?:the\s+)?(?:Supplier|Seller|Vendor|Contractor|Bidder|Manufacturer)|we)"
               r"(?:\s+(?:does|do|will|shall|hereby))*\s*")
_NO_LD_HEAD = re.compile(r"\s*(?:" + _WHOLE_CONTRACT + r"\s*,?\s*)?(?:" + _LD_SUBJECT + r")?", re.IGNORECASE)
# An exclusion scoped to delays the vendor does not cause (ruling R29): force majeure, or delays caused by or
# attributable to the Buyer. The back-to-back delay scenario already assumes the delay is the vendor's, so such
# an exclusion leaves the gap as computed; the formula says so. Any other scope is not classified ("other").
_LD_PARTY = r"(?:the\s+)?(?:Buyer|Customer|Employer|Purchaser)"
_LD_FM = r"force\s+majeure(?:\s+(?:events?|situations?|cases?|circumstances?|delays?))?"
_LD_CAUSER = rf"(?:{_LD_PARTY}|{_LD_FM})(?:\s+(?:or|and|and/or)\s+(?:{_LD_PARTY}|{_LD_FM}))*"
_LD_CAUSED = rf"(?:caused\s+by|attributable\s+to|due\s+to|arising\s+(?:from|out\s+of)|resulting\s+from)\s+{_LD_CAUSER}"
_LD_CAUSE_PREP = r"(?:in\s+(?:the\s+)?(?:case|event)\s+of|in\s+respect\s+of|to|for|on|in|during|under)"
_LD_CAUSE = (rf"(?:{_LD_CAUSE_PREP}\s+(?:(?:any\s+|a\s+|the\s+)?(?:delays?|events?|cases?)\s+{_LD_CAUSED}|"
             rf"{_LD_CAUSED}|(?:any\s+)?(?:Buyer|Customer|Employer|Purchaser)[-\s]caused\s+delays?|"
             rf"(?:any\s+|a\s+)?{_LD_FM})|"
             rf"(?:where|if|when|in\s+case|insofar\s+as|to\s+the\s+extent(?:\s+that)?)\s+(?:the\s+|a\s+|any\s+)?"
             rf"delays?\s+(?:is|are|was|were|has\s+been)\s+{_LD_CAUSED}|{_LD_CAUSED})")
_LD_CAUSE_RE = re.compile(_LD_CAUSE, re.IGNORECASE)
_LD_CAUSE_HEAD = re.compile(r"\s*(?:(?:" + _WHOLE_CONTRACT + "|" + _LD_CAUSE + r")\s*,?\s*)*(?:" + _LD_SUBJECT + r")?",
                            re.IGNORECASE)
_LD_CAUSE_TAIL = re.compile(_NO_LD_WORDS + r"(?:\s*,?\s*(?:" + _WHOLE_CONTRACT + "|" + _LD_CAUSE + r"))*\s*[.!]?\s*",
                            re.IGNORECASE)
_LEADING_PREP = re.compile(r"^" + _LD_CAUSE_PREP + r"\s+", re.IGNORECASE)
# NO_LD with nothing but the exclusion's own words inside the match (Task 6 review r5): NO_LD lets free text
# stand between the exclusion word and "liquidated", or between "liquidated" and "waived/excluded", so that a
# scope there ("Liquidated damages for the first two weeks are waived") is still seen as an exclusion. "Delay"
# before "liquidated damages" and "for delay" after it name the LDs, they do not scope them ("Liquidated damages
# for delay are excluded"). Only a match of this strict form can be "whole" or "cause"; any other NO_LD match
# is "other".
_NO_LD_STRICT = re.compile(
    r"\b(?:no|without|exclude[sd]?|waive[sd]?|not\s+(?:accept\w*|applicable|agree\w*))\s+(?:to\s+)?"
    r"(?:(?:any|all|the)\s+)?liquidated|"
    r"(?:\bdelay\s+)?liquidated\s+damages?\b(?:\s*\(\s*(?:the\s+)?LDs?\s*\))?(?:\s+for\s+(?:any\s+)?delays?\b)?\s*:?"
    r"(?:\s+(?:are|is|shall|will|hereby|be|been|have|has))*\s+"
    r"(?:excluded|waived|not\s+(?:accepted|applicable|agreed)|do(?:es)?\s+not\s+apply)|"
    r"\bno\s+LDs?\b|\bLDs?\s+(?:(?:are|is|shall\s+be|will\s+be)\s+)?(?:excluded|waived|not\s+applicable)|"
    r"\bLDs?\s+(?:do(?:es)?|shall|will)\s+not\s+apply", re.IGNORECASE)


@dataclass(frozen=True)
class LdExclusion:
    """One clause that excludes or waives liquidated damages (NO_LD). `scope`: "whole" (the whole offer, as
    `no_lds`), "cause" (only delays the vendor does not cause: force majeure or the Buyer's; `causes` holds that
    wording as quoted) or "other" (any other scope, or one that cannot be classified)."""
    clause: str
    scope: Literal["whole", "cause", "other"]
    causes: tuple[str, ...] = ()


def ld_exclusion(clause: str) -> LdExclusion | None:
    """How one clause (no ";" inside) excludes LDs, or None when it does not (ruling R29)."""
    if not NO_LD.search(clause):
        return None
    m = _NO_LD_STRICT.search(clause)
    if not m:  # a scope inside the exclusion's own words
        return LdExclusion(clause.strip(), "other")
    head, tail = clause[:m.start()], clause[m.end():]
    if _NO_LD_HEAD.fullmatch(head) and _NO_LD_TAIL.fullmatch(tail):
        return LdExclusion(clause.strip(), "whole")
    if _LD_CAUSE_HEAD.fullmatch(head) and _LD_CAUSE_TAIL.fullmatch(tail):
        causes = tuple(_LEADING_PREP.sub("", c.group(0).strip())
                       for part in (head, tail) for c in _LD_CAUSE_RE.finditer(part))
        if causes:
            return LdExclusion(clause.strip(), "cause", causes)
    return LdExclusion(clause.strip(), "other")


def ld_exclusions(quote: str) -> list[LdExclusion]:
    """Every clause of a quote that excludes or waives LDs, classified by `ld_exclusion`."""
    found = (ld_exclusion(clause) for sentence in split_sentences(quote) for clause in sentence.split(";"))
    return [e for e in found if e is not None]


def no_lds(sentence: str) -> bool:
    """Whether the sentence excludes liquidated damages from the whole offer: NO_LD, and nothing in its clause
    that could limit the exclusion to some delays or causes."""
    m = NO_LD.search(sentence)
    if not m:
        return False
    start = sentence.rfind(";", 0, m.start()) + 1
    end = sentence.find(";", m.end())
    exclusion = ld_exclusion(sentence[start:] if end < 0 else sentence[start:end])
    return exclusion is not None and exclusion.scope == "whole"


_PER_WEEK_FACTOR = {"week": 1.0, "day": 7.0, "month": 12 / 52}
_CAP_BASIS = (("delayed_portion", re.compile(r"delayed|undelivered|late(?:ly)?\s+delivered|affected", re.IGNORECASE)),
              ("contract", re.compile(r"contract|order|total\s+(?:price|value)", re.IGNORECASE)))


# Bonds named before their percentage: "a warranty bond of 5% of the Contract Price". The nearest bond
# named before the number is the one it belongs to ("an advance payment bond of 10% and a performance bond of 10%").
_BONDS = re.compile(
    r"(?P<warranty_bond_pct>\bwarranty\s+(?:bond|guarantee|security)|\bdefects?\s+liability\s+(?:bond|guarantee|security))|"
    r"(?P<performance_bond_pct>\bperformance\s+(?:bond|guarantee|security))|"
    r"(?P<advance_bond_pct>\badvance\s+payment\s+(?:bond|guarantee|security)|\bdown[- ]payment\s+(?:bond|guarantee|security))",
    re.IGNORECASE)
# What may stand between a bond's name and its value: "of", "equal to ten percent (", "(the Bond) shall be",
# or an opening bracket ("Performance bond (10%) of the Contract Price").
_BOND_GAP = re.compile(
    r"\s*(?:\([^)]{0,20}\)\s*)?\(?\s*(?:(?:shall\s+be|is|of|equal\s+to|amounting\s+to|in\s+the\s+(?:amount|value|sum)\s+of|"
    r"for|valued\s+at|:|,)\s*)*(?:[a-z\-]+\s+(?:percent|per\s?cent)\s*\(\s*|\(\s*)?", re.IGNORECASE)


def _bond_named_before(field_name: str, before: str) -> bool:
    """The bond named last before the percentage is this field's, with only linking words between them."""
    named = list(_BONDS.finditer(before))
    return (bool(named) and named[-1].lastgroup == field_name
            and _BOND_GAP.fullmatch(before[named[-1].end():]) is not None)


# A termination fee: the sentence names the fee or termination for convenience. A percentage that is a
# bond, a cap, interest or a rate is not the fee; two different percentages (a schedule) are not one fee.
_TERMINATION_FEE = re.compile(
    r"\b(?:termination|cancell?ation)\s+(?:fees?|charges?|compensation|payments?)\b|"
    r"\b(?:terminat|cancel)\w*\s+(?:\w+\s+){0,3}?for\s+(?:its\s+)?convenience\b", re.IGNORECASE)
_NOT_A_FEE = re.compile(r"\bbond\b|\bguarantee\b|\bsecurity\b|\bcap(?:ped)?\b|\binterest\b|\bliquidated\b|(?-i:\bLDs?\b)",
                        re.IGNORECASE)
_PER_YEAR = re.compile(r"^\s*(?:per\s+(?:annum|year)\b|p\.\s?a\.|a\s+year\b)", re.IGNORECASE)


def _termination_fee(quote: str) -> FieldValue | None:
    found: dict[float, re.Match] = {}
    for sentence in split_sentences(quote):
        if not _TERMINATION_FEE.search(sentence):
            continue
        for clause in _clauses(sentence):
            for m in _PERCENT.finditer(clause):
                after = clause[m.end():]
                if _RATE_PERIOD.match(after) or _RATE_WEEKLY.match(after) or _PER_YEAR.match(after):
                    continue
                if _NOT_A_FEE.search(clause[max(0, m.start() - 40):m.start()]):
                    continue
                if _after_decimal_comma(clause, m):
                    return None
                found.setdefault(float(m.group(1)), m)
    if len(found) != 1:
        return None
    return _percent_value(next(iter(found.values())))


def _percent_value(m: re.Match, factor: float = 1.0, shown: str = "", qualifier: str | None = None) -> FieldValue:
    number = float(m.group(1))
    return FieldValue("percent", round(number * factor, 9), shown or f"{_fmt(number)} %", qualifier=qualifier)


def _cap_basis(clause: str) -> str:
    return next((basis for basis, pattern in _CAP_BASIS if pattern.search(clause)), "unspecified")


def _parse_percent(quote: str, field_name: str, require_context: bool = True) -> FieldValue | None:
    """One percentage, chosen by the field: `ld_rate` (per week; per day or month is converted),
    `ld_cap` (a maximum on liquidated damages), `liability_cap` (a limit on total liability), a bond's
    value (`warranty_bond_pct`, `performance_bond_pct`, `advance_bond_pct`) or `termination_fee_pct`.
    "No liquidated damages" for the whole offer (`no_lds`) reads as 0 for both LD fields, unless the quote also
    states an LD percentage: then the two disagree and nothing is read (None, never 0). A percentage written
    with a decimal comma ("7,5 %", which matches as "5 %") is not read: None, never a value a tenth or ten
    times off."""
    if not require_context:
        m = _PERCENT.search(quote)
        return _percent_value(m) if m and not _after_decimal_comma(quote, m) else None
    if field_name == "termination_fee_pct":
        return _termination_fee(quote)
    sentences = split_sentences(quote)
    if field_name in ("ld_rate", "ld_cap") and any(no_lds(s) for s in sentences):
        if any(_PERCENT.search(s) and _LD_CONTEXT.search(s) for s in sentences if not no_lds(s)):
            return None  # "no LDs" beside a stated LD percentage: which governs is for a person to decide
        return FieldValue("percent", 0.0, "0 %", qualifier="none")
    for sentence in sentences:
        for clause in _clauses(sentence):
            for m in _PERCENT.finditer(clause):
                before, after = clause[:m.start()], clause[m.end():]
                period, weekly = _RATE_PERIOD.match(after), _RATE_WEEKLY.match(after)
                per = bool(period or weekly)
                decimal_comma = _after_decimal_comma(clause, m)
                if field_name == "ld_rate" and per and _LD_CONTEXT.search(sentence):
                    if decimal_comma:
                        return None
                    unit = period.group(1).lower() if period else ("day" if weekly.group(1).lower() == "daily" else "week")
                    return _percent_value(m, _PER_WEEK_FACTOR[unit], f"{_fmt(float(m.group(1)))} % per {unit}", "per_week")
                is_cap = not per and bool(_CAP_BEFORE.search(before[-60:]) or _CAP_AFTER.match(after))
                if field_name == "ld_cap" and is_cap and _LD_CONTEXT.search(sentence):
                    return None if decimal_comma else _percent_value(m, qualifier=_cap_basis(clause))
                if (field_name == "liability_cap" and is_cap and _LIABILITY_CONTEXT.search(clause)
                        and not _LD_CONTEXT.search(clause)):
                    return None if decimal_comma else _percent_value(m, qualifier=_cap_basis(clause))
                if not per and field_name in _BONDS.groupindex and _bond_named_before(field_name, before):
                    return None if decimal_comma else _percent_value(m)
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


# The amount a supplier credit insurance secures: credit wording is required ("performance security of
# EUR 500,000" is a bond), and a sentence about a bond or guarantee is not read.
_SECURED_CONTEXT = re.compile(r"^(?=.*\b(?:credit\s+(?:insurance|insurer|limit|cover)|trade\s+credit))"
                              r"(?!.*\b(?:bonds?|guarantees?|performance\s+security|advance\s+payment)\b)", re.IGNORECASE)


def _parse_money_cap(quote: str, field_name: str = "", require_context: bool = True) -> FieldValue | None:
    """A capped amount of money with its currency (never assumed): "capped at EUR 500,000". For
    `credit_secured_value`, the amount a credit insurance secures: "secured value of EUR 2,000,000"."""
    context = _SECURED_CONTEXT if field_name == "credit_secured_value" else _MONEY_CAP_CONTEXT
    for sentence in split_sentences(quote):
        if _has_carve_out(sentence) or (require_context and not context.search(sentence)):
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


# --- Contract terms ------------------------------------------------------------------------------
#
# A rate per day or week (delay LDs), a project event (where a warranty starts), payment milestones,
# the governing law and the disputes forum. Rates and percentages are Decimal, read as written. A
# wording that cannot be read for certain (a rate per month, two events, two laws) is None.

def _dec_text(number: Decimal) -> str:
    """A Decimal as a sentence writes it: no exponent, no trailing zeros ("0.5", "1", "1.05", "100")."""
    return format(number.normalize(), "f")


_RATE_UNIT = re.compile(
    r"^\s*(?:of\s+(?:the\s+)?[^.;,%]{0,40}?\s+)?"
    r"(?:(?:per|for\s+each|each|every|/)\s*(?:(?:complete|full|started|commenced|whole|calendar)\s+)?"
    r"(day|week|month)s?\b|(daily|weekly|monthly)\b)", re.IGNORECASE)
_PERIOD_WORDS = {"daily": "day", "weekly": "week", "monthly": "month"}


def _after_decimal_comma(text: str, m: re.Match) -> bool:
    """True when a percentage's number is the tail of a decimal comma ("0,5 %" matches as "5 %").
    Such a number is not read: a rate ten times too high is worse than no rate."""
    return bool(re.search(r"\d,$", text[:m.start()]))


def _rate_base(sentence: str, m: re.Match) -> str | None:
    """What a rate is a percentage of, read from its own clause (up to the next comma, semicolon or
    percentage): "delayed_portion" when the clause names the delayed part (as _CAP_BASIS reads a cap), else
    None (the order or contract price, or not stated)."""
    clause = re.split(r"[,;%]", sentence[m.end():], maxsplit=1)[0]
    return "delayed_portion" if _CAP_BASIS[0][1].search(clause) else None


def _rate_matches(quote: str):
    """(sentence, rate, period, base) for the first rate per day or week in the quote; None when there is none
    or its number cannot be read."""
    for sentence in split_sentences(quote):
        for m in _PERCENT.finditer(sentence):
            unit = _RATE_UNIT.match(sentence[m.end():])
            if not unit:
                continue
            if _after_decimal_comma(sentence, m):
                return None
            period = (unit.group(1) or _PERIOD_WORDS[unit.group(2).lower()]).lower()
            if period == "month":
                continue
            return sentence, Decimal(m.group(1)), period, _rate_base(sentence, m)
    return None


def _parse_rate_per_period(quote: str) -> FieldValue | None:
    """A percentage per day or per week: (Decimal rate, "day" | "week"). A rate per month is not
    read: a month is not a whole number of weeks, so it has no exact weekly equivalent. A rate of the
    delayed portion only carries qualifier "delayed_portion"."""
    found = _rate_matches(quote)
    if found is None:
        return None
    _, rate, period, base = found
    return FieldValue("rate_per_period", (rate, period), f"{_dec_text(rate)}% per {period}", qualifier=base)


def delayed_rate_clause(quote: str) -> str | None:
    """The sentence stating a rate of the delayed portion, when the quote's rate is one."""
    found = _rate_matches(quote)
    return found[0].strip() if found is not None and found[3] == "delayed_portion" else None


def per_week(value: tuple[Decimal, str]) -> Decimal:
    """A rate per day or per week, restated per week (a day's rate times 7). Exact: Decimal throughout."""
    rate, period = value
    if period == "day":
        return rate * 7
    if period == "week":
        return rate
    raise ValueError(f"not a rate per day or per week: {period!r}")


# Project events, matched longest wording first ("cold commissioning" before "commissioning").
# Abbreviations are matched in capitals only, so "cod" or "pac" in running text is not an event.
_EVENT_PATTERNS = (
    ("cold_commissioning", r"\bcold\s+commissioning\b"),
    ("hot_commissioning", r"\bhot\s+commissioning\b"),
    ("cod", r"\bcommercial\s+operations?(?:\s+date)?\b|(?-i:\bCOD\b)"),
    ("pac", r"\bprovisional\s+acceptance\b|(?-i:\bPAC\b)"),
    ("fac", r"\bfinal\s+acceptance\b|(?-i:\bFAC\b)"),
    ("notice_to_proceed", r"\bnotice\s+to\s+proceed\b|(?-i:\bNTP\b)"),
    ("delivery", r"\bdeliver(?:y|ies|ed)\b"),
    ("commissioning", r"\bcommissioning\b"),
)
EVENT = re.compile("|".join(f"(?P<{code}>{pattern})" for code, pattern in _EVENT_PATTERNS), re.IGNORECASE)
EVENT_LABELS = {
    "notice_to_proceed": "notice to proceed", "delivery": "delivery", "cold_commissioning": "cold commissioning",
    "hot_commissioning": "hot commissioning", "commissioning": "commissioning", "cod": "COD", "pac": "PAC",
    "fac": "FAC",
}
# The words that make an event the start point: "from PAC", "starts on the delivery date".
_EVENT_START = re.compile(
    r"\b(?:from|after|following|as\s+of|on|upon|at|starting|commencing|starts?|begins?)\s+(?:the\s+)?"
    r"(?:date\s+of\s+(?:the\s+)?)?(?:issue\s+of\s+(?:the\s+)?)?(?:successful\s+)?$", re.IGNORECASE)
# "whichever is earlier": a start point made of two events, which no single event stands for.
_COMBINED_EVENT = re.compile(r"\bwhichever\b|\bthe\s+(?:earlier|later|first)\s+of\b", re.IGNORECASE)


def _one_event(text: str) -> str | None:
    """The one event `text` names, or the one it names as a start point when it names several."""
    if _COMBINED_EVENT.search(text):
        return None
    found = [(m.lastgroup, bool(_EVENT_START.search(text[max(0, m.start() - 60):m.start()])))
             for m in EVENT.finditer(text)]
    codes = {code for code, _ in found}
    if len(codes) > 1:
        codes = {code for code, started in found if started}
    return codes.pop() if len(codes) == 1 else None


def _parse_event(quote: str) -> FieldValue | None:
    code = _one_event(" ".join(s for s in split_sentences(quote) if not _has_carve_out(s)))
    return FieldValue("event", code, EVENT_LABELS[code]) if code else None


@dataclass(frozen=True)
class Milestone:
    percent: Decimal
    event: str  # a code from ORDINALS["event"]
    days_after: int | None  # None: no number of days stated ("10% at PAC")
    condition: str | None  # what the payment is made against ("warranty bond"), as written

    @property
    def display(self) -> str:
        timing = f"{self.days_after} days after" if self.days_after is not None else "at"
        shown = f"{_dec_text(self.percent)}% {timing} {EVENT_LABELS[self.event]}"
        return f"{shown} against {self.condition}" if self.condition else shown


# A new milestone starts at a percentage after ";", "," or "and".
_MILESTONE_SPLIT = re.compile(
    r"\s*(?:[;,]\s*(?:and\s+)?|\band\s+)(?=\d+(?:\.\d+)?\s*(?:%|per\s?cent\b|percent\b))", re.IGNORECASE)
# "30 days", and the figure of "thirty (30) days".
_MILESTONE_DAYS = re.compile(r"(?<![\w.])(\d+)\)?\s*(?:calendar\s+)?days?\b", re.IGNORECASE)
_DAYS_WORD = re.compile(r"\bdays?\b", re.IGNORECASE)
# Periods this reader does not turn into calendar days: working days, weeks, months.
_MILESTONE_UNREAD = re.compile(
    r"(?<![\w.])\d+\s*(?:(?:working|business|banking)\s+days?|weeks?|months?)\b", re.IGNORECASE)
_CONDITION = re.compile(
    r"\b(?:against|subject\s+to|upon\s+(?:presentation|receipt|submission)\s+of)\s+(?:(?:the|a|an)\s+)?(.+)$",
    re.IGNORECASE)
# A payment due before the event, or counted from something else (an invoice), is not N days after it.
_NOT_AFTER_EVENT = re.compile(r"\bbefore\b|\bprior\s+to\b|\bin\s+advance\b|\bahead\s+of\b", re.IGNORECASE)
_DAYS_ANCHOR = re.compile(
    r"\s+(?:after|from|following|of)\s+(?:the\s+)?(?:date\s+of\s+(?:the\s+)?)?(?:issue\s+of\s+(?:the\s+)?)?"
    r"(?:successful\s+)?", re.IGNORECASE)


def _parse_milestone(segment: str) -> Milestone | None:
    m = _PERCENT.search(segment)
    rest = segment[m.end():]
    condition = _CONDITION.search(rest)
    timing = rest[:condition.start()] if condition else rest
    event = _one_event(timing)
    if event is None or _MILESTONE_UNREAD.search(timing) or _NOT_AFTER_EVENT.search(timing):
        return None
    days = _MILESTONE_DAYS.search(timing)
    if not days and _DAYS_WORD.search(timing):
        return None  # "within thirty days": a period written in words is not "at" the event
    if days:
        # The days must count from the event itself: "30 days after delivery", not "30 days of invoice".
        anchor = _DAYS_ANCHOR.match(timing, days.end())
        counted_from = anchor and EVENT.match(timing, anchor.end())
        if not counted_from or counted_from.lastgroup != event:
            return None
    return Milestone(Decimal(m.group(1)), event, int(days.group(1)) if days else None,
                     condition.group(1).strip(" .;,") if condition else None)


def _parse_milestone_list(quote: str) -> FieldValue | None:
    """Payment milestones in order. Every percentage must be tied to one project event and the
    percentages must add up to 100; anything else is a partial or unreadable schedule, so None."""
    sentences = split_sentences(quote)
    if any(_after_decimal_comma(s, m) for s in sentences for m in _PERCENT.finditer(s)):
        return None  # "12,5 %" would split into "12" and "5 %"
    segments = [part for sentence in sentences for part in _MILESTONE_SPLIT.split(sentence)
                if _PERCENT.search(part)]
    milestones = tuple(_parse_milestone(segment) for segment in segments)
    if not milestones or None in milestones or sum(ms.percent for ms in milestones) != 100:
        return None
    return FieldValue("milestone_list", milestones, "; ".join(ms.display for ms in milestones))


# Jurisdictions, read only beside the word "law": "the laws of Germany", "English law".
_LAWS = (
    ("english", "English", r"\bEngland(?:\s+and\s+Wales)?\b|\bEnglish\b"),
    ("scottish", "Scottish", r"\bScotland\b|\bScottish\b"),
    ("german", "German", r"\bGermany\b|\bGerman\b"),
    ("swiss", "Swiss", r"\bSwitzerland\b|\bSwiss\b"),
    ("austrian", "Austrian", r"\bAustria\b|\bAustrian\b"),
    ("french", "French", r"\bFrance\b|\bFrench\b"),
    ("dutch", "Dutch", r"\bNetherlands\b|\bDutch\b"),
    ("swedish", "Swedish", r"\bSweden\b|\bSwedish\b"),
    ("new_york", "New York", r"\bNew\s+York\b"),
    ("singapore", "Singapore", r"\bSingapore(?:an)?\b"),
)
_LAW_AFTER = re.compile(r"^\s+(?:substantive\s+)?laws?\b", re.IGNORECASE)
_LAW_BEFORE = re.compile(
    r"\blaws?\s+(?:in\s+force\s+in|of)\s+(?:the\s+)?(?:(?:State|Federal\s+Republic|Republic|Kingdom)\s+of\s+(?:the\s+)?)?$",
    re.IGNORECASE)


def _parse_law(quote: str) -> FieldValue | None:
    found: dict[str, str] = {}
    for sentence in split_sentences(quote):
        for code, name, pattern in _LAWS:
            for m in re.finditer(pattern, sentence):
                if _LAW_AFTER.match(sentence[m.end():]) or _LAW_BEFORE.search(sentence[:m.start()]):
                    found[code] = name
    if len(found) != 1:
        return None  # no law named, or two: the clause is read by a person
    code, name = found.popitem()
    return FieldValue("law", code, f"{name} law")


_FORUM_CONTEXT = re.compile(r"arbitra|disput|\bcourts?\b|tribunal|jurisdiction|settle", re.IGNORECASE)
_FORUMS = (
    ("dis", "DIS arbitration",
     r"German\s+Arbitration\s+Institute|Deutsche\s+Institution\s+f(?:ü|ue)r\s+Schiedsgerichtsbarkeit|(?-i:\bDIS\b)"),
    ("icc", "ICC arbitration", r"International\s+Chamber\s+of\s+Commerce|(?-i:\bICC\b)(?!\s*Incoterms)"),
    ("lcia", "LCIA arbitration", r"London\s+Court\s+of\s+International\s+Arbitration|(?-i:\bLCIA\b)"),
)
_COURTS = re.compile(
    r"\bcourts?\s+(?:of|in|at)\s+(?:the\s+)?(?:city\s+of\s+)?"
    r"(?-i:([A-Z][\w\-]*(?:\s+(?:am|an|der|upon|on|de|del)\s+[A-Z][\w\-]*)?))", re.IGNORECASE)
_NOT_A_PLACE = frozenset({"competent", "law", "justice", "appeal", "first", "arbitration", "international",
                          "federal", "canton", "republic", "state", "kingdom"})


def _parse_forum(quote: str) -> FieldValue | None:
    """The disputes forum: "dis", "icc", "lcia", or "courts:<city>". Two forums is None."""
    found: dict[str, str] = {}
    for sentence in split_sentences(quote):
        if not _FORUM_CONTEXT.search(sentence):
            continue  # "DAP per ICC Incoterms" names no forum
        for code, shown, pattern in _FORUMS:
            if re.search(pattern, sentence, re.IGNORECASE):
                found[code] = shown
        for m in _COURTS.finditer(sentence):
            city = m.group(1)
            # "the courts of the Canton of Zurich": a title followed by "of" is not the place.
            if city.lower() not in _NOT_A_PLACE and not re.match(r"\s+of\b", sentence[m.end():]):
                found["courts:" + "_".join(city.lower().split())] = f"courts of {city}"
    if len(found) != 1:
        return None
    code, shown = found.popitem()
    return FieldValue("forum", code, shown)


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
    "lead_time": _parse_lead_time,
    "altitude": _parse_altitude,
    "standard": _parse_standard,
    "rate_per_period": _parse_rate_per_period,
    "event": _parse_event,
    "milestone_list": _parse_milestone_list,
    "law": _parse_law,
    "forum": _parse_forum,
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


# --- Contract-term scope fields (contract_ontology). Each is read to a normalized value from its usual
# wording, or None; the citation-only fields (CITATION_ONLY, below) are recorded as quoted instead. A tuple
# value is compared as a set by check(); the event and document codes in it are
# in a fixed order, so two equal sets give equal tuples.

def _flat(quote: str) -> str:
    return re.sub(r"\s+", " ", _normalize_text(quote)).strip()


# Wording that turns a statement around. A reader that meets it where it matters returns None, not a guess.
_NEGATION = re.compile(r"\bnot\b|\bno\b(?!․)|\bnever\b|\bnone\b|\bneither\b|\bnor\b|\bwithout\b|n['’]t\b",
                       re.IGNORECASE)
_EXCLUSION = re.compile(r"\bexclud\w*|\bexcept\w*\b|\bsave\s+(?:for|where|that)\b|\bcaused\s+by\s+the\s+"
                        r"(?:buyer|purchaser|employer|owner)\b", re.IGNORECASE)
# "not", "shall not be", "is not" right before a phrase: "shall not be subject to escalation".
_NEGATED_BEFORE = re.compile(r"\b(?:not|no|never|without)\b(?:\s+\w+){0,2}\s*$", re.IGNORECASE)


def _polarity(pattern: re.Pattern, text: str) -> set[str]:
    """How each occurrence of `pattern` in `text` stands: "negated" (a "not" within two words before it, as
    in "shall not be subject to escalation"), "unclear" (a negation earlier in the same clause, as in "no part
    of the price is variable", which this reader does not try to scope) or "stated"."""
    return {state for _, state in _polarity_matches(pattern, text)}


def _polarity_matches(pattern: re.Pattern, text: str) -> list[tuple[re.Match, str]]:
    found = []
    for m in pattern.finditer(text):
        clause = re.split(r"[.;,:]\s", text[:m.start()])[-1]
        if _NEGATED_BEFORE.search(clause[-30:]):
            found.append((m, "negated"))
        elif _NEGATION.search(clause):
            found.append((m, "unclear"))
        else:
            found.append((m, "stated"))
    return found


_ANY_EVENT = "|".join(f"(?:{pattern})" for _, pattern in _EVENT_PATTERNS)
# Delay wording followed by a chain of events. The chain stops at the first word that is not a connector,
# so "delay in delivery, capped at 10% of the price payable at PAC" names delivery only.
_LD_TRIGGER_CHAIN = re.compile(
    r"(?:\blate\b|\bdelay(?:s|ed)?\b|\bfailure\s+to\s+(?:achieve|reach|complete)\b)"
    r"(?:\s+(?:in|of|to|the|any|for|achieving|reaching|completing)\b)*\s+(?:" + _ANY_EVENT + r")"
    r"(?:\s*(?:,|and/or|and|or|/)\s*(?:(?:any|the)\s+)?(?:(?:late|delayed|delay\s+(?:in|of))\s+)?(?:"
    + _ANY_EVENT + r"))*", re.IGNORECASE)


def _ld_triggers(quote: str) -> FieldValue | None:
    """The milestones whose delay triggers LDs: "late delivery and/or any late Cold Commissioning and/or Hot
    Commissioning" -> (delivery, cold_commissioning, hot_commissioning)."""
    text = _flat(quote)
    if any(no_lds(s) for s in split_sentences(text)):
        return None
    found = set()
    # Clause by clause: a clause that negates or excludes ("LDs are not applicable to late delivery", "delays
    # to PAC caused by the Buyer are excluded"), or waives LDs however scoped ("LDs are waived for late
    # commissioning"), names no trigger.
    for clause in re.split(r"[;.]\s+|[;.]$", text):
        if _NEGATION.search(clause) or _EXCLUSION.search(clause) or NO_LD.search(clause):
            continue
        found |= {event.lastgroup for chain in _LD_TRIGGER_CHAIN.finditer(clause)
                  for event in EVENT.finditer(chain.group(0))}
    if not found:
        return None
    codes = tuple(code for code in ORDINALS["event"] if code in found)
    return FieldValue("scope", codes, ", ".join(EVENT_LABELS[c] for c in codes))


_FA_NAME = re.compile(r"\b(?:framework|master(?:\s+supply)?|frame)\s+(?:supply\s+)?agreement\b", re.IGNORECASE)
_INCORPORATION = re.compile(
    r"\bsubject\s+to\b|\bincorporat\w+|\bforms?\s+(?:an\s+integral\s+)?part\s+of\b|\bunder\s+the\s+terms\s+of\b|"
    r"\bpursuant\s+to\b|\bin\s+accordance\s+with\b|\bgoverned\s+by\b|\bshall\s+apply\b", re.IGNORECASE)
_ABBREVIATED_REF = re.compile(r"\b(No|Nr|Ref|Nos)\.")
_DATE = (r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|"
         r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")
_FA_IDENTIFIER = re.compile(
    r"\s*(?:\(\s*)?(?:(?:the\s+)?(?:\"[^\"]{1,40}\"|“[^”]{1,40}”)\s*)?(?:,\s*)?"
    r"(?:(?:(?:No\.?|Number|Ref(?:erence)?\.?)\s*[:#]?\s*[A-Z0-9][\w\-/.]*[\w])\s*(?:,\s*)?)?"
    r"(?:dated\s+(?:" + _DATE + r"))?")


def _fa_reference(quote: str) -> FieldValue | None:
    """The framework agreement a contract or offer incorporates: "subject to the terms of the Framework
    Agreement dated 10 January 2026" -> "framework agreement dated 10 january 2026". The name alone is a
    reference too ("framework agreement"); it needs incorporating wording in the same sentence, and a
    sentence that negates or excepts anything ("does not apply", "is not subject to") is not read."""
    # "Agreement No. FA-2025-017" must not split at "No.": its dot is held as a one-dot leader meanwhile.
    text = _ABBREVIATED_REF.sub("\\1\u2024", _flat(quote))
    for sentence in split_sentences(text):
        m = _FA_NAME.search(sentence)
        if not m or not _INCORPORATION.search(sentence):
            continue
        if _NEGATION.search(sentence) or _EXCLUSION.search(sentence):
            return None
        ident = _FA_IDENTIFIER.match(sentence.replace("\u2024", "."), m.end())
        shown = (sentence.replace("\u2024", ".")[m.start():m.end()] + (ident.group(0) if ident else "")).strip(" ,(")
        return FieldValue("scope", shown.lower(), shown)
    return None


# Contract documents, matched in this order (the conditions before the order they are "of").
_DOCUMENTS = (
    ("framework_agreement", r"framework\s+agreement|master\s+(?:supply\s+)?agreement"),
    ("special_conditions", r"special\s+(?:terms\s+and\s+)?conditions|particular\s+conditions"),
    ("general_conditions", r"general\s+(?:terms\s+and\s+)?conditions|general\s+terms"),
    ("contract_agreement", r"contract\s+agreement|agreement\s+form"),
    ("purchase_order", r"purchase\s+order|(?-i:\bP\.?O\.?)(?![\w])"),
    ("technical_specification", r"technical\s+specification|specification|material\s+requisition|(?-i:\bMR\b)|"
                                r"scope\s+of\s+(?:supply|work)"),
    ("offer", r"\boffer\b|\bproposal\b|\bquotation\b|\bbid\b|\btender\b"),
    ("annexes", r"\bannex(?:es)?\b|\bappendi(?:x|ces)\b|\bschedules?\b"),
)
_PRECEDENCE = re.compile(r"precedence|prevail|priority|conflict|inconsisten|discrepan", re.IGNORECASE)
_LIST_MARK = re.compile(r"(?:(?<=[\s;:,])|^)\(?(?:\d{1,2}|[a-h]|[ivx]{1,4})[).](?=\s)")


def _document_code(item: str) -> str | None:
    """The one known document an item names, or None (an unknown document, or an item naming two)."""
    codes = [code for code, pattern in _DOCUMENTS if re.search(pattern, item, re.IGNORECASE)]
    if codes[:1] == ["special_conditions"] or codes[:1] == ["general_conditions"]:
        codes = codes[:1]  # "General Conditions of Purchase", "Special Conditions of Contract"
    return codes[0] if len(codes) == 1 else None


def _precedence_order(quote: str) -> FieldValue | None:
    """The ranking of the contract documents, highest first: "... in the following order: (1) the Purchase
    Order; (2) the Framework Agreement; (3) the Technical Specification" -> (purchase_order,
    framework_agreement, technical_specification). It must be a list after the last colon, and every item
    must name one known document; a ranking written as prose ("X prevails over Y") is left to the reader."""
    text = _flat(quote)
    if not _PRECEDENCE.search(text) or ":" not in text:
        return None
    listed = _LIST_MARK.sub(";", text.rsplit(":", 1)[1])
    items = [re.sub(r"^(?:and|then|finally)\s+|^the\s+|\s+(?:and|then)$|[\s.]+$", "", part.strip(),
                    flags=re.IGNORECASE)
             for part in re.split(r"\s*[;,]\s*|\s+(?:and|then)\s+(?=the\b)", listed)]
    items = [item for item in items if item]
    if len(items) < 2:
        return None
    codes = tuple(_document_code(item) for item in items)
    if None in codes or len(set(codes)) != len(codes):
        return None  # one document twice: the ranking is not a plain order
    return FieldValue("scope", codes, " > ".join(items))


_ON_SITE = re.compile(r"\bon[- ]site\b(?!\s+acceptance)|\bat\s+(?:the\s+)?site\b(?!\s+acceptance)", re.IGNORECASE)
# "Works" alone is the project in FIDIC wording ("remedy any defect in the Works"), so a works is the factory
# only as someone's works, ex works, or the place parts are returned to.
_AT_FACTORY = re.compile(
    r"\b(?:at|to|in)\s+(?:the\s+)?(?:(?:supplier|seller|vendor|manufacturer|contractor)(?:'s|’s)?\s+)?"
    r"(?:factory|workshop)\b|\b(?:supplier|seller|vendor|manufacturer)(?:'s|’s)?\s+works\b|\bex[- ]works\b|"
    r"\breturned?\s+to\s+(?:the\s+)?(?:factory|works)\b", re.IGNORECASE)
_REMEDY = re.compile(r"warrant|repair|defect|remed|replac|rectif", re.IGNORECASE)


def _warranty_on_site(quote: str) -> FieldValue | None:
    """1 when warranty repairs are made on site, 0 when at the factory; both named is None (which applies
    when is not for this reader). Only clauses about repairs count: in "Delivery ex works; warranty repairs
    are made on site", ex works is the Incoterm."""
    clauses = [c for c in re.split(r"[;.]\s+|[;.]$", _flat(quote)) if _REMEDY.search(c)]
    factory = any(_AT_FACTORY.search(c) for c in clauses)
    on_site = any(_ON_SITE.search(c) for c in clauses)
    if factory and on_site:
        return None
    if factory:
        return FieldValue("scope", 0, "at the factory")
    if on_site:
        return FieldValue("scope", 1, "on site")
    return None


_ALL_COMPONENTS = re.compile(
    r"\ball\s+(?:the\s+)?(?:components|parts|equipment|items|goods|materials)\b|\bentire\s+(?:supply|equipment|scope)\b|"
    r"\bwhole\s+(?:of\s+the\s+)?(?:supply|equipment)\b", re.IGNORECASE)
# Coverage limited to some components. "only" must be about components ("only the components listed"), not
# about something else ("shall only start at PAC"); "except normal wear and tear" excludes a cause, not a part.
_LISTED_COMPONENTS = re.compile(
    r"\bonly\s+(?:the\s+|those\s+)?(?:components|parts|items|equipment)\b|"
    r"\b(?:components|parts|items)\s+(?:listed|specified|named)\b|\blisted\s+(?:components|parts|items)\b|"
    r"\blimited\s+to\s+(?:the\s+)?(?:components|parts|items)\b|"
    r"\b(?:except|excluding|other\s+than)\b(?!\s+(?:for\s+)?(?:normal\s+|fair\s+)?wear\s+and\s+tear)|"
    r"\bwear(?:ing)?\s+parts\b|\bconsumables\b|\bnot\s+cover", re.IGNORECASE)
_EXCEPT_AS_STATED = re.compile(r"\b(?:except|save|unless)\s+(?:as|where|otherwise)\b", re.IGNORECASE)


def _warranty_coverage(quote: str) -> FieldValue | None:
    """1 when the warranty covers all components, 0 when it covers listed ones or carves some out."""
    text = _flat(quote)
    if not _REMEDY.search(text):
        return None
    if _EXCEPT_AS_STATED.search(text):
        return None  # "except as stated in Clause 5": what is excepted is elsewhere
    if _LISTED_COMPONENTS.search(text):
        return FieldValue("scope", 0, "listed components only")
    if _ALL_COMPONENTS.search(text):
        return FieldValue("scope", 1, "all components")
    return None


# --- Citation-only fields (ruling R18). Antova does not normalize or compare these in v1: the escalation
# formula is quoted, not computed (spec 4.2), and a named place or a list of carve-outs from the liability
# cap is read by a person from the quoted clause. The reader only records that the clause was found: its
# value and display are the quote itself, whitespace collapsed, with no meaning read into it. check() never
# passes or fails such a field; it is not comparable ("compare the quoted clauses by hand").
CITATION_ONLY: frozenset[str] = frozenset({"named_place", "escalation_formula", "liability_exclusions"})
NOT_COMPARABLE = "compare the quoted clauses by hand"

# What a quote must be about to count as the clause for each field. Presence only: nothing here decides
# what the clause says, so the worst it can do is leave a clause unrecorded (shown as missing).
_CITATION_TOPIC = {
    "named_place": re.compile(r"\b(?:EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b|ex[- ]works|delivered\s+at\s+place|"
                              r"delivered\s+duty\s+paid|free\s+carrier"),
    "escalation_formula": re.compile(r"escalat|adjust|index|formula|variation|\bfirm\b|\bfixed\b", re.IGNORECASE),
    "liability_exclusions": re.compile(
        r"^(?=.*liabilit)(?=.*(?:\bexcept|\bsave\b|\bother\s+than\b|\bnot\s+(?:apply|limit|be\s+limited)|"
        r"\bunlimited\b|\bexclu|\bcarve))", re.IGNORECASE | re.DOTALL),
}


def citation_only(field: FieldDef) -> bool:
    """Whether the field is only cited, never compared (CITATION_ONLY)."""
    return field.name in CITATION_ONLY


def _quoted_clause(field_name: str):
    topic = _CITATION_TOPIC[field_name]

    def read(quote: str) -> FieldValue | None:
        text = re.sub(r"\s+", " ", quote).strip()
        if not text or not topic.search(text):
            return None
        return FieldValue("scope", text, text)
    read.__name__ = f"_{field_name}_clause"
    return read


_CURRENCY_NAMES = (
    ("EUR", r"\bEUR\b|€|\beuros?\b"), ("GBP", r"\bGBP\b|£|\bpounds?\s+sterling\b|\bsterling\b"),
    ("USD", r"\bUSD\b|\$|\bUS\s+dollars?\b"), ("CHF", r"\bCHF\b|\bSwiss\s+francs?\b"),
    ("SEK", r"\bSEK\b"), ("NOK", r"\bNOK\b"), ("DKK", r"\bDKK\b"), ("PLN", r"\bPLN\b"),
)
_PRICE_WORD = re.compile(r"\bprices?\b|\bcurrency\b|\bpayments?\b|\bquoted\b|\binvoic", re.IGNORECASE)


def _price_currency(quote: str) -> FieldValue | None:
    """The currency of the price: "The Contract Price is in EUR" -> "EUR". Two currencies is None."""
    text = _flat(quote)
    if not _PRICE_WORD.search(text):
        return None
    # Every currency in the quote counts ("Prices in EUR; spares in GBP." is two, so None).
    found = {code for code, pattern in _CURRENCY_NAMES if re.search(pattern, text, re.IGNORECASE)}
    if len(found) != 1:
        return None
    code = found.pop()
    return FieldValue("scope", code, code)


_NTP = r"(?:the\s+)?(?:issue\s+of\s+(?:the\s+)?)?(?:NTP|notice\s+to\s+proceed)\b"
_FIXED_AFTER_NTP = re.compile(
    r"\b(?:fixed|firm)\b[^.;]{0,60}\b(?:after|following|from|upon|once|on)\s+" + _NTP + r"|"
    r"\b(?:after|following|from|upon|once)\s+" + _NTP + r"[^.;]{0,60}\b(?:fixed|firm|not\s+subject\s+to)\b|"
    r"\b(?:until|before|prior\s+to)\s+" + _NTP + r"[^.;]{0,30}\b(?:fixed|firm)\s+(?:thereafter|afterwards)\b",
    re.IGNORECASE)
_UNTIL_NTP = re.compile(r"\s+(?:\w+\s+){0,3}?(?:until|before|prior\s+to)\s+" + _NTP, re.IGNORECASE)
# Wording that lets the price move. Each is read only where no "not" stands before it, so "shall not be
# subject to escalation" says the price is fixed.
_MOVES = re.compile(r"\bsubject\s+to\s+(?:any\s+)?(?:price\s+)?(?:escalation|adjustment|variation|indexation)\b|"
                    r"\bvariable\b|\bindexed\b", re.IGNORECASE)
_FIXED_UNTIL_NTP = re.compile(r"\b(?:fixed|firm)\b[^.;]{0,40}\buntil\b[^.;]{0,30}(?:NTP|notice\s+to\s+proceed)",
                              re.IGNORECASE)


def _price_fixed_after_ntp(quote: str) -> FieldValue | None:
    """1 when the price is fixed once the NTP is issued, 0 when it may still move (escalation, variable)."""
    text = _flat(quote)
    # Movement only until the NTP ("subject to escalation until NTP and fixed thereafter") is not movement after it.
    moves = {state for m, state in _polarity_matches(_MOVES, text) if not _UNTIL_NTP.match(text, m.end())}
    fixed = _polarity(_FIXED_AFTER_NTP, text)
    if "unclear" in moves | fixed:
        return None  # "no part of the price is variable": a negation this reader does not scope
    if "stated" in moves or "negated" in fixed or _FIXED_UNTIL_NTP.search(text):
        return FieldValue("scope", 0, "not fixed after NTP")  # "The price is not fixed after NTP."
    if "stated" in fixed:
        return FieldValue("scope", 1, "fixed after NTP")
    return None


_PARTLY_VARIABLE = re.compile(r"\bpart(?:ly|ially)?\s+variable\b|\bpart(?:ly)?\s+fixed\s+and\s+part(?:ly)?\s+variable\b",
                              re.IGNORECASE)
_LUMP_SUM = re.compile(r"\blump[- ]sum\b", re.IGNORECASE)
_PRICE_TYPES = {"fixed_lump_sum": "fixed lump sum", "partly_variable": "partly variable", "variable": "variable"}


def _price_type(quote: str) -> FieldValue | None:
    """fixed_lump_sum, partly_variable or variable. A lump sum that is still adjusted is partly variable."""
    text = _flat(quote)
    partly, lump, moves = (_polarity(p, text) for p in (_PARTLY_VARIABLE, _LUMP_SUM, _MOVES))
    if "unclear" in partly | lump | moves:
        return None
    if "stated" in partly:
        code = "partly_variable"
    elif "stated" in lump:
        code = "partly_variable" if "stated" in moves else "fixed_lump_sum"
    elif "stated" in moves:
        code = "variable"
    else:
        return None
    return FieldValue("scope", code, _PRICE_TYPES[code])


_COMPANY = r"[A-Z][\w&'’\-]*(?:\s+(?:[A-Z][\w&'’\-]*|&|of|plc|AG|SA|SE|NV|BV|GmbH|Ltd\.?|Limited|Inc\.?|S\.A\.))*"
_INSURER = re.compile(
    r"(?i:\binsurer)\s*[:\-]\s*(?P<a>" + _COMPANY + r")|"
    r"(?i:\b(?:credit\s+insurance|insured|insurance\s+cover|cover(?:ed)?|policy)\b[^.;]{0,40}?"
    r"\b(?:with|from|by|issued\s+by|provided\s+by|placed\s+with)\s+(?:the\s+)?)(?P<b>" + _COMPANY + r")")


_CREDIT_CONTEXT = re.compile(r"\bcredit\s+(?:insurance|insurer|cover|risk|limit)|\btrade\s+credit|\bexport\s+credit",
                             re.IGNORECASE)
_PARTIES = frozenset({"supplier", "seller", "vendor", "buyer", "purchaser", "contractor", "employer", "owner",
                      "manufacturer", "company", "client"})


def _credit_insurer(quote: str) -> FieldValue | None:
    """The credit insurer named: "credit insurance with Northgate Credit Assurance plc" -> its name. Only in a
    sentence about credit insurance ("insured by the Supplier until delivery" is cargo cover), and a party
    of the contract is not an insurer's name."""
    for sentence in split_sentences(_flat(quote)):
        if not _CREDIT_CONTEXT.search(sentence):
            continue
        m = _INSURER.search(sentence)
        if m:
            shown = (m.group("a") or m.group("b")).strip(" ,.")
            if shown.lower() in _PARTIES:
                return None
            return FieldValue("scope", shown.lower(), shown)
    return None


_CONTRACT_SCOPE_PARSERS = {
    "ld_triggers": _ld_triggers,
    "liability_exclusions": _quoted_clause("liability_exclusions"),
    "warranty_on_site": _warranty_on_site,
    "warranty_coverage": _warranty_coverage,
    "named_place": _quoted_clause("named_place"),
    "price_currency": _price_currency,
    "price_fixed_after_ntp": _price_fixed_after_ntp,
    "price_type": _price_type,
    "escalation_formula": _quoted_clause("escalation_formula"),
    "credit_insurer": _credit_insurer,
    "fa_reference": _fa_reference,
    "order": _precedence_order,
}


def _parse_scope(quote: str, field_name: str) -> FieldValue | None:
    """1 ("included") or 0 ("not included") for what the field names, or None when the text does not say.
    A contract-term scope field is read by its own reader (_CONTRACT_SCOPE_PARSERS)."""
    if field_name in _CONTRACT_SCOPE_PARSERS:
        return _CONTRACT_SCOPE_PARSERS[field_name](quote)
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


# Parsers that also need the field's name (an LD cap and a liability cap are both percentages; a warranty
# period and a design life are both months).
NAME_PARSERS = {"percent": _parse_percent, "scope": _parse_scope, "months": _parse_months,
                "money_cap": _parse_money_cap}
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
    """Return (passed, offered value expressed in the required unit).

    A citation-only field (CITATION_ONLY) is never compared: the result is (False, NOT_COMPARABLE), and the
    caller records it as not comparable (CheckResult.comparable=False), so it neither passes nor fails.
    """
    if citation_only(field):
        return False, NOT_COMPARABLE
    req, off = required.value, offered.value
    if field.kind == "rate_per_period":
        # Compared per week; a daily offer also shows its weekly equivalent.
        a, b = per_week(off), per_week(req)
        passed = {"ge": a >= b, "le": a <= b}.get(field.operator, a == b)
        shown = offered.display if off[1] == "week" else f"{offered.display} ({_dec_text(a)}% per week)"
        return passed, shown
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
    if field.kind == "scope" and isinstance(req, tuple) and isinstance(off, tuple):
        # A set of codes (LD triggers): "ge" is every required one offered.
        # An order of precedence is a ranking, so it is equal only in the same order.
        if field.operator == "ge":
            return set(off) >= set(req), offered.display
        return off == req, offered.display
    if isinstance(req, Decimal) and isinstance(off, Decimal):
        if required.qualifier != offered.qualifier:
            return False, offered.display  # two currencies: not comparable without a dated rate
        passed = {"ge": off >= req, "le": off <= req}.get(field.operator, off == req)
        return passed, offered.display
    if isinstance(req, IPCode) and isinstance(off, IPCode):
        return (off.solid >= req.solid and off.liquid >= req.liquid), offered.display
    if field.operator == "le" and field.kind == "event":
        # Earlier is better (title passing at delivery rather than at PAC). An unknown event fails.
        order = ORDINALS["event"]
        if req not in order or off not in order:
            return False, offered.display
        return order.index(off) <= order.index(req), offered.display
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
    if value.kind == "rate_per_period":
        return float(per_week(raw))  # "0.01% per day" and "1% per week" on one scale
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
        if value.qualifier == "years":
            return any(int(m.group(1)) * 12 == value.value for m in _YEARS.finditer(text))
        if value.qualifier == "days":
            return any(days_to_months(int(m.group(1))) == value.value for m in _PERIOD_DAYS.finditer(text))
        return any(int(m.group(1)) == value.value for m in _MONTHS.finditer(text))
    if kind == "lead_time":
        return any(math.isclose(_weeks(float(m.group(2) or m.group(1)), m.group(3)), value.value, rel_tol=1e-9)
                   for m in _LEAD_TIME.finditer(text))
    if kind == "percent":
        if value.qualifier == "none":
            return True  # "no liquidated damages": read from wording, no number to find
        shown = float(value.display.split(" ", 1)[0])
        return any(math.isclose(float(m.group(1)), shown, rel_tol=1e-9) for m in _PERCENT.finditer(text))
    if kind == "rate_per_period":
        return any(Decimal(m.group(1)) == value.value[0] and not _after_decimal_comma(text, m)
                   for m in _PERCENT.finditer(text))
    if kind == "milestone_list":
        percents = {Decimal(m.group(1)) for m in _PERCENT.finditer(text) if not _after_decimal_comma(text, m)}
        days = {int(m.group(1)) for m in _MILESTONE_DAYS.finditer(text)}
        return all(ms.percent in percents and (ms.days_after is None or ms.days_after in days) for ms in value.value)
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
    return True  # material, protocol, marshalling, event, law, forum: read from words, no number involved


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


# A citation-only clause that already shows another field's value: the named place's clause states the
# Incoterm ("DAP Rotterdam, Incoterms 2020."), so the Incoterm is not shown again ahead of it.
_SHOWN_IN_CLAUSE = {"named_place": ("incoterm",)}


def display_text(param: ParameterDef, values: dict[str, FieldValue]) -> str:
    shown = {f.name: values[f.name].display if f.name in values else "—" for f in param.fields}
    hidden = False
    for clause_field, others in _SHOWN_IN_CLAUSE.items():
        if clause_field not in values or clause_field not in shown:
            continue
        for other in others:
            # Left out only when the clause states that very value; a different Incoterm stays visible.
            if other in values and re.search(rf"\b{re.escape(values[other].display)}\b", values[clause_field].display):
                shown[other], hidden = "", True
    text = param.display_template.format(**shown)
    return text.strip() if hidden else text
