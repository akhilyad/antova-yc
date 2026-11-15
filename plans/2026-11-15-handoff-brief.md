# Antova Handoff Brief (v2, 2026-11-15)

Sources: `akhilyad/antova-backend-demo` @ `dc2d9e9`, `akhilyad/antova-yc` @ `341f03c`, local `feat/surface-coverage` @ `8f6091c`. Read, run and cross-checked. No code changed.

v2 replaces v1. Section 7 lists what v1 got wrong.

---

## 0. The five things that matter before a live demo

1. **The workbench's offline sample shows the wrong headline.** `frontend/src/lib/sample402.json` has VoltSwitch at €418,000 → **€482,800, fully priced**. The current engine gives **€479,650, a lower bound** (warranty unpriced under rate table v2; `test_sample_pack.py:115`, `test_pipeline.py:127`, and the README). Live and replay disagree on the one number the pitch rests on.
2. **The workbench can't complete a review.** `submitReviewDecision()` exists in `lib/api.ts` but no screen calls it, and there's no client for `GET …/reviews`. A tender with open flags can never reach Verify from the workbench.
3. **The workbench invents data where the backend has none.** `normalizeBrief()` turns a missing adder into `0`, a missing `price_status` into `PRICED`, a missing cell into `MANUAL_INSPECTION_REQUIRED`, `confidence` into `1.0`, `criticality` into `MANDATORY`. The product rule is "an unknown is never counted as zero"; this layer breaks it silently.
4. **Replay is silent and pretends to write.** When the backend is slow (>4 s) or 5xx, the proxy serves stubs: `verify` returns VERIFIED, overrides return 204, nothing is saved. The audit page also falls back to the 402 sample for any ID containing `402`.
5. **Two design systems.** The Vite brief app follows "Footnote" (`web/DESIGN.md`: warm paper, Geist, one oxblood accent, colour only when something is wrong, no green). The workbench is navy/bronze/zinc with green success and system Inter. A demo that moves from `/review/*` (Vite) to `/audit/*` (Next) changes look mid-click.

---

## 1. System architecture and ownership

### Four front-end surfaces, one backend (copied twice)

| Surface | Where | Stack | State |
| :-- | :-- | :-- | :-- |
| FastAPI backend + pipeline | `antova-yc/antova_backend/` | Python, Pydantic, Pint, SQLite | Source of truth. `antova-backend-demo/antova_backend/backend/` is a copy (only `.gitignore`, `requirements.txt` and two tests differ) |
| **Brief + review app** | `antova_backend/web/` (both repos) | Vite, React 19, zod, TanStack Query, decimal.js | Mature. zod schemas in `src/api/schemas.ts`, domain logic in `src/domain/` (money, ranking, leveling, verdict, contradiction, replay), Playwright + axe e2e, prerendered. Serves `/b/{share_id}` and `/review/*` on Vercel |
| **Workbench** | `antova-backend-demo/antova_backend/frontend/` | Next 15, Tailwind 3 | The UI being polished. Hand-written types, no runtime validation, own fallback layer. Serves everything else on Vercel |
| Landing | `antova-yc/src/` | Vite | Marketing |
| Local `workbench/` | this checkout, untracked | Next | Older copy of the workbench: no governance/reports/settings pages, no `/api` proxy, fails `tsc` on `globals.css`. Delete it |

### Wire

```
Browser ─▶ Next workbench ─▶ /api/[[...path]] (route.ts) ─▶ BACKEND_URL (FastAPI)
                                │ timeout 4 s or upstream ≥500
                                └─▶ getMockResponse(): sample402.json + stubs
Browser ─▶ /b/*, /review/* ─▶ Vite app ─▶ /api/* (FastAPI directly)
```

Routes (identical in both repos):

- `GET /api/health`, `POST /api/jobs/ingest` → `{job_id, status:"QUEUED"}`, `GET /api/jobs/{id}`
- `/api/review/*`, token-guarded; the reviewer recorded is the token's principal, never a client-sent name: `tenders`, `tenders/{id}`, `files`, `manifest`, `evaluate`, `overrides/{param}/{vendor}` (409 while VERIFIED), `reviews` (GET history, PUT decision), `verify`, `withdraw`, `evidence/{param}/{source}/{which}.png`, `export-tbe.xlsx` (409 until VERIFIED), `clarifications.csv|xlsx?vendor=`
- `/api/briefs/{share_id}` (+ `evaluate`, `evidence`), public, run cost stripped

