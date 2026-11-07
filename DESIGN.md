# Fakturama Image-to-Cash: Design Document (working draft)

> **Draft to condense.** This version collects everything (decisions, live findings, numbers) so
> the final 1–4 page Part 1 document can be cut down from it. Suggested final outline:
> §1 Overview, §2 Extraction, §3 Control grounding, §4 Matching & verification, §5 Failure
> handling, §8 Trade-offs, §9 Risks. Keep §6 (step table) as an appendix if space allows.

---

## 1. Overview

**Goal.** Take one order image and produce a saved **Order** in Fakturama, then a linked **Invoice**
created from that order's *Create a follow-up document › Invoice*, with the payment status set.
Every step is verified. Ambiguous or conflicting data stops the run for manual review and leaves
the application as it is.

**Constraints from the brief.**
- Use Microsoft UI Automation, OCR and/or LLMs.
- Don't rely on hardcoded coordinates or a fixed layout.
- Select or create the Debtor, payment method, VAT and products.
- Verify each step.

**Approach in one sentence.** An LLM is used **once** to turn the image into validated structured
data; everything after that is deterministic Python driving Fakturama through UIA. Windows OCR is
used **only** where Fakturama exposes nothing to UIA (its NatTable tables, and the labels on its
tab bars).

```
order image
   │  (1 LLM call, cached by image hash)
   ▼
Extraction ─► JSON ─► Pydantic OrderData ─► deterministic validation ─► (1 retry with feedback) ─► manual review
   │
   ▼
Runner: stages of steps ─ every step = action ► verify ► annotated screenshot ► next
   │   order ─► debtor ─► products ─► save_order ─► invoice
   │
   ├─ ui/app.py       attach to Fakturama, find dialogs, FindFirst / FindAll
   ├─ ui/find.py      UIA snapshots (one cache request), lookup by type+name / label geometry
   ├─ ui/input.py     keystrokes with proven focus, combos, checkboxes, dates; read-back
   ├─ ui/ocr.py       Windows.Media.Ocr inside UIA-provided rectangles
   ├─ ui/table.py     NatTable reader: OCR words → rows → grid-line columns → cells
   ├─ ui/wait.py      bounded polling waits (no bare sleeps)
   ├─ match.py        NONE / EXACT / AMBIGUOUS / CONFLICT
   └─ ui/evidence.py  numbered annotated screenshots
   ▼
artifacts/<run-id>/  extracted.json · run.log · NN_<stage>_<step>.png · summary.json
```

**Result.** Live end to end: `status: ok` in **63.5 s** (PO000008 + INV000006, paid on 2026-07-18,
678.30; all master data selected). Before optimisation the same run took 156 s. 129 offline tests.

---

## 2. Extraction (image → data)

| Aspect | Decision | Why |
|---|---|---|
| Engine | LLM vision call: Anthropic `claude-haiku-5-5` by default, Gemini as a free-tier fallback, via `LLM_PROVIDER` | Robust to layout; one call per image |
| Output | Forced tool call / JSON schema (`record_order`) with a flat schema | No free-text parsing |
| Model | Pydantic `OrderData`; money as `Decimal`, rounded `ROUND_HALF_UP` to 2 places | No float errors |
| Validation | Deterministic: Σ line nets = net total, VAT and gross consistent (±0.01 per line), discount/VAT in [0,100], qty > 0, PAID ⇒ payment date, payment method mappable, currency EUR | The LLM is never trusted to be right |
| Retry | Once, with the list of issues **and** the previous answer, asking for transcription, not adjustment | Fixes misreads without inviting "made-up" numbers |
| Failure | Still invalid → `ManualReviewRequired` | No guessing |
| Cache | Key = sha256(image) + provider + model + prompt version; only validated results are stored, and they are re-validated on read | Zero tokens on re-runs; no stale bad data |
| Usage rule | The LLM is used **only** here; matching, navigation and verification are deterministic | Reproducible, auditable runs |

