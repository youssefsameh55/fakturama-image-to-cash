# Fakturama Image-to-Cash

Turns one order image into a saved Fakturama **Order** and its linked **Invoice** (with the payment
status set), verifying every step. The order data is extracted once by an LLM vision call. Fakturama
is then driven through Microsoft UI Automation (pywinauto, UIA backend), with Windows OCR used only
for Fakturama's custom-drawn tables. There are no hardcoded screen coordinates. Anything ambiguous or
conflicting stops the run for manual review and leaves Fakturama as it is.

- `DESIGN.md`: architecture, decisions, verification strategy, failure handling, trade-offs (Part 1 draft)
- `PLAN.md`: the agreed implementation plan the work followed

## Status

| Milestone | State |
|---|---|
| M1 Skeleton + UIA probe tool | done |
| M2 Extraction + offline tests | done; the live call matches the expected fixture exactly (Gemini, 1 attempt) |
| UIA probe of the screens | done (`artifacts/probe/`, findings in "UI layer") |
| M3 UI toolkit (find / wait / input / OCR / tables / evidence) | done, verified live |
| M4 Run framework + stage `order` | done, verified live |
| M5 Stage `debtor` (match, payment method, create, select) | done; create path and select path verified live |
| M6 Stage `products` (VAT, products, item lines) | done; create path and select path verified live |
| M7 Stages `save_order` + `invoice` | done, verified live |
| **End-to-end run** | **`status: ok` in 63.5 s** (run `20261107-054628`: PO000008 + INV000006, paid, all entities *selected*) |
| M8 Deliverables | README done, `DESIGN.md` drafted; screen recording pending |

Latest end-to-end timing (second run on the same image, so all master data is selected):

```
  status      : ok  (exit 0)
  order       : PO000008    invoice: INV000006
  entities    : {'debtor': 'selected', 'vat': {'VAT 19%': 'selected'},
                 'product': {'CHR-ERG-01': 'selected', 'MAT-DESK-02': 'selected'}}
  prepare     :     0.6 s   (extraction/JSON + attach)
  order       :     8.1 s
  debtor      :     5.7 s
  products    :    13.9 s
  save_order  :    14.2 s
  invoice     :    20.8 s
  TOTAL       :    63.5 s
```

The *create* paths (new Debtor with two addresses, new payment method, new VAT, new products)
were verified live in earlier runs of the same stages.

## Setup

Requirements: Windows 10/11, Fakturama 2.x running with the English UI, Python 3.12, and Windows OCR
for en-US (installed with the English language pack).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env   # then set ANTHROPIC_API_KEY, or GEMINI_API_KEY with LLM_PROVIDER=gemini
```

Start Fakturama, maximize it, close any open editor tabs, and don't touch mouse or keyboard
during a run. The automation must run at the same privilege level as Fakturama.

## Usage

```powershell
python -m i2c run samples/order_01.png                  # full flow (extraction cached after the first call)
python -m i2c run --from-json <extracted.json>           # same, without the LLM
python -m i2c extract samples/order_01.png              # extraction only -> artifacts/<run-id>/extracted.json

