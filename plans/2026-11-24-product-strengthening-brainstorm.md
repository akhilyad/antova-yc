# Antova: what would actually make it stronger

A thinking document, not a roadmap. Written 2026-11-24. No code changed, nothing committed or pushed.
Priorities, target customer, team, budget and timeline are deliberately left open; section 6 asks the
questions that would settle them.

---

## 0. What I'm reasoning from

Antova today, from this repo (README, `antova_backend/antova_ingest/`, the 2026-11-15 handoff brief and
the 2026-11-20 demo plan):

- **One equipment class, on purpose:** 33 kV gas-insulated switchgear. One rate table family
  (`mv_gis_switchgear_v1/v2/ext_v1`).
- **The model only finds quotes.** It returns a page and verbatim words. A quote that isn't on its page
  word for word is dropped, and a recall sweep catches value-shaped sentences the model missed. Every
  number, unit conversion, pass/fail and euro amount is computed in code.
- **Already built, in some form:** document precedence (drawing beats cover letter, contradictions keep
  both pages), gap pricing from a versioned rate table with "unpriced, never zero", exposures (LD caps,
  liability limits) kept apart from true job cost, schedule float against the site need date, a
  5,000-run rate sensitivity, a clarification register built from fixed templates, a review gate with
  a named reviewer per cell where a changed re-ingest makes old approvals stale, evidence hashes,
  TBE workbook and PDF report exports, a shareable buyer brief. Priced at €1,200 per tender.
- **Known weak spots (as of 2026-11-15):** the workbench filled missing values with zeros and
  defaults, replay mode pretended writes succeeded, and a review couldn't be completed from the
  workbench. The consolidation work may have fixed some of this; I haven't re-checked.

One thing stands out before going through the list. Several items in the brainstorm already exist
(audit trail, reporting, bid-level risk). The question for those is "deepen and expose," not "add."

---

## 1. The list, consolidated

Your brainstorm has about 32 phrases. They collapse into 17 capabilities.

| # | Capability | Your phrases | What it would mean for Antova | Exists today? |
|---|---|---|---|---|
| 1 | Document analysis | "automated document analysis" | The core engine | Yes, one equipment class |
| 2 | Bid-level risk assessment | "real-time risk assessment" | Contradictions, exposures, schedule float per bid | Partly |
| 3 | Vendor benchmarking | "vendor performance benchmarking", "streamlines supplier selection" | What each vendor has done across past tenders | No. Each tender stands alone |
| 4 | Contract compliance | "ensures optimal contract compliance", "historical contract management" | Carry the evaluated bid into the contract and check delivery against it | No |
| 5 | Supply chain disruption monitoring | "automated alerts for potential supply chain disruptions" | Watching vendors and markets around and after award | No |
| 6 | ERP / procurement system integration | 4×: "integration with existing procurement ERP platforms", "full API integration … seamless workflow synchronization", "integration capabilities with existing ERP systems", "automated API integration for third-party service synchronization" | Pull tenders in, push evaluations and awards out | REST API only, no connectors |
| 7 | Dashboards and analytics | 5×: "centralized dashboard", "user-friendly dashboard", "intuitive dashboard for real-time analytics", "personalized dashboard for tracking project progress", "advanced analytics … cost efficiency" | A portfolio view across tenders | Per-tender view |
| 8 | Alerts and notifications | 4×: "customizable alerts for key stakeholders", "personalized notifications for upcoming deadlines", "push notifications for urgent project updates", plus #5 | Telling the right person something needs them | No |
| 9 | Reporting and export | 3×: "reporting", "automated reporting tools for executive decision-making", "export … in PDF format" | Award memo, TBE, exec summary | Yes: PDF report, TBE workbook, clarification register |
| 10 | Approval workflow | "secure multi-level approval workflows for all budget requests" | Sign-off chain | Single-level review gate |
| 11 | Audit trail | "real-time audit logs for improved transparency" | Who decided what, on which evidence | Yes, per cell |
| 12 | Access control | "role-based access control system" | Roles, permissions | Token principals; roles thin |
| 13 | Storage, backup, scale | 5×: "secure cloud-based data storage", "secure cloud-based data backups", "secure cloud storage …", "scalable cloud architecture for seamless growth" | Infrastructure | Basic (R2 storage on the Cloudflare deploy) |
| 14 | Real-time collaboration | "integrated real-time collaboration tools" | Team working on one tender | No |
| 15 | Mobile app | "cross-platform mobile app for iOS and Android" | Second client | No |
| 16 | Offline access | "offline accessibility … low-connectivity environments" | Local copies, sync | No |
| 17 | Dark mode | 2× | Theme | No |