Fields are optional in the model but required by the validator, so a missing value becomes a named
issue (e.g. `missing_reference`) that the retry prompt can point to. Live: the sample extracted in
**1 attempt** (Gemini, about 6 s) and matches the expected fixture exactly. `--from-json` re-runs
the same validation without any LLM call.

**Sample data:** WEB-2026-0714-A17, 2026-07-14, Northstar Office GmbH (Marta Klein), billing
Friedrichstrasse 88, 10117 Berlin; delivery Northstar Office Warehouse, Beusselstrasse 44, 10553
Berlin. Lines: 2 × CHR-ERG-01 at 250.00 −10% @19% = 450.00, and 3 × MAT-DESK-02 at 40.00 @19% =
120.00. Totals 570.00 / 108.30 / 678.30. Bank Transfer, PAID 2026-07-18.

---

## 3. Control grounding (finding and operating controls without coordinates)

**Probe first.** Before writing UI code for a screen, it was dumped with `tools/dump_tree.py`
(UIA tree: control type, name, AutomationId, class, rectangle, patterns, plus a screenshot). The
selectors are built from those dumps. Several dumps are kept as offline test fixtures.

**Order of preference:**
1. **UIA by control type + name.** SWT names many inputs after their label (`Edit "Cust.Ref."`);
   buttons are named by their tooltip (`Button "Save the current contents"`).
2. **UIA label geometry.** The nearest input right of (or below) the `Text` label, vertically
   overlapping it. This handles unnamed fields (No., Date, the Net/Gross combo), duplicate labels
   (told apart by control type), two-column forms (stop at the next label) and one label for two
   fields ("ZIP - City", "First Name Last Name").
3. **UIA structure.** Unnamed action icons are the upper `Image` under a label (select existing,
   never the green +). The table rectangle is the largest pane containing only scrollbars. A tab
   bar is the parent folder's strip down to its first content child.
4. **OCR inside a UIA rectangle.** Table cells, row positions and tab labels. Clicks go to the
   centre of an OCR word box inside a rectangle that UIA provided.

There are **no fixed coordinates**: every click point comes from a UIA rectangle or an OCR word box
found in this run. Mouse and OCR share one pixel space (per-monitor DPI awareness).

**Tables (NatTable) are opaque to UIA** (probe-confirmed). The reader:
- groups OCR words into rows by y;
- takes column boundaries from the **grid lines** detected in the header strip (dark or light
  lines; a header-gap fallback exists);
- fuzzy-matches header text to canonical names, by position when a grey header can't be read;
- re-reads empty or partly read cells by pasting the column's header in front as context.

Rows are clicked at their OCR word boxes.

**Input** is chosen to change Fakturama's *data*, not just the widget:
- **Text:** real keystrokes, sent only after UIA reports keyboard focus on the target. UIA
  ValuePattern is only a fallback; it can change the widget but not the saved data, as seen live
  with the Company field.
- **Dates:** spinner widgets are driven with arrow keys per field. The field order is learned at
  run time and every keypress is read back.
- **Combos:** select, then typing, then a verified arrow-walk.
- **Checkboxes:** Toggle pattern, verified.
- **Tabs:** SWT tab items don't support UIA select, so the label is OCR'd on the tab bar and
  clicked, then the switch is verified.
- **Dialog rows:** double-click a data cell.
- **Item cells:** double-click → the inline `Edit` exposed by UIA → Enter, then the result is
  OCR'd back.

**Synchronisation:** `wait_until(condition, timeout)` polling, with no bare sleeps. A table counts
as "stable" after 2 identical OCR reads. Waits on a single control read only that control's live
property.

---

## 4. Matching & verification

### 4.1 Matching (deterministic, `match.py`)

Normalisation: Unicode NFKC, trim, collapse whitespace, casefold. Outcomes: `NONE` → create,
`EXACT` → select, `AMBIGUOUS` / `CONFLICT` → manual review.

