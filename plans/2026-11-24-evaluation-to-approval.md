# Evaluation to Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Python audit engine so that, for one vendor's offer, it reads the commercial terms, checks them against the vendor's framework agreement and the EPC's owner-contract terms, compares price to the frozen budget, and produces a cited contract approval sheet (CAS) as JSON, DOCX and PDF behind its own sign-off gate.

**Architecture:** A second, equipment-agnostic ontology (`CT-01`…`CT-17`) runs through the existing locate → verify → parse pipeline for two new document roles. A new pipeline stage resolves each term across offer / framework agreement / owner contract, runs the checks and gap calculations in Decimal code, and emits contract findings. New tables hold counterparties, projects, contract terms, a per-vendor contract gate and finding dispositions. The CAS is assembled from stored results only; no model writes any of it.

**Tech Stack:** Python 3, FastAPI 0.135.3, SQLAlchemy 2.0.48 on SQLite, Pydantic, reportlab 5.1.0 (PDF), python-docx 1.1.2 (DOCX, new), pytest.

**Spec:** `specs/2026-11-24-evaluation-to-approval-design.md` (approved 2026-11-24). Background: `plans/2026-11-24-product-strengthening-brainstorm.md`.

**Scope of this plan:** the backend in `antova_backend/` of this repo only. Three follow-on plans are needed and are *not* covered here: (1) workbench UI (Contract tab, CAS page, upload slots) in `antova-backend-demo`, which must first confirm the "no invented zeros / no silent fake writes" fixes from the 2026-11-15 handoff brief; (2) hosting the Python pipeline for the pilot; (3) per-customer DOCX template mapping.

All commands run from `antova_backend/` with `python -m pytest …` (verified to work on this machine; `py` does not have the dependencies).

## Global Constraints

- The model only locates: it returns page and verbatim words. Every value, comparison, conversion and amount is computed in code. No model output is written into the CAS, a risk line or a verdict.
- Money and rates are `Decimal`. An unknown is `None` with a reason string; it is never shown as `0`, blank or compliant. A true zero is returned only when every input is known.
- Every CAS field is in exactly one state: `cited`, `computed`, `inherited` or `missing`.
- Contract documents (`framework_agreement`, `owner_contract`) are never sent to a model provider unless `ANTOVA_ZERO_RETENTION_REF` is set, or the tender manifest has `"synthetic": true`.
- All new fixtures are synthetic: invented company names, numbers and clause wording. No real customer or former-employer document, name, price or term enters the repo.
- Attribution comes from the request's token (`antova_auth`), never from a name in a request body.
- Contract findings and dispositions never change the tender-level TBE status (`TENDER_STATUSES`) or its open items.
- `SCHEMA_VERSION` goes from 10 to 11 with an upgrade function; existing databases upgrade in place.
- Commit after every task on branch `feat/evaluation-to-approval`. Never push without the founder's explicit approval.

## Review Focus

1. **Currency mismatch inside one comparison.** A framework agreement states a liability cap in EUR; the offer is in GBP. Expect: converted with the buyer-entered rate (conversion shown), or `None` with "no GBP→EUR rate entered". Pinned in Task 6.
2. **Amendment order unknown.** An amendment is undated, or dated on/before the agreement it amends. Expect: state `conflict`, both citations, flag `amendment_order_unknown`, no silent pick. Pinned in Task 5.
3. **LD rate per week vs per day.** One document says "1% per week", the other "0.15% per day". Expect: compared on a common basis with the conversion in the formula, no rounding before comparison. Pinned in Task 6.
4. **Different legal names for one supplier.** The framework agreement is with a parent; the offer comes from a subsidiary. Expect: never auto-linked; a name match is only a suggestion. Pinned in Task 9.
5. **Re-ingest after dispositions.** Evidence behind a disposed finding changes. Expect: the disposition is shown as stale and the contract gate reports it as an open item. Pinned in Task 11.

---

### Task 0: Branch and baseline

**Files:**
- Modify: `antova_backend/antova_requirements.txt`

- [ ] **Step 1:** `git switch main && git switch -c feat/evaluation-to-approval`. The untracked spec and plan files come along.
- [ ] **Step 2:** Add `python-docx==1.1.2` to `antova_requirements.txt`; `python -m pip install python-docx==1.1.2`.
- [ ] **Step 3:** Run `python -m pytest -q`. Expected: all pass. Record the count in the commit message.
- [ ] **Step 4:** Commit spec, brainstorm, this plan and the requirements change: `git commit -m "docs: evaluation-to-approval spec and plan; add python-docx"`.

---

### Task 1: Contract field kinds

**Files:**
- Modify: `antova_ingest/types.py` (`FieldKind`), `antova_ingest/values.py` (`parse_field`, `check`), `antova_ingest/ontology.py` (`ORDINALS`)
- Test: `antova_ingest/tests/test_values_contract.py`

**Interfaces:**
- Produces:
  - `FieldKind` gains `"rate_per_period"`, `"event"`, `"milestone_list"`, `"law"`, `"forum"`.
  - `rate_per_period` value: `tuple[Decimal, Literal["day", "week"]]`, display e.g. `"0.5% per day"`.
  - `event` value: one of `ORDINALS["event"] = ("notice_to_proceed", "delivery", "cold_commissioning", "hot_commissioning", "commissioning", "cod", "pac", "fac")` (earliest first).
  - `milestone_list` value: `tuple[Milestone, ...]`, `@dataclass(frozen=True) class Milestone: percent: Decimal; event: str; days_after: int | None; condition: str | None` (in `values.py`).
  - `law` value: normalized lower-case jurisdiction (`"german"`, `"english"`, `"swiss"`, `"new_york"`, …). `forum` value: `"dis"`, `"icc"`, `"lcia"`, or `"courts:<city>"`.
  - `values.per_week(value: tuple[Decimal, str]) -> Decimal`: the rate per week (day × 7), exact.