What the consolidation shows: most of the list is about what happens *around* an evaluation (who sees
it, where it goes, how it's stored, how people get pinged). Very little is about what happens *inside*
it. The things a renewable buyer would weigh when deciding whether to trust Antova over their own
spreadsheet are mostly absent. That's section 3.

---

## 2. Differentiators, table stakes, and things I'd park

### A. Strengthens the core job (differentiators)

- **Document analysis depth (#1).** More equipment classes and a thicker commercial evaluation. This is
  where the product wins or loses. Detail in section 3.
- **Vendor benchmarking (#3), in a specific form.** Not star ratings or generic scorecards. Deviation
  history: "VoltSwitch excluded witnessed FAT in 3 of its last 4 bids, and its cover letter disagreed
  with its drawings twice." No buyer can get that today, and it falls out of data Antova already
  structures. This is the closest thing on the list to a moat. It needs a tender history store first.
- **Bid-level risk (#2).** You've already built the right version of this (exposures, float,
  contradictions). I'd drop the word "real-time." A tender evaluation runs over weeks; what a buyer
  needs is complete and traceable, not live.
- **Contract compliance (#4), narrow version only.** The agreed-deviations list from the evaluation
  becomes a contract exhibit and the checklist for post-award document review and FAT witnessing.
  Antova already holds that baseline, so this is cheap. Full contract lifecycle management is a
  different category with established players; stay out of it.
- **Multi-level approval (#10), for evaluations, not budgets.** Engineer → package manager →
  commercial lead → delegation-of-authority signatory, each with a recorded decision. Budget request
  approval belongs in the buyer's ERP / purchase-to-pay system; building it would be a detour.

### B. Table stakes (expected, won't win a deal, can block one)

RBAC (#12), audit trail (#11), storage/backup/retention (#13), PDF/Excel export (#9), basic
integration (#6), email or Teams notifications (#8). And one that's missing from your list: **SSO**.

These live on the security questionnaire and the IT onboarding checklist. A utility, TSO or large EPC
won't get you through vendor onboarding without SSO, evidence toward SOC 2 or ISO 27001, a data
processing agreement and an answer on EU data residency. A mid-size developer might run a pilot with
none of it. So when these become urgent depends almost entirely on who the first paying customer is.

I put ERP integration here rather than in A on purpose. Buyers will ask for it, but the evaluation
doesn't happen in the ERP. Tender documents live in email, SharePoint, Aconex, or a sourcing tool's
event attachments. The ERP gets the purchase order at the end.

### C. Low value for this use case (I'd park)

- **Mobile app (#15).** Bid evaluation means two or three 300-page PDFs side by side. Nobody does a TBE
  on a phone. The one mobile moment is "something needs my approval," and a responsive approval page
  plus an email or Teams link covers it without a second codebase.
- **Offline access (#16).** Tendering happens in offices. Offline copies of confidential bids on
  laptops cut against tender confidentiality, and two offline approvals that need reconciling would
  undermine the review gate. Offline matters on site, for delivery inspection and QA, which is a
  phase you aren't in yet.
- **Real-time co-editing (#14).** What an evaluation team actually needs is: assign a flag to the
  protection engineer, comment on it, and see clearly who decided. Live cursors don't help. Concurrent
  sign-off is a correctness problem you'd have to solve anyway.
- **Dark mode (#17).** Cheap and harmless, zero buyer impact. One caution: the design system uses colour
  only when something is wrong, so a dark theme doubles the colour QA on exactly the signals that
  matter.
- **Supply chain disruption monitoring (#5), as a feed.** The problem is real in renewables
  (transformer lead times, module detentions at customs, tariff changes) but it's a separate product
  with separate data, and companies already sell it. The version that fits Antova is narrow: at bid
  time, read each bid's origin and traceability declarations and check them against the buyer's
  policy, and compare stated lead time to the site need date (already built as schedule float).

### Candid ranking

| Capability | Value for renewable bid evaluation | Build cost | Depends on |
|---|---|---|---|
| Document analysis depth (more classes, commercial terms) | High | High, per class | Ontology, rate table, labelled test set per class |
| Clarification and bid-revision loop *(gap, see 3.6)* | High | Medium | Stable data contract |
| Lifecycle cost / TCO *(gap, see 3.2)* | High | Medium | Per-class formulas, guarantee extraction |
| Vendor deviation history | High, grows with data | Medium | Tender history store, vendor entity resolution |
| Evaluation sign-off chain | Medium–High | Low–Medium | Org model, roles |
| Agreed-deviations exhibit | Medium | Low | Already have the data |
| SSO, RBAC, retention, residency | Gate, not value | Medium | Org model |
| Portfolio dashboard | Medium | Low–Medium, then endless | History store |
| ERP / sourcing connectors | Medium, buyer-specific | High, per system | Stable data contract, org model |
| Notifications (email/Teams) | Medium | Low | Event model, deadlines |
| Supply chain monitoring feed | Low for this product | High, ongoing | External data |
| Mobile, offline, co-editing, dark mode | Low | Medium–High (low for dark mode) | — |

---

## 3. Gaps: what a renewable buyer would notice is missing

Ordered by how much I think each one matters.

### 3.1 Equipment coverage

Today it's one class. A solar, wind or BESS plant's procurement packages include MV switchgear, main
power transformers, inverter / MV skid stations, PV modules, trackers, BESS (containers, PCS, EMS),
HV and MV cable, SCADA and power plant controllers, and the HV substation. The scaling constraint is
not UI. It's the ontology, the rate table and a labelled test set for each class.

A way to choose the next one: spend × how often bids deviate × how much of the existing rule engine
carries over. Power transformers look like the natural second class to me. Same buyer, same
electrical engineer, a clear standard (IEC 60076), EU Ecodesign minimum-efficiency rules to check, and
the biggest hidden-cost lever of any item on the list (losses, below).

### 3.2 Lifetime cost, not only price

Renewable buyers often evaluate total cost of ownership, not purchase price:

- transformer no-load and load losses, capitalized at a €/kW rate the buyer sets
- inverter efficiency and availability guarantees
- PV module degradation and power warranty terms
- BESS capacity retention, round-trip efficiency, augmentation obligations
- LTSA / O&M pricing, spare parts

"True job cost" becoming "true lifetime cost" is the most natural extension of your headline, and it's
the most Antova-shaped gap on this page: a deterministic formula over numbers quoted from the bid,
each with its page.

### 3.3 The commercial half

The technical evaluation is deeper than the commercial one. You have LD caps, liability limits,
Incoterms and warranty start point. Likely missing, and standard in a commercial bid evaluation:
multi-currency with a stated FX date, price validity, escalation and indexation formulas (copper and
steel indices are common on transformers and cable), payment milestones and their cash-flow cost,
bonds and parent company guarantees. Each is again "find the clause, compute in code."

### 3.4 Vendor qualification, before the bid

Many deviations are really qualification failures. Things buyers check: type-test certificates (from
an accredited lab, and covering the ratings actually offered), grid code compliance certificates for
the country (in Germany, for example, unit certificates under VDE-AR-N 4110/4120), whether lenders
accept the manufacturer (bankability lists for modules and BESS), factory audit results, reference
installations, financial standing. A qualified-vendor record per supplier is also what makes vendor
benchmarking possible.

### 3.5 Policy and regulatory checks inside bids

Origin and traceability rules increasingly decide whether a bid is acceptable at all: US forced-labour
import rules for modules and polysilicon, US tax-credit domestic-content and foreign-entity rules
(changed in 2025, check the current state), the EU Forced Labour Regulation (applies from December
2027), EU Net-Zero Industry Act non-price criteria in renewable auctions (phasing in), and local
content regimes such as India's approved module list or South Africa's REIPPPP. Which ones matter
depends entirely on geography. All of them are "find the declaration, check it against the rule,"
so they fit the architecture.

### 3.6 The clarification loop and bid revisions

You already generate the clarification questions. The loop around them is where evaluators lose
weeks: send questions, receive answers, re-ingest the revised bid or BAFO, diff it against the last
round, see which deviations closed, and carry the rest forward. Re-ingest already marks old approvals
stale, which is the right foundation. I'd rank this above dashboards for day-to-day value.

### 3.7 Evaluation method and defensibility

Two audiences will ask "show me why":

- **Utilities and public buyers** procuring under EU utilities rules must apply their published award
  criteria and can be challenged by losing bidders.
- **Lenders' independent engineers** review procurement on project-financed plants.

Antova deliberately doesn't score bids. That's a defensible position, but customers using weighted
scoring will ask for it. The choice: stay unscored (findings plus true cost, scoring lives elsewhere),
or support buyer-configured weights computed in code from the findings. Either way, an evaluation
report written for the file, one an IE or a challenge review would accept, is a product in itself.

### 3.8 Who owns the rate table

Every euro on the screen rests on the rate table. Who maintains it, per customer, region and year? Can
a buyer substitute their own rates? Is each rate's source shown? Can rates be calibrated against what
change orders actually cost after award? If a buyer doesn't trust the rates, they'll discard the
headline number, and the headline number is the pitch.

### 3.9 Messy documents

Scanned bids (common from some manufacturers), drawings (single-line diagrams, GA drawings), Excel
price schedules, bids in German, Spanish or Chinese, and IEEE/UL standards instead of IEC for US
projects. Today scanned pages go to manual review. That's honest, but it's also where a lot of the
real volume will land.

### 3.10 Confidentiality between bidders and between customers

Bids are confidential. Losing bidders' prices must never reach anyone who shouldn't see them, inside or
outside the buyer. If vendor benchmarking ever uses data from more than one customer, that's a
contract and ethics question before it's a technical one.

### 3.11 Vendor identity

"VoltSwitch GmbH" and "VoltSwitch Schaltanlagen AG" need to resolve to one vendor. Small, but
benchmarking, qualification and dashboards all depend on it, and it's much cheaper to do from the
first tender than to clean up later.

---

## 4. Dependencies, AI risk, and where scope balloons

### 4.1 What depends on what

```
One clean data contract (no invented zeros, no silent fake writes)
 └─▶ everything below

Org and user model ─▶ RBAC + SSO ─▶ evaluation sign-off chain ─▶ audit trail across roles ─▶ integrations
                                                                   (an integration has to act as someone)

Tender history store + vendor identity ─▶ vendor deviation history ─▶ portfolio dashboard ─▶ rate calibration

Event model (deadlines, status changes, flag assignments) ─▶ notifications and alerts

Agreed-deviations baseline ─▶ contract exhibit ─▶ post-award document check ─▶ rate calibration

Per-class ontology + rate table + labelled test set ─▶ each new equipment class, TCO, commercial terms
```

The first line matters most. A dashboard or an ERP push built on a layer that turns "unknown" into 0
spreads the one error the product promises never to make.

### 4.2 Risks specific to the AI part

- **Keep the core rule.** "The model only finds quotes" is your strongest asset. Several brainstorm
  phrases ("real-time risk assessment", "actionable insights", "advanced analytics") invite a model
  that judges or summarizes. The moment a model writes "VoltSwitch is high-risk," you lose the property
  that every claim traces to a page. A rule I'd propose: every new feature must be expressible as code
  over cited facts. Insights are queries over structured findings, not generated prose.
- **Recall is the failure that counts.** A missed deviation becomes a change order and costs the
  buyer's trust for good. A false flag costs a reviewer two minutes. That means measured recall per
  equipment class on a labelled set (you have `answer_key.py`, `eval.py`, `locator_eval.py` to build
  on), reported per release, and a coverage statement on every report: what Antova checked and what it
  didn't. An honest coverage statement will do more for trust than a claimed accuracy figure.
- **Reproducibility.** A challenge or an IE review asks for the exact documents, model, rate table and
  decisions behind an award. You have run fingerprints and versioned rates. New features (dashboards
  that recompute, integrations that edit) must not bypass that ledger.
- **Model updates.** A provider updating the locating model changes behaviour silently. A regression
  gate on the labelled set before switching versions.
- **The number travels.** "€479,650+, lower bound, one gap unpriced" has to survive every export, ERP
  field and dashboard tile. An ERP field holding `479650` with no qualifier breaks the rule downstream,
  where you can't see it.

### 4.3 Where scope balloons, and the narrow version

| Item | Why it balloons | Narrow version |
|---|---|---|
| ERP integration | Every ERP × every customer's configuration is a project; master data mapping never ends | Export in the customer's own TBE template, one inbound path (a mailbox, a SharePoint folder or sourcing-event attachments), a documented API with webhooks. Build a real connector when a paying customer's IT co-owns it |
| Supply chain monitoring | External data, entity matching at scale, continuous operation | Bid-time origin and traceability checks; lead time vs need date |
| Contract compliance | Contract lifecycle management is its own category | Agreed-deviations exhibit; later, post-award documents checked against it |
| Dashboards | Every stakeholder wants their own view | One portfolio page that answers: what's open, what's due, where's the money |
| Multi-level approval | "Budget requests" pulls you into purchase-to-pay | Evaluation sign-off chain only |
| Mobile / offline | Second codebase or a sync engine | Responsive approval page, email/Teams links |
| New equipment classes | Each is weeks of ontology, rates and labelled data | One class at a time, with a recall bar it must pass before release |

---

## 5. Three directions to choose between

Not a roadmap, but the brainstorm implicitly mixes three different strategies, and they pull against
each other.

**A. Deeper on each tender.** More equipment classes, lifetime cost, commercial normalization, the
clarification loop. Keeps per-tender pricing and the sharpest demo. Risk: it stays a tool with low
switching cost.

**B. Wider across the equipment's life.** Qualification before the bid; contract exhibit and
post-award checks after it. The evaluation becomes the baseline that follows the equipment to site.
Risk: longer sales cycles, and you start meeting contract-management and QA tools.

**C. Up to the portfolio.** Tender history, vendor benchmarking, dashboards, integrations. Antova becomes
the system of record, priced by subscription, with a data advantage that compounds. Risk: benchmarking
means nothing until there are many tenders in it, and enterprise table stakes become mandatory early.

**My lean, pending section 6:** A first, plus two cheap seeds of the others. From C, store every tender
in a queryable history with vendor identity resolved, from now on, so benchmarking has data when you
need it. From B, the agreed-deviations exhibit, since the data already exists. Reason: dashboards and
benchmarking are only as good as the number of evaluated tenders, and depth is what earns tenders.
This flips if the first customer is a utility with a sourcing platform and a security team; then the
table stakes in 2B move to the front.

---

## 6. Questions that would sharpen this

Most important first. Each says what the answer changes.

1. **Who is the first paying buyer: a developer/IPP, an EPC, a utility or TSO, or someone else?**
   Changes when SSO and security certification become urgent, whether ERP integration matters, which
   procurement rules apply, and whether lender defensibility is a selling point.
2. **Which items on your list came from a real buyer conversation, and what did they say?**
   Separates pull from an enterprise checklist. One buyer quote outweighs this whole document.
3. **Which equipment class comes next, and which geography: EU (IEC) or US (IEEE/UL)?**
   Decides the ontology work, the regulatory checks in 3.5 and the rate tables.
4. **Is Antova a per-tender tool or the buyer's system of record?**
   €1,200 per tender points to a tool. Dashboards, history and benchmarking point to a subscription
   platform. The answer moves direction C up or down.
5. **Would you (and your customers) allow vendor data to be pooled across customers?**
   Decides whether benchmarking is "your own history" or "the market," and what the contracts must say.
6. **How do your target buyers decide today: lowest compliant evaluated price, or weighted scoring?
   And how many people sign?**
   Decides the scoring question in 3.7 and the shape of the sign-off chain.
7. **Where do the tender documents physically live at your target buyer?**
   Decides the one inbound integration worth building first.
8. **Are the projects usually project-financed, with an independent engineer reviewing procurement?**
   If yes, an evaluation report built for the IE could be a wedge on its own.
9. **What horizon are you planning for: the next pilot, or the next 12 months?**
   Changes how much of section 2B belongs in the near term.

---

### Answers so far

- **Q1, target buyer (2026-11-24):** developers/IPPs, EPCs and utilities, across the energy industry.
  Recorded as the long-term market. Still open: which of the three is the *first* paying customer,
  since the three pull the near-term build in different directions:

  | Segment | What it pulls toward | Sales cycle |
  |---|---|---|
  | Developer / IPP | Lender and IE defensibility, TCO, fast pilots with no security gate, small teams | Short |
  | EPC | Volume (many tenders, often under their own bid deadline), speed, the clarification loop, evaluating for a client | Medium |
  | Utility / TSO | Published award criteria and challenge-proof records, SSO and certification, sourcing-platform integration, framework agreements | Long |

  Serving all three at once means building all their table stakes at once. My suggestion: one
  segment for the first paying customers, chosen by where there's already a warm conversation.

- **Warm conversation (2026-11-24): an EPC.** EPCs are the first paying segment. Developers/IPPs and
  utilities stay in the market story.

### What "EPC first" changes

An EPC buys equipment to deliver someone else's plant, usually on a lump-sum turnkey contract. A
deviation it misses comes straight out of its own margin, because the owner won't pay for a change
the EPC accepted. That sharpens the pitch: "the cost of this deviation to your margin."

It also brings in things the general list above doesn't cover:

1. **Back-to-back terms (new gap, high value).** The EPC's prime contract with the owner sets LDs,
   warranty periods, performance guarantees and liability caps. Each vendor's offer must carry those
   down. A vendor capping LDs at 5% under a prime contract with 10% is a gap the EPC carries itself.
   `exposure.py` already computes LD and liability exposure against required terms; the change is to
   read the required terms from the prime contract instead of only the MR.
2. **Two evaluation stages.** At proposal stage the EPC prices its own bid to the owner from
   budgetary quotes, under its own deadline. At execution stage it runs the formal TBE/CBE on firm
   offers. They need different things (speed and rough completeness vs a signed, defensible record).
   Which one the EPC wants Antova for is the open question below.
3. **The owner reviews the EPC's work.** Vendors usually come from the owner's approved vendor list,
   and the owner often approves the TBE and every deviation the EPC wants to accept. That means a
   deviation-request register and a TBE in the owner's or EPC's template, sent through document
   control (Aconex, SharePoint or similar). Integration priority shifts from ERP to document control.
4. **Split responsibility.** Engineering signs the technical evaluation, procurement the commercial
   one, and a project or package manager recommends the award. The sign-off chain in 2A becomes
   near-term.
5. **Volume across projects.** An EPC runs the same packages on many projects, so the tender history
   and vendor deviation history (direction C's cheap seed) fill quickly. A per-project view of all
   packages is more useful than a portfolio dashboard.
6. **Equipment scope.** A solar or BESS EPC buys the whole balance of plant. Coverage beyond MV
   switchgear matters sooner than it would for a developer buying one package at a time.

- **Stage (2026-11-24): execution.** The EPC wants Antova for the formal TBE/CBE on firm offers, after
  it has won its own contract with the owner.

### What "execution stage" changes

This fits the current product: firm documents, a named reviewer per decision, a TBE export. What it
adds is the shape of the workflow around one evaluation, from issuing the enquiry to placing the PO:

```
Owner contract + MR ─▶ enquiry to approved vendors ─▶ offers in ─▶ TBE/CBE (Antova today)
   ─▶ clarifications ─▶ revised offers / BAFO ─▶ re-evaluate ─▶ deviation requests to owner
   ─▶ owner approval ─▶ engineering + procurement sign-off ─▶ award recommendation ─▶ PO
   ─▶ agreed deviations become a PO exhibit ─▶ vendor documents and FAT checked against it
```

Candidate near-term set for this buyer, still to be ranked against what the EPC actually asked for:

| Candidate | Why it matters at execution stage | Builds on |
|---|---|---|
| Clarification rounds and revised offers | Several rounds per package are normal; tracking which deviations closed is weeks of work today | `clarify.py`, stale-approval on re-ingest |
| Back-to-back check against the owner contract | The EPC's margin exposure, offer by offer | `exposure.py` |
| Deviation-request register for the owner | The owner approves deviations before award | Findings register |
| Engineering / procurement / package-manager sign-off | Matches how EPCs split responsibility | Review gate |
| TBE in the EPC's own template | The output goes to the owner through document control | TBE workbook export |
| Agreed-deviations PO exhibit | The evaluation carries into the order, then into vendor document review and FAT | Findings register |
| Project view of all packages | An EPC runs a dozen packages per plant at once | Tender store |
| Next equipment class | The EPC buys the whole balance of plant | Ontology, rates, labelled set |

The last row is the biggest single piece of work, and which class comes next depends on what this EPC
builds.

What moves down for now: utility-style public procurement rules, deep ERP connectors, lender/IE
reports (they matter to the EPC's client, not first to the EPC). Table stakes depend on the EPC's
size: a global EPC will run a security review before a pilot, a regional solar EPC may not.

- **Plants and packages (2026-11-24):** solar PV and BESS; mainly MV switchgear and transformers.

---

## 6b. What four real procurement documents show

Reviewed locally on 2026-11-24 as structure only: a contract approval sheet (CAS) for an inverter and
transformer station supply-and-service contract, a supplier NDA, a framework-agreement conditions
matrix across inverter, station and SCADA suppliers, and a commodity price history workbook. All
drafted and audited by hand. **No names, prices or terms from them are copied here, and none should
go into this repo, the demo data, the rate tables or the pitch** (see 6c).

### What they reveal

1. **MV switchgear and transformers are often not bought on their own.** On solar and BESS plants
   they usually arrive inside an inverter / transformer station (an MV skid) from the inverter OEM.
   The BoQs list switchgear upgrades (a higher kA rating) and FAT witnessing as separately priced
   option lines. So the unit Antova must evaluate is the *station*: transformer (IEC 60076) +
   MV switchgear (IEC 62271) + inverter + auxiliaries, with deviations inside it. Today's ontology
   covers the switchgear part.
2. **Execution-stage buying is often a call-off under a framework agreement**, not an open tender.
   Each supplier has a framework agreement with its own warranty period and start point, payment
   milestones, termination fees, bonds, NTP deadline, LD rate and cap, passing of title and design
   life. A project purchase order then refers back to it. The check that matters is "does this
   project PO and offer conform to the framework, and to our contract with the owner", alongside
   "does it meet the spec".
3. **The framework matrix is maintained by hand**, with clauses pasted and truncated into cells and
   links out to the document store. Extracting it automatically, with each cell citing its clause,
   is the same quote-locating job Antova already does, applied to contracts.
4. **LD terms differ a lot between suppliers.** A rate per day and a cap are two numbers that should
   be normalized (for example: days of delay until the cap is reached, money covered against the
   owner contract's LDs). That's the back-to-back check from the EPC section, now with real inputs.
5. **The decision artifact is the CAS.** It holds: contract purpose, which buyer entity signs (parent
   or project SPV), law and jurisdiction, price, currency and whether it's fixed, lump sum or variable,
   the frozen budget from the investment decision, Incoterms, delivery plan, payment terms
   (conditional on bonds), bonds, supplier credit insurance, LDs, warranty coverage, start and
   length, warranty bond, a free-text risk box, and sign-off by PM, procurement, engineering and
   finance, with business director, legal and grid added only under certain conditions.
   - The engineering sign-off line *presupposes the spec checklist was applied.* That checklist is
     Antova's TBE. Today the link between them is a person's word.
   - In the sample, the frozen-budget field and the risk box were left empty, and legal wasn't
     involved. A pre-filled CAS would put the budget variance and the findings in front of the
     signatories, and could suggest when legal or the business director should be added.
6. **Buyers already benchmark prices**, normalized per Wp and per kVA, by project size, DC/AC ratio,
   country, manufacturer, quarter and FX rate, in a spreadsheet updated by hand. That history is the
   best possible source for Antova's rate tables (it answers 3.8: the customer's own option-line
   prices) and for a "this offer is out of line with your last N quarters" check.
7. **Post-award tracking exists too**: COD, PAC, FAC and warranty expiry per project. Warranty start
   point and length from the contract feed directly into it. That's the narrow contract-compliance
   idea from 2A, with a clear first use.
8. **NDAs constrain what Antova may keep.** A typical supplier NDA allows use only for the stated
   purpose and requires return or erasure on request. Antova stores vendor bids, so it needs
   per-counterparty deletion, a retention policy and a record of what was erased. That's a table
   stake this buyer will check.

### 6c. A caution on these documents

They're from a former employer, and at least one is an NDA that limits use of the information to
the purpose stated in it. This isn't legal advice, but the safe line is: learn the *structure* (which
fields, which checks, which approvals), and keep the content (counterparties, prices, terms, people)
out of the product, the samples and anything published. If any of it is needed as real test data,
that needs the owner's written permission. Worth a short check with a lawyer before a pilot.

---

## 6d. Approaches for the next build

All three assume EPC, execution stage, solar and BESS, MV switchgear and transformers.

**A. From evaluation to approval.** Extend Antova past the TBE to the CAS. Read the framework
agreement, the project PO / offer and the owner-contract terms; check the offer against all three
(back-to-back LDs, warranty start and length, bonds, payment terms, title, NTP deadline); compare
price to the frozen budget; produce a pre-filled CAS where every field cites its clause, the risk box
lists the open findings, and the approver list follows the conditions (legal and business director
added when the findings call for them). Equipment scope stays as it is today.
*Trade-off:* mostly contract and commercial work on the existing engine, and it applies to every
package an EPC buys, not only switchgear. It doesn't widen the technical coverage.

**B. The transformer station as the unit.** Add transformer and station ontologies (IEC 60076, the
station's switchgear, auxiliaries, losses, Ecodesign limits, option lines such as kA upgrades and FAT
witnessing) so Antova evaluates the actual package an EPC buys for solar and BESS.
*Trade-off:* deepest technical value and closest to the current demo story, but it's the largest
piece of work (ontology, rate table and labelled documents) and leaves the commercial side and the
approval as they are.

**C. Price memory first.** Import past offers and BoQs into a price history normalized per Wp and per
kVA, use it for reasonableness checks, and calibrate rate tables from the customer's own option-line
prices.
*Trade-off:* cheap to start and builds the data seed for benchmarking, but on its own it's a
spreadsheet replacement, and it depends on the customer handing over history.

**My pick: A, with C's import as a small side task.** Every purchase this EPC makes ends in a CAS, so A
saves time on every contract from the first pilot, and the CAS is where all the separate findings
finally reach the people who sign. B is the right next step once A is in use, and C's history makes
B's rate tables credible.

## 7. What happens next

Nothing gets built from this document. Once you've answered what you can from section 6 and picked a
direction (or argued with mine), I'll write a design doc for that direction, then an implementation
plan, and ask for your approval before writing any code or pushing anything.