### Ownership decisions (recommended)

1. **`antova-yc` owns the backend.** Delete `antova-backend-demo/antova_backend/backend/`; the demo repo deploys `frontend/` and points `BACKEND_URL` at a deployed `antova-yc` API.
2. **Give each UI one job.** The Vite app keeps the buyer-facing brief (`/b/{share_id}`) and its print view. The workbench becomes the operator console: portfolio, ingest, the review gate, overrides, exports. The workbench links to `/b/{share_id}` for "view as the buyer sees it" instead of rebuilding the brief.
3. **One contract package.** Lift `web/src/api/schemas.ts` and the pure `web/src/domain/*.ts` modules into a shared package (`packages/antova-contract`) that both UIs import. The workbench drops `lib/types.ts` and `normalizeBrief()`.

---

## 2. What the backend computes vs. what the workbench shows

| Backend output | Workbench types | Workbench renders | Notes |
| :-- | :-- | :-- | :-- |
| Compliance status, checks, primary citation, evidence PNG | ✓ | ✓ | |
| `financials`: true evaluated price, itemized adders | ✓ | ✓ | |
| `price_status`, low/high totals and adders | ✓ | ✗ | Normalizer defaults `price_status` to `PRICED`; the lower bound can't show |
| `secondary_citation` | ✓ | partly | |
| `conflict_note` | ✓ | ✗ | |
| Cell `flags[]` (incl. `EXPOSURE_GAP`) | ✓ | ✗ | These are what the review gate counts |
| `review` summary | ✓ | ✗ | |
| Review decisions (PUT) and history (GET) | PUT only | ✗ | No caller; no GET client |
| `open_items` (5 kinds incl. `project_field`) | ✓ | count only | |
| `tender.project` baseline | `Record<string, any>` | ✗ | Reviewed via `PUT …/reviews/PROJECT.<field>/-` |
| `tender.findings` register | `Record<string, any>` (backend sends a **list**) | ✗ | Neither UI renders it; Vite's zod `Tender` strips it. TBE workbook does include a findings sheet |
| `ingest_stats.warnings` (incl. bid-outlier) | ✓ | ✗ | Outlier is a free-text warning, not an open item |
| Vendor FX fields | ✓ | ✗ | |
| `stage_log` | ✓ | ✗ | |
| Verify 409 bodies (`open_items[]`, `code:"run_changed"`) | — | ✗ | Client throws `Error(detail)`; the list and code are lost |

---

## 3. Defect register (with evidence)

| # | Sev | Where | Defect | Fix |
| :-- | :-- | :-- | :-- | :-- |
| D1 | Critical | `frontend/src/lib/sample402.json` | Stale: vendor-b €482,800 PRICED, no `findings`/`project`; engine gives €479,650 lower bound | Regenerate from the live API with a script like `web/scripts/snapshot-sample.ts` (it refuses to write invented data); CI test compares the snapshot's totals with `test_sample_pack` |
| D2 | Critical | `components/*`, `lib/api.ts` | Review decisions unreachable: `submitReviewDecision` has no caller; no reviews GET | Review panel per flagged cell: approve / override / reject with required note; history list |
| D3 | Critical | `lib/api.ts:56` `normalizeBrief` | Fabricates 0, `PRICED`, `COMPLIANT`, `MANDATORY`, `1.0`, `MV_GIS_SWITCHGEAR`, and cells for vendors the backend omitted | Replace with zod parse of the shared schema; on failure show an error, never a guess |
| D4 | Critical | `app/api/[[...path]]/route.ts` | Silent replay, fake `verify`/override/withdraw success, writes fall back too | Header `x-antova-source`; replay read-only (503 on writes except `evaluate`); visible banner |
| D5 | High | `app/audit/[tenderId]/page.tsx:121` | Any tender ID containing `402` falls back to the sample on error | Remove; one explicit "Open offline sample" action, labelled |
| D6 | High | `lib/api.ts:455` `verifyTender` | Drops `open_items[]` and `run_changed` from 409 | Typed `ApiError` with `status`, `code`, `open_items`; UI lists blockers and offers reload on `run_changed` |
| D7 | High | `lib/api.ts:15` | `NEXT_PUBLIC_REVIEW_TOKEN` default `dev-review-token-antova` ships in the bundle; since the backend signs decisions with the token's principal, every visitor signs as the same reviewer | Token server-side in the proxy; per-reviewer tokens or session login; show the signed-in reviewer |
| D8 | High | `route.ts` stubs | Shape drift: `open_items_count`/`created_at`/no `share_id`; job `progress` | Generate stubs from recorded responses only |
| D9 | Medium | `lib/types.ts` | `findings` and `project` typed `any`; no `StartIngestResponse` (`QUEUED`) type | From the shared schema |
| D10 | Medium | backend `pipeline.py:111` | Bid-outlier warning only in `ingest_stats.warnings` (string) | Structured warning with code; optionally an `open_item` so it reaches the gate |
| D11 | Medium | workbench styling | ~1,900 raw `zinc-*` utilities, 237 hex literals, Tailwind `antova.*` colours hardcoded, 852 `dark:` overrides; green success contradicts Footnote | See Track 1 P2 |
| D12 | Low | `components/ui/` | 1,751-line `dashboard-with-collapsible-sidebar.tsx`, duplicated at `frontend/components/ui/` | Split; delete duplicate |
| D13 | Low | dashboard | `totalVolume` sums `true_evaluated_price ?? 0` (unknown as zero) | Exclude nulls and say "n of m priced" |