- [ ] **Step 1: Write the failing tests**

```python
def test_rate_per_day():
    v = parse_field("rate_per_period", "0.5 % of the Price for each Day of delay", "ld_rate")
    assert v.value == (Decimal("0.5"), "day") and v.display == "0.5% per day"

def test_rate_per_week():
    v = parse_field("rate_per_period", "liquidated damages of 1% per week of delay", "ld_rate")
    assert v.value == (Decimal("1"), "week")

def test_per_week_is_exact():
    assert per_week((Decimal("0.15"), "day")) == Decimal("1.05")

def test_event_start_points():
    assert parse_field("event", "warranty starts on the delivery date", "start").value == "delivery"
    assert parse_field("event", "from Provisional Acceptance (PAC)", "start").value == "pac"

def test_milestones_parse_and_sum():
    v = parse_field("milestone_list",
        "85% within 30 days after delivery; 15% within 240 days after delivery against a warranty bond", "payment")
    assert [m.percent for m in v.value] == [Decimal("85"), Decimal("15")]
    assert v.value[1].days_after == 240 and v.value[1].condition == "warranty bond"

def test_law_and_forum():
    assert parse_field("law", "governed by the laws of Germany", "law").value == "german"
    assert parse_field("forum", "German Arbitration Institute (DIS)", "forum").value == "dis"

def test_event_check_later_start_is_better():
    f = FieldDef("start", "event", "ord", "Warranty start")
    assert check(f, fv("event", "delivery"), fv("event", "pac"))[0] is True
    assert check(f, fv("event", "pac"), fv("event", "delivery"))[0] is False

def test_unparseable_rate_is_none():
    assert parse_field("rate_per_period", "liquidated damages as agreed", "ld_rate") is None
```

(`fv(kind, value)` is a local helper building a `FieldValue`.)

- [ ] **Step 2:** `python -m pytest antova_ingest/tests/test_values_contract.py -v` → FAIL (unknown kinds).
- [ ] **Step 3:** Implement the five parsers in `values.py` and wire them into `parse_field`. Extend `check` for `rate_per_period` (compare `per_week`, operator `ge`), `event` (`ord` over `ORDINALS["event"]`), `law`/`forum` (`eq`). `milestone_list` has no `check` branch; it's compared in Task 6. Add `ORDINALS["event"]`.
- [ ] **Step 4:** Run the new file and `python -m pytest antova_ingest/tests/test_values.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(values): contract field kinds`.

---

### Task 2: Contract-terms ontology and prompt

**Files:**
- Create: `antova_ingest/contract_ontology.py`
- Modify: `antova_ingest/extract.py` (`build_system_prompt`, new `system_prompt_for`), `antova_ingest/artifacts.py` (`run_fingerprint` gains `contract_ontology` hash)
- Test: `antova_ingest/tests/test_contract_ontology.py`