| Entity | Searched by | EXACT | Stops (manual review) |
|---|---|---|---|
| Debtor | company, in *Select the address* | company + first name + name + ZIP + city equal | same company but other fields differ; > 1 exact |
| Payment method | name, in Data › terms of payment | name (and description) equal | > 1 with that name; differing definition |
| VAT | name `VAT <p>%`, in Data › VATs | name + value equal | same name, other value |
| Product | SKU, in *Select a product* | item number equal | duplicate SKU rows; differing description is a warning (configurable to stop) |

OCR-aware tolerances: truncated cells match by visible prefix (≥ 4 characters), and item numbers
fold look-alikes (O/0, I/1, S/5, B/8, Z/2) on **both** sides. Each tolerance only widens the set of
candidates; two candidates are `AMBIGUOUS` and stop the run, so a tolerance can never cause a
silent wrong pick.

### 4.2 Verification principles
- Every step = **action + verification**. The step's message (what was verified) goes into
  `summary.json`, with a green-captioned screenshot.
- **Read-back is not proof of persistence.** After every save, the record is read back from
  Fakturama itself:
  - Debtors list (company, first name, name);
  - terms of payment and VATs lists;
  - the saved order and invoice editors through UIA (Cust.Ref., date, paid);
  - Data › Documents.
- **Exact where possible:** UIA values (header fields, totals, address boxes) are compared
  exactly. OCR is used for tables, and money is compared as `Decimal` with ±0.01 tolerance.
- **Save exactly once per record.** Clicked once, then verified (Save disables again). Any dialog
  that appears is reported.

## 5. Failure handling

