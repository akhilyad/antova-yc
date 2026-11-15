# Antova copy audit: change plan (2026-11-15)

Status: plan only. No code has been changed. Waiting for approval.

## Scope and sources

| Surface | Where the code lives | Live at | Notes |
| :-- | :-- | :-- | :-- |
| Workbench (Next.js) | `antova-backend-demo` repo, local checkout `D:\antova-yc-ready-main\antova_backend_UI`, path `antova_backend/frontend/src/` | `https://antova-backend-demo-antovabackend.vercel.app/` (every route except `/b/*` and `/review/*`) | Holds both known issues. Short prefix below: **WB/** |
| Brief and review app (Vite) | `antova_backend/web/src/` here (byte-identical copy at `antova-backend-demo/antova_backend/backend/web/src/`) | `/b/*`, `/review/*` | Copy is centralised in `content/copy.ts`. Short prefix: **BR/** |
| Backend strings shown in the UI | `antova_backend/*.py` and `antova_backend/antova_ingest/*.py` here (copied to `antova-backend-demo/antova_backend/backend/`) | The live site is in replay mode: `/api/health` returns `"mode":"replay"` and every response carries `X-Antova-Source: replay`. Strings come from `WB/lib/sample402.json`, which is a snapshot of backend output, plus the mock in `WB/app/api/[[...path]]/route.ts` | Short prefix: **BE/** |

Method: I parsed every `.tsx`/`.ts` file with the TypeScript compiler and every `.py` file with `ast`, pulled out all visible strings (JSX text, `title`, `aria-label`, `placeholder`, toasts, errors, API `detail` and `message` fields), and read the live replay JSON from the deployed API. I traced the click handlers for the override buttons through `lib/api.ts` to the FastAPI routes.

Not changed by this plan: vendor quotes and spec text (evidence must stay verbatim), synthetic bid PDF contents (`BE/samples/`), developer-only CLI and config errors (`verify.py`, `costs.py` rate-table validation, `eval.py`), code comments, and enum codes such as `MANUAL_INSPECTION_REQUIRED` that never render.

## Decisions needed before editing

1. **The "Override Adder" button opens the wrong mode.** On the live audit page it opens `CellReviewPanel`, not `OverrideDrawer`. The panel opens on **Approve** (`CellReviewPanel.tsx:61`), so a user who clicked "Override" lands on "Approve". Option A (recommended): label it `Edit cost adjustment` and open the panel on the edit mode, a one-line state change. Option B: copy only, label it `Review cost adjustment`.
2. **The ledger's pencil icon always opens PARAM-01** (`WB/app/audit/[tenderId]/page.tsx:614`, fallback `paramId || 'PARAM-01'`), whichever bidder row you click. I propose it opens that bidder's line-item panel instead. Behaviour change, flagged here because the new label depends on it.
3. **The overview page hardcodes wrong numbers.** `WB/app/page.tsx:205` says VoltSwitch carries "+€64,800" for "€482,800". The live replay data gives +€61,650 and an evaluated total of at least €479,650 (one gap unpriced). The rewrite below takes the figures from data instead of hardcoding them.
4. **`/demo` route** (`WB/components/ui/dashboard-with-collapsible-sidebar.tsx`, 1,751 lines) is an older second dashboard. It contradicts the main app (it says Nordwerk has "100% specification compliance", that an override was "Priced by founder", and a €482,800 total). Recommended: delete the route. If you keep it, Table E applies.
5. **Backend badge strings** ("Apparent Low Bidder", "Lowest Evaluated Price", "Compliant Baseline") are asserted in 8 test files and `samples/TENDER-2026-417/expected.json`. Renaming them means updating those fixtures in the same change.
6. **Engine names in the UI** (Pint 0.26.2, PyMuPDF, FastAPI, SQLite) mean nothing to a procurement manager. I propose removing them from page copy. The version line stays on the Governance page only.

## Term map applied throughout

| Current term | Replacement | Why |
| :-- | :-- | :-- |
| Adder, price adder, rate adder, add-back, net adders | Cost adjustment (plural: cost adjustments) | Standard leveling term. "Adder" is internal. |
| True Job Cost, true evaluated price, true reconciled cost of ownership | Evaluated total | The term procurement teams use for a leveled price |
| Nominal quote, headline price, quoted bid, base proposal | Base bid (column header: Quoted price) | Plain and standard |
| Apparent low bidder | Lowest base bid | States the fact without hedging |
| Recommended Award, L1 Recommended, Recommended Bidder (L1) | Recommended award, Rank 1 | Sentence case. L1 is internal ranking jargon. |
| Scope omission, unquoted equipment omission, scope gap | Missing scope | Standard term |
| Technical Gaps / Commercial Gaps / Net Omissions | Technical gaps / Commercial gaps / Total cost adjustments | Sentence case. "Net omissions" is wrong because the column holds money, not omissions. |
| Delta vs Quote | Difference vs lowest bid | Says what the number is compared against |
| Seal, sealed, Verify & Seal | Sign off, signed off | Procurement language. "Seal" sounds like crypto. |
| Unlock draft, withdraw | Reopen for edits | Says what happens |
| Ingest, ingestion, staging, commit manifest | Upload, process | User language |
| Clause, parameter (when shown to users) | Requirement | Matches the brief app (`copy.ts`) |
| Proposal extracted verbatim | Bidder's text | Plain |
| Employer | Client | EPC buyers call the owner "client" |
| Deterministic, dimensional precedence, ontology, grounding, recall sweep, locator | Remove, or say what it does ("checked by code", "found on page") | Internal engineering words |
| Leveled, leveling, reconciliation | Adjusted, comparison | Plain |
| Em dash as an empty-cell placeholder | `n/a` or a word such as "Not ranked" | Rule: no em dashes |

## Table A: the two known issues (priority)

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| WB/components/LineItemDrilldown.tsx:172 (button) | Override Adder | Edit cost adjustment | The handler (`onOpenOverride`) opens the review panel for this requirement and bidder. Its edit path calls `PUT /api/review/tenders/{id}/overrides/{param}/{vendor}` with a new euro amount and a required reason, replacing the amount calculated from the rate table. That is editing a line-item cost adjustment, not waiving it (setting €0 is one possible edit). See decision 1. |
| WB/components/LineItemDrilldown.tsx:161 (section label) | Scope Omission Price Adder | Cost adjustment for missing scope | Names the money and the cause in standard terms |
| WB/components/LineItemDrilldown.tsx:164 (fallback) | Formula: Calculated scope omission adder | Formula: calculated from the rate table | Says where the number comes from |
| WB/components/TrueJobCostTable.tsx:263 (`title`) | Override adder amount | Edit cost adjustments | Tooltip for the pencil icon. See decision 2. |
| WB/components/TrueJobCostTable.tsx:265 (`aria-label`) | Override adder for {name} | Edit cost adjustments for {name} | Screen reader label matches the tooltip |
| WB/components/TrueJobCostTable.tsx:285 (banner heading) | Procurement Leveling & Award Analysis | Award recommendation | Sentence case. Says what the box gives you. |
| WB/components/TrueJobCostTable.tsx:288 (banner body) | Nominal apparent low bidder **{low}** ({lowBase}) carries unquoted scope omissions and warranty delivery boundaries, elevating its true evaluated cost to **{lowEval}**. Recommended award is **{best}** at **{bestEval}**, delivering verified specification compliance and the lowest true total cost of ownership. | **{low}** has the lowest base bid at {lowBase}. Missing scope and commercial gaps add {lowAdj} ({lowTech} technical, {lowComm} commercial), for an evaluated total of {lowEval}. **{best}** has the lowest evaluated total at {bestEval}, which is {diff} less. Recommended award: **{best}**. | Removes "nominal", "apparent", "elevating" and the "-ing" clause. States the base bid, the cost added, the evaluated total and the difference. Drops "verified specification compliance": the brief is still a draft, so that claim is unproven. |
| Same banner, when the low bidder has unpriced gaps | (same as above, with "≥" in the figure) | ...for an evaluated total of at least {lowEval}. {n} {gap/gaps} still {has/have} no price, so the real total is higher. **{best}**... | States the commercial risk outright. Rendered with live replay data: "VoltSwitch Industrial has the lowest base bid at €418,000. Missing scope and commercial gaps add €61,650 (€49,000 technical, €12,650 commercial), for an evaluated total of at least €479,650. 1 gap still has no price, so the real total is higher. Nordwerk Schaltanlagen GmbH has the lowest evaluated total at €452,000, which is at least €27,650 less. Recommended award: Nordwerk Schaltanlagen GmbH." |

## Table B: workbench (Next.js), all other strings

### B1. Ledger tab: `WB/components/TrueJobCostTable.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :55 heading | True Job Cost & Scope Omission Ledger | Evaluated totals by bidder | Plain heading in sentence case |
| :58 pill | Deterministic Reconciliation | Calculated from the rate table | Says what produced the numbers |
| :62 subtitle | Nominal supplier quotes leveled against unquoted equipment omissions, transport boundaries, and warranty shortfalls. | Each base bid plus the cost to close its missing scope, delivery terms and warranty shortfalls. | Removes "nominal" and "leveled". Active and concrete. |
| :69 legend | L1 Recommended | Recommended award | L1 is internal |
| :74 legend | Scope Gap Add-Back | Cost adjustment | Term map |
| :81 table `aria-label` | Bid Audit Ledger | Evaluated totals by bidder | Matches the heading |
| :85 column | Vendor Proposal | Bidder | Standard |
| :86 column | Quoted Bid | Quoted price | Term map |
| :87 column | Technical Gaps | Technical gaps | Sentence case |
| :88 column | Commercial Gaps | Commercial gaps | Sentence case |
| :89 column | Net Omissions | Total cost adjustments | The column holds euros, not omissions |
| :91 column | True Job Cost | Evaluated total | Term map |
| :93 column | Delta vs Quote | Difference vs lowest bid | The code subtracts the lowest base bid, not the row's own quote. The current label is misleading. |
| :94 column | Actions | Actions | No change |
| :101 empty state | No evaluated vendor bids available in this tender. | No bids have been evaluated for this tender yet. | Active voice |
| :150 rank placeholder | — | Not ranked | No em dash. Says why the cell is empty. |
| :163 badge | Recommended Award | Recommended award | Sentence case |
| :168 badge | Apparent Low Bidder | Lowest base bid | Term map |
| :173 fallback | Offer PDF | Bid document | Plain |
| :182 cell | Unpriced | No price | Plain |
| :228 note | lower bound · 1 unpriced | at least · {n} unpriced | The "1" is hardcoded. It must use the real count. |
| :233 cell | Incomplete Bid | Bid price missing | Says what is missing |
| :243 cell | Baseline | Lowest bid | Plain |
| :246 placeholder | — | n/a | No em dash |
| :256 `aria-label` | Inspect {name} breakdown | Show line items for {name} | Says what opens |
| :259 button | Inspect | Line items | Says what opens |

### B2. Line-item panel: `WB/components/LineItemDrilldown.tsx` (Table A covers the button)

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :36 heading | Parameter Breakdown: {name} ({country}) | Line items: {name} ({country}) | "Parameter" is internal |
| :39 summary | Base: {x} · Adders: +{y} · True Cost: {z} | Base bid: {x} · Cost adjustments: +{y} · Evaluated total: {z} | Term map |
| :46 `aria-label` | Close parameter breakdown | Close line items | Matches the heading |
| :56 empty state | No specification parameters available for drilldown. | No requirements to show for this bidder. | Plain |
| :115 label | Specification Clause | Your requirement | Matches the brief app ("Your spec") |
| :128 label | Proposal Extracted Verbatim | Bidder's text | Plain |
| :131 label | Confidence: {n}% | Match confidence: {n}% | Says what is confident |
| :136 empty | No explicit quotation identified in proposal | Not found in the bid | Short and factual |
| :149 link | Inspect Evidence | View page | Says what opens |

### B3. Review panel: `WB/components/CellReviewPanel.tsx` (opened by every edit button on the audit page)

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :106 error | A reviewer justification note is required for institutional audit trail. | Add a reason. It is saved with your decision. | Plain. Drops "institutional". |
| :117 error | Please enter a valid numeric adder amount in EUR. | Enter an amount in euros, for example 18500 or 18500.50. | Gives a working example |
| :130 error | Action failed. Please try again. | Couldn't save your decision. Try again. | Says what failed |
| :149 label | Bidder: | Bidder: | No change |
| :155 `aria-label` | Close review panel | Close | Short |
| :168 label | Compliance Status | Result | Matches the brief app |
| :180 label | Cost to Close | Cost to close | Sentence case |
| :192 label | Employer Requirement Clause | Your requirement | Term map |
| :195 empty | No specification text recorded. | No requirement text found in the spec. | Plain |
| :203 label | Extracted Proposal Verbatim | Bidder's text | Plain |
| :212 empty | "No explicit text identified in proposal" (inside curly quotes) | Not found in the bid | Drops the curly quotes around a non-quote |
| :212, :276 quote marks | `&ldquo;` `&rdquo;` | `"` | Rule: straight quotes |
| :228 button | Inspect Crop | View page | "Crop" is internal |
| :239 badge | Contradiction Detected | Documents disagree | Plain. Says what happened. |
| :248 heading | Audit Decision Record | Decision history | Plain |
| :253 loading | Loading decision history... | Loading decision history... | No change |
| :257 empty | No previous human review decisions recorded for this cell. | No decisions yet. | Short |
| :287 notice | Tender is verified and sealed. Review decisions and overrides are locked. | This tender is signed off. Reopen it to change decisions or amounts. | Says how to proceed |
| :293 heading | Record Review Decision | Your decision | Short |
| :306 option | Approve | Accept | Plain |
| :319 option | Override | Edit amount | Says what the option does |
| :332 option | Reject | Reject | No change |
| :341 label | Override True Cost Adder (EUR) | New cost adjustment (€) | Term map |
| :357 label | Original rate table adder: | Calculated from the rate table: | Term map |
| :365 label | Audit Justification Note (Required) | Reason (required) | Plain |
| :373 placeholder | Confirm specification compliance or approved variance rationale... | Example: Datasheet p.4 confirms 50 kA for 3 s. | A concrete example teaches more than a description |
| :375 placeholder | State pricing basis, commercial agreement, or rate revision source... | Example: Vendor quoted €6,150 freight in clarification of 12 Nov. | Concrete |
| :376 placeholder | State technical or commercial deficiency leading to rejection... | Example: Quote is from a different product range. | Concrete |
| :396 button | Cancel | Cancel | No change |
| :406 button | Approve Cell | Accept | "Cell" is internal |
| :408 button | Save Override | Save new amount | Says what is saved |
| :409 button | Reject Claim | Reject | Short |

### B4. Audit page shell: `WB/app/audit/[tenderId]/page.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :135 error | Failed to fetch tender audit brief from backend engine | Couldn't load this tender. | Plain |
| :149 toast | Loaded offline recorded package for TENDER-2026-402 | Showing the saved sample for TENDER-2026-402. | Plain |
| :153 error | Failed to load local offline package | Couldn't load the saved sample. | Plain |
| :176 toast | Scenario rates recalculated across all vendor bills of quantities. | Totals updated with the new rates. | Short and active |
| :180 error | Sensitivity calculation failed | Couldn't update the totals. Try again. | Plain |
| :193 toast | Sealing audit brief... | Signing off... | Term map |
| :195 toast | Audit brief verified and sealed for award. | Signed off. Exports are now available. | Says what changed |
| :203 error | Verification blocked: {n} open {items require} resolution in the Review Gate. | Can't sign off yet: {n} open {item/items} to resolve. | Short |
| :208 error | The tender was re-ingested or updated on the backend since you opened this brief. Please reload to review updated numbers. | This tender changed after you opened it. Reload to see the new numbers. | Plain |
| :211 error | Verification failed. Resolve open items first. | Couldn't sign off. Resolve the open items first. | Term map |
| :221 toast | Unlocking audit brief... | Reopening for edits... | Term map |
| :223 toast | Brief unlocked. Changes and overrides permitted. | Reopened. You can change decisions and amounts. | Plain |
| :228 error | Failed to unlock brief. | Couldn't reopen. Try again. | Plain |
| :236 toast | TBE master workbook (.xlsx) downloaded successfully. | Bid evaluation workbook downloaded. | Plain. Drops "successfully". |
| :250 toast | Clarification register (.{format}) downloaded. | Clarification list downloaded. | Plain |
| :314 banner | Operating in Read-Only Offline Replay: Displaying recorded package snapshot for {id}. Live mutations, reviews, and overrides require an active backend engine. | Sample data, read-only. You are viewing a saved copy of {id}. Decisions and amount changes need a live connection. | Plain. Two short sentences. |
| :318 pill | Replay Snapshot | Saved sample | Plain |
| :329 banner | Audit Version Conflict: The backend tender was re-ingested. Your open brief is stale. | Newer data available. This tender was processed again after you opened it. | Plain |
| :338 button | Reload Brief | Reload | Short |
| :352 eyebrow | Tender Audit | Bid evaluation | Plain |
| :362 status | VERIFIED & SEALED / DRAFT REVIEW | Signed off / Draft | Sentence case. Term map. |
| :366 meta | Employer: {client} · Specification: {doc} ({n} pages parsed) | Client: {client} · Spec: {doc} ({n} pages read) | Term map |
| :382 button | Export TBE (.xlsx) | Download workbook (.xlsx) | Plain |
| :392 link | Buyer Brief | Shareable brief | Says what it is |
| :402 link | Print Brief | Print | Short |
| :411 stat | Bidders Evaluated | Bids compared | Plain |
| :414 stat unit | Proposals | bids | Plain |
| :417 stat note | source PDF documents indexed | documents read | Plain |
| :423 stat | Technical Clause Coverage | Requirements checked | Plain |
| :426 stat unit | Clauses | requirements | Term map |
| :429 stat note | Pint 0.26.2 unit dimensional engine | Units converted and checked by code | Decision 6 |
| :435 stat | Recommended Bidder (L1) | Recommended award | Term map |
| :438 stat | Awaiting Award | Not decided | Plain |
| :441 stat | True Cost: | Evaluated total: | Term map |
| :447 stat | Open Items / Clarifications | Open items | Short |
| :454 stat unit | Open Gaps | open | Short |
| :457 stat note | Ready for verified seal / Audit sign-offs required | Ready to sign off / Needs your review | Term map |
| :501 tab | True Job Cost Ledger | Evaluated totals | Term map |
| :515 tab | Compliance Matrix | Requirements check | Plain |
| :529 tab | Commercial Risks | Commercial terms | Matches what the tab shows |
| :540 tab | Findings Register | Findings | Short |
| :551 tab | Rate Sensitivity | Rate scenarios | Plain |
| :565 tab | Sign-Off & Clarifications | Sign-off and clarifications | Sentence case, no ampersand |
| :581 tab | Reports & Exports | Downloads | Plain |
| :588 label | Drilldown: | Line items: | Plain |
| :593 hint | Select vendor for line item drilldown | Select a bidder to see its line items | Plain |
| :606 error | Error rendering {activeTab} view | This tab couldn't load. | Plain |
| :715 heading | Procurement Committee Sign-Off & Clarifications | Sign-off and clarifications | Sentence case |
| :718 subtitle | Clear open verification items, export the Technical Bid Evaluation (TBE) master workbook, or print the official sign-off sheet. | Resolve open items, then download the workbook or print the sign-off sheet. | Sequence is clear. Drops "official". |
| :729 button | Print Sign-Off Sheet | Print sign-off sheet | Sentence case |
| :740 button | Exporting... | Preparing download... | Plain |
| :745 button | Export TBE (.xlsx) | Download workbook (.xlsx) | Consistent |
| :756 heading | Open Verification Items ({n}) | Open items ({n}) | Short |
| :760 status | Cleared for Award Verification | Ready to sign off | Term map |
| :764 status | Action Required Before Brief Sealing | Resolve before sign-off | Term map |
| :797 `aria-label` | Review {vendor} clause {param} | Review {vendor}, {param} | Term map |
| :799 button | Review & Sign | Review | The button opens a review, it does not sign |
| :807 empty | Zero unpriced gaps remain. All technical and commercial deviations are fully leveled and priced in the evaluation ledger. | No open items. Every gap has a price or a decision. | Short. Drops "leveled". |
| :816 heading | Vendor Clarification Register | Clarification questions for bidders | Says what the file holds |
| :819 subtitle | Formal clarification logs citing exact specification clauses and proposal quotations. | One question per gap, with the spec page and the bidder's quote. | Concrete |
| :833 button | Download (.csv) | Download CSV | Plain |
| :845 button | Download (.xlsx) | Download Excel | Plain |
| :856 heading | Procurement Exports & Audit Dossier | Downloads | Plain |
| :859 subtitle | Download verified commercial workbooks and committee packages for {id}. | Files for {id}. Available after sign-off. | Says the rule (exports return 409 until verified) |
| :869 card | TBE Master Workbook (.xlsx) | Bid evaluation workbook (.xlsx) | Plain |
| :873 card text | Complete multi-tab Technical Bid Evaluation with verified Pint evaluation formulas, vendor rankings, and cell-by-cell price adders. | Rankings, evaluated totals and every cost adjustment with its formula. | Plain. Term map. |
| :881 button | Download TBE Workbook | Download workbook | Consistent |
| :890 card | Clarification Register (.xlsx) | Clarification questions (.xlsx) | Consistent |
| :894 card text | Clause-by-clause vendor queries citing exact specification paragraphs and proposal deviations for formal procurement letters. | One question per gap, ready to send to each bidder. | Plain |
| :902 button | Download Register (.xlsx) | Download questions (.xlsx) | Consistent |
| :911 card | Printable Executive Dossier | Sign-off sheet | Plain |
| :915 card text | Clean print-optimized layout ready for paper sign-off, committee signatures, and formal EPC board submission. | A printable summary with signature lines for the award committee. | Plain |
| :923 button | Open Print Sheet | Open sign-off sheet | Consistent |
| :974 toast | Audit decision successfully recorded in verification log. | Decision saved. | Short |
| :1016 heading | Tender Package Unavailable from Backend | Can't load this tender | Plain |
| :1024 label | Connection Status: Offline / 500 | Server not responding | Plain |
| :1029-1034 text | Diagnostic Error: The remote backend returned HTTP 500 or is currently booting up. Ensure NEXT_PUBLIC_API_URL is set in Vercel project environment variables... | The server didn't respond. It may be starting up. Try again in a minute. (Move the env-var hint to a collapsed "Technical details" line.) | Users can't act on env vars |
| :1045 button | Retry Connection | Try again | Plain |
| :1052 button | Open Offline Recorded Sample (TENDER-2026-402) | Open the sample tender | Plain |
| :1060 link | Go to Ingest Pipeline → (`&rarr;`) | Upload a new tender | No decorative arrow. Plain. |
| :1072, :1111 loading | Loading tender audit data / Loading audit workbench... | Loading tender... | Plain |

### B5. Overview page: `WB/app/page.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :96 heading | Executive Procurement Overview | Overview | Short |
| :99 subtitle | Automated Technical Bid Evaluation (TBE) & commercial scope leveling across active packages. | Every open tender, its status and its recommended award. | Plain |
| :109 button | Ingest Package | Upload tender | Term map |
| :117 button | Open Bid Audit | Open tender | Plain |
| :130 stat | Evaluated Package Volume | Tenders evaluated | Plain |
| :137 stat | L1 Recommendation: | Recommended award: | Term map |
| :148 stat | Scope Omission Exposure | Missing scope cost | Term map |
| :155 stat note | Identified unquoted scope add-backs across bids | Cost to close missing scope, all bids | Plain |
| :165 stat | Evaluation Engine | Calculation check | Plain |
| :172 stat note | Deterministic Unit Validation active | Units checked by code, not AI | Decision 6. Says what matters to the buyer. |
| :182 stat | Clarifications Register | Clarification questions | Consistent |
| :186 / :189 | Pending / Downloadable clarification register ready | Ready / Download from the tender page | Plain |
| :202 banner heading | Critical Commercial Rank Reversal Detected | Lowest bid is not the lowest cost | States the finding in plain words |
| :205 banner body | Apparent low bidder **VoltSwitch Industrial** (€418,000 quoted) carries **+€64,800 in unquoted equipment omissions**, elevating its true reconciled cost of ownership to **€482,800**. Verified lowest true total cost is **Nordwerk Schaltanlagen GmbH** at **€452,000**. | **{low}** quoted the lowest base bid at {lowBase}. Missing scope adds {lowAdj}, for an evaluated total of {lowEval}. **{best}** has the lowest evaluated total at {bestEval}. | Removes "apparent" and "elevating". Takes numbers from data: the hardcoded €64,800 and €482,800 disagree with the ledger (decision 3). Drops "Verified": the tender is a draft. |
| :214 button | Review Commercial Risks | See the cost breakdown | Says where it goes |
| :224 heading | Active Tender Packages & Procurement Status | Tenders | Short |
| :227 subtitle | Deterministic evaluation state, open item tracking, and audit sign-off | Status, open items and sign-off for each tender | Plain |
| :234 button | + Ingest New Tender | Upload tender | Term map. Drops the decorative "+" (an icon can carry it). |
| :243-246 columns | Package Scope & Equipment / Audit Status / Open Items / Ingested Date | Package / Status / Open items / Uploaded | Sentence case. Term map. |
| :257 empty | No active tender packages found | No tenders yet | Short |
| :260 empty | Ingest a new tender package (MR specification + vendor bid PDFs) to begin automated bid evaluation. | Upload your spec and the bids you received to start. | Plain |
| :267 button | Ingest First Tender | Upload a tender | Term map |
| :295 / :300 status | Verified & Sealed / Draft Audit | Signed off / Draft | Term map |
| :319 button | Open Audit | Open | Short |
| :337 footer | All evaluations powered by Pint 0.26.2 unit dimensional engine. Bids normalized against IEC/IEEE specifications with zero LLM hallucination. | Code checks every unit and does every sum. AI only finds the quotes, and each quote links to its page. | Plain and accurate (matches `BR/content/copy.ts:574`). "Zero LLM hallucination" is a claim nobody can verify. |
| :341 link | View Engine Audit Specifications → | How the checks work | No decorative arrow |
| :352 loading | Loading overview data | Loading... | Short |

### B6. Requirements check tab: `WB/components/ComplianceMatrix.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :61 heading | Clause-by-Clause Compliance Matrix (Pint 0.26.2 Engine) | Requirements check | Plain. Decision 6. |
| :64 pill | Deterministic Dimensional Precedence | Checked by code | Plain |
| :68 subtitle | Technical specification parameters evaluated with mathematical unit conversions and clause-by-clause grounding. | Each requirement compared with each bid, with units converted before comparing. | Plain |
| :81 placeholder | Search clause ID or name... | Search requirements | Term map |
| :97 / :102 | Severity: / Filter by criticality | Priority: / Filter by priority | One word for one idea |
| :104-108 options | All Clauses ({n}) / Mandatory Only / High Criticality / Medium Criticality / Commercial Only | All ({n}) / Mandatory / High priority / Medium priority / Commercial | Sentence case. Short. |
| :120 toggle | Gaps Only | Gaps only | Sentence case |
| :127 table caption | Technical Compliance Matrix | Requirements check | Consistent |
| :131 column | Specification Clause & Requirement | Requirement | Short |
| :147 empty | No technical parameters match the selected filter criteria. | No requirements match these filters. | Plain |
| :216 cell | €? Unpriced | No price yet | Readable without a decoding step |
| :216, :257 placeholder | — | n/a | No em dash |
| :228 cell | Not indicated | Not stated | Plain |
| :234 cell | ✗ (Req: {x}) | Required: {x} | No decorative symbol. Status colour already shows the miss. |
| :241 cell | No offer data | Not in the bid | Plain |
| :270 button | Review | Review | No change |

### B7. Commercial terms tab: `WB/components/CommercialRiskTable.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :37 heading | Commercial, Warranty & Logistics Risk Exposure | Commercial terms | Short |
| :40 pill | Incoterms & Liability Audits | Delivery, warranty and testing | Says what is compared |
| :44 subtitle | Cross-examination of Incoterms delivery liabilities, defect liability periods, and witnessed factory acceptance tests. | Delivery terms, warranty period and witnessed factory tests, compared with your spec. | Plain |
| :52 empty | No vendor proposals currently available for commercial risk cross-examination. | No bids to compare yet. | Plain |
| :88 label | Commercial Adders | Commercial cost adjustments | Term map |
| :101 label | Delivery Incoterm (2020) | Delivery terms (Incoterms 2020) | Plain first |
| :106 / :111 | Offered: / Req: | Offered: / Required: | No abbreviation |
| :108, :133, :163 | Compliant | Meets spec | Matches the brief app status words |
| :116 fallback | See proposal terms | See the bid | Plain |
| :125 label | Defect Liability Period | Warranty period | Common term |
| :142 note | Clock origin: from {x} | Starts at: {x} | Plain |
| :147 fallback | Standard vendor warranty | Standard warranty | Short |
| :156 label | Witnessed Factory Testing (I-FAT) | Witnessed factory test (FAT) | Sentence case |
| :166 note | Shortfall: Req {x} | Short by: required {x} | Plain |
| :171 fallback | Included per standard offer | Included in the bid | Plain |
| :180 label | Total True Cost: | Evaluated total: | Term map |

### B8. Findings tab: `WB/components/FindingsRegister.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :63 generated text | {name}: quoted {text \|\| 'omitted'} vs requirement {req} | {name}: bid says {text \|\| 'nothing'}, spec requires {req} | Plain |
| :71 | Unpriced under rate table | No price in the rate table | Plain |
| :122 heading | Audited Findings Register | Findings | Short |
| :125 count unit | Items | findings | Plain |
| :138, :155 options | All Bidders / All Severities | All bidders / All priorities | Sentence case. One word for one idea. |
| :167 empty | No findings matching the selected filters. | No findings match these filters. | Grammar |
| :235 | Unpriced | No price | Plain |
| :238 tags | Adder / Exposure | Added to total / Risk only | Says how each kind of money is treated: exposure is never added to the evaluated total (`BE/antova_ingest/findings.py:10-12`) |
| :256 | Unpriced gap: (Never defaulted to €0) | No price yet. Not counted as €0. | Plain |
| :261 / :271 | Formula: / Citations: | Formula: / Sources: | Plain |

### B9. Rate scenarios tab: `WB/components/SensitivityPanel.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :87 heading | Commodity & Rate Sensitivity Scenario Engine | Rate scenarios | Short |
| :91 | Calculating... | Updating... | Plain |
| :96 subtitle | Adjust macroeconomic commodity multipliers and simulate vendor ranking sensitivity before award. | Change copper, labour and freight rates to see if the ranking changes. | Plain |
| :109 button | Reset Baseline | Reset all | Plain |
| :123 button | Apply Scenario | Update totals | Says what happens |
| :134-135 | Copper (LME Index) / Impacts Cu-ETP busbars & cables | Copper price (LME) / Affects copper busbars and cables | Plain |
| :141, :194, :248 `aria-label` | Reset Copper / Reset Labor / Reset Freight | Reset copper / Reset labour / Reset freight | Sentence case. Spelling matches the brief app ("labour"). |
| :157, :210, :264 `aria-label` | Copper multiplier slider / Site labor rate slider / Freight multiplier slider | Copper price / Site labour rate / Freight rate | Screen readers already announce "slider" |
| :178, :285 | 1.00x (Base) | 1.00× (rate table) | Says what "base" means |
| :187-188 | Site Labor Hourly Rate / Field cabling & installation supervision | Site labour rate / Cabling and installation supervision | Plain |
| :232 | €60/h (Base) | €60/h (rate table) | Consistent |
| :241-242 | Heavy Haul & Sea Freight / Incoterms DAP/DDP transport leveling | Freight / Transport to site (DAP or DDP) | Plain |
| :296 | Active calculation basis: Copper ... · Labor ... · Freight ... | Current rates: copper ... · labour ... · freight ... | Plain |
| :301 | Pending changes staged — Click "Apply Scenario" to recalculate | Rates changed. Select "Update totals" to recalculate. | Removes em dash and curly quotes |

### B10. Evidence viewer: `WB/components/EvidenceModal.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| :98 | Specification (MR Spec) | Your spec | Plain |
| :106 dialog label | Documentary Evidence & Citation Inspector | Source page | Short |
| :118 heading | Documentary Evidence Inspector | Source page | Short |
| :120 pill | PyMuPDF Verified | Quote found on page | Says what was checked |
| :124 | Clause: {id} · Document: {doc} | Requirement: {id} · Document: {doc} | Term map |
| :140 / :150 tabs | Primary / Secondary | Main source / Other source | Plain |
| :158 `aria-label` | Close modal | Close | Short |
| :188-206 buttons | Zoom Out / Zoom In / Reset Zoom | Zoom out / Zoom in / Reset zoom | Sentence case |
| :218 loading | Rendering verified PyMuPDF coordinate crop... | Loading page image... | Plain |
| :228 alt | Evidence crop for clause {id} | Page image for {id} | Plain |
| :236 | Direct visual PDF crop unavailable for this parameter | Page image not available | Plain |
| :239 | Inspection details below contain the exact extracted verbatim and bounding box coordinates. | The exact quote and its position on the page are below. | Plain |
| :250 | Source Document & Citation | Source | Short |
| :261 / :262 | Extracted Quotation / Deterministic Match | Quote / Exact match | Plain |
| :265 fallback | "Extracted parameter confirmed in submitted technical documentation." (curly quotes) | No quote recorded. | The fallback claims a confirmation that did not happen |
| :272 / :278 | Document Page: / Bounding Box: | Page: / Position on page: | Plain |
| :280 | Coordinate verified | Position checked | Plain |
| :284 / :286 | Target Entity: / Employer Specification / Vendor ({x}) | Document from: / Your spec / Bidder ({x}) | Term map |
| :290 / :293 | Audit Status: / Verified Traceable | Status: / Linked to source | Plain |
| :300 | Every evaluated rate adder and compliance finding in Antova maps to a verified page coordinate in the vendor's submitted proposal or employer MR specification. | Every result and cost adjustment links to a page in the bid or in your spec. | Term map |
| :308 | Tender Ref: {id} · Press ESC to dismiss | Tender {id} · Press Esc to close | Plain |
| :314 button | Close Inspector | Close | Short |

### B11. Sign-off rail and baseline card

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| WB/components/ReviewGateRail.tsx:57 | Audit Verification Gate | Before sign-off | Plain |
| :68 status | SEALED / {n} BLOCKING / CLEARED | Signed off / {n} open / Ready | Sentence case. Term map. |
| :74-78 | Reviewed: / Flags: / Stale: | Reviewed: / Flagged: / Changed since review: | "Stale" is internal |
| :87 | Every unpriced gap, review flag, and manual inspection item must be audited before the brief can be verified. | Price or decide every open item before you sign off. | Active voice |
| :102 tag | FLAG | Check | Sentence case |
| :123 | €? UNPRICED | No price yet | Readable |
| :131 button | Price / Accept | Set price or accept | Plain |
| :153 / :175 buttons | Inspect / Confirm | View / Confirm | Plain |
| :198 | Audit sealed · TBE workbook & commercial exports unlocked | Signed off. Downloads are available. | Term map |
| :202 | {n} item blocks / items block | {n} open item / open items | Plain |
| :206 | Zero blockers remaining · Ready for verified seal | No open items. Ready to sign off. | Term map |
| :227 button | Verify & Seal Audit | Sign off | Term map |
| WB/components/ProjectBaselineCard.tsx:45 | Confirmed baseline project constraint | Project value confirmed | Plain |
| :51 | Failed to record project baseline review | Couldn't save. Try again. | Plain |
| :63 heading | Project Baseline Constraints | Project values you entered | Says where the numbers came from (`BE/antova_review.py:36` "Buyer-entered project value") |
| :66 unit | Parameters | values | Plain |
| :70 | Must be audited before verified seal | Confirm these before sign-off | Active voice |
| :102 placeholder | Audit note / confirmation basis... | Example: Contract value from the board approval of 3 Nov. | Concrete |
| :125, :140 button | Confirm Baseline | Confirm | Short |

### B12. Shell, navigation and tender picker: `WB/components/layout/AppShell.tsx`, `WB/components/TenderCommandPopover.tsx`, `WB/app/layout.tsx`, `WB/components/StatusTag.tsx`, `WB/lib/formatting.ts`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| WB/app/layout.tsx:5 (page title) | Antova — Technical Bid Evaluation & Tender Auditing | Antova: bid evaluation for electrical tenders | Removes em dash. Plain. |
| WB/app/layout.tsx:6 (meta) | Automated technical bid evaluation and tender auditing engine for electrical and EPC procurement. | Antova compares electrical bids with your spec and prices every gap. | Plain and active |
| AppShell.tsx:128-132 breadcrumbs | Workspace / Overview, Workspace / Document Ingestion, Administration / Governance ... | Overview, Upload, Governance, Settings | Breadcrumb roots add nothing |
| AppShell.tsx:135-140, :265-305 nav | True Job Cost Ledger / Compliance Matrix / Commercial Risks / Rate Sensitivity / Review & Sign-off (and "Sign-Off" elsewhere) / Reports & Exports | Evaluated totals / Requirements check / Commercial terms / Rate scenarios / Sign-off / Downloads | Matches the tab names in B4. Fixes the inconsistent "Sign-off" vs "Sign-Off". |
| AppShell.tsx:142, :144 | Workspace / {id} / {tab or 'Bid Audit'} / Workspace / Antova | {id} / {tab} | Short |
| AppShell.tsx:170-174 | Unified / Enterprise EPC | (remove) | Decorative labels |
| AppShell.tsx:207-219 | Executive Overview / Tenders Registry | Overview / Tenders | Short |
| AppShell.tsx:239-244 | Bid Evaluation Workbench / Bid Audit | Tender / Bid evaluation | Plain |
| AppShell.tsx:327-330 | Document Ingestion | Upload | Term map |
| AppShell.tsx:350-353 | Reports & TBE Exports / Reports & Exports | Downloads | Consistent |
| AppShell.tsx:373-376 | System Governance & Pint Math / Governance | How the checks work | Says what the page explains |
| AppShell.tsx:386-389 | Settings & Tolerances / Settings | Settings | Short |
| AppShell.tsx:444-449 | Verified & Sealed / Draft ({n} open items) | Signed off / Draft ({n} open) | Term map |
| AppShell.tsx:473-476 | Download Technical Bid Evaluation (.xlsx) / Export TBE | Download workbook (.xlsx) / Download | Consistent |
| AppShell.tsx:485-488 | Unlock draft for manual overrides / Unlock Draft | Reopen to change decisions or amounts / Reopen | Term map |
| AppShell.tsx:494-497 | Verify and seal brief for award / Verify & Seal | Sign off this tender / Sign off | Term map |
| AppShell.tsx:507-508 | Print Audit Report | Print sign-off sheet | Consistent |
| AppShell.tsx:559 | Loading workspace... | Loading... | Short |
| TenderCommandPopover.tsx:137 | Select Tender | Choose tender | Plain |
| TenderCommandPopover.tsx:141, :240 / :145, :245 | Verified / Draft | Signed off / Draft | Term map |
| TenderCommandPopover.tsx:166 placeholder | Search by tender ID or title... | Search tenders | Short |
| TenderCommandPopover.tsx:186 | Search Results / Active Tenders | Results / Tenders | Sentence case |
| TenderCommandPopover.tsx:192 | No matching tenders found for "{q}" (curly quotes) | No tenders match "{q}" | Straight quotes |
| TenderCommandPopover.tsx:226 | open clarifications | open items | Consistent |
| TenderCommandPopover.tsx:262 | Ingest & Create New Tender | Upload new tender | Term map |
| TenderCommandPopover.tsx:264 | ESC to close | Esc to close | Key name in sentence case |
| StatusTag.tsx:41 / :59 | ({word}) (Overridden) / · Override | {word} · amount edited | Plain |
| formatting.ts:47-76 status labels | COMPLIANT / UPGRADE / DEVIATION / OMITTED / CONFLICT / INSPECT | Meets spec / Exceeds spec / Gap / Missing / Documents disagree / Needs inspection | Use the same words as `WB/lib/domain/status.ts:16-22` and `BR/content/copy.ts:593-599`. The UI uses two vocabularies for the same statuses today. |
| formatting.ts:87-106 priority labels | MANDATORY / HIGH / MEDIUM / COMMERCIAL | Mandatory / High / Medium / Commercial | Sentence case |

### B13. Upload page: `WB/app/ingest/page.tsx`, `WB/components/SplitUploadDropzone.tsx`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| ingest/page.tsx:16 | Tender & Bid Ingestion Workbench | Upload a tender | Plain |
| ingest/page.tsx:19 | Upload the Employer Material Requisition (MR) specification and vendor bid packages for automated Pint unit evaluation. | Upload your spec (material requisition) and the bids you received. | Plain |
| ingest/page.tsx:28-63 steps | Stage 1 / 1. Document Upload / MR Spec & Vendor Proposals; Stage 2 / 2. Classification & Map / Role assignment & country tags; Stage 3 / 3. Pint Math Validation / PyMuPDF OCR + Unit checks; Stage 4 / 4. Review Manifest / Commit to True Job Cost Ledger | 1. Upload / Spec and bids; 2. Sort / Which file is which; 3. Check / Read values and convert units; 4. Review / Confirm and evaluate | Drops the doubled "Stage 1 / 1." numbering and engine names. Note: "OCR" is false, the backend says "OCR is not supported" (`BE/antova_ingest/pipeline.py:285`). |
| SplitUploadDropzone.tsx:31-36 progress log | [Demo Replay Mode] Intake: Ingesting MR Specification & Vendor Bid PDFs... (and 5 similar) | Sample run: reading the documents... / finding each quote... / checking each quote is on its page... / reading the values... / pricing the gaps... / done. Opening the tender... | Matches the brief app's stage names (`BR/content/copy.ts:293-298`). No brackets, no exclamation mark. |
| :64 | [Preset Run] Enqueuing deterministic ingest evaluation for {id}... | Starting {id}... | Plain |
| :74 | [Demo Replay Mode] Replaying verified benchmark package for {id}... | Loading the saved sample for {id}... | Plain |
| :88 | Ingest pipeline complete! Redirecting to Audit Workbench... | Done. Opening the tender... | No exclamation mark |
| :95 | Ingestion job failed during backend rule evaluation. | Processing stopped. Nothing was saved. Try again. | Says the effect |
| :102 | Lost polling connection to background job {jobId}. Backend may be busy. | Lost contact with the server. Check the tender list in a minute. | Says what to do |
| :113 | Please select an Engineering Tender Specification PDF | Add your spec PDF first. | Plain |
| :119-134 | Staging and streaming documents to Antova backend... / Streaming {file}... / Streaming vendor proposal: {file}... / Validating and committing tender manifest... | Uploading... / Uploading {file}... / Uploading bid: {file}... / Saving the file list... | Plain |
| :166 | Queuing deterministic parsing and rule evaluation on backend engine... | Queued. Processing starts shortly. | Plain |
| :172 / :177 | Backend did not return a valid job ID for ingestion. / Failed to stream documents or start ingestion job on backend. | The server didn't start processing. Try again. / Upload failed. Try again. | Plain |
| :190 | Instant Repository Evaluation Run | Try a sample tender | Plain |
| :194 | Execute live rule evaluation on verified benchmark packages directly in the local repo: | Run a saved sample to see a full result: | Plain |
| :204, :211 | Replay TENDER-2026-402 / Replay TENDER-2026-417 | Open sample 402 / Open sample 417 | Plain |
| :222 | Tender & Multi-Vendor Proposal Ingestion Workbench | Upload a tender | Consistent |
| :225 | Stage the baseline engineering specification against competitor bids for automated gap pricing. | Add your spec and every bid. Antova prices each gap against your spec. | Plain |
| :233 / :248 / :262 labels | Tender Reference ID / Package Title / Procurement Client / EPC Entity | Tender ID / Package name / Client | Short |
| :282 / :284 | (A) Engineering Specification / BOM / MANDATORY BASELINE | Your spec / Required | Plain |
| :302 | Select or Drop Baseline MR Specification PDF | Drop your spec PDF here, or browse | Plain |
| :305 | {n} KB · PDF Ready / Max 50MB · Up to 600 Pages · PDF Layer Validated | {n} KB, ready / PDF up to 50 MB and 600 pages | Plain. Check these limits against `BE/antova_upload_api.py` before shipping. |
| :311-312 | Default Repo Target: MR-33kV-GIS-Rev4.pdf / Ontology Rules: 8 Evaluated Clauses (Pint 0.26) | (remove) | Developer detail |
| :321 / :323 | (B) Vendor Proposals (Bids) / MULTI-VENDOR | Bids / One PDF per bidder | Plain |
| :350-351 | {n} Vendor Proposal(s) Staged / Select or Drop Vendor Bid PDFs | {n} {bid/bids} added / Drop bid PDFs here, or browse | Real plural instead of "(s)" |
| :354 | Upload multiple competitor bids for simultaneous audit comparison | Add every bid you want to compare | Plain |
| :383 | Deterministic Pint unit engine + PyMuPDF coordinate indexing. | (remove) | Decision 6 |
| :393 / :397 buttons | Auditing Documents... / Commit & Execute Bid Audit | Processing... / Evaluate bids | Plain |
| :412 / :430 | Live Audit Pipeline Execution: / Current Stage: | Progress: / Step: | Plain |
| :14 default title | 350 MWp Solar + BESS Substation — 33kV GIS Switchgear Package | 350 MWp Solar + BESS Substation: 33 kV GIS Switchgear Package | No em dash (same fix applies to sample data, see D3) |

### B14. Downloads, Governance, Settings, print pages

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| WB/app/reports/page.tsx:41 / :44 | TBE master workbook downloaded successfully. / Failed to export TBE | Workbook downloaded. / Couldn't download the workbook. | Plain |
| :55 / :58 | Clarification register (.{f}) downloaded. / Failed to export clarifications | Clarification questions downloaded. / Couldn't download the questions. | Plain |
| :72 | Procurement Reports & TBE Export Center | Downloads | Short |
| :75 | Generate audited Technical Bid Evaluation (TBE) workbooks, vendor clarification matrices, and executive sign-off sheets. | The workbook, the clarification questions and the sign-off sheet for each tender. | Plain |
| :109-123 | TBE Master Workbook (.xlsx) / Formal evaluation matrix / Complete multi-sheet workbook including True Job Cost Ledger, clause-by-clause unit compliance, commercial omissions, and formula-backed rate adders. / Download TBE Workbook | Bid evaluation workbook (.xlsx) / Excel / Rankings, evaluated totals, the requirements check and every cost adjustment with its formula. / Download workbook | Term map |
| :135-158 | Vendor Clarifications Register / Export format: CSV / XLSX / Structured logs of all commercial deviations, scope omissions, and technical clarifications citing exact specification paragraphs and vendor quotations. / CSV / Excel (.xlsx) | Clarification questions / CSV or Excel / One question per gap, with the spec page and the bidder's quote. / CSV / Excel | Plain |
| :171-185 | Executive Sign-Off Sheet / Print-optimized layout / Clean committee sign-off summary with executive financial comparisons, commercial risk disclaimers, and signature blocks. / Open Print Sheet | Sign-off sheet / For printing / A one-page summary with evaluated totals and signature lines. / Open sign-off sheet | Plain |
| :195 | Audit Provenance & Cryptographic Traceability | Where every number comes from | The page has no cryptography. Plain. |
| :198 | Every exported document includes deterministic run checksums, PyMuPDF bounding coordinates, and Pint 0.26.2 unit math logs. Certified for submission to commercial lending syndicates and institutional EPC boards. | Every file shows the source page for each quote and the formula for each cost adjustment. | Removes "Certified for submission...", a certification that does not exist |
| WB/app/governance/page.tsx:22 | System Governance & Deterministic Pint Validation | How the checks work | Plain |
| :25 | Pint 0.26.2 mathematical unit conversion engine, PyMuPDF coordinate grounding, and rule audit specifications. | What AI does, what code does, and how amount edits are recorded. | Plain |
| :33-66 tiles | Engine Status / HEALTHY / Unit Engine / Dimensional Precedence Active / OCR & Grounding / PyMuPDF 1.25+ / Bounding Coordinate Rects | Service / Running / Unit checks / On / Quotes on the page / Every quote is located on its page / (remove "Bounding Coordinate Rects") | Plain. "OCR" is false (see B13). Versions stay on this page only. |
| :76 | Mathematical Rigor & Compliance Precedence Rules | Rules the system follows | Plain |
| :82-83 | Zero LLM Numeric Hallucination / All monetary calculations, adders, and unit conversions are executed through pure Python Pint equations and deterministic arithmetic. | Code does every sum / AI finds the quotes. Code converts units and calculates every cost adjustment and total. | Accurate and plain (matches `BR/content/copy.ts:574`) |
| :90-91 | Coordinate Provenance Requirement / Every extracted quote must include document name, page number, and bounding box coordinates verified against the original PDF. | Every quote has a page / Each quote names its document and page, and the system checks the text is on that page. | Plain |
| :98-99 | Immutable Audit Trail on Overrides / Manual engineering cost overrides cannot delete or overwrite the underlying automated calculation; both values and user justification are stamped permanently in the export ledger. | Edits never erase the calculation / When you edit a cost adjustment, the workbook keeps the calculated amount, your amount and your reason. | Plain and active |
| :85, :93, :101 | Enforced | Always on | Plain |
| WB/app/settings/page.tsx:25 | Platform Settings & Evaluation Baseline | Settings | Short |
| :28 | Configure default macroeconomic evaluation rates, tolerance thresholds, and engine parameters. | Default rates for new rate scenarios. | The page has no tolerance or engine controls |
| :38-71 | Default Macroeconomic Rate Baselines / Default Copper Index Multiplier / Default Site Labor Hourly Rate (€/hr) / Default Heavy Haul Freight Multiplier | Default rates / Copper price multiplier / Site labour rate (€/h) / Freight multiplier | Plain |
| :89-99 | Backend Engine Connection / Configured API Base URL: / (Same-origin proxy /api/review/tenders) / To point to a different hosted backend instance on Vercel, set NEXT_PUBLIC_API_URL in your project environment variables. | Server connection / Server address: / (this site) / (move the env-var hint to "Technical details") | Users can't act on env vars |
| :106 / :115 | Preferences saved / Save Preferences | Saved / Save | Short |
| WB/app/print/[tenderId]/page.tsx:32 | Generating official printable procurement sign-off sheet... | Preparing the sign-off sheet... | Plain |
| :40 | Failed to load tender audit data from Antova backend. | Couldn't load this tender. | Plain |
| :61 / :69 | Return to Interactive Workbench / Print Official Sign-Off Report | Back to the tender / Print | Plain |
| :77-79 | ANTOVA PROCUREMENT AUDIT REPORT / TECHNICAL BID EVALUATION & COMMERCIAL RECONCILIATION | Bid evaluation and award recommendation | Sentence case |
| :85 | Engine Version: FastAPI 3.0.0 / Pint 0.26.2 | Antova version 3.0.0 | Decision 6 |
| :96-98 | Equipment Package: / Baseline Spec Document: / Rate Table Applied: | Package: / Spec: / Rate table: | Short |
| :106 | 1. Commercial Bid Reconciled Ranking & Omission Summary | 1. Ranking by evaluated total | Plain |
| :112-118 columns | Vendor / Origin, Quoted Price (Bid), Tech Adders, Comm. Adders, Total Omissions, True Evaluated Cost, Status | Bidder, Quoted price, Technical adjustments, Commercial adjustments, Total adjustments, Evaluated total, Status | Term map |
| :136 | Recommended Award / Subordinate | Recommended award / (blank) | "Subordinate" is odd and unneeded |
| :147 | Executive Summary & Commercial Reconciliation | Summary | Short |
| :165-178 | The nominal lowest bidder at quote submission was **{low} ({base})**. Following systematic engineering clause audits against specification **{doc}**, {low} was assessed **+{adj} in mandatory cost add-backs** ({tech} technical scope omissions and {comm} commercial terms adjustments). This causes a commercial rank reversal, identifying **{best} at {eval}** as the true lowest evaluated cost package. | **{low}** submitted the lowest base bid at {base}. Checked against spec {doc}, its missing scope and commercial gaps add {adj} ({tech} technical, {comm} commercial), for an evaluated total of {lowEval}. **{best}** has the lowest evaluated total at {eval} and is the recommended award. | Same fix as the ledger banner. Removes "nominal" and the "-ing" clause. Adds the low bidder's evaluated total, which the current text omits. |
| :182-188 | The recommended lowest evaluated bidder is **{best} ({eval})**. Audited scope omissions and commercial terms adjustments total **+{adj}**, evaluated against specification {doc} and benchmark rate table {v}. | **{best}** has the lowest evaluated total at {eval}. Its cost adjustments total {adj}, priced from rate table {v} against spec {doc}. | Plain and active |
| :195-196 | Commercial Exception: {names} submitted non-priced or incomplete commercial schedules and require formal price submission before commercial ranking can be finalized. | Missing prices: {names} did not price every item. Ask for the missing prices before you award. | Active voice. States the action. |
| :207 | Tender Evaluation Committee Sign-Off & Verification Signatures: | Approvals | Short |
| :210-221 | Chief Procurement Officer / EPC Consortium GmbH / Senior Electrical Lead Engineer / High Voltage Substation Dept / Head of Estimating & Contracts / Commercial Operations | Procurement lead / Electrical lead / Commercial lead (drop the hardcoded company and department names) | The sign-off sheet should not invent the client's org chart |

### B15. Error boundary and API client messages that reach the screen

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| WB/components/ErrorBoundary.tsx:40-56 | Component Rendering Error / A client-side exception occurred while rendering this view. Tab data may be incomplete or structured differently. / Retry Rendering Tab | This tab couldn't load / Something in this tender's data didn't display. Your data is safe. / Try again | Plain |
| WB/lib/api.ts:89 | Invalid brief payload received from backend | The server sent data this page can't read. | Plain |
| WB/lib/api.ts:249, :260, :300-376, :403, :422, :444, :473, :494, :524, :550, :567 | Failed to list tenders: {status} {text} / Failed to fetch brief for {id}: ... / Upload failed: {status} / Failed to set override: {status} / Failed to delete override: {status} / Review decision failed: {status} / Export failed: {status} / Verification failed: {status} / Withdraw failed: {status} ... | Couldn't load tenders. / Couldn't load {id}. / Upload failed. / Couldn't save the amount. / Couldn't reset the amount. / Couldn't save your decision. / Couldn't download. / Couldn't sign off. / Couldn't reopen. (each followed by " Try again.") | These show in toasts whenever the server sends no `detail`. Status codes mean nothing to buyers. Keep them in the console. |
| WB/lib/api.ts:490 | Verify the brief before exporting it | Sign off before you download the workbook. | Term map. Also the backend text, see D1. |

## Table C: brief and review app (Vite), `BR/`

This app already follows a plain-language style. Changes are few.

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| BR/content/copy.ts:689 | Override | Edit cost adjustment | Same action as Table A. One name across both apps. |
| BR/content/copy.ts:690 | Override adder (€) | New cost adjustment (€) | Term map |
| BR/content/copy.ts:692 | e.g. engineer's estimate after vendor clarification | Example: engineer's estimate after the vendor clarified | Plain |
| BR/content/copy.ts:693 / :694 | Save override / Remove override | Save amount / Use calculated amount | Says what happens on removal |
| BR/content/copy.ts:695 | Overridden: {note} | Amount edited: {note} | Plain |
| BR/content/copy.ts:697 | Say why you're overriding this adder. | Add a reason for the new amount. | Term map |
| BR/content/copy.ts:699 | Withdraw the brief before changing overrides | Reopen the brief before you edit amounts. | Term map |
| BR/content/copy.ts:700-703 | Override saved. / Override removed. / Couldn't save the override. Try again. / Couldn't remove the override. Try again. | Amount saved. / Calculated amount restored. / Couldn't save the amount. Try again. / Couldn't restore the amount. Try again. | Term map |
| BR/content/copy.ts:626 | Ingested again. This brief is back to draft. | Processed again. This brief is back to draft. | Term map |
| BR/content/copy.ts:630-631 | The evidence for this override changed, so the new ingest cleared it. Enter it again if it still applies: (and plural) | The source page for this edited amount changed, so the new run removed the edit. Enter it again if it still applies: | Term map |
| BR/content/copy.ts:650-665 | Ingest {id} again? / ...ingesting it again takes it back to draft and clears any override whose evidence has changed. / {n} overrides on this brief: each stays only if the evidence it rests on is unchanged... / Ingest again | Process {id} again? / ...processing it again takes it back to draft and removes any edited amount whose source page changed. / {n} edited amounts on this brief: each stays only if its source page is unchanged... / Process again | Term map |
| BR/content/copy.ts:754-766, :773 | Run ingest / Couldn't start the ingest. Try again. / No tenders yet. Ingest one with | Process a tender / Couldn't start processing. Try again. / No tenders yet. Process one with | Term map. This screen is for operators, so keep the CLI command. |
| BR/content/copy.ts:744 (column) | Ingested | Processed | Term map |
| BR/content/copy.ts:297 (stage) | Settling contradictions | Checking where documents disagree | Plain |
| BR/content/copy.ts:308, :474 | {vendor}: {from} → {to} / {from} → {to} | {vendor}: {from} to {to} / {from} to {to} | Arrow characters are decorative. Screen readers read them unevenly. |
| BR/content/copy.ts:118, :128, :130, :224 | Get Early Access | Get early access | Sentence case |
| BR/content/copy.ts:126 / :127, :132 / :129 | Watch 1-Minute Walkthrough / See Sample Check / Upload Files to Check | Watch the 1-minute walkthrough / See a sample check / Upload files to check | Sentence case |
| BR/content/copy.ts:121, :136, :140, :146, :152 | SMART BID CHECKER / THE REAL PROOF / PAGE 1 · PROMISED PRICE / PAGE 48 · HIDDEN NOTES / REAL TOTAL COST: $1,385,000 | Bid checker / The proof / Page 1: quoted price / Page 48: the exclusions / Evaluated total: $1,385,000 | Keep uppercase as a CSS style, not in the string. "Smart" is filler. |
| BR/content/copy.ts:133-134, :175, :179-187 | Sample Check: Equipment & Contractor Bids / Done in Minutes / Direct Link to Every Page / Your Data Stays Private / How It Works / 01 / Upload Your Files ... | Sample check: equipment and contractor bids / Done in minutes / A link to every page / Your data stays private / How it works / 1. Upload your files ... | Sentence case |
| BR/content/copy.ts:148-149, :171 | +$185,000 in surprise bills later / Hidden deep in the fine print / Hidden notes add {adder} in surprise bills later. | +$185,000 not in the quote / On page 48 / The exclusions add {adder} to the real cost. | States the cost gap. Drops "surprise bills". |
| BR/content/copy.ts:40, :46 (FAQ) | Vendors often win deals with low bids, then charge huge extra fees... / Regular AI guesses and makes mistakes. Antova does not guess... clicks straight to the exact page where the vendor hid the note. | Low bids often leave out scope, and the vendor charges for it later. Antova finds the missing scope before you sign. / AI chat tools can make up numbers. In Antova, AI only finds the quotes. Code checks units and does every sum, and each quote links to its page. | More accurate. "Hid" accuses the vendor of intent. |
| BR/content/copy.ts:110, :122 vs workbench | The lowest bid is almost never the cheapest. | The lowest bid is often not the lowest cost. | "Almost never" is a claim the data doesn't support |
| BR/components/landing/Receipt.tsx:50 | (Apparent Lowest Bid) | (Lowest base bid) | Banned word |
| BR/components/landing/Receipt.tsx:52, :75, :91 | `&ldquo;` `&rdquo;` | `"` | Straight quotes |
| BR/components/landing/Receipt.tsx:54 | The quote on page one looks like a winner. It seems like the lowest price in the room. | Page one shows the lowest price of all the bids. | One idea, stated as fact |
| BR/components/landing/Receipt.tsx:77 | Deep in page 48, setup, testing, and basic warranty are quietly left out of the deal. | Page 48 excludes setup, testing and basic warranty. | Direct. Drops the dramatic adverbs. |
| BR/components/landing/FAQAccordion.tsx:41 | ● FREQUENTLY ASKED QUESTIONS | Questions and answers | Decorative dot removed. Sentence case. |
| BR/components/landing/HowItWorks.tsx:32 | ● THREE SIMPLE STEPS | Three steps | Decorative dot removed |
| BR/components/landing/Hero.tsx:90-137 | Done in Minutes / Direct Link to Every Page / Your Data Stays Private / LIVE DEMO / 3 BIDS CHECKED / REAL TOTAL COST | Done in minutes / A link to every page / Your data stays private / Live demo / 3 bids checked / Evaluated total | Sentence case |
| BR/components/landing/Hero.tsx:27-30 | Equipment Purchase List / Contract Agreement / Pricing & Extra Fees / Delivery & Service Terms | Equipment list / Contract / Prices and extras / Delivery and service terms | Sentence case |
| BR/components/ui/pulse-stripe-hero.tsx:363 | Key Documents & Bidders | Documents and bidders | Sentence case |
| Landing currency | `$` on the landing (`copy.ts:148-152`, `:169`), `€` in the brief and the workbench | Pick one currency for the sample (recommend €) | Mixed currencies in one demo look like an error |

Note: the Vite landing (`/`) is not routed on the demo domain, where Next owns `/`. The landing rows matter only where the Vite app is served at its root.

## Table D: backend strings that reach the UI

### D1. API error `detail` and open-item messages (shown in toasts and the sign-off rail)

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| BE/antova_review_api.py:39 | Withdraw the brief before changing overrides | Reopen the brief before you edit amounts. | Term map. Shown when an edit is refused (409). |
| BE/antova_review_api.py:228 | Verify the brief before exporting it | Sign off the brief before you download the workbook. | Term map |
| BE/antova_review_api.py:254 | This tender was ingested again after you opened it. Reload it and check it before verifying. | This tender was processed again after you opened it. Reload it and check it before you sign off. | Term map |
| BE/antova_review_api.py:261 | Open items remain | Resolve the open items before you sign off. | Says what to do |
| BE/antova_review_api.py:53 | adder_eur has at most 2 decimal places | Use at most 2 decimal places for the amount. | A field name leaks into the UI |
| BE/antova_review.py:32 | Gap found but not priced | Gap has no price yet. Enter an amount or accept it. | Says the action |
| BE/antova_review.py:33 | Evidence needs inspection | Check this quote against its page. | Plain |
| BE/antova_review.py:34 | Bid price not found; fix the source and re-ingest | Bid price not found. Correct the bid file and process it again. | Term map |
| BE/antova_review.py:35, :39 | Review flag not approved yet | Flag not reviewed yet. | Plain |
| BE/antova_review.py:36 | Buyer-entered project value not confirmed yet | Confirm the project value you entered. | Active voice |
| BE/antova_review.py:40 | Evidence changed since it was approved; review again | The source page changed after you accepted it. Review it again. | Plain |
| BE/antova_review.py:41 | Evidence rejected by the reviewer; fix the source and re-ingest | You rejected this quote. Correct the bid file and process it again. | Term map |
| BE/antova_ingest/pipeline.py:745 | this gap is money at risk, not a price; the findings register shows the exposure. Approve it to accept the gap as shown, or set an adder if the buyer prices it | This gap is a risk, not a cost, so it is not added to the total. Accept it as shown, or enter a cost adjustment if you want to price it. | Term map. Starts with a capital letter. |
| BE/antova_auth.py:76 / :83 | Review is not configured on this server / Invalid review token | Review is not set up on this server. / That review token didn't work. | Matches `BR/content/copy.ts:622` |
| BE/antova_upload_api.py:86 | Withdraw the brief before changing its documents | Reopen the brief before you change its documents. | Term map |
| BE/antova_upload_api.py (other messages) | The PDF is encrypted, The file is empty, ... | No change | Already plain |
| BE/antova_workers.py:29 | Interrupted: the service restarted before this job finished. Submit it again. | No change | Already plain |
| BE/antova_api.py:107 | Job not found | No change | Already plain |

### D2. Badges, stage names, flags and warnings in the brief payload

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| BE/antova_ingest/pipeline.py:763 (vendor `badge`) | Apparent Low Bidder | Lowest base bid | Banned word. Update fixtures (decision 5). |
| BE/antova_ingest/pipeline.py:761 | Lowest Evaluated Price | Lowest evaluated total | Sentence case. Term map. |
| BE/antova_ingest/pipeline.py:765 | Compliant Baseline | Meets spec | Plain |
| BE/antova_ingest/pipeline.py:759 | Price not found | Bid price not found | Says which price |
| BE/antova_engine.py:227 (`negotiating_vendor.basis`) | lowest true evaluated price | lowest evaluated total | Term map |
| BE/antova_engine.py:220 | has deviations with no rate ({ids}), so its price is only a lower bound | has gaps with no price ({ids}), so its evaluated total is a minimum | Plain |
| BE/antova_engine.py:362 | The ranking is not stable: {best} is lowest at its base price, but within the adder ranges {rivals} could be lower. | The ranking could change: {best} is lowest on calculated rates, but at the high end of the rate ranges {rivals} could cost less. | Term map. Says what would cause the change. |
| BE/antova_ingest/pipeline.py:51-52 (`stage_log.stage`) | Verify citations / Resolve precedence | Check each quote is on its page / Check where documents disagree | Matches the brief app stage names |
| BE/antova_ingest/pipeline.py:118 | {vendor}: headline price EUR {x} is far from the median of the other bids (EUR {y}); check the cited passage | {vendor}: bid price €{x} is far from the other bids (median €{y}). Check the quoted page. | Term map. Use the € sign as the UI does. |
| BE/antova_ingest/pipeline.py:184 | {vendor}: evaluated price {bound}EUR {price} (headline plus priced gaps) is {ratio} {direction} the median of the other bids' evaluated prices (EUR {middle}); its priced gaps drive the difference | {vendor}: evaluated total {bound}€{price} is {pct}% {above/below} the other bids' median (€{middle}). Its cost adjustments cause most of the difference. | Readable. Check the ratio prints as a percentage, the current f-string looks like it prints a raw decimal. |
| BE/antova_ingest/pipeline.py:559 / :564 / :568 | headline price is worded "{x}"; it may be a unit rate... / headline price {x} names no currency... / headline price is in {cur} and rate table {v} has no dated FX rate... | bid price is worded "{x}". It may be a unit rate, not the total. Check the quoted page. / bid price {x} has no currency. It was not evaluated. / bid price is in {cur} and rate table {v} has no exchange rate for it. It was not evaluated. | Term map. One idea per sentence. |
| BE/antova_ingest/precedence.py:121, :146 | possible evidence on p.{n} not reviewed: the recall sweep read {x} in the {section}, but the locator did not cite it | Page {n} may also mention {x} ({section}). Check it. | "Recall sweep" and "locator" are internal |
| BE/antova_ingest/precedence.py:140 | {where} was found by the recall sweep, not by the locator; check the passage before relying on it | {where} was found in a second pass. Check the page before you rely on it. | Internal jargon |
| BE/antova_ingest/pipeline.py:646 / :650 | the locator reports this parameter is not stated in {file} (pages {p} searched) / the locator returned neither a passage nor NOT_STATED for this parameter in {file} | Not stated in {file} (pages {p} searched). / No answer for this requirement in {file}. Check the bid by hand. | Internal jargon |
| BE/antova_ingest/pipeline.py:325 / :338 | {file}: answered by fallback model {m}, not {asked} / {file}: too long for one call; located in {n} overlapping page windows ({spans}) | No change (operator-only, shown in the stage log) | Diagnostic for operators. Leave unless it reaches buyers. |
| BE/antova_ingest/pipeline.py:476 | the passage on {where} states an exception or deviation from a standard; compliance is not read from it | The text on {where} lists an exception to the standard, so it was not counted as meeting the spec. | Plain and active |
| BE/antova_ingest/pipeline.py:481 / :486 / :493 | ...; value not read | ...; value not used | Small fix. "Read" suggests a reading failure. |
| BE/antova_ingest/precedence.py:95 (flag text) | warranty {x} months runs from {a}; the MR requires {y} months from {b}. The MR states no duration between the two, so the shortfall cannot be computed and is left unpriced | Warranty is {x} months from {a}. The spec requires {y} months from {b}. The spec doesn't say how long {a} to {b} takes, so the shortfall has no price yet. | Starts with a capital letter. "Spec" instead of "MR". |
| BE/antova_ingest/precedence.py:248 (`conflict_note`) | {sec} p.{n} claims {x}; {pri} p.{m} states {y}. {pri} governs. | {sec} p.{n} says {x}. {pri} p.{m} says {y}. The {pri} takes priority. | Plain. Matches `BR/content/copy.ts:327`. |
| BE/antova_ingest/findings.py:152 | {name} is claimed in a cover letter or compliance table, not shown in a datasheet, drawing or test report | {name} appears only in a cover letter or compliance table, not in a datasheet, drawing or test report. | Plain |
| BE/antova_ingest/findings.py:148 | {name} could not be established from the bid; ask the vendor | {name} is not clear from the bid. Ask the vendor. | Plain |
| BE/antova_ingest/findings.py:142 | the rate table has no rate for this gap | The rate table has no price for this gap. | Matches `BR/content/copy.ts:452` |
| BE/antova_ingest/findings.py:212 | Delivery date not assessed: {reason} | Delivery date not checked: {reason} | Plain |
| BE/antova_ingest/schedule.py, exposure.py reason strings | the vendor states no lead time, the buyer has not stated what a week of delay costs, ... | Capitalise the first letter when shown alone. No wording change. | Already plain |

### D3. Replay data on the live site: `WB/lib/sample402.json` and `WB/app/api/[[...path]]/route.ts`

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| route.ts:13, sample402.json `tender.title`, WB/components/Header.tsx:55 | 350 MWp Solar + BESS Substation — 33kV GIS Switchgear & RTU Protection Package | 350 MWp Solar + BESS Substation: 33 kV GIS Switchgear and RTU Protection Package | No em dash. Same fix in the backend sample manifests. |
| route.ts:21, :29 | 500 MW Hybrid Wind/Solar Collector Substation — 33kV GIS / 200 MW Grid Booster BESS — High Voltage Switchgear | ...: 33 kV GIS / ...: High Voltage Switchgear | No em dash |
| route.ts:47 (`detail` on every refused write) | Offline replay mode is read-only. Live mutations, reviews, and overrides require an active backend engine. | This is sample data and can't be changed. Connect a live server to save decisions and amounts. | Plain. Users hit this on every edit attempt on the live site. |
| route.ts:100 (CSV header) | Vendor ID,Vendor Name,Clause ID,Clause Name,Quoted Value,Required Value,Pricing Status,Adder EUR | Bidder ID,Bidder,Requirement ID,Requirement,Offered,Required,Price status,Cost adjustment (EUR) | Term map |
| route.ts:193 | Upstream unreachable | Server not responding | Plain |
| sample402.json `badge` (2 places) | Apparent Low Bidder | Lowest base bid | Regenerate from the backend after D2 instead of editing by hand |
| sample402.json headline figures | VoltSwitch €418,000 to €482,800, fully priced (per the handoff brief) | Regenerate | The live API above already returns €479,650 with one gap unpriced. Confirm which file the deployed build reads. |

## Table E: `/demo` route (only if it is kept, see decision 4)

`WB/components/ui/dashboard-with-collapsible-sidebar.tsx` and `WB/components/OverrideDrawer.tsx` (the drawer is used only here).

| Location | Current text | Proposed text | Reason |
| :-- | :-- | :-- | :-- |
| dashboard...:803 | Nominal low bid of €418,000 leveled to €482,800 (+€64,800 net adders) after I-FAT, DDP & busbar gap pricing. | VoltSwitch's base bid of €418,000 becomes an evaluated total of {lowEval} after pricing the factory test, delivery and busbar gaps. | Banned word. Hardcoded figure is wrong (decision 3). |
| dashboard...:802 | Commercial rank reversal flagged: VoltSwitch Industrial | Lowest bid is not the lowest cost: VoltSwitch Industrial | Plain |
| dashboard...:814-815 | Lowest Evaluated Cost Confirmed: Nordwerk Schaltanlagen GmbH / Quoted at €452,000 with 100% specification compliance and €0 omissions. Recommended for award. | Lowest evaluated total: Nordwerk Schaltanlagen GmbH / Base bid €452,000 with no cost adjustments. Recommended award. | Drops the unproven "100% compliance" claim |
| dashboard...:826-827 | Founder Cell Override recorded for PARAM-06 (Warranty Basis) / Priced by founder: freight €6,150 + 18 months storage x €175 = €9,300 adder with PyMuPDF citation hash. | Amount edited for PARAM-06 (warranty basis) / Freight €6,150 + 18 months storage × €175 = €9,300 cost adjustment. | "Founder" and "citation hash" are internal. Use the × sign. |
| dashboard...:1395 | ...with strict zero-hallucination validation before running boolean comparison operators. | ...Code converts units, then compares them. | Plain |
| dashboard...:1404 | ...Antova applies rate table additions directly to the vendor's base proposal, ensuring accurate procurement ranking. | ...Antova adds the rate-table cost to the base bid. The ranking uses the evaluated total. | Removes the "-ing" clause |
| dashboard...:1185 | View Reconciled Ledger → | View evaluated totals | No decorative arrow |
| dashboard...:272, :1266 | Please verify & seal the brief before downloading official TBE / Download Official TBE (.xlsx) | Sign off before you download the workbook. / Download workbook (.xlsx) | Term map |
| dashboard... (all other headings) | Title Case headings with "Deterministic", "Pint", "Leveled", "Seal" | Same treatment as Table B | Consistency |
| OverrideDrawer.tsx:111, :127 | Procurement Cost Override | Edit cost adjustment | Same action, same name |
| OverrideDrawer.tsx:130 | Line item scope gap adjustment & formal audit note | Change the amount for one line item and record why. | Plain |
| OverrideDrawer.tsx:148 | Audited Scope Item | Line item | Plain |
| OverrideDrawer.tsx:163-176 | Automated Rate / Calculated by engine / Proposed Value / {x} delta | Calculated amount / From the rate table / New amount / {x} difference | Plain |
| OverrideDrawer.tsx:184 | Override Adder Amount (€ EUR) | New cost adjustment (€) | Term map. "€ EUR" is doubled. |
| OverrideDrawer.tsx:202 | Replaces the engine's automated formula adder for this parameter cell. | Replaces the calculated amount for this line item. | Plain |
| OverrideDrawer.tsx:211 | Mandatory Engineering Justification | Reason (required) | Plain |
| OverrideDrawer.tsx:69 | Adder must be between €0.00 and €10,000,000.00 | Enter an amount from €0 to €10,000,000. | Plain |
| OverrideDrawer.tsx:73 | A mandatory engineering audit note (minimum 8 characters) is required for compliance traceability | Add a reason of at least 8 characters. | Plain |
| OverrideDrawer.tsx:93 (confirm dialog) | Revert this line item to the automated rate table adder? | Go back to the calculated amount for this line item? | Plain |
| OverrideDrawer.tsx:226 | This explanation is stamped permanently in the TBE export and procurement audit log. | Your reason is saved in the workbook and the decision history. | Plain |
| OverrideDrawer.tsx:234-237 | Governance & Audit Trail / Applying an override marks the tender brief as modified. Overridden cells are flagged in red within the True Job Cost Ledger and export workbooks. | Record of changes / Edited amounts show in red in the evaluated totals and in the workbook. | Plain |
| OverrideDrawer.tsx:259 / :288 | Revert to Auto / Save Override | Use calculated amount / Save amount | Plain |
| OverrideDrawer.tsx:86 / :100 | Failed to apply override / Failed to delete override | Couldn't save the amount. / Couldn't restore the calculated amount. | Plain |

## Dead code (no copy work needed)

- `WB/components/Header.tsx` is not rendered by any page; `AppShell` replaced it. Its strings (hardcoded tender titles with em dashes, "Packages Hub", "Seal tender to download official TBE workbook") never reach users. Recommend deleting the file.
- The local `workbench/` folder in this repo is an older copy of the workbench (handoff brief, section 1). None of the rows above target it.

## Implementation notes

- Tests that assert current strings and must change in the same commits: `antova_backend/web/e2e/3-review.spec.ts:48` ("Override adder (€)"), `antova_backend/web/src/content/copy.test.ts`, backend tests asserting badge and open-item text (`antova_ingest/tests/test_pipeline.py`, `test_db.py`, `test_amendments.py`, `test_dev_tools.py`, `test_antova_briefs.py`, `test_antova_review.py`), fixtures `web/src/test/fixtures/*.json`, `web/src/generated/sample-brief.json`, `samples/TENDER-2026-417/expected.json`.
- The backend lives in two repos (`antova-yc` here and the copy in `antova-backend-demo/antova_backend/backend/`). Apply D1 and D2 here, then sync the copy, as the handoff brief recommends.
- Workbench strings are scattered across components with no copy file. Suggest moving them into `WB/content/copy.ts`, mirroring the brief app, so the next audit is one file.
- Suggested order: Table A first, then B1 to B4, then D, then the rest.