---

## 4. Track 1: UI (`antova-backend-demo/antova_backend/frontend`)

### P0: data integrity (blocks the demo)

- [ ] D1 regenerate the sample; add a snapshot test.
- [ ] D3 replace `normalizeBrief` with the shared zod schema.
- [ ] D4/D5 replay header, banner, read-only; delete the `402` fallback.
- [ ] D7 move the token into the proxy.
- [ ] D6 typed API errors; verify shows blockers.

### P1: complete the operator workflow

- [ ] **Review gate rail** on the audit page: counts from `brief.review` (open / stale / rejected / reviewed), the list from `open_items` grouped by kind, each row jumps to its cell. Verify is disabled with the reason until `open_items` is empty; on success show reviewer, time and share link.
- [ ] **Cell review panel** (D2): evidence crop + quote, flags with codes, decision buttons with required note, history from `GET …/reviews`. Stale state explains that the evidence changed since approval.
- [ ] **Project baseline card**: each field with value, source, note and review state; confirm via `PROJECT.<field>/-`.
- [ ] **Findings register tab**: grouped by vendor, ordered critical → major → minor (backend already sorts), kind label (deviation, conflict, omitted, unstated, claim_only, schedule_unassessed, critical_path_breach), citations into the evidence modal, money split into **Adder** (in True Job Cost) and **Exposure** (risk, never added) with formula and named inputs. `amount: null` shows the reason, never €0.
- [ ] **Price truth**: `€479,650+` with "lower bound · 1 unpriced" when `price_status` says so; low–high range under totals; rank reversal (headline rank → evaluated rank) at the top of the audit page. Reuse `domain/ranking.ts` and `domain/money.ts`.
- [ ] **Conflicts**: both quotes side by side with `conflict_note`.
- [ ] **Warnings strip** from `ingest_stats.warnings`, outlier first; no-text pages per document.
- [ ] **FX line** under non-EUR headlines.
- [ ] **Exports** grouped: TBE workbook (disabled with reason until verified), clarifications CSV/XLSX for all or one vendor (`?vendor=`), and a "View buyer brief" link to `/b/{share_id}`.
- [ ] **Run provenance** footer: rate table version + effective date, run id/fingerprint, sweep recall, stage timeline.

### P2: visual system (institutional, both themes)

**Decision needed (recommendation first):** adopt Footnote in the workbench, adding a dark variant. The alternative is a separate "operator" skin, which keeps two brands in one demo.

