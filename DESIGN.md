# Fakturama Image-to-Cash: Design

## 1. Overview

**Goal.** From one order image, produce a saved **Order** in Fakturama and a linked **Invoice**
created through the Order's *Create a follow-up document › Invoice*, with the payment status set.
Every step is verified; ambiguous or conflicting data stops the run for manual review and leaves
the application as it is.

**Approach.** An LLM is used **once**, to turn the image into validated structured data. Everything
after that is deterministic Python driving Fakturama through Microsoft UI Automation (UIA). Windows
OCR is used only where Fakturama exposes nothing to UIA: its NatTable tables and its tab labels,
always inside a rectangle that UIA provided. No coordinates are hardcoded.

```
order image ─► LLM extraction (1 call, cached) ─► validation ─► (1 retry with feedback) ─► manual review
                                                      │
                                                      ▼
            runner: stages of steps; every step = action ► verify ► annotated screenshot
            order ─► debtor ─► products ─► save_order ─► invoice
                                                      │
                                                      ▼
            artifacts/<run-id>/: extracted.json · run.log · NN_<step>.png · summary.json
```

**Result.** End to end `status: ok` in 63.5 s with all master data selected; the create paths
(Debtor with two addresses, payment method, VAT, products) were verified live as well. 129 offline
tests.

## 2. Extraction (image → data)

| Aspect | Decision | Why |
|---|---|---|
| Engine | LLM vision call; Anthropic Claude Haiku by default, Gemini as a free-tier fallback (`LLM_PROVIDER`) | Robust to layout changes; one call per image |
| Output | Forced tool call with a flat JSON schema (Gemini: JSON mode) | No free-text parsing |
| Model | Pydantic `OrderData`; money as `Decimal`, `ROUND_HALF_UP`, 2 places | No float errors |
| Validation | Deterministic: line net = qty × price × (1 − discount); Σ lines = net; VAT and gross consistent (±0.01 per line); percentages in 0–100; qty > 0; required fields; PAID ⇒ payment date | The LLM is never trusted to be right |
| Retry | Once, with the failed checks and the previous answer, asking to re-transcribe, not to adjust numbers | Fixes misreads without inviting invented numbers |
| Failure | Still invalid → manual review, before Fakturama is touched | No guessing |
| Cache | Key = image hash + provider + model + prompt version; only validated results are stored | Zero tokens on re-runs |

Matching, navigation and verification never use the LLM, so runs are reproducible and auditable.

## 3. Control grounding (no coordinates)

**Probe first.** Each screen was dumped with a probe tool (UIA tree: type, name, id, class,
rectangle, patterns, plus a screenshot) before any UI code was written for it. Selectors come from
those dumps; several dumps are replayed in the offline tests. All label strings live in
`config/settings.yaml`.

**Order of preference:**
1. **UIA control type + name.** SWT names many inputs after their label; toolbar buttons are named by
   their tooltip.
2. **UIA label geometry.** The nearest input right of (or below) a `Text` label. This covers unnamed
   fields, duplicate labels (told apart by control type), two-column forms and one label owning two
   fields ("ZIP - City").
3. **UIA structure.** Action icons are the upper unnamed `Image` under a label ("select existing",
   never the green +); a table is the largest pane containing only scrollbars.
4. **OCR inside a UIA rectangle.** Table cells, row positions and tab labels. Clicks go to the centre
   of an OCR word box found in this run. Per-monitor DPI awareness keeps UIA, screenshots and mouse
   in one pixel space.

**Tables (NatTable) are opaque to UIA.** The reader groups OCR words into rows, takes column
boundaries from the grid lines in the header strip, fuzzy-matches header text to the configured
names, and re-reads cells that OCR dropped or cut short ("0", "VAT 19%" → "VAT") by pasting the
column header in front as context. A table counts as read after two identical reads.

**Input that changes Fakturama's data, not just the widget** (each found live):
- text is typed with real keystrokes, only after UIA confirms keyboard focus; ValuePattern changed
  a field on screen while Fakturama saved it empty;
