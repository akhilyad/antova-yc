"""Who must approve a contract, and which reviewer role may accept each contract finding.

The rules are data (antova_ingest/approver_rules/<name>.json, versioned like the rate tables);
this module only reads and applies them. Every suggestion carries the reason it was made.

Money against the director threshold is compared in the budget currency (`budget_currency`). An
amount in another currency is converted with the buyer-entered rate (`fx`, contract_checks.fx_convert);
a rate is never fetched or assumed. An amount that cannot be compared (no rate, no currency, or no
budget currency given) adds business_director with a reason naming it (ruling R37).
"""

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from collections.abc import Sequence
from pathlib import Path

from antova_auth import ROLES

from .contract_checks import fx_convert
from .contract_ontology import LEGAL_TERMS
from .costs import ConfigError
from .types import BaselineFx

RULES_DIR = Path(__file__).parent / "approver_rules"
RULES_PATTERN = r"[a-z0-9_]{1,64}"
CATEGORIES = ("fa_conformance", "back_to_back", "budget", "missing")


@dataclass(frozen=True)
class ApproverRules:
    name: str
    version: int
    always: tuple[str, ...]
    procurement_when_negotiated: str
    legal_terms_role: str
    director_role: str
    director_money_threshold: Decimal
    acceptance: dict
    legal_terms_accept: str


@dataclass(frozen=True)
class Suggestion:
    role: str
    reason: str


def _role(value, where: str) -> str:
    if value not in ROLES:
        raise ConfigError(f"approver rules: {where} is {value!r}, not one of {list(ROLES)}")
    return value


def parse_rules(data: dict) -> ApproverRules:
    try:
        extra = set(data["acceptance"]) - set(CATEGORIES)
        if extra:
            raise ConfigError(f"approver rules: unknown acceptance categories {sorted(extra)}")
        return ApproverRules(
            name=str(data["name"]), version=int(data["version"]),
            always=tuple(_role(r, "always") for r in data["always"]),
            procurement_when_negotiated=_role(data["procurement_when_negotiated"], "procurement_when_negotiated"),
            legal_terms_role=_role(data["legal_terms_role"], "legal_terms_role"),
            director_role=_role(data["director_role"], "director_role"),
            director_money_threshold=Decimal(str(data["director_money_threshold"])),
            acceptance={c: _role(data["acceptance"][c], f"acceptance.{c}") for c in CATEGORIES},
            legal_terms_accept=_role(data["legal_terms_accept"], "legal_terms_accept"))
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ConfigError(f"approver rules are not valid: {exc!r}") from exc


def load_rules(name: str | None = None) -> ApproverRules:
    """Rules by name; the default comes from ANTOVA_APPROVER_RULES (read at call time), else default_v1."""
    name = name if name is not None else os.getenv("ANTOVA_APPROVER_RULES", "default_v1")
    if not re.fullmatch(RULES_PATTERN, name):
        raise ValueError(f"invalid approver rules name {name!r}")
    path = RULES_DIR / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"approver rules {name!r} not found") from exc
    except ValueError as exc:
        raise ConfigError(f"approver rules {name!r} are not valid JSON: {exc}") from exc
    return parse_rules(data)


def _amount(m: dict) -> Decimal | None:
    if m.get("amount") is None:
        return None
    try:
        return Decimal(str(m["amount"]))
    except InvalidOperation:
        return None


def _money_reasons(findings: list[dict], currency: str | None, threshold: Decimal,
                   fx: Sequence[BaselineFx]) -> list[str]:
    """Director reasons from finding money (ruling R37): the largest amount at or over the threshold in the
    budget currency (converted at the buyer rate when needed), and every amount that could not be compared."""
    best: tuple[Decimal, str] | None = None
    uncompared: list[str] = []
    for f in findings:
        for m in f.get("money") or []:
            amount = _amount(m)
            if amount is None:
                continue
            shown = f"{m.get('currency') or '(no currency)'} {amount}"
            if currency is None:
                uncompared.append(f"{shown} (no budget currency given)")
                continue
            if not m.get("currency"):
                uncompared.append(f"{shown} (the amount has no currency)")
                continue
            converted = fx_convert(amount, m["currency"], currency, fx)
            if converted.amount is None:
                uncompared.append(f"{shown} ({converted.reason})")
                continue
            text = shown if m["currency"] == currency else f"{shown} (= {converted.formula})"
            if converted.amount >= threshold and (best is None or converted.amount > best[0]):
                best = (converted.amount, text)
    out = []
    if best is not None:
        out.append(f"A finding carries {best[1]}, at or over {currency} {threshold}.")
    if uncompared:
        where = f"the {currency} {threshold}" if currency else f"the {threshold}"
        out.append(f"A finding carries {'; '.join(uncompared)}, which could not be compared with {where} threshold.")
    return out


def suggest_approvers(rules: ApproverRules, *, findings: list[dict], budget_variance_pct: Decimal | None,
                      signing_before_ntp: bool | None, procurement_negotiated: bool | None,
                      budget_currency: str | None = None, fx: Sequence[BaselineFx] = ()) -> list[Suggestion]:
    """The roles that must approve, in a stable order, each with why. An unknown flag (None) does not fire;
    finding money that cannot be compared with the threshold does (ruling R37)."""
    out = [Suggestion(r, "Always required for a contract approval.") for r in rules.always]
    if procurement_negotiated is True:
        out.append(Suggestion(rules.procurement_when_negotiated, "Procurement negotiated this contract."))
    legal = sorted({f["param_id"] for f in findings if f.get("param_id") in LEGAL_TERMS})
    if legal:
        out.append(Suggestion(rules.legal_terms_role, f"A finding is on a legal term ({', '.join(legal)})."))
    reasons = []
    if signing_before_ntp is True:
        reasons.append("The contract is to be signed before notice to proceed.")
    if budget_variance_pct is not None and budget_variance_pct > 0:
        reasons.append(f"The contract is {budget_variance_pct}% over the frozen budget.")
    reasons += _money_reasons(findings, budget_currency, rules.director_money_threshold, fx)
    if reasons:
        out.append(Suggestion(rules.director_role, " ".join(reasons)))
    seen: set[str] = set()
    unique = []
    for s in out:  # a role is listed once, with its first reason
        if s.role not in seen:
            seen.add(s.role)
            unique.append(s)
    return unique


def role_to_accept(rules: ApproverRules, finding: dict) -> str:
    """The role that may accept this finding. A finding on a legal term is legal's, whatever its category."""
    if finding.get("param_id") in LEGAL_TERMS:
        return rules.legal_terms_accept
    category = finding.get("category")
    if category not in rules.acceptance:
        raise ValueError(f"finding {finding.get('id')!r} has unknown category {category!r}")
    return rules.acceptance[category]