- [ ] **Tokens**: import Footnote names (`--paper --surface --band --ink --ink-2 --ink-3 --rule --edge --ox --ox-wash`) and map Tailwind colours to them; ban raw `zinc-*`/hex with an ESLint rule. Starting dark values to test (check contrast before adopting): paper `#141210`, surface `#1C1916`, band `#24201B`, ink `#EEE9E0`, ink-2 `#C4BCB0`, ink-3 `#A39A8D`, rule `#2E2923`, edge `#7A7266`, ox `#E07A6A`, ox-wash `#3A1F1A`.
- [ ] **Colour rules from Footnote**: colour only marks what's wrong; "Verified" is ink, not green; unpriced uses a hatch + `€?`, not a hue.
- [ ] **Type**: Geist Variable self-hosted (`@fontsource-variable/geist`, as the Vite app does) or `next/font/local`; system mono for quotes, IDs, hashes; Footnote scale 12–60; tabular lining figures on `body`; nothing under 12 px.
- [ ] **One `StatusTag`** with Footnote glyphs: Meets spec ✓ · Gap ✗ · Contradiction ⇄ · Missing ∅ · Exceeds spec ↑ · Unpriced €? · Needs inspection !
- [ ] **Money format** `€482,800`, adders `+€18,500`, superseded prices struck.
- [ ] **Density**: 32–36 px rows; sticky header and first column on the matrix; real `<table>` with `th scope`; no clickable `div`s.
- [ ] **Theme toggle** follows system by default (no `data-theme` → `prefers-color-scheme`), explicit choice stored; no flash (inline script in `layout.tsx`).
- [ ] **Motion** from Footnote duration tokens, all zero under reduced motion.
- [ ] Split the dashboard monolith; delete duplicate `components/ui/`.
- [ ] Print: the workbench links to the Vite print view rather than keeping its own `/print`.

---

## 5. Track 2: backend (`antova-yc/antova_backend`)

### P0

- [ ] One backend: remove the demo copy; deploy the API; CORS for the workbench origin.
- [ ] Per-reviewer tokens (or session auth) so the decision log means something in a demo with more than one person; keep "reviewer comes from the principal".
- [ ] Publish a fixtures endpoint or script output for 402/417/418 from the current engine; the UIs' samples come only from it.

### P1: contract

- [ ] Add JSON Schema for the domain parts (`tender`, `financials`, `findings`, `project`) to OpenAPI. Keep the engine's dicts; describe them so the shared zod schema can be checked against OpenAPI in CI instead of drifting.
- [ ] `schema_version` on every brief; the client refuses unknown majors.
- [ ] `ranking` block in the brief (headline order, evaluated order, `is_lower_bound`, unpriced count), so neither UI re-derives it differently. Today `web/src/domain/ranking.ts` does it client-side.
- [ ] Structured warnings: `[{code:"price_outlier", vendor_id, value, median, message}]` alongside the strings.
- [ ] Tender list adds `client`, `equipment_class`, `vendor_count`, `lowest_evaluated`, `final`.
- [ ] Document the two job enums (`QUEUED` on accept, then `PENDING|PROCESSING|COMPLETED|FAILED`) in OpenAPI.

### P2: pipeline

- [ ] `total_exposure_eur` per vendor in `financials`, kept separate from adders.
- [ ] Outlier as an `open_item` kind (or a cell flag) if the buyer should sign it off.
- [ ] `GET /api/rates/{version}` for the rate line behind each adder.
- [ ] `GET /api/classes` from the class-keyed ontology for the ingest form.
- [ ] Clarifications: check the extended-class kinds (schedule, LD, liability) produce questions; today the kinds are `PRICE MANUAL CONFLICT DEVIATION OMITTED EVIDENCE BASIS FLAG`.

---

## 6. Integration contract and sign-off

### Payloads the workbench depends on (from the code)