- dates are set with arrow keys per field (text set through UIA reverted to today); the field order
  is learned at run time;
- combos: select, then typing, then a verified arrow walk; checkboxes through the Toggle pattern;
- SWT tabs can't be selected through UIA: the tab bar is OCR'd, the label clicked, the switch verified;
- selector rows are chosen by double-click; Items cells are edited through the inline editor UIA
  exposes, then OCR'd back.

**Synchronisation:** bounded polling waits only (no bare sleeps); waits on one control read only
that control's live property.

## 4. Matching & verification

**Matching** (normalised: Unicode NFKC, trimmed, whitespace collapsed, case-folded). Outcomes:
`NONE` → create, `EXACT` → select, `AMBIGUOUS` / `CONFLICT` → manual review.

| Entity | Searched by | EXACT | Stops |
|---|---|---|---|
| Debtor | company, in *Select the address* | company + first name + name + ZIP + city | same company, other fields differ; > 1 exact |
| Payment method | name, in Data › terms of payment | name + description | > 1 with the name; differing definition |
| VAT | `VAT <p>%`, in Data › VATs | name + value | same name, other value |
| Product | SKU, in *Select a product* | item number | duplicate SKUs (a differing description is a warning) |

OCR tolerances (a truncated cell matches by its visible prefix; item numbers fold O/0, I/1 look-alikes
on both sides) only widen the candidate set. Two candidates are `AMBIGUOUS` and stop the run, so a
tolerance can never cause a silent wrong pick.

**Verification principles.**
- Every step is action + verification; the verification message goes into `summary.json` with a
  green-captioned screenshot.
- A read-back from the widget is not proof of persistence. After each save the record is read back
  from Fakturama itself: the Debtors, terms of payment and VATs lists, the saved Order and Invoice
  editors (UIA), and Data › Documents.
- Exact where possible: UIA values (header fields, totals, address boxes) are compared exactly; OCR'd
  table values are compared as `Decimal` with ±0.01.
- Save is clicked exactly once per record, then verified (Save disables again; any dialog is reported).

| Brief | Step | Verified by |
|---|---|---|
| 1.1–1.8 | New Order; Date, Cust.Ref.; Net, With VAT | independent re-read of all header fields |
| 2.1–2.4 | search the company in *Select the address* | match outcome; EXACT is double-clicked |
| 2.10 | payment method: reuse, or create with the code mapping | listed after save with name + description |
| 2.5–2.13 | create Debtor (main address = invoice role; a different delivery address = second address with delivery role); save; select in the Order | read back from the Debtors list; must match EXACT on the second search |
| — | Order's addresses | invoice and delivery boxes contain the source address |
| 3.1–3.6 | each VAT rate: reuse, or create (code S) | listed with its value |
| 3.7–3.12 | per line: select or create the product (gross = net × (1 + VAT)), add it | exactly one line for the SKU appears |
| 3.13–3.17 | Qty, Discount (U.Price if needed) | OCR of Qty, U.Price, VAT, Discount and line price |
| 4.1–4.4 | discount 0 %, free shipping; save once | all lines, addresses and totals vs source; Cust.Ref. and Date re-read after save |
| 4.5 | Data › Documents, by order number | date, total, state "open" |
| 4.6 | follow-up › Invoice | invoice editor open |
| 5.1–5.4 | check copied data; method, paid, date, value; save once | everything copied; payment fields re-read; Cust.Ref. and paid re-read after save |
| 5.5 | Data › Documents, by number | invoice paid/unpaid + total; order still open + total |

## 5. Failure handling

| Situation | Behaviour |
|---|---|
| Extraction invalid after the retry | Manual review before Fakturama is touched (exit 3) |
| AMBIGUOUS / CONFLICT match, missing data, method unavailable in the Invoice | `ManualReviewRequired(reason, step)`; red screenshot; candidate rows in `summary.json` (exit 3) |
| UI error: control missing, value didn't stick, timeout, dialog during save | Stop with the exact cause (exit 4) |
| An unsaved editor of the same kind is already open | Refuse to start (never write into a stale editor) |
| The order already has a line for the SKU | Not added again |
| Keyboard focus can't be proven | Refuse to type |

