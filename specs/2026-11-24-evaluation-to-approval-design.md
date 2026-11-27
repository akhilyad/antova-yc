# Evaluation to approval: design

Status: complete, awaiting founder approval, 2026-11-24. Not committed. No code written.
Founder decisions so far: approach A; Python pipeline; contract text only to a zero-retention provider.
Section 9 lists the remaining calls I made, for checking.
Background and the reasoning behind this choice: `plans/2026-11-24-product-strengthening-brainstorm.md`
(sections 6, 6b, 6d).

---

## 1. Intent

Antova today ends at the technical bid evaluation. At an EPC, the purchase doesn't end there. It ends
when four or five people sign a contract approval sheet (CAS) that summarizes price, payment, bonds,
LDs, warranty, law and risk. That sheet is filled by hand. Its engineering line assumes "the spec
checklist was applied" without linking to it. Its risk box and budget field are often left empty. And
nobody checks, clause by clause, that the vendor's terms match the framework agreement with that
vendor or carry down the EPC's own obligations to the owner.

This feature carries Antova from the evaluation to the signature: it checks each offer's commercial
terms against the framework agreement and the owner contract, compares price to budget, and produces
a pre-filled CAS in which every field cites the clause it came from and every open finding is in the
risk section.

**Who it's for:** an EPC (or a developer with an in-house EPC) buying solar PV and BESS equipment at
execution stage: firm offers, often call-offs under a framework agreement. The people involved are the
project manager who owns the CAS, procurement who negotiated it, the engineer who signs the technical
line, and finance who checks price against budget.

**Why now:** the first warm customer is exactly this buyer. The CAS step applies to every package they
buy, so it is useful from the first pilot regardless of equipment class.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Read commercial terms from three kinds of document: the vendor's offer or project PO draft, the
   framework agreement (FA) with that vendor, and the owner-contract terms the EPC must pass down.
2. Check the offer against the FA and against the owner-contract terms, term by term, with citations
   on both sides.
3. Compare the offer price with the frozen budget, in the budget's currency.
4. Produce a CAS: a page in the workbench and an export (DOCX and PDF), every field cited, the risk
   section built from open findings, and a suggested list of approvers based on stated rules.
5. Work for a single offer (a call-off with no competition) as well as for several bids.

**Non-goals (this round)**

- New equipment classes. Technical evaluation stays MV GIS switchgear; the commercial checks here are
  equipment-agnostic, so they apply to a transformer-station offer even before the station ontology
  exists.
- Running signatures. The CAS is exported to the buyer's existing e-signature flow. Antova does not
  replace it.
- ERP or document-control connectors.
- Full contract lifecycle management, redlining, or negotiation.
- Price benchmarking against history (approach C) beyond a stub import, see 7.

**Anti-goals (would make this a failure even if it works)**

- A model writes any part of the CAS, the risk text or a verdict. Every field is either a verbatim
  quote with its page or a value computed in code from quoted values.
- An unknown term shows up as zero, blank-but-looking-fine, or "compliant". Missing is shown as
  missing, with what is needed to resolve it.
- The CAS can be exported while findings are unreviewed, without saying so on its face.
- Any real customer document, counterparty name, price or term lands in the repo, the samples or the
  demo. All test material is synthetic (see 6.4).

## 3. Constraints

- **Architecture stays as is.** The locator returns page and verbatim words; `verify.py` drops quotes
  not on the page; values are parsed and compared in code; amounts are Decimal; unknown is `None` with
  a reason (`exposure.py` convention).
- **Review rules carry over, with a second gate.** Contract cells use the same per-cell review and
  stale-on-re-ingest rules (`antova_review.py`), but the CAS has its own gate per tender and vendor,
  separate from the tender's TBE status (see 9.1). The CAS export is refused until both the TBE and that
  vendor's contract gate are verified, unless exported as a draft that is watermarked as such.
- **Confidentiality.** Contracts and FAs are more sensitive than bids. Per-counterparty deletion and a
  stated retention period are required before a pilot (see 4.7).
- **Units and currencies.** Percentages, days vs weeks, months, and currencies are normalized in code
  with the conversion shown. FX uses a buyer-entered rate with a date; Antova never fetches or assumes
  a rate.

## 4. The design

### 4.1 Inputs

Two new document roles beside `mr_spec` and `vendor_bid` in `DocumentEntry.role`:

