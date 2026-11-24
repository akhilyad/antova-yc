"""The contract terms checked before approval (spec 4.2): CT-01 to CT-17.

A second ontology beside ontology.PARAMETERS, independent of equipment class. It has the same
structure (ParameterDef and FieldDef) and is located in framework agreements, owner contracts and
the commercial terms of vendor bids. "Better is" is always the buyer's side, as in the equipment
ontology: a field passes when the offered value is at least as good as the required one.

Plausible ranges are written like values in a contract, as in ontology.py. A design life is held in
months, so 10 to 50 years is 120 to 600 months; values.py reads "35 years" as 420 months and shows both.
An NTP deadline written in days is restated in months (days x 12 / 365), also shown.
"""

from __future__ import annotations

from antova_engine import Criticality
from antova_ingest.ontology import FieldDef, ParameterDef

# The document roles that hold contract terms only. A vendor bid holds both kinds and is located
# twice: once for the equipment parameters and once for these terms.
CONTRACT_ROLES: frozenset[str] = frozenset({"framework_agreement", "owner_contract"})

_PERCENT_OF_PRICE = "as a percentage of the contract price"
_BOND = ("Quote the clause that states the percentage of the contract price and how long the bond runs "
         "or when it is released.")

CONTRACT_PARAMETERS: tuple[ParameterDef, ...] = (
    ParameterDef(
        "CT-01", "Delay LDs", "Liquidated Damages", Criticality.HIGH,
        (FieldDef("ld_rate", "rate_per_period", "ge", "LD rate", plausible=("0.01% per day", "5% per day")),
         FieldDef("ld_cap", "percent", "ge", "LD cap", plausible=("1 %", "100 %")),
         FieldDef("ld_triggers", "scope", "ge", "LD triggers")),
        "{ld_rate}; cap {ld_cap}; triggered by {ld_triggers}",
        f"Delay liquidated damages: the rate per day or per week of delay {_PERCENT_OF_PRICE}, the cap on "
        "them, and the milestones whose delay triggers them (delivery, cold commissioning, hot "
        "commissioning). If liquidated damages are excluded or not accepted, quote that.",
        sweep_terms=(r"liquidated damages", r"\bLDs?\b", r"delay damages", r"late delivery"),
    ),
    ParameterDef(
        "CT-02", "Limitation of liability", "Liability", Criticality.HIGH,
        (FieldDef("liability_cap", "percent", "ge", "Liability cap", plausible=("1 %", "1000 %")),
         FieldDef("liability_cap_money", "money_cap", "ge", "Liability cap (amount)"),
         FieldDef("liability_exclusions", "scope", "eq", "Exclusions from the cap")),
        "{liability_cap} / {liability_cap_money}; exclusions {liability_exclusions}",
        f"The cap on the supplier's total liability, {_PERCENT_OF_PRICE} or as an amount with its currency, "
        "and what is excluded from the cap. Not liquidated damages and not the defect liability period.",
        sweep_terms=(r"limitation of liability", r"aggregate liability", r"total liability", r"liabilit\w+"),
    ),
    ParameterDef(
        "CT-03", "Warranty", "Warranty", Criticality.HIGH,
        (FieldDef("warranty_months", "months", "ge", "Warranty months", plausible=("6", "120")),
         FieldDef("warranty_start", "event", "ord", "Warranty starts at"),
         FieldDef("warranty_on_site", "scope", "ge", "Warranty on site"),
         FieldDef("warranty_coverage", "scope", "ge", "Warranty coverage")),
        "{warranty_months} months from {warranty_start}; on site {warranty_on_site}; covers {warranty_coverage}",
        "The warranty or defect liability period, the event it runs from (delivery, commissioning, provisional "
        "or final acceptance), whether repairs are made on site or at the factory, and whether it covers all "
        "components or only listed ones.",
        sweep_terms=(r"warrant\w+", r"defects? liability", r"defects notification", r"guarantee period"),
    ),
    ParameterDef(
        "CT-04", "Warranty bond", "Bonds & Guarantees", Criticality.HIGH,
        (FieldDef("warranty_bond_pct", "percent", "ge", "Warranty bond", plausible=("1 %", "30 %")),
         FieldDef("warranty_bond_months", "months", "ge", "Warranty bond months", plausible=("1", "120"))),
        "{warranty_bond_pct} for {warranty_bond_months} months",
        f"The warranty bond or defects liability guarantee: its value {_PERCENT_OF_PRICE} and its duration. "
        + _BOND,
        sweep_terms=(r"warranty bond", r"warranty guarantee", r"defects? liability (?:bond|guarantee)"),
    ),
    ParameterDef(
        "CT-05", "Performance bond", "Bonds & Guarantees", Criticality.HIGH,
        (FieldDef("performance_bond_pct", "percent", "ge", "Performance bond", plausible=("1 %", "30 %")),
         FieldDef("performance_bond_release", "event", "ord", "Performance bond released at")),
        "{performance_bond_pct}, released at {performance_bond_release}",
        f"The performance bond or performance guarantee: its value {_PERCENT_OF_PRICE} and the event at which "
        "it is released or returned. " + _BOND,
        sweep_terms=(r"performance (?:bond|guarantee|security)",),
    ),
    ParameterDef(
        "CT-06", "Advance payment bond", "Bonds & Guarantees", Criticality.HIGH,
        (FieldDef("advance_bond_pct", "percent", "ge", "Advance payment bond", plausible=("1 %", "30 %")),),
        "{advance_bond_pct}",
        f"The advance payment bond or guarantee against a down payment: its value {_PERCENT_OF_PRICE}, or the "
        "statement that no advance payment is made.",
        sweep_terms=(r"advance payment (?:bond|guarantee|security)", r"down[- ]payment (?:bond|guarantee)"),
    ),
    ParameterDef(
        "CT-07", "Payment milestones", "Payment", Criticality.HIGH,
        (FieldDef("milestones", "milestone_list", "ge", "Payment milestones"),),
        "{milestones}",
        "The payment schedule: each instalment as a percentage of the price, the event it is tied to, the "
        "number of days after that event it is paid, and what it is paid against (an invoice, a bond, a "
        "certificate). Quote the whole schedule.",
        sweep_terms=(r"payment terms", r"terms of payment", r"payment schedule", r"milestone", r"instal+ments?"),
    ),
    ParameterDef(
        "CT-08", "Termination for convenience fees", "Termination", Criticality.COMMERCIAL,
        (FieldDef("termination_fee_pct", "percent", "le", "Termination fee", plausible=("0 %", "100 %")),),
        "{termination_fee_pct}",
        "What the buyer pays if it terminates for convenience: the fee schedule as a percentage of the price "
        "against the time before delivery at which notice is given.",
        sweep_terms=(r"termination for convenience", r"terminat\w+ (?:fee|charge|compensation)",
                     r"cancellation (?:fee|charge)s?"),
    ),
    ParameterDef(
        "CT-09", "Passing of title", "Title & Risk", Criticality.COMMERCIAL,
        (FieldDef("title_event", "event", "le", "Title passes at"),),
        "title passes at {title_event}",
        "The event at which ownership (title) of the equipment passes to the buyer. Not the passing of risk "
        "under the Incoterm, unless the clause ties title to it.",
        sweep_terms=(r"\btitle\b", r"ownership", r"property in the (?:goods|equipment)"),
    ),
    ParameterDef(
        "CT-10", "NTP deadline", "Schedule", Criticality.COMMERCIAL,
        (FieldDef("ntp_deadline_months", "months", "ge", "NTP deadline months", plausible=("1", "60")),),
        "notice to proceed within {ntp_deadline_months} months",
        "How long after the purchase order the buyer may issue the notice to proceed (NTP) before the price "
        "or the offer lapses, in months or days.",
        sweep_terms=(r"notice to proceed", r"\bNTP\b"),
    ),
    ParameterDef(
        "CT-11", "Design life / minimum operating period", "Design Life", Criticality.COMMERCIAL,
        (FieldDef("design_life_months", "months", "ge", "Design life months", plausible=("120", "600")),),
        "design life {design_life_months} months",
        "The design life or minimum operating period the supplier commits to for the equipment, in years.",
        sweep_terms=(r"design life", r"service life", r"operating (?:life|period)", r"lifetime"),
    ),
    ParameterDef(
        "CT-12", "Incoterms and named place", "Delivery Terms", Criticality.COMMERCIAL,
        (FieldDef("incoterm", "incoterm", "ord", "Incoterm"),
         FieldDef("named_place", "scope", "eq", "Named place")),
        "{incoterm} {named_place}",
        "The delivery Incoterm (Incoterms 2020) and the named place it applies to.",
        sweep_terms=(r"\b(?:EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b", r"incoterm", r"ex[- ]works",
                     r"delivered (?:at place|duty paid)", r"free carrier"),
    ),
    ParameterDef(
        "CT-13", "Price basis", "Price Basis", Criticality.COMMERCIAL,
        (FieldDef("price_currency", "scope", "eq", "Price currency"),
         FieldDef("price_fixed_after_ntp", "scope", "ge", "Price fixed after NTP"),
         FieldDef("price_type", "scope", "eq", "Lump sum or variable"),
         FieldDef("escalation_formula", "scope", "eq", "Escalation formula")),
        "{price_currency}; fixed after NTP {price_fixed_after_ntp}; {price_type}; escalation {escalation_formula}",
        "The price basis: the currency, whether the price is fixed once the notice to proceed is issued, "
        "whether it is a fixed lump sum, partly variable or variable, and any price escalation or adjustment "
        "formula (quote the formula as written).",
        sweep_terms=(r"price basis", r"lump[- ]sum", r"fixed price", r"escalation", r"price adjustment",
                     r"variable price"),
    ),
    ParameterDef(
        "CT-14", "Governing law and disputes", "Law & Disputes", Criticality.COMMERCIAL,
        (FieldDef("law", "law", "eq", "Governing law"),
         FieldDef("forum", "forum", "eq", "Disputes forum")),
        "{law}; disputes: {forum}",
        "The law that governs the contract and where disputes are settled (the arbitration institution and "
        "seat, or the courts).",
        sweep_terms=(r"governing law", r"governed by", r"\blaws? of\b", r"arbitrat\w+", r"jurisdiction",
                     r"disputes?"),
    ),
    ParameterDef(
        "CT-15", "Supplier credit insurance", "Credit Insurance", Criticality.COMMERCIAL,
        (FieldDef("credit_insurer", "scope", "eq", "Credit insurer"),
         FieldDef("credit_secured_value", "money_cap", "ge", "Secured value")),
        "{credit_insurer}; secured {credit_secured_value}",
        "Supplier credit insurance: the insurer named and the value it secures.",
        sweep_terms=(r"credit insurance", r"credit insurer", r"trade credit", r"credit limit"),
    ),
    ParameterDef(
        "CT-16", "Incorporation of the framework agreement", "Contract Structure", Criticality.HIGH,
        (FieldDef("fa_reference", "scope", "eq", "Framework agreement incorporated"),),
        "{fa_reference}",
        "The clause that makes a framework agreement part of this contract or offer: quote it with the "
        "agreement's name, number or date as written.",
        sweep_terms=(r"framework agreement", r"master (?:supply )?agreement", r"incorporated", r"form(?:s)? part of"),
    ),
    ParameterDef(
        "CT-17", "Order of precedence", "Contract Structure", Criticality.COMMERCIAL,
        (FieldDef("order", "scope", "eq", "Order of precedence"),),
        "{order}",
        "The clause that ranks the contract documents against each other if they conflict: quote the whole "
        "ranking.",
        sweep_terms=(r"order of precedence", r"precedence", r"prevail", r"in (?:the )?(?:event|case) of (?:any )?"
                     r"(?:conflict|inconsistency|discrepancy)"),
    ),
)

CONTRACT_PARAMS_BY_ID: dict[str, ParameterDef] = {p.id: p for p in CONTRACT_PARAMETERS}

# The equipment parameter that holds the same term in the TBE, for the CAS's MR column.
TBE_EQUIVALENT: dict[str, str] = {"CT-01": "PARAM-10", "CT-02": "PARAM-11", "CT-12": "PARAM-06"}

# The terms a finding on which needs the legal approver.
LEGAL_TERMS: frozenset[str] = frozenset({"CT-02", "CT-08", "CT-09", "CT-14"})