In every case Fakturama is left as it is for a human, and `summary.json` records the status, the
stopping step, the entities selected or created, every step's verification, warnings and timings.

## 6. Testing

**Offline: 129 automated tests** (`pytest`, no Fakturama needed, run after every change):
- **Unit tests:** money math (half-up rounding, gross price, mixed VAT rates), parsing into the data
  model, every validation rule (the sample's correct data plus one deliberate error per test), and
  matching (all four outcomes per entity, truncated cells, OCR look-alikes).
- **Extraction service with a fake LLM provider:** first-try success, cache hit, retry with
  feedback, and two failures → manual review with nothing cached.
- **Replay tests on real recordings:** control lookups run against real UIA probe dumps of the
  Fakturama screens, and table reading runs real Windows OCR on screenshots captured in live runs
  where reading had failed (regression tests for those bugs).
- **UI helpers and runner with fake controls:** waits, text-entry fallbacks, the focus guard,
  arrow-key dates, and the runner's exit codes (ok / manual review / error).

**Live tests against Fakturama** (run by hand; evidence in `artifacts/<run-id>/`):
- end to end on the sample with all master data existing: `status: ok` in 63.5 s;
- create paths (new Debtor with two addresses, payment method, VAT, products) on an empty database,
  stage by stage;
- recreation after master data was deleted by hand (products; payment method for an existing Debtor).

Each failure found live was fixed and, where possible, recorded as a replay test. Not yet run live:
the two extra samples (new 7 % VAT and Credit Card; UNPAID invoice) and the negative ZIP-conflict
case, which is covered by unit tests only.

## 7. Trade-offs & alternatives considered

| Alternative | Why not (or not primarily) |
|---|---|
| OCR-first extraction | Cheaper, but brittle on layout; kept as a future option with LLM fallback |
| Vision-model clicking (an LLM looks at screenshots and acts) | Non-deterministic, slow and hard to verify; the brief asks for verification and clear stops |
| Writing to Fakturama's database directly | Fast, but bypasses Fakturama's numbering, business logic and editors, and isn't UI automation |
| Fixed coordinates / image templates | Break on resolution, DPI or layout changes; ruled out by the brief |
| UIA only | Impossible: tables expose no rows and tabs can't be selected through UIA |
| ValuePattern for all input | Fast, but shown not to persist in SWT |
| Full-window UIA reads everywhere | Simple, but 0.7–0.9 s each; pane-scoped reads and `FindFirst` cut a run from 156 s to 63.5 s with every check kept |

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| OCR misreads | Only for tables and tab labels; tolerances lead to AMBIGUOUS, not wrong picks; exact UIA re-reads wherever a value exists in UIA; regression tests on live captures |
| Fakturama UI changes | Labels in `settings.yaml`; geometry relative to labels; probe tool to re-derive selectors |
| Different screen size / DPI | DPI awareness; no coordinates |
| User touching mouse/keyboard during a run | Documented as not allowed; focus proven before typing |
| Long tables | Not handled yet: only visible rows are read (searches narrow lists) |
| LLM misextraction | Deterministic validation, one retry, then manual review |
| Re-runs creating duplicates | Master data is matched and reused; no duplicate lines; no stale editors. A duplicate-order guard (same external reference) is future work |

## 9. Decisions on ambiguities in the brief

- A delivery address different from billing becomes a second Debtor address with the Delivery role.
- Fakturama's proposed Customer ID is kept; the printed customer ID is extracted but not used.
- Contact name: first token = first name, the rest = last name.
- "Exact" = equal after whitespace/case normalisation.
- A differing product description is a warning (configurable to stop).
- The payment method is ensured on every run, because the Invoice needs it even for an existing Debtor.
- The unit ("pcs") isn't entered (no field); an existing VAT's code isn't re-checked (no column).