**Interfaces:**
- Produces:
  - `CONTRACT_PARAMETERS: tuple[ParameterDef, ...]` with ids `CT-01`…`CT-17` (CT-01…CT-15 exactly as the spec table 4.2; CT-16 "Incorporation of the framework agreement" with one `scope` field `fa_reference`; CT-17 "Order of precedence" with one `scope` field `order`).
  - `CONTRACT_ROLES: frozenset[str] = frozenset({"framework_agreement", "owner_contract"})`.
  - `CONTRACT_PARAMS_BY_ID: dict[str, ParameterDef]`.
  - `TBE_EQUIVALENT: dict[str, str] = {"CT-01": "PARAM-10", "CT-02": "PARAM-11", "CT-12": "PARAM-06"}` (for the CAS's MR column).
  - `LEGAL_TERMS: frozenset[str] = frozenset({"CT-02", "CT-08", "CT-09", "CT-14"})`.
  - `extract.system_prompt_for(role: str, equipment_class: str) -> str`: the contract prompt (built from `CONTRACT_PARAMETERS`) for `vendor_bid` documents' contract pass and for contract roles; the existing equipment prompt otherwise.
  - `contract_ontology_hash() -> str` in `artifacts.py`.

Plausible ranges to pin: LD rate 0.01%–5% per day; LD cap 1%–100%; warranty 6–120 months; bond percent 1%–30%; design life 10–50 years; NTP deadline 1–60 months.

- [ ] **Step 1: Failing tests:** ids are `CT-01`…`CT-17` in order and unique; every field kind is a valid `FieldKind`; `CONTRACT_PARAMS_BY_ID["CT-01"].fields` names are `("ld_rate", "ld_cap", "ld_triggers")`; `system_prompt_for("framework_agreement", "mv_gis_switchgear")` contains every CT name and none of the PARAM names; `system_prompt_for("mr_spec", …)` equals `build_system_prompt(…)`; a 0.001%-per-day LD rate is not `plausible`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Criticality: HIGH for CT-01…CT-07 and CT-16, COMMERCIAL for the rest.
- [ ] **Step 4:** Run the new file and `test_ontology_registry.py` → PASS.
- [ ] **Step 5:** Commit `feat(ontology): contract-terms ontology CT-01..CT-17`.

---

### Task 3: Manifest and baseline types

**Files:**
- Modify: `antova_ingest/types.py`
- Test: `antova_ingest/tests/test_manifest_contract.py`

**Interfaces:**
- Produces (Pydantic, in `types.py`):
  - `DocumentEntry.role: Literal["mr_spec", "vendor_bid", "framework_agreement", "owner_contract"]`; new optional fields `counterparty_id: str | None`, `amends: str | None` (file name of the document it amends), `dated: date | None`.
  - `BaselineMoney(amount: Decimal, currency: str, source="buyer_entered", note: str | None)`; currency is 3 upper-case letters.
  - `BaselineFx(from_currency: str, to_currency: str, rate: Decimal, dated: date, source="buyer_entered")`; `rate > 0`.
  - `OwnerTerms(contract_value: BaselineMoney | None, ld_rate: Decimal | None, ld_rate_basis: Literal["percent_per_day", "percent_per_week", "money_per_day", "money_per_week"] | None, ld_cap: Decimal | None, ld_cap_basis: Literal["percent", "money"] | None, package_ld_share_pct: Decimal | None, liability_cap: BaselineMoney | None, warranty_months: int | None, warranty_start: str | None, law: str | None, source="buyer_entered")`.
  - `ProjectBaseline` gains `frozen_budget: BaselineMoney | None`, `fx: list[BaselineFx] = []`, `delay_scenario_days: BaselineNumber | None`, `owner_terms: OwnerTerms | None`, `procurement_negotiated: bool | None`, `signing_before_ntp: bool | None`, `project_id: str | None`.
- Validation rules: `framework_agreement` requires `counterparty_id` and no `vendor_id`; `owner_contract` has neither; `amends` must name another entry of the same role and counterparty; `warranty_start` must be in `ORDINALS["event"]`; `package_ld_share_pct` within 0–100.

- [ ] **Step 1: Failing tests:** one valid manifest with all four roles loads; each validation rule above rejects with a message naming the field; an old 402 manifest still loads unchanged; `ProjectBaseline` with only the old fields still validates.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run with `test_project_baseline.py` → PASS.
- [ ] **Step 5:** Commit `feat(types): contract document roles and owner/budget baseline`.

---

### Task 4: Locating contract documents, behind the zero-retention guard

**Files:**
- Modify: `antova_ingest/config.py`, `antova_ingest/extract.py` (`ModelLocator`, `ResponseStore.key_for`), `antova_ingest/chat_locator.py`, `antova_ingest/answer_key.py`, `antova_ingest/pipeline.py` (`stage_parsed`, `stage_located`, `stage_verified`), `antova_ingest/artifacts.py`
- Test: `antova_ingest/tests/test_contract_locate.py`

**Interfaces:**
- Consumes: `CONTRACT_ROLES`, `system_prompt_for` (Task 2); new roles (Task 3).
- Produces:
  - `config.zero_retention_reference() -> str | None` (reads `ANTOVA_ZERO_RETENTION_REF`, stripped; empty means unset).
  - Locator attribute `sends_text: bool`: `True` on `ModelLocator` and `ChatCompletionsLocator`, `False` on `AnswerKeyLocator` and test fakes.
  - Vendor bids get a second locate pass with the contract prompt; contract-role documents get only that pass. Located answers carry the parameter ids they answer, so CT and PARAM evidence never mix.
  - Fingerprint `parts` gain `"locate_provider"`, `"locate_model"`, `"zero_retention_ref"` (empty string when unset), `"contract_ontology"`.
- Guard rule (exact warning text): when `entry.role in CONTRACT_ROLES and locator.sends_text and not manifest.synthetic and zero_retention_reference() is None`, the document is skipped and `IngestResult.warnings` gets `f"{entry.file}: not read. Contract documents go only to a model provider with a zero-retention agreement; set ANTOVA_ZERO_RETENTION_REF."`. The contract pass on vendor bids is skipped the same way and for the same reason.

- [ ] **Step 1: Failing tests** (use a recording fake locator with `sends_text = True`):
  - `test_contract_docs_refused_without_reference`: FA not passed to the locator; warning text exact; bids and MR still located.
  - `test_contract_docs_allowed_with_reference` (monkeypatch env `ANTOVA_ZERO_RETENTION_REF="DPA-2026-11"`): FA located; fingerprint `zero_retention_ref == "DPA-2026-11"`.
  - `test_synthetic_manifest_bypasses_guard`.
  - `test_answer_key_locator_never_guarded` (`sends_text` False).
  - `test_store_keys_differ_by_prompt`: the same PDF under `vendor_bid` equipment pass and contract pass gets two different `ResponseStore` keys.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run the new file, `test_pipeline.py`, `test_staged_run.py`, `test_chat_locator.py` → PASS (existing packs have no contract documents, so their fingerprints only gain the new keys).
- [ ] **Step 5:** Commit `feat(pipeline): locate contract terms behind zero-retention guard`.

---

### Task 5: Resolving each term across offer, framework agreement and owner contract

**Files:**
- Create: `antova_ingest/contract_terms.py`
- Test: `antova_ingest/tests/test_contract_terms.py`

**Interfaces:**
- Consumes: verified `Evidence` per document (pipeline), `merge_requirement`-style parsing via `values.parse_field`, `OwnerTerms` (Task 3).
- Produces:

```python
@dataclass(frozen=True)
class TermSource:
    kind: Literal["document", "buyer_entered", "inherited"]
    values: dict[str, FieldValue]
    citations: tuple[Citation, ...]      # inherited: (incorporation clause, FA clause)

@dataclass(frozen=True)
class ContractTerm:
    param_id: str
    vendor_id: str
    offered: TermSource | None
    fa: TermSource | None
    owner: TermSource | None
    state: Literal["cited", "inherited", "missing", "conflict"]
    flags: tuple[dict, ...]               # precedence.flag(code, message) dicts

def resolve_contract_terms(manifest: TenderManifest,
                           evidence: Mapping[str, list[Evidence]],   # file name -> verified CT evidence
                           ) -> dict[tuple[str, str], ContractTerm]: ...
```

Rules:
- Vendor ↔ FA link is the manifest's `counterparty_id` on both entries; never by name.
- Within one counterparty, an amendment dated after its FA governs the fields it states; the FA governs the rest; both citations are kept. Undated, or dated on/before the FA → `state="conflict"`, flag `amendment_order_unknown`.
- Offer silent on a term: if CT-16 evidence exists in the offer or the FA, the offer source is `inherited` (FA values, citations = incorporation clause + FA clause). Otherwise `missing`.
- Owner side: `owner_contract` document evidence, else `OwnerTerms` mapped to CT-01/CT-02/CT-03/CT-14 fields as `buyer_entered`, else `None`.
- Within the offer, two disagreeing passages for one field use the existing tier order (`precedence._tier`); equal tiers → `conflict`.

- [ ] **Step 1: Failing tests** (build `Evidence` with `tests/helpers.evidence`):
  - `test_offer_cites_its_own_term`; `test_amendment_after_fa_governs` (FA warranty 60 months dated 2026-01-10, amendment 66 months dated 2026-03-02 → 66, two citations); `test_amendment_order_unknown` (undated amendment → `conflict`, flag code `amendment_order_unknown`); `test_inherited_needs_incorporation_clause` (with CT-16 → `inherited`; without → `missing`); `test_owner_terms_from_form` (`OwnerTerms(warranty_months=60, warranty_start="pac")` → owner source `buyer_entered`); `test_no_fa_means_fa_none` (no FA for the counterparty → `fa is None`, not missing); `test_vendor_without_counterparty_has_no_fa`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(contract): resolve terms across offer, FA and owner contract`.

---

### Task 6: Term checks and gap calculations

**Files:**
- Create: `antova_ingest/contract_checks.py`
- Modify: `antova_ingest/exposure.py`
- Test: `antova_ingest/tests/test_contract_checks.py`, `antova_ingest/tests/test_exposure.py` (append)

**Interfaces:**
- Consumes: `ContractTerm` (Task 5), `values.check`, `values.per_week`, `BaselineFx` (Task 3).
- Produces:

```python
@dataclass(frozen=True)
class TermCheck:
    param_id: str; vendor_id: str
    baseline: Literal["fa", "owner"]
    field: str
    passed: bool | None          # None: not comparable, see reason
    required: str; offered: str
    required_citations: tuple[Citation, ...]; offered_citations: tuple[Citation, ...]
    reason: str = ""

@dataclass(frozen=True)
class TermGap:
    kind: Literal["warranty", "payment_timing"]
    value: Decimal | None; unit: Literal["months", "days"]
    formula: str; reason: str = ""

def check_terms(terms: Mapping[tuple[str, str], ContractTerm], fx: Sequence[BaselineFx]) -> list[TermCheck]
def warranty_gap(offered: ContractTerm) -> TermGap          # offered vs owner, CT-03
def payment_timing(offered: ContractTerm) -> TermGap        # offered vs FA, CT-07

# exposure.py
def back_to_back_ld_gap(owner_rate_money_per_day, owner_cap_money, package_share_pct,
                        vendor_rate: tuple[Decimal, str] | None, vendor_cap_pct, po_value,
                        delay_days) -> Exposure             # kind "back_to_back_ld"
def liability_back_to_back(owner_required_money, vendor_cap_pct, po_value) -> Exposure  # kind "back_to_back_liability"
```

Pinned behaviour:
- Owner LD given as percent is converted to money with `OwnerTerms.contract_value` before calling `back_to_back_ld_gap`; the conversion is part of the formula.
- `back_to_back_ld_gap`: owner owed = `min(rate×days, cap) × share/100`; vendor pays = `min(po×rate/100×days, po×cap/100)` with a weekly rate applied as `rate×days/7`; gap = `max(owed − pays, 0)`, quantized to cents only at the end.
- `warranty_gap`: same start event → `owed − offered` months (≥0 shown as a shortfall, <0 as surplus `0`); different start events → `None`, reason `f"the vendor's warranty starts at {a} and the owner warranty at {b}; there are no dates to bridge them"`.
- `payment_timing`: weighted days after the common event; milestones anchored to different events → `None` with reason; positive difference (offer pays later) passes.
- Money fields in different currencies are converted only with a `BaselineFx` for that pair; otherwise `passed=None`, reason `"no GBP→EUR rate entered"` (currencies from the values).

- [ ] **Step 1: Failing tests:**
  - `test_back_to_back_gap`: owner €10,000/day, cap €1,000,000, share 100, vendor `(0.5, "day")`, cap 10, PO €2,000,000, 28 days → `Decimal("80000.00")`.
  - `test_back_to_back_true_zero`: same at 14 days → `Decimal("0.00")`.
  - `test_back_to_back_weekly_rate`: vendor `(Decimal("3.5"), "week")` equals the 0.5/day result at 28 days.
  - `test_back_to_back_share_missing`: share `None` → amount `None`, reason contains `"share of owner LDs"`.
  - `test_ld_rate_week_vs_day_check` (Review Focus 3): FA `(1, "week")`, offer `(0.15, "day")` → passes (1.05 ≥ 1), `offered` text shows "0.15% per day (1.05% per week)".
  - `test_liability_currency_needs_fx` (Review Focus 1): FA cap €500,000, offer cap £400,000 → `passed is None`, reason `"no GBP→EUR rate entered"`; with `BaselineFx("GBP","EUR",1.17,…)` → £400,000 = €468,000.00 < €500,000 → `passed is False`.
  - `test_warranty_gap_same_start`: offer 60 months from delivery, owner 66 from delivery → `Decimal("6")` months.
  - `test_warranty_gap_different_start_is_none`.
  - `test_payment_timing`: FA 100% at 30 days after delivery; offer 85% at 30 + 15% at 240 after delivery → offer 61.5 days, difference `Decimal("31.5")`, passes.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run both files → PASS.
- [ ] **Step 5:** Commit `feat(contract): FA conformance, back-to-back checks and gaps`.

---

### Task 7: Budget and FX lines

**Files:**
- Create: `antova_ingest/budget.py`
- Test: `antova_ingest/tests/test_budget.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class PriceBasis:
    lump_sum: Decimal | None; currency: str | None
    options: tuple[tuple[str, Decimal], ...]   # selected option lines
    variable: tuple[str, ...]                   # listed, never summed
    citations: tuple[Citation, ...]

@dataclass(frozen=True)
class BudgetLine:
    label: Literal["offered", "offered_with_options", "variance", "variance_with_options", "variance_true_job_cost"]
    amount: Decimal | None; currency: str | None
    formula: str; reason: str = ""

def price_basis(term_ct13: ContractTerm | None, headline: PriceRead | None) -> PriceBasis
def budget_lines(price: PriceBasis, true_job_cost: Decimal | None, tjc_currency: str | None,
                 budget: BaselineMoney | None, fx: Sequence[BaselineFx]) -> list[BudgetLine]
```

Rules: one buyer-entered rate per currency pair governs; the rate table's FX is never used here; the formula names the rate and its date. Missing budget → every variance line `None`, reason `"frozen budget not entered"`.

- [ ] **Step 1: Failing tests:** GBP lump sum 2,000,000, option ("25 kA upgrade", 50,000), budget EUR 2,400,000, fx GBP→EUR 1.17 dated 2026-11-01 → offered `2340000.00`, with options `2398500.00`, variance `-60000.00`, with options `-1500.00`, formula contains `"1.17 (2026-11-01)"`; missing fx → `None`, reason `"no GBP→EUR rate entered"`; missing budget; variable parts listed and absent from every sum; true job cost in EUR with GBP budget converts with the same buyer rate.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(contract): budget and FX lines`.

---

### Task 8: Contract stage and contract findings

**Files:**
- Create: `antova_ingest/contract_findings.py`
- Modify: `antova_ingest/pipeline.py` (new stage after `leveled`), `antova_ingest/artifacts.py` (`STAGE_NAMES` adds `"contracted"`)
- Test: `antova_ingest/tests/test_contract_findings.py`

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces:
  - `stage_contracted(bodies: dict) -> dict` returning `{"terms": [...], "checks": [...], "gaps": [...], "budget": {vendor_id: [...]}, "findings": [...], "not_checked": [...]}` as plain JSON (via `artifacts._plain`).
  - `build_contract_findings(terms, checks, gaps, exposures, budget) -> tuple[list[dict], list[dict]]` → `(findings, not_checked)`.
  - Finding dict keys: `id` (`f"{vendor_id}|{param_id}|{baseline}|{field}"`, budget findings use `f"{vendor_id}|budget|{label}"`), `vendor_id`, `param_id`, `category` (`"fa_conformance" | "back_to_back" | "budget" | "missing"`), `severity` (`"critical"` for HIGH params, `"high"` budget overrun, `"medium"` otherwise), `message` (template text only), `money` (`Money` list as in `findings.py`), `gap` (TermGap dict or `None`), `citations`, `evidence_hash` (sha256 of the canonical JSON of values and citations).
  - `not_checked` entries: `{"vendor_id", "baseline", "reason"}` e.g. reason `"no framework agreement provided"`.

- [ ] **Step 1: Failing tests:** a failing FA check yields one `fa_conformance` finding with both citations; a passing check yields none; a `missing` term on a HIGH param yields a `missing` finding; a vendor with no FA yields a `not_checked` entry and no FA findings; budget overrun yields `budget` finding; ids are stable across two runs of the same input; a tender with a single offer (a call-off with no competition) produces terms, checks and findings for that one vendor; `evidence_hash` changes when a cited value changes; the staged run on pack 402 (no contract docs) still produces an empty `contracted` body and unchanged earlier stage hashes.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus `test_staged_run.py`, `test_sample_pack.py`.
- [ ] **Step 5:** Commit `feat(pipeline): contracted stage and contract findings`.

---

### Task 9: Schema v11, counterparties, projects, persistence

**Files:**
- Modify: `antova_db.py`, `antova_service.py` (`persist`)
- Test: `test_antova_contract_db.py` (repo root of `antova_backend`)

**Interfaces:**
- Produces (SQLAlchemy models):
  - `CounterpartyRecord` (`counterparties`: `id` str64 PK, `legal_name`, `aliases` JSON text, `parent_company` nullable, `created_at`).
  - `ProjectRecord` (`projects`: `id` PK, `name`, `buyer_entity`, `owner_terms` JSON of `OwnerTerms` or NULL, `created_at`). A tender whose baseline has `project_id` and no `owner_terms` uses its project's; a tender's own `owner_terms` win.
  - `tenders.project_id` nullable FK; `vendors.counterparty_id` nullable, indexed; `source_documents.counterparty_id`, `.amends`, `.dated`.
  - `ContractTermRecord` (`contract_terms`: PK `(tender_id, vendor_id, param_id)`, `body` JSON, `evidence_hash`).
  - `ContractGateRecord` (`contract_gates`: PK `(tender_id, vendor_id)`, `status` in `("DRAFT", "VERIFIED")`, `verified_at`, `verified_by`, `verified_run_id`, `fa_version`).
  - `FindingDispositionRecord` (`finding_dispositions`: append-only; `tender_id`, `vendor_id`, `finding_id`, `disposition`, `reason`, `waiver_role`, `reviewer`, `evidence_hash`, `created_at`).
  - `ApproverRemovalRecord`, `DeletionLogRecord` (`counterparty_id`, `deleted_by`, `deleted_at`, `counts` JSON), `PriceHistoryRecord` (columns in Task 18).
  - `_upgrade_v10_to_v11(conn)`; `SCHEMA_VERSION = 11`.
  - `suggest_counterparty(db, vendor_name: str) -> list[CounterpartyRecord]` (case-insensitive match on legal name and aliases; suggestion only).
  - `fa_version(db, counterparty_id) -> str`: sha256 over the sorted sha256s of that counterparty's FA and amendment documents.
  - `persist()` writes contract terms and findings and creates a `DRAFT` gate per vendor that has an offer.

- [ ] **Step 1: Failing tests:** `test_project_owner_terms_inherited` (tender without owner terms picks up its project's; tender with its own keeps them); a v10 database (built with the existing fixture path in `test_antova.py`) upgrades to 11 with data intact and `check_foreign_keys` clean; `persist` of a result with contract terms writes rows and DRAFT gates; `fa_version` changes when an amendment is added; **Review Focus 4** `test_subsidiary_not_auto_linked`: FA counterparty "Kestrel Power Conversion AG", offer from "Kestrel Power Conversion UK Ltd" → `suggest_counterparty` returns the AG as a suggestion and `vendors.counterparty_id` stays `NULL` until set explicitly.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus `test_antova.py`, `antova_ingest/tests/test_db.py`.
- [ ] **Step 5:** Commit `feat(db): schema v11 for contracts, counterparties and gates`.

---

### Task 10: Reviewer roles and approver rules

**Files:**
- Modify: `antova_auth.py`
- Create: `antova_ingest/approvers.py`, `antova_ingest/approver_rules/default_v1.json`
- Test: `test_antova_auth.py` (append), `antova_ingest/tests/test_approvers.py`

**Interfaces:**
- Produces:
  - `ROLES = ("engineering", "procurement", "project_management", "finance", "legal", "business_director")`.
  - `ReviewerPrincipal(id, name, roles: frozenset[str] = frozenset())`; the founder token holds all roles; `ANTOVA_REVIEWERS` entries accept optional `"roles": [...]`; an unknown role raises `ReviewerConfigError`.
  - `load_rules(name: str = os.getenv("ANTOVA_APPROVER_RULES", "default_v1")) -> ApproverRules`.
  - `suggest_approvers(rules, *, findings: list[dict], budget_variance_pct: Decimal | None, signing_before_ntp: bool | None, procurement_negotiated: bool | None) -> list[Suggestion]`, `Suggestion(role: str, reason: str)`.
  - `role_to_accept(rules, finding: dict) -> str`.
- Default rules (`default_v1.json`): project_management, engineering, finance always; procurement when `procurement_negotiated`; legal when any finding has `param_id in LEGAL_TERMS`; business_director when `signing_before_ntp`, or `budget_variance_pct > 0`, or any finding money ≥ 100000 (budget currency); acceptance roles: `fa_conformance` → procurement, `back_to_back` → project_management, `budget` → finance, `missing` → procurement, any `param_id in LEGAL_TERMS` → legal (overrides).

- [ ] **Step 1: Failing tests:** roles parse; unknown role rejected; founder has all roles; each default rule fires with its reason text; a CT-14 back-to-back finding requires `legal`; with no findings and no flags the list is exactly PM, engineering, finance.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(auth): reviewer roles and approver rules`.

---

### Task 11: Finding dispositions and negotiation questions

**Files:**
- Modify: `antova_review_api.py`, `antova_ingest/clarify.py`
- Create: `antova_contract_api.py` (router mounted under `/api/review`, same token guard as `antova_review_api`)
- Test: `test_antova_dispositions.py`

**Interfaces:**
- Consumes: Tasks 8–10.
- Produces routes:
  - `PUT /api/review/tenders/{tender_id}/vendors/{vendor_id}/findings/{finding_id}/disposition` body `{"disposition": "accept" | "negotiate" | "waiver_needed", "reason": str (1–2000), "waiver_role": str | null}` → 200 with the stored row; 403 `{"detail": "Accepting this finding needs the <role> role."}` when the principal lacks `role_to_accept`; 409 when that vendor's gate is VERIFIED; 404 unknown finding.
  - `GET …/vendors/{vendor_id}/dispositions` → latest disposition per finding with `"state": "current" | "stale"` (stale when the finding's `evidence_hash` differs from the one stored).
  - `clarify.contract_rows(findings, dispositions) -> list[dict]`: one fixed-template question per `negotiate` finding, `kind="negotiation"`, citations from the finding; included in the existing clarifications CSV/XLSX for that vendor.

- [ ] **Step 1: Failing tests:** accept by a procurement reviewer on an `fa_conformance` finding → 200 and recorded reviewer from the token; accept by an engineering-only reviewer → 403 with exact text; negotiate adds a row to `clarifications.csv?vendor=…`; **Review Focus 5** `test_disposition_goes_stale_on_reingest`: dispose, re-ingest with a changed cited value → GET shows `"stale"`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus `test_antova_review.py`.
- [ ] **Step 5:** Commit `feat(api): finding dispositions and negotiation questions`.

---

### Task 12: Contract gate and contract payload

**Files:**
- Modify: `antova_contract_api.py`, `antova_review.py` (new `contract_open_items`)
- Test: `test_antova_contract_gate.py`

**Interfaces:**
- Produces:
  - `antova_review.contract_open_items(terms, findings, dispositions, cell_reviews, fa_version_now: str, fa_version_at_ingest: str) -> list[OpenItem]`: unreviewed `conflict` or flagged contract cells, findings without a current disposition, stale dispositions, `fa_version` changed since ingest (message `"The framework agreement for this vendor changed in another tender. Re-run this tender."`).
  - `GET …/vendors/{vendor_id}/contract` → `{"rows": [{"param_id", "name", "offered", "fa", "owner", "mr", "state", "checks"}], "not_checked": [...], "gate": {...}}`; `mr` comes from the TBE cell via `TBE_EQUIVALENT`.
  - `POST …/vendors/{vendor_id}/contract/verify` body `{"run_id": str}` → 409 with open items (same shape as `_open_items_conflict`) or 200; `POST …/contract/withdraw`.
  - Contract cell reviews reuse `PUT /tenders/{id}/reviews/{param_id}/{vendor_id}` with `CT-` param ids; TBE `review_summary` and `open_items` must ignore `CT-` ids.
  - A cited CT-17 (order of precedence) cell always needs a review before the gate can verify: that review is the reviewer confirming the precedence order for this vendor.

- [ ] **Step 1: Failing tests:** verify refused with each open-item kind, including an unreviewed CT-17 cell; verify succeeds once all are resolved; verifying a vendor's contract gate leaves `tenders.status` unchanged; a `CT-` cell review doesn't change the TBE open-item count; an FA change in tender B makes tender A's gate report the stale-FA item.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(api): per-vendor contract gate`.

---

### Task 13: CAS assembly

**Files:**
- Create: `antova_cas.py`
- Modify: `antova_contract_api.py`
- Test: `test_antova_cas.py`

**Interfaces:**
- Produces:
  - `build_cas(loaded: LoadedTender, vendor_id: str, *, contract: dict, dispositions: dict, rules: ApproverRules, removals: list) -> dict` with top-level keys exactly `project, law, price, delivery, payment_bonds, lds, warranty, risks, technical, approvers, meta`; every field `{"label", "state", "display", "citations", "formula", "reason"}`, `state ∈ {"cited", "computed", "inherited", "missing"}`.
  - `risks`: one entry per finding `{"finding_id", "category", "message", "money", "gap", "disposition", "disposition_state", "citations"}`, grouped by `technical`, `fa_conformance`, `back_to_back`, `budget`.
  - `technical`: TBE status, deviation and open-item counts, link path `/audit/{tender_id}`.
  - `approvers`: suggestions minus recorded removals, each removal with its reason and reviewer.
  - `meta`: run fingerprint, rate table version, ontology and contract-ontology hashes, `zero_retention_ref`, TBE status, contract gate status, unreviewed count.
  - `GET …/vendors/{vendor_id}/cas` → that dict; `PUT …/vendors/{vendor_id}/cas/approvers/{role}` `{"removed": true, "reason": str}` records a removal.

- [ ] **Step 1: Failing tests:** for a fixture with an FA, owner terms and budget: `lds` field `ld_cap` is `cited` with the offer citation; an inherited warranty is `inherited` with two citations; a missing NTP deadline is `missing` with reason; `price.variance` is `computed` with the FX formula; no field anywhere has `display == "0"` while `state == "missing"`; `approvers` drops a removed role and shows its reason.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(cas): assemble the contract approval sheet`.

---

### Task 14: CAS export (DOCX and PDF)

**Files:**
- Create: `antova_cas_export.py`
- Modify: `antova_contract_api.py`
- Test: `test_antova_cas_export.py`

**Interfaces:**
- Produces:
  - `cas_docx(cas: dict, *, draft: bool) -> bytes` (python-docx), `cas_pdf(cas: dict, *, draft: bool) -> bytes` (reportlab). Sections in the spec's order 1–11; evidence appendix lists every citation (file, page, verbatim quote).
  - Draft watermark text: `f"DRAFT: {n} findings unreviewed"` with `n = cas["meta"]["unreviewed"]`.
  - Routes `GET …/vendors/{vendor_id}/cas.docx` and `cas.pdf`, query `draft: bool = False`. Without `draft`: 409 `"Sign off the technical evaluation before you download the approval sheet."` unless TBE VERIFIED; 409 `"Sign off this vendor's contract terms before you download the approval sheet."` unless the gate is VERIFIED. With `draft=true`: always 200, watermarked.
  - Footer on every page: run fingerprint, rate table version, contract-ontology hash, zero-retention reference (or "none").

- [ ] **Step 1: Failing tests:** both 409 messages exact; draft export contains the watermark text; final export doesn't; DOCX text (read back with python-docx) contains every risk message and every citation file name; PDF is non-empty and starts with `%PDF`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(cas): DOCX and PDF export`.

---

### Task 15: Deletion, retention and the public brief

**Files:**
- Modify: `antova_contract_api.py`, `antova_review.py` (`public_brief_payload`)
- Create: `antova_ingest/retention.py`
- Test: `test_antova_retention.py`

**Interfaces:**
- Produces:
  - `DELETE /api/review/counterparties/{counterparty_id}` (needs `project_management` role, or the founder): removes that counterparty's document files and their saved locator responses, page images, `contract_terms`, dispositions and `CT-` cell reviews for linked vendors; deletes stage artifacts of affected runs and sets affected tenders and gates to `DRAFT` with stage-log note `"artifacts purged after a counterparty deletion; re-ingest needed"`; writes a `DeletionLogRecord` with counts (no content). Returns the counts.
  - `retention.purge_expired(db, now: datetime) -> list[str]` deletes tenders older than `ANTOVA_RETENTION_DAYS` (unset → no purge) with the same file cleanup; CLI `python -m antova_ingest.retention`.
  - `public_brief_payload` never includes contract documents, contract terms, findings with `category` in the contract categories, or the CAS.

- [ ] **Step 1: Failing tests:** after deletion, no file with that counterparty's FA sha256 remains under the tender dirs or the response store; counts match; a second tender sharing the counterparty is reset to DRAFT; deletion by an engineering-only reviewer → 403; purge with `ANTOVA_RETENTION_DAYS=30` removes a 31-day-old tender and keeps a 29-day-old one; public brief JSON contains no `"framework_agreement"` string.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(privacy): counterparty deletion, retention, public brief`.

---

### Task 16: Synthetic call-off pack TENDER-2026-419

**Files:**
- Create: `samples/scenario_419.py`
- Modify: `samples/generate_sample_pack.py` (`PACKS`), `antova_ingest/answer_key.py` (contract answers), `antova_ingest/locator_eval.py` (accept `CONTRACT_PARAMETERS`)
- Generate: `samples/TENDER-2026-419/` (PDFs, `manifest.json`, `expected.json`)

**Interfaces:**
- Produces a pack with invented parties only: EPC "Brightmere Renewables EPC Ltd"; vendors "Harlow Energy Systems Ltd" (counterparty `cp-harlow`, conforming) and "Kestrel Power Conversion UK Ltd" (counterparty `cp-kestrel`, FA with "Kestrel Power Conversion AG"); one MR (reuse 402's MR spec text), two offers, two FAs, one amendment to the Kestrel FA (dated later, warranty 60 → 66 months), one owner-contract excerpt. Kestrel's offer deliberately: LD cap 5% vs FA 10%; warranty from delivery vs owner from PAC; silent on title (inherited via a CT-16 clause); liability cap in GBP vs FA in EUR. Footer `"SYNTHETIC SAMPLE — not a real offer"`. `manifest.synthetic = true`.
- `expected.json` gains a `contract` block with the expected value and page for every CT term per document.

- [ ] **Step 1:** Write `scenario_419.py` and run `python -m samples.generate_sample_pack --pack TENDER-2026-419` (add the flag if the generator lacks it).
- [ ] **Step 2: Failing test** `antova_ingest/tests/test_sample_pack_419.py`: every expected contract quote is on its stated page word for word; `locator_eval` with `AnswerKeyLocator` gives recall 1.0 for every CT term.
- [ ] **Step 3:** Fix the scenario until it passes. **Step 4:** Run the full suite → PASS.
- [ ] **Step 5:** Commit `test(samples): synthetic call-off pack 419`.

---

### Task 17: End-to-end CAS round trip and recall bar

**Files:**
- Create: `test_antova_cas_round_trip.py`
- Modify: `antova_backend/docs/` runbook (new `contract-recall.md`)

- [ ] **Step 1: Failing test** `test_cas_round_trip`: ingest 419 with `AnswerKeyLocator` (no env reference; synthetic manifest); verify TBE; review flagged CT cells; dispose every Kestrel finding (accept LD cap gap as procurement, negotiate warranty start, accept the rest with the needed roles); verify Kestrel's gate; export DOCX. Assert: `lds.ld_cap` cited from the Kestrel offer page; `back_to_back_ld` exposure equals the value computed by hand in the test from the scenario's numbers; warranty gap is `None` with the different-start reason; title is `inherited`; liability check shows the GBP→EUR conversion; final DOCX has no `"DRAFT"`; `approvers` include `legal` (CT-02 finding) and `business_director` only if the budget is exceeded in the scenario.
- [ ] **Step 2:** Run → it should already pass if Tasks 1–16 are right; any failure is a defect in the owning task, fixed there.
- [ ] **Step 3:** Write `docs/contract-recall.md`: the command for a live recall run on pack 419 (`python -m antova_ingest.locator_eval --pack TENDER-2026-419 --contract`), the bar (per-term recall ≥ the equipment ontology's on pack 402, recorded in the doc with date and model), and the rule that terms below the bar are marked manual in `CONTRACT_PARAMETERS` (`locate_hint` prefix `"MANUAL:"` → the stage marks them `missing` with reason `"not reliably found; check by hand"`).
- [ ] **Step 4:** Full suite → PASS.
- [ ] **Step 5:** Commit `test(cas): end-to-end approval sheet round trip`.

---

### Task 18: Price history import and comparison

**Files:**
- Create: `antova_ingest/price_history.py`
- Modify: `antova_contract_api.py`, `antova_cas.py` (`price.history` field)
- Test: `antova_ingest/tests/test_price_history.py`

**Interfaces:**
- Produces:
  - CSV columns exactly: `project,country,capacity_mwp,capacity_mva,dc_ac_ratio,equipment,manufacturer,offer_date,price,currency,option_lines` (`option_lines` as `label=amount;label=amount`). Rows failing validation are reported by line number and not imported.
  - `POST /api/review/price-history` (multipart CSV) → `{"imported": n, "rejected": [{"line", "reason"}]}`.
  - `price_comparison(db, *, equipment: str, basis: Literal["per_wp", "per_kva"], value: Decimal, currency: str, offer_date: date) -> dict | None`: median of the same equipment and basis over the four calendar quarters before `offer_date`, same currency only (other currencies excluded and counted); `None` when fewer than 3 rows.
  - CAS `price.history` field: `computed` with the comparison set size and median, or `missing` with reason `"fewer than 3 comparable past offers"`.

- [ ] **Step 1: Failing tests:** median of [1.10, 1.20, 1.40] €/Wp → 1.20; rows older than four quarters excluded; other currency excluded and counted; two rows → `None`; malformed date rejected with its line number.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, full suite PASS.
- [ ] **Step 5:** Commit `feat(cas): price history comparison`.