| Role | What it is | Per |
|---|---|---|
| `framework_agreement` | The FA with one vendor, plus its amendments | Vendor |
| `owner_contract` | The EPC's contract with the owner, or the excerpt with the pass-down clauses | Tender / project |

The vendor's offer (or the draft project PO the vendor has accepted) stays `vendor_bid`.

Amendments: an FA can have amendments that override clauses. Each amendment is its own document entry
with an `amends` reference and a date; the later document governs for the clauses it covers, and both
citations are kept (the same pattern `precedence.py` uses for drawing vs cover letter).

Owner-contract terms can also be **buyer-entered** instead of read from a PDF, using the existing
`buyer_entered` source pattern from `ProjectBaseline` (shown as "entered by <reviewer>", reviewed like
any other value). Many EPCs won't share the full owner contract; the handful of pass-down numbers is
enough.

Buyer-entered per tender (extends `ProjectBaseline`): frozen budget (amount and currency), FX rate and
date if currencies differ, the contract value basis for percentages, and the delay scenario in weeks
for LD comparisons (already an input to `ld_recovery_shortfall`).

### 4.2 Contract-terms ontology

A second ontology, independent of equipment class, alongside `ontology.PARAMETERS`. Same structure:
`ParameterDef` with fields, operators, plausible ranges and sweep terms. Proposed parameters:

| ID | Term | Fields (kind) | Better is |
|---|---|---|---|
| CT-01 | Delay LDs | rate (percent), rate unit (per day / per week), cap (percent), triggers (scope: delivery, cold commissioning, hot commissioning) | higher rate, higher cap, more triggers |
| CT-02 | Limitation of liability | cap (percent or money_cap), exclusions (scope) | higher cap |
| CT-03 | Warranty | period (months), start event (delivery / commissioning / PAC), on-site vs factory, coverage (all components or listed) | longer, later start, on-site, all |
| CT-04 | Warranty bond | percent, duration | higher, longer |
| CT-05 | Performance bond | percent, release event | higher |
| CT-06 | Advance payment bond | percent | higher, or none needed |
| CT-07 | Payment milestones | list of (percent, event, days after event, condition) | later payment, more conditional |
| CT-08 | Termination for convenience fees | schedule of (percent, days before delivery) | lower |
| CT-09 | Passing of title | event | earlier |
| CT-10 | NTP deadline | months or days from PO | later |
| CT-11 | Design life / minimum operating period | years | longer |
| CT-12 | Incoterms and named place | incoterm, place | reuses PARAM-06 ordinals |
| CT-13 | Price basis | currency, fixed after NTP (yes/no), lump sum / partly variable / variable, escalation formula (quoted, not computed in v1) | fixed lump sum |
| CT-14 | Governing law and disputes | law, forum | equal to the required one |
| CT-15 | Supplier credit insurance | insurer, secured value | present when required |

New field kinds needed in `values.py`: `rate_per_period` (a percent per day or per week, normalized to
per day with the conversion shown), `event` (an ordinal list of project events: NTP, delivery,
cold commissioning, hot commissioning, COD, PAC, FAC), `milestone_list`, `law`. Everything else reuses
existing kinds (`percent`, `months`, `money_cap`, `incoterm`, `scope`).

PARAM-06, PARAM-10 and PARAM-11 already cover Incoterms, LDs and liability in the equipment ontology.
CT-12, CT-01 and CT-02 replace them for the contract checks; the equipment ontology keeps them for
the TBE until the two are merged (left to the builder, see 8).

### 4.3 Requirements come from three baselines

Today every check compares a bid to the MR. Here each contract term can have up to three required
values, and each comparison is a separate check with its own citation pair:

| Check | Required side | Question it answers |
|---|---|---|
| FA conformance | The FA (as amended) | Has the vendor slipped terms back from what we already agreed? |
| Back-to-back | Owner-contract terms | Does the vendor carry our obligations to the owner, or do we carry the gap? |
| Spec | The MR | As today |

A term can pass one and fail another (the offer matches the FA's LD cap, but what that cap pays back
per week is less than the EPC owes the owner per week). The CAS shows all three. Back-to-back is
compared in money, not percent (see 9.2). A missing baseline produces no check for it and is listed as "not checked: no FA
provided", never silently skipped.