# development
python -m i2c run --from-json <json> --stop-after order       # run stage by stage
python -m i2c run --from-json <json> --start-at save_order    # continue in the editors already open
python tools/dump_tree.py --window ".*Fakturama.*" --delay 5 --out artifacts/probe/<name>  # UIA probe
python tools/read_table.py --view "VATs" --kind vats          # read one table via UIA rect + OCR
python tools/bench_uia.py                                     # read-only cost of UIA access strategies
pytest -q                                                      # 129 offline tests
```

Stages, in order: `order`, `debtor`, `products`, `save_order`, `invoice`.
Exit codes: 0 ok · 2 usage · 3 manual review · 4 error.

### What a run produces

`artifacts/<run-id>/`:

- `extracted.json` (validated order data) and `run.log`.
- One annotated screenshot per step, `NN_<stage>_<step>.png`. A green banner means verified, red
  means stopped; the controls involved are outlined.
- `summary.json`:
  - status (`ok` | `manual_review` | `error`), order and invoice numbers;
  - per entity whether it was **selected or created** (debtor, payment method, each VAT, each product);
  - every step with its verification message, duration and screenshot;
  - warnings (e.g. an OCR'd value that was also checked exactly elsewhere);
  - `stage_durations_s`, `total_s` (wall clock, including extraction and attaching);
  - `perf`: counts and time of UIA reads (per calling function), OCR passes and screenshots.

The timing block above is printed to the console and written to `run.log` at the end of every run,
including failed ones.

## How it works (short; details in `DESIGN.md`)

1. **Extract:** one LLM call with a forced JSON tool schema, then deterministic validation (line
   sums, VAT, totals, dates, PAID needs a date). On errors it retries once with the problems
   listed; if it still fails, the run stops for manual review. The result is cached by image hash.
2. **Drive Fakturama:** a runner executes stages of steps. Each step does one action, then
   **verifies** it (read-back via UIA, or OCR for tables), takes a screenshot, and only then
   continues. Every save is clicked once and then verified.
3. **Match deterministically:** debtor, payment method, VAT and product are each `NONE` (create),
   `EXACT` (select), `AMBIGUOUS` or `CONFLICT` (stop for manual review). No LLM is involved.

## Decisions on ambiguities in the brief

From `PLAN.md` §9:
- **G1** Billing ≠ delivery in the sample, while the brief only covers identical addresses: the
  delivery address becomes a second Debtor address with the Delivery role.
- **G2** `CUST-1007` is extracted, but Fakturama's proposed Customer ID is kept.
- **G3** Contact name split: first token = first name, the rest = last name.
- **G4** "Exact" means equal after whitespace/case normalisation.
- **G5** Table rows are read with OCR because NatTable exposes no rows to UIA (confirmed by probe).
- **G6** An existing SKU with a different description is a warning (configurable to stop).

Further decisions:
- **Extraction:**
  - Fields are optional in the model but required by the validator, so a missing value becomes a
    named error the retry prompt can point to.
  - The retry shows the previous answer and asks for transcription, not "fixing" numbers.
  - Only validated results are cached, and a cached result is re-validated on read.
  - A forced tool call is used, falling back to `tool_choice=auto` for models that reject it.
- **VAT tolerance** is ±0.01 per line (Fakturama may round VAT per line).
- **The payment method is created before the Debtor** (only when the Debtor must be created), so
  that no unsaved editor waits in the background during tab switches. The result is the same as the
  brief's order.
- **The delivery address name** ("Northstar Office Warehouse") goes into that address's
  "additional name". The order's address box doesn't display it, so the order check compares
  company, street, ZIP, city and country.
- **The unit ("pcs")** is extracted but not entered: Fakturama's item line has no unit field.
- **Documents are found by their number** (order, then invoice). The Cust.Ref. shown there is OCR'd
  and compared loosely (warning only), because its exact value is re-read via UIA from the saved editor.

## UI layer: what the probes and live runs showed

### Tables (NatTable) and OCR
- **Tables are opaque to UIA.** Selector dialogs, Items, Documents, VATs and terms of payment are
  empty `Pane`s in UIA. UIA gives the rectangle (the largest pane that contains only scrollbars, or an
  open inline cell editor); OCR runs inside it only.
- **Columns come from the grid lines** in the header strip (dark or light), not from header text
  positions: headers are centred in some tables and left-aligned in others.
- **Headers are fuzzy-matched** to names in `settings.yaml` ("Com an" → Company). When a selected row
  turns the header grey and OCR misses it, names are assigned by position, but only if the column
  count matches and every header that was read sits in its expected place.
- **Dropped or partial cells are re-read with context.** Windows OCR drops short tokens ("0", "2",
  "0%") and parts of cells ("VAT 19%" → "VAT"). The column's own header is pasted in front of the cell
  and that image is OCR'd again, trying a few layouts. This only happens for columns some check reads.
- **OCR repairs:** "0/0" → "%" (standalone tokens only). Truncated cells ("Northstar Offic...",
  "Northstar Office . „") match by visible prefix (≥ 4 characters). Item numbers fold OCR look-alikes
  on both sides ("CHR-ERG-OI" = "CHR-ERG-01"). Grid strokes OCR'd as "I", "|" or "1" are dropped.
  Every relaxation turns an ambiguity into `AMBIGUOUS`, which stops the run.
- **No upscaling** (`ocr.upscale: 1`): on a 3000-px-wide screen, the plan's 2× made OCR worse.
- **The invoice editor is a scrolled form.** Its Items pane runs on under the Remarks/totals panel,
  so the table rectangle is cut at the "Remarks" label.
- **Data › Documents is maximized while it's read** (via its own Maximize button), because a narrow
  layout truncates every cell. It's always restored afterwards.

### Controls and input
- **Controls are found by label.** SWT names many inputs after their label; others are found as the
  nearest input right of the `Text` label. Duplicate labels ("VAT") are told apart by control type.
  Two-column forms and one-label-two-field rows ("ZIP - City") are handled.
- **Action icons** (select existing contact or product) are unnamed `Image`s; the run takes the upper
  one stacked under the label, never the green +.
- **Text is typed with real keystrokes, and only with proven keyboard focus.** A Company set through
  UIA ValuePattern showed on screen but was saved **empty**. In another run, keys meant for a dialog
  reached the Items table, which added a line by itself. Every value is read back, and saved records
  are read back too (Debtors list, terms of payment list, saved editors).
- **Dates use arrow keys per field.** Text set through UIA reverted to today. The field order (month,
  day, year) is learned at run time; every keypress is read back.
- **Combos:** pywinauto select, then typing, then arrow-walk, each verified (surrounding whitespace
  ignored).
- **Clicks use the real mouse** at UIA rectangle centres or OCR word boxes. UIA Invoke can block on
  modal dialogs. The mouse is then parked on the title bar, because tooltips covered OCR'd rows.
- **Tabs:** SWT tab items don't support UIA select and report unscaled rectangles. The run OCRs the
  tab bar (rectangle from the parent folder in UIA), clicks the label and verifies the switch. Label
  positions are reused while the folder's tab list is unchanged.
- **Selector rows are picked by double-click** on a data cell; a single click didn't select.
  "Select a product" may pick a typed item number by itself and close; that's accepted only if exactly
  one line for that SKU appears.
- **Address type** is set through a popup with role checkboxes, closed with Esc. The payment method
  sits on the Debtor's Miscellaneous tab.
- **Line cells** are edited through the inline `Edit` that UIA exposes inside the table. A line
  discount is displayed negative ("-10.00 %").

### Safety guards
- The run refuses to start an editor if an unsaved one of the same kind is open, and refuses to add
  a line for a SKU already in the order.
- Every save is clicked once and then verified. A Fakturama dialog appearing during Save stops the
  run with its text: "Document number is not the next free one" was caught this way.
- One live session hit a Fakturama-internal `NullPointerException` in `DocumentEditor.doSave`
  (`~/.fakturama2/.metadata/.log`) on an order whose lines had been deleted and re-added by hand many
  times. Fresh orders (PO000003 to PO000008) saved normally.

### Performance (156 s → 63.5 s, with every required check kept)
- `perf` showed about 75% of a run spent reading the UI tree: 0.7–0.9 s per full-window read, growing
  with every open editor and view. OCR (about 3–4 s per run) and screenshots (about 3 s) were minor.
- Fixes:
  - single controls are found with UIA `FindFirst`;
  - waits poll one control's live property (e.g. Save's `IsEnabled`);
  - editor and view reads cover only that pane;
  - tab names come from one `FindAll`;
  - snapshots use one UIA cache request;
  - screenshots are encoded in a background thread;
  - waits poll every 0.1 s and keys are sent 10 ms apart.
- Latest run: 111 snapshots, mostly pane-scoped (avg 164 ms, 18.2 s), 109 single lookups (11.0 s), 6
  tab-name queries (3.2 s), 369 OCR passes (3.0 s), 26 screenshots (2.9 s).

## Skipped / not implemented

- **Screen recording:** not made yet. The annotated per-step screenshots cover the evidence requirement.
- **An existing VAT's code (S) isn't re-checked:** the VATs list has no code column. New VATs get code S.
- **The negative test** (same company, different ZIP → manual review) is unit-tested on the matcher
  but hasn't been run against Fakturama.
- **No scrolling in tables:** only visible rows are read. Searches narrow the lists down, and the
  sample has 2 lines.
- **Launching Fakturama** is out of scope (it must be running, English UI, maximized).
- **Only the sample order was run.** No photos or skewed scans were tried.
- **Test data:** the development database contains several test orders and invoices
  (PO000001–PO000008, INV000001–INV000006, one incomplete PO000002).

## If I had 3 more hours

1. **A recorded clean run on a fresh database** (all create paths), then the same image again (all
   select paths), plus the negative ZIP-conflict test.
2. **Less OCR in verification:** reopen saved documents and read their fields via UIA, keeping OCR
   for choosing rows only.
3. **Table scrolling** for long orders and large lists.
4. **More speed:** about half of the remaining 63 s is deliberate verification. Next would be
   caching pane subtrees between consecutive checks of the same editor and cutting repeated tab
   switches during address checks.
5. **OCR-first extraction with LLM fallback** (stretch goal) and tests on photographed orders.
6. **CI** for the 129 offline tests, which already replay real UIA dumps and live OCR captures
   from `tests/fixtures/`.