```ts
// POST /api/jobs/ingest
{ job_id: string; status: 'QUEUED' }
// GET /api/jobs/{id}
{ job_id; status: 'PENDING'|'PROCESSING'|'COMPLETED'|'FAILED'; progress_pct: number; stage: string;
  created_at: string; completed_at: string|null; result?: IngestStats & {stage_log}; error?: string|null }

// GET /api/review/tenders
{ tender_id; title; status:'DRAFT'|'VERIFIED'; share_id; ingested_at; verified_at: string|null; open_items: number }[]

// GET /api/review/tenders/{id}  -> Brief
{ share_id; status; verified_at; synthetic; ingest_mode; ingest_stats; stage_log; documents;
  tender: Tender & { project?: Record<string,{value;source;note?}>; findings?: Finding[] };  // absent when the class has none
  financials: VendorFinancials[]; overrides; open_items: OpenItem[];
  review: { open_flags; stale; rejected; reviewed; final } }

type OpenItem = { kind:'unpriced'|'needs_inspection'|'missing_price'|'review_flag'|'project_field';
                  vendor_id: string /* '-' for project */; param_id: string|null /* 'PROJECT.<field>' */; message }

type Finding = { vendor_id; param_id;
  kind: 'deviation'|'conflict'|'omitted'|'unstated'|'claim_only'|'schedule_unassessed'|'critical_path_breach';
  severity: 'critical'|'major'|'minor'; message; citations: Citation[];
  money: { basis:'adder'|'exposure'; kind; amount: string|null /* "1234.00" */; formula; reason;
           lines: {label,qty,unit,rate,driver}[]; inputs: {name,value,source}[] }[] }

// PUT /api/review/tenders/{id}/reviews/{param}/{vendor}   body { action:'approve'|'override'|'reject'; note: string(1..1000) }
//   -> ReviewRow & { state:'none'|'open'|'reviewed'|'stale'|'rejected' }   409 while VERIFIED
// POST /api/review/tenders/{id}/evaluate   body { copper_multiplier 0.5–2.5; site_labor_rate? 20–200; freight_multiplier 0.5–2.5 }
// POST /api/review/tenders/{id}/verify     body { run_id? }
//   200 { share_id, verified_at }
//   409 { detail:'Open items remain', open_items: OpenItem[] }
//   409 { detail, code:'run_changed' }
// Proxy adds to every response:  x-antova-source: live | replay
```

### Demo script and sign-off checks

1. `pytest` in `antova_backend`: 1,099 passed in 5 min 10 s on the local checkout (15 Nov). The demo repo's README badge still says 175.
2. Workbench `tsc --noEmit` and `next build` green (tsc passes today on the demo repo's copy).
3. Portfolio lists 402/417/418 with real open-item counts; the source header says `live`.
4. **402 audit**: VoltSwitch €418,000 headline, lowest; evaluated `€479,650+` (lower bound, warranty unpriced); ranked last. Same numbers in replay mode.
5. Open a flagged cell: crop, quote, flags; approve with a note; gate count drops; history shows the token's reviewer.
6. Verify with items open → blockers listed. Clear them → Verify succeeds; overrides and reviews lock (409). Withdraw → editable again.
7. Re-ingest in another tab, then Verify → "reloaded since you opened it" with a reload action.
8. Override an adder → totals and rank update; remove → restored.
9. TBE workbook refused before verify, downloads after (includes findings sheet for the extended class); clarifications per vendor download.
10. Extended-class tender: findings tab shows critical → minor, exposures apart from adders, `amount: null` shows its reason; project baseline needs confirmation before verify.
11. Stop the backend: replay banner within 4 s, every write disabled, numbers unchanged from live.
12. Both themes on portfolio, audit, findings, review panel, evidence modal: no raw zinc or hex in `.tsx` (lint rule), focus ring visible, axe clean (reuse the Vite app's `@axe-core/playwright` setup).

---

## 7. Corrections to v1

- **`QUEUED` is not a bug.** It is the accept response (`StartIngestResponse` in `web/src/api/schemas.ts`); polling uses the four-state enum. v1 said to add it to `JobStatus`.
- **The backend already has Pydantic response models** (`antova_models.py`). Domain parts are `dict[str, Any]` on purpose and validated by the Vite client's zod schemas. The fix is to describe them, not to rebuild them.
- **Verify already returns 409 with `open_items`**, and guards against re-ingest with `run_id` (`run_changed`). The gap is in the workbench client.
- **The TBE workbook already includes the findings register.**
- **Severity literals** are `critical | major | minor` (from criticality; `claim_only` is always minor; `critical_path_breach` always critical).
- **Demo headline**: v1 assumed the workbench would show €479,650+. Its bundled sample shows €482,800.
- **Visual direction**: v1 proposed a navy dark theme and green success. That contradicts the project's own design system; v2 aligns with Footnote.

## 8. Decisions for Akhil

1. Workbench adopts Footnote (recommended) or keeps a separate operator skin.
2. Workbench as operator console linking to the Vite brief (recommended), or a full replacement for the Vite review pages.
3. Shared contract package now (recommended) or OpenAPI-generated types later.
4. Bid-outlier warning: informational, or something a reviewer must sign off.