Money: where a gap has a computable exposure, reuse `exposure.py` (LD recovery shortfall, liability
shortfall) with the required side coming from the relevant baseline. New exposure functions, same
rules (pure, Decimal, unknown is `None` with reason):

- `warranty_shortfall`: months between the warranty the vendor gives and the warranty the EPC owes
  the owner, after aligning start events. Unaligned start events with no date to bridge them is
  `None` with reason, never zero (same rule as the existing strict warranty policy).
- `payment_timing`: the weighted days to payment for offered vs FA milestones. Shown as days, not
  money, in v1; turning it into money needs a buyer-entered cost of capital, which is left out.

None of these exposures is added to the true job cost (unchanged rule from `findings.py`).

### 4.4 Budget

`budget_variance = offered price (converted to budget currency) − frozen budget`, shown with the FX
rate and date used. If any priced gaps exist, a second line shows the variance against the true job
cost. Missing budget means the field shows "not entered", and a finding is raised so the CAS can't
quietly omit it.

### 4.5 The CAS

A new page per tender and selected vendor, plus export. Sections mirror a typical CAS and every field
has one of three states: **cited** (quote plus page link), **computed** (formula shown, inputs cited),
**missing** (what is needed).

1. Project and contract data: project, buyer entity, seller entity and parent company, contract
   purpose (supply / supply and service / service / O&M), contract start (signing or NTP) and dates.
2. Law and jurisdiction (CT-14), with FA and owner-contract comparison.
3. Price: amount, currency, fixed or not, price basis (CT-13), budget and variance (4.4), true job
   cost from the TBE.
4. Incoterms and delivery: CT-12, earliest delivery, schedule float from `schedule.py`.
5. Payment and bonds: CT-07, CT-04 to CT-06, CT-15.
6. LDs: CT-01, with the LD recovery shortfall exposure for the stated delay scenario.
7. Warranty: CT-03, CT-04, with the warranty shortfall.
8. Risks: generated from the findings register, not written. One line per open or accepted finding:
   what, which baseline, money or days, citation, reviewer decision. Grouped by technical, FA
   conformance, back-to-back, budget.
9. Technical evaluation: the TBE status, link to the full evaluation, counts of deviations and open
   items. This is the link the engineering signatory currently has to take on trust.
10. Approvers: a suggested list from rules (4.6), with the reason each one is on it. Names are filled
    by the buyer.
11. Evidence appendix (export only): every cited passage, page images for findings.

**Export:** DOCX (so the buyer can paste into their own CAS template) and PDF. Exports carry the run
fingerprint, the rate table and ontology versions, and the review state. A draft export is
watermarked "DRAFT: n findings unreviewed".

Template fit: v1 uses Antova's own layout with the section order above. A per-customer field mapping
to their own DOCX template is a later step (8).

### 4.6 Approver suggestion rules

Rules are data (a small JSON per customer, versioned like rate tables), evaluated in code. Defaults:

| Approver | Always / when |
|---|---|
| Project manager | Always |
| Procurement | When the offer was negotiated by procurement (buyer flag) |
| Engineering | Always; blocked while technical findings are open |
| Finance / project controlling | Always |
| Legal | Any FA-conformance or back-to-back finding on law, liability, termination, title |
| Business director | Signing before NTP, or budget variance above a threshold, or any finding above a money threshold |
| Grid | Any finding tagged to grid-code parameters (none in v1; hook only) |

Antova suggests; it does not route or sign. The buyer can remove a suggested approver, and the removal
is recorded with a reason, like an override.

### 4.7 Confidentiality and retention

- Per-counterparty deletion: delete all documents, extracted text, page images and evidence for one
  vendor across all tenders, with a deletion record (who, when, what, counts) kept without content.
- Retention period per deployment, stated in the pilot agreement (one isolated deployment per pilot
  customer, see 9.5).
- FAs and owner contracts are visible only to that deployment's reviewers; never included in the
  public brief (`/b/{share_id}` strips them).

### 4.8 Workbench surfaces

- Ingest: two new upload slots (FA per vendor, owner-contract terms as upload or form).
- Audit page: a "Contract" tab beside Bids and Rate scenarios: one row per contract term, columns for
  offer / FA / owner contract / MR, each cell cited, status coloured only when something fails
  (existing design rule).
- CAS page: `/audit/{tender}/cas?vendor=…`, the sections in 4.5, with export buttons.