| Situation | Behaviour |
|---|---|
| Invalid extraction after 1 retry | `ManualReviewRequired` before touching Fakturama (exit 3) |
| AMBIGUOUS / CONFLICT match, missing data, unavailable payment method | `ManualReviewRequired(reason, step)`; red screenshot; `summary.json` lists the candidate rows (exit 3) |
| UI error (control missing, value didn't stick, timeout, Fakturama dialog during save) | Stop with the exact cause (exit 4); app left as it is |
| An unsaved editor of the same kind is already open | Refuse to start (no writing into a stale editor) |
| The order already holds a line for the SKU | Reuse it (only possible on a resumed run); never add twice |
| Keyboard focus can't be proven | Refuse to type |
| Every run | `summary.json` with status, entities selected/created, step log, warnings, timings, `perf` |

Resuming (development and operations): `--start-at STAGE` continues in the editors that are open.
It detects an already saved order or invoice tab and doesn't save it again.

## 6. The flow, step by step (brief → implementation → checks)

| Brief | Step | Action | Verified by |
|---|---|---|---|
| 1.1–1.8 | `order.open_order` | toolbar New Order (refuses if an unsaved one is open) | editor open, No. proposed and kept |
| | `order.set_header` | Date (arrow keys), Cust.Ref. (keys) | read-back; date re-checked after focus leaves; editor dirty |
| | `order.set_modes` | Net, With VAT | read-back |
| | `order.verify_header` | — | independent re-read of all header fields |
| 2.1–2.4 | `debtor.find_debtor` | upper Addresses icon → search company → OCR → match | EXACT → double-click; NONE → cancel; else stop |
| 2.10 | `debtor.ensure_payment_method` | (create path) Data › terms of payment; create with code mapping if missing | listed after save with name/description |
| 2.5–2.12 | `debtor.create_debtor` | New Contact; company, name, main address (Invoice role), additional address (Delivery role, G1), alias, Net, payment; save once | read back from the Debtors list (company + names) |
| 2.13 | `debtor.select_created_debtor` | search again, must be EXACT, double-click | — |
| | `debtor.verify_addresses` | — | order's invoice and delivery boxes equal the source |
| 3.1–3.6 | `products.ensure_vats` | Data › VATs; create `VAT 19%` (code S, value 19) if missing | listed with value |
| 3.7–3.12 | `products.add_line_N` | upper Items icon → search SKU → match; create product (gross 297.50 / 47.60, cost 0, stock 0, VAT by description) if missing | the line for the SKU appears exactly once |
| 3.13–3.17 | `products.set_line_N` | Qty, Discount (U.Price if needed) via inline cell edit | OCR read-back of Qty, U.Price, VAT, Discount and **line price** |
| 4.1–4.3 | `save_order.check_order` | order discount 0%, free shipping | addresses + all lines + totals vs source |
| 4.4 | `save_order.save_order` | Save once | Save disabled; tab renamed; Cust.Ref. and Date re-read exactly |
| 4.5 | `save_order.verify_in_documents` | Data › Documents, search the order number (view maximized) | Date, Total, state "open"; Cust.Ref. (OCR warning + exact editor check) |
| 4.6 | `save_order.create_invoice` | the order's **follow-up › Invoice** (not the toolbar) | invoice editor open, number recorded |
| 5.1 | `invoice.check_invoice` | — | copied Cust.Ref., Order Date, Net / With VAT, invoice + delivery address, lines, totals |
| 5.2–5.3 | `invoice.set_payment` | method; PAID → tick paid, date (arrow keys), Value = total | independent re-read of all four |
| 5.4 | `invoice.save_invoice` | Save once | Cust.Ref. and paid re-read from the saved editor |
| 5.5 | `invoice.verify_documents` | search the invoice number, then the order number | invoice "paid" + total; order still "open" + total |

## 7. Live findings that shaped the design

Each finding was caught by a verification step, then fixed. Most are now regression tests.

| Finding | Consequence |
|---|---|
| Date text set via UIA reads back correctly, then reverts to today | arrow-key date entry plus a re-check after focus leaves |
| Company set via ValuePattern shows on screen but is saved empty | keystrokes first; persisted values read back from lists and saved editors |
| SWT tab items: no UIA select, unscaled rectangles | OCR the tab bar and click the label; verify the switch |
| Single click doesn't select dialog rows | double-click a data cell |
| "Select a product" picks a typed item number by itself and closes | accepted only if exactly one new line for that SKU appears |
| Keys sent while the main window was foreground landed in the Items table, which then added a line | type only with proven keyboard focus |
| Windows OCR drops short tokens and parts of cells ("0", "VAT 19%" → "VAT"), and misreads "%" | header-context re-reads; "0/0" → "%" |
| Selected row: grey header with light grid lines, header text unreadable | two-polarity line detection; positional header names when consistent |
| Narrow views truncate cells ("POOO...", "$678...") | Documents read maximized; truncation-aware matching |
| The invoice form scrolls; the Items pane runs under the totals | Items rectangle cut at the Remarks label |
| Fakturama NPE in `DocumentEditor.doSave` after heavy manual line edits | save-dialog detection; documented as a Fakturama issue |
| A fresh DB has only VAT "Tax-free" and payment "Cash" | VAT and payment-method creation are required, not optional |

## 8. Trade-offs & alternatives considered

| Alternative | Why not (or not primarily) |
|---|---|
| **OCR-first extraction** | Cheaper, but brittle on layout; kept as a stretch goal with LLM fallback |
| **Vision-model clicking** (LLM looks at screenshots and decides) | Non-deterministic, slow, hard to verify; the brief asks for verification and clear stops |
| **Writing to Fakturama's database / API directly** | Fast, but bypasses Fakturama's business logic, numbering and editors, and isn't the UI automation the brief asks for |
| **Fixed coordinates / image templates** | Break on resolution, DPI, layout or theme changes; forbidden by the brief |
| **UIA only** | Impossible: NatTable tables expose no rows, and tab items can't be selected via UIA |
| **ValuePattern for all input** | Fast, but proven unreliable for persistence in SWT |
| **Full-window UIA snapshots everywhere** | Simple, but 0.7–0.9 s each; pane-scoped reads and `FindFirst` are 5–100× cheaper |

**Performance trade-off.** Every brief-required check is kept. Speed came from making each check
cheaper, not from skipping checks: 156 s → **63.5 s**.

| | Before | After |
|---|---|---|
| UI-tree reads | 127 full-window × 0.92 s = 117 s | 111 snapshots, mostly pane-scoped (18.2 s) + 109 `FindFirst` (11.0 s) + 6 tab-name queries (3.2 s) = 32.4 s |
| OCR | 4.5 s | 3.0 s |
| Screenshots | 3.7 s (encoding on the main path) | 2.9 s (encoding in the background) |

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| OCR misreads | Used only for tables and tab labels; tolerances lead to AMBIGUOUS rather than wrong picks; exact UIA re-reads wherever a value exists in UIA; regression tests on live captures |
| Fakturama UI changes (labels, layout) | All labels in `config/settings.yaml`; geometry is relative to labels, never absolute; probe tool to re-derive selectors |
| Different screen size / DPI | Per-monitor DPI awareness; no coordinates; OCR scale configurable |
| User interaction during a run | Documented as not allowed; focus is proven before typing; the mouse is parked after clicks |
| Long tables | Not handled: visible rows only (searches narrow lists); scrolling is future work |
| Fakturama-internal errors | Save dialogs are detected and reported; app left as it is for a human |
| LLM misextraction | Deterministic validation, one retry with feedback, then manual review |
| Duplicate records from re-runs | The select path matches existing debtor, VAT, payment and products; duplicate line guard; stale-editor guard |

## 10. Testing & evidence

- **129 offline tests** (`pytest -q`), covering:
  - calc and validation (golden fixture plus mutations);
  - extraction service with a fake provider (retry, cache);
  - matching (all four outcomes and tolerances);
  - table assembly (synthetic and live OCR captures);
  - UIA lookups replayed from real probe dumps;
  - input helpers (dates, focus guard);
  - the runner (ok / manual review / error);
  - document checks.
- **Live runs** cover the create paths (empty database) and the select paths (second run). The
  latest full run passed in 63.5 s.
- **Evidence per run:** an annotated screenshot per step, `summary.json` (entities
  selected/created, step messages, warnings, timings, perf) and `run.log`.
- **Tools:**
  - `dump_tree.py`: UIA probe;
  - `read_table.py`: live table read;
  - `bench_uia.py`: read-only cost of UIA strategies.

## 11. Ambiguities in the brief (decisions)

- **G1** Delivery ≠ billing: a second Debtor address with the Delivery role, its name in
  "additional name".
- **G2** The proposed Customer ID is kept; `CUST-1007` is extracted but not used.
- **G3** Contact split: first token = first name, the rest = last name.
- **G4** "Exact" = equal after whitespace/case normalisation.
- **G5** Table rows are read via OCR.
- **G6** A differing product description is a warning (configurable to stop).
- The payment method is created before the Debtor (no unsaved editor in the background).
- The unit ("pcs") isn't entered: there's no field for it.
- An existing VAT's code isn't re-checked: there's no column for it.
- Documents are located by number; Cust.Ref. there is OCR-compared with a warning, and exact in
  the saved editor.

## 12. Limitations & next steps

**Limitations:**
- no table scrolling;
- an existing VAT's code isn't re-checked;
- the negative ZIP-conflict test hasn't been run live;
- only the sample image was run;
- Fakturama must already be running (English UI);
- no screen recording yet.

**Next steps:**
1. A recorded clean run on a fresh database, the same image again, and the negative test.
2. Fewer OCR verifications (reopen saved documents and read via UIA).
3. Table scrolling.
4. More speed: cache pane subtrees across consecutive checks; fewer address-tab switches.
5. OCR-first extraction with LLM fallback.
6. CI.