## 5. Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| A | Evaluation to approval (this document) | Equipment-agnostic, useful on every contract; doesn't widen technical coverage | **Chosen** by the founder on 2026-11-24 |
| B | Transformer station ontology | Deepest technical value; largest build; leaves commercial and approval as they are | Next, once A is in use |
| C | Price history first | Cheap, seeds benchmarking; alone it's a spreadsheet replacement | Small side task (7) |

## 6. Evaluation and testing

### 6.1 Correctness rules carried over

Every new check gets the existing test shapes: a passing value, a failing value, a missing value
(`None` with reason), a contradiction between two documents, unit conversion shown.

### 6.2 Recall bar

Before release, the contract locator must find each CT term on a labelled set with recall at least
equal to the current equipment ontology's on its benchmark (measured with `locator_eval.py`). Terms
below the bar ship as "manual: not reliably found" rather than unchecked.

### 6.3 The CAS round trip

A test that ingests a synthetic package (MR, three bids, one FA with one amendment, owner-contract
terms entered by form), verifies it, exports the CAS, and checks every field's state and citation.

### 6.4 Synthetic documents only

New fixtures: a synthetic FA, amendment and owner-contract excerpt written for Antova, with invented
companies, numbers and clause wording, in the style of the existing synthetic tender 402. No real
document, name, price or term from any customer or former employer goes into fixtures.

## 7. Side task: price history import (from approach C)

An import of the buyer's own past offers as CSV (columns: project, country, capacity in MWp / MVA,
DC/AC ratio, equipment, manufacturer, offer date, price, currency, option lines). Stored per tenant.
Used in v1 for one thing only: showing on the CAS "price per Wp / per kVA vs the median of your last
four quarters for this equipment", with the comparison set listed. No rate-table calibration yet.

## 8. Left to the builder

- Whether CT-01/02/12 replace PARAM-10/11/06 in the TBE now or later, and how the two ontologies share
  locator calls.
- Storage layout for FA and owner-contract documents and their amendments in `antova_db.py`.
- DOCX generation library and PDF route (the existing PDF report path is the default).
- Exact JSON shape of approver rules, and where thresholds live.
- How the Contract tab and CAS page share components with the existing audit page, within the
  consolidation plan in `plans/2026-11-20-benchmark-ui-consolidation.md`.
- Order of work, as long as the data contract fix (no invented zeros, no silent fake writes, handoff
  brief items 3 and 4) is confirmed done before any CAS field is rendered from workbench data.

## 9. Decisions after the builder check

A reviewer read this as the engineer who'd build it and raised eight questions. Seven are settled
below as **my calls**, open to correction. One is the founder's (9.9).

### 9.1 A separate CAS gate per tender and vendor (my call)

The tender's TBE status (`TENDER_STATUSES`, VERIFIED) stays as it is and covers the technical
evaluation of all bids. Contract cells and findings get their own status per (tender, vendor):
DRAFT → VERIFIED → withdrawn, with the same lock and stale rules. Uploading an FA, amendment or
owner-contract terms never un-verifies the TBE. Open contract findings on a vendor nobody is
selecting block nothing. Reason: the CAS is about one vendor; a tender-wide gate would make every FA
upload undo technical sign-off.

### 9.2 Back-to-back in money, per week of delay (my call)

Percentages of different contract values can't be compared. Owner-contract terms are entered as:
owner contract value, owner LD rate and cap (in percent of that value or in money), and the share of
owner LDs the EPC assigns to a delay in this package (buyer-entered, no default; missing makes the
comparison `None` with reason). The check compares money per week of delay and money at the cap:
what the EPC would owe the owner for this package's delay vs what the vendor pays back. New exposure
function `back_to_back_ld_gap`; `ld_recovery_shortfall` stays for FA and MR comparisons where both
sides are percentages of the same PO value. Same pattern for liability and bonds.

### 9.3 Silence in the offer: "inherited", only with a citable clause (my call)

A term the offer or call-off PO doesn't state takes the FA's value **only** if a clause incorporating
the FA can be cited (in the PO, the offer, or the FA's own call-off clause). Such cells get a fourth
state, **inherited**, showing both citations (the incorporation clause and the FA term). With no
incorporation clause, the term is missing.

If the offer attaches the vendor's own general terms, the FA's order-of-precedence clause is located
and cited, the precedence order is shown for the reviewer to confirm once per vendor, and any term
where the vendor's terms and the FA differ is a finding regardless of precedence (it's a negotiation
point even if the FA wins). The order is stored as data per vendor, not hard-coded, and resolved in a
new module beside `precedence.py`.

### 9.4 A minimal counterparty and project record (my call)

FAs are stored once per counterparty and referenced by tenders; owner-contract terms are stored once
per project and referenced by its tenders. This needs two small records that don't exist today:

- **Counterparty:** id, legal name, aliases, parent company. Tender vendors link to it by an explicit
  choice at ingest (suggested by name match, confirmed by the reviewer, never auto-linked).
- **Project:** id, name, buyer entity, owner-contract terms.

When an FA or amendment is added or changed, every tender referencing it marks the affected contract
cells stale; review happens per tender (a reviewer confirms the reading in the context of that
purchase). Per-counterparty deletion uses the counterparty id, not a name search. This is also the
vendor identity seed the brainstorm (3.11) asked for.

### 9.5 One isolated deployment per pilot customer (my call)

No multi-tenancy in this round. Each pilot customer gets its own deployment, storage bucket and
reviewer tokens. "Per customer" settings (approver rules, retention, price history) are per
deployment. Real tenant isolation is a later project, once there's more than a handful of customers.

### 9.6 A second decision on findings: disposition (my call)

Reviewing a cell means "this is read correctly". Accepting a commercial gap is a different decision.
Each contract finding gets a **disposition**: accept (with reason), negotiate (goes to the
clarification register), or waiver needed (named approver role). Dispositions are recorded with the
reviewer's identity, like reviews. To make "who may accept" meaningful, reviewer tokens gain a role
(engineering, procurement, project management, finance); accepting a finding requires the role
the approver rules attach to it. The CAS risk section shows the disposition, not the cell review.

### 9.7 Currency and price basis (my call)

- **Offered price** on the CAS is the contract lump sum as stated, with selected option lines listed
  separately and summed into a second total; variable or provisional parts are listed and not summed.
- **One FX rate per tender, per currency pair:** the buyer-entered rate (with date) governs the CAS
  and the budget lines. The rate table's dated rates keep governing the gap adders inside the true job
  cost. Where both appear on one sheet, both are shown with their source and date; Antova never mixes
  them in one sum.
- The second variance line (vs true job cost) is in the budget currency, converting the true job cost
  at the buyer-entered rate, with that conversion shown.

### 9.8 Smaller points settled

Approver rule thresholds and their JSON shape, and DOCX/PDF tooling, stay with the builder (section 8).

### 9.9 For the founder: which engine, and can contracts go to the model provider?

The design above is written against the Python pipeline in `antova-yc` (`verify.py`, `exposure.py`,
`antova_review.py`). The live workbench on Cloudflare Workers runs its own built-in engine, calling a
third-party model API. Two decisions:

1. **Where does this get built?** **Decided 2026-11-24: the Python pipeline.** Consequence: the
   pipeline needs a host before the pilot, and that hosting step belongs in the implementation plan.
   The Workers engine is out of scope for this feature.
2. **May FA and owner-contract text be sent to the external model provider?** Contracts are more
   sensitive than bids, and the deletion and retention promise in 4.7 only holds if the provider
   doesn't keep the text. This may need a zero-retention agreement, a different provider, or the
   pilot customer's written consent. Note: the Python locator (`chat_locator.py`) calls the same
   provider as the Workers engine, through an OpenAI-compatible endpoint set by `EXPLABS_BASE_URL`,
   so switching to another compatible provider is configuration, not a rebuild.

   **Decided 2026-11-24: contract text goes only to a provider with a written zero-retention
   guarantee.** How the code enforces it:
   - A deployment setting declares the configured provider zero-retention, with a reference to the
     agreement (document name and date). It is off by default.
   - Documents with role `framework_agreement` or `owner_contract` are refused by the locator while
     the setting is off. The ingest fails for those documents with that reason, and bids and the MR
     still process. Buyer-entered owner-contract terms (the form) never touch the model and always work.
   - The run fingerprint records the provider, model and the zero-retention reference, so a CAS shows
     which provider read its contracts.
   - Confirming the current provider's terms, or choosing another provider, is a pre-pilot task
     outside the code.
