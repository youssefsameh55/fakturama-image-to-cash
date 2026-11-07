# Fakturama Image-to-Cash

Turns one order image into a saved Fakturama **Order** and its linked **Invoice** (with the
payment status set), checking every step. Order data is extracted once by an LLM vision call.
Fakturama is then driven through Microsoft UI Automation (pywinauto, UIA backend), with Windows
OCR used for Fakturama's custom-drawn tables. No hardcoded screen coordinates.

See `PLAN.md` for the full design. `DESIGN.md` (Part 1) will be written after the UI probe.

## Status

| Milestone | State |
|---|---|
| M1 Skeleton + UIA probe tool | done |
| M2 Extraction + offline tests | done; live call matches the fixture exactly (Gemini, 1 attempt) |
| UIA probe of 10 screens | done (`artifacts/probe/`, findings below) |
| M3 UI toolkit (find/wait/input/ocr/table/evidence) | done; checked live on VATs, terms of payment and Select the address |
| M4 Run framework + stage `order` | done; live run verified (PO000001, Jul 14 2026, Cust.Ref., Net, With VAT) |
| M5 Stage `debtor` (match / payment method / create / select) | done; verified live (create path and select-existing path) |
| M6 Stage `products` (VAT, products, lines) | done; verified live (VAT + products created, lines set and read back, 27.7 s) |
| M7 Stages `save_order` + `invoice` | code done, offline-tested; live run pending |
| M8 Deliverables | not started |

## Setup

Requirements: Windows 10/11, Fakturama (English UI), Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env   # then fill in ANTHROPIC_API_KEY (or GEMINI_API_KEY + LLM_PROVIDER=gemini)
```

## Usage

```powershell
python -m i2c extract samples/order_01.png            # extraction only -> artifacts/<run-id>/extracted.json
python -m i2c run --from-json <extracted.json> --stop-after order   # drive Fakturama, stage by stage
python tools/dump_tree.py --list                       # list Fakturama's top-level windows
python tools/dump_tree.py --window ".*Fakturama.*" --delay 5 --out artifacts/probe/main
python tools/read_table.py --view "VATs" --kind vats   # live: read a table via UIA rect + OCR
pytest -q                                              # offline tests
```

## Decisions on ambiguities in the brief

See `PLAN.md` §9 (G1–G6). Additional implementation decisions:

- **Fields are optional in the model, required by the validator.** A missing value comes back as a
  named validation error (e.g. `missing_reference`) instead of a generic parse error, so the retry
  prompt can say exactly what to re-read.
- **Retry feedback includes the previous answer** and tells the model to transcribe, not to adjust
  numbers until the checks pass.
- **Only validated extractions are cached.** A cached entry is re-validated on read; if it no
  longer passes (e.g. after a rule change), it is ignored.
- **Forced tool call with fallback.** `claude-haiku-5-5` accepts the forced `record_order` tool
  call. If `ANTHROPIC_MODEL` is set to a model that rejects forced `tool_choice` (Opus 5.5,
  Sonnet 5.5), the provider retries once with `tool_choice=auto`.
- **VAT total tolerance is ±0.01 per line**, because Fakturama may round VAT per line.
- **Exit codes:** 0 ok · 2 usage · 3 manual review · 4 provider/config error.

### UI layer (from the probe)

- **Tables are read with OCR, as expected.** Fakturama's NatTable tables (selector dialogs,
  Items, Documents, VATs, terms of payment) show up in UIA as empty `Pane`s with no header or
  cell children. UIA still supplies the table's rectangle: it is the largest pane whose only
  children are scrollbars. OCR runs inside that rectangle only.
- **Column boundaries come from the grid lines, not from header text positions.** Header text is
  centred in some tables and left-aligned in others, so midpoints between header words would
  put long cell text in the wrong column. The reader finds the vertical grid lines in the header
  strip of the captured image instead, with a header-gap fallback.
- **OCR headers are fuzzy-matched to the names in `settings.yaml`.** For example, "Com an" maps
  to "Company" and "Cit" to "City". Exact matches are assigned first, so "Name" never takes
  "First Name".
- **No OCR upscaling (`ocr.upscale: 1`)**, unlike the plan's 2×. On the 3000-px-wide test screen,
  upscaling the probe screenshots made recognition worse. The setting is still configurable.
- **Cells that OCR drops are read again with context.** Windows OCR drops short tokens that stand
  alone ("0", "2", "0%"). Padding, scaling and tight crops didn't help. If a cell came back empty
  but still contains dark pixels, the column's own header (cropped from the same image) is pasted
  in front of the cell, that small image is OCR'd again, and only the words right of the header
  are kept. A lone "O" is read as zero. The live payment row went from `Cash | Pay Cash` to
  `Cash | Pay Cash | 0% | 0 | 0`.
- **`%` misreads are repaired.** Windows OCR reads "0.00 %" as "0.00 0/0". The repair only applies
  to a standalone token, so dates are left alone.
- **Controls are found by label.** SWT names many inputs after their label (`Edit "Cust.Ref."`);
  the rest (No., Date, the Net/Gross combo) are found by geometry, as the nearest input to the
  right of the `Text` label. Labels that occur twice ("VAT") are told apart by control type. Forms
  with two columns and one label for two fields ("ZIP - City") take only the inputs before the
  next label.
- **Date fields are set with the arrow keys, not typed text.** The first live run showed that text
  set through UIA ("Jul 14, 2026") read back correctly at first, then the widget redrew from its
  own value ("Nov 7, 2026"). The final independent re-read caught this. Dates are now set like a
  user would: click the field's left edge, find the field order by pressing Up on each field and
  seeing which part changed, then step year, month and day to the target. Every keypress is read
  back. The date is checked again after focus leaves the field.
- **Clicks use the real mouse at the centre of the control's UIA rectangle.** UIA `Invoke` on an
  SWT button that opens a modal dialog can block until the dialog closes.
- **Text input uses real keystrokes first, then UIA ValuePattern, and every value is read back.**
  The plan put ValuePattern first, and the live runs changed that. A Debtor's Company set through
  ValuePattern showed in the editor and read back correctly, but was saved **empty**: the widget
  changed, Fakturama's data didn't. So read-back alone isn't proof. After saving, records are
  read back from Fakturama's own lists (Debtors, terms of payment). The order of methods is
  configurable (`input.text_methods`).
- **The fresh Fakturama database has only VAT "Tax-free 0%" and payment term "Cash".** So creating
  the VAT rate and the payment method is required for the sample order, not an optional extra.
- **The payment method is created before the Debtor, not halfway through it.** The plan creates
  it while the Debtor editor is open. Doing it first gives the same result without an unsaved
  editor waiting in the background during a tab switch. It only runs when the Debtor has to be
  created.
- **A different delivery address becomes "additional address #1" with only the Delivery role**
  (G1). Its name ("Northstar Office Warehouse") goes into that address's "additional name" field,
  since the Debtor has a single company name.
- **Action icons are found by structure.** The "select existing" icons next to Addresses and
  Items are unnamed UIA `Image`s, so the stage picks the upper icon stacked under the label,
  never the green +.
- **`--start-at STAGE`** (development only) continues in the editors that are already open, so a
  stage can be re-run without redoing the previous ones.
- **Selector rows are chosen by double-click.** One click on a row (on the row number or on a data
  cell) didn't select it, so OK filled nothing into the order. Double-clicking a data cell, at a
  point taken from the OCR word box, picks the row and closes the dialog. If the dialog is still
  open afterwards, OK is pressed once. The order's address box is then checked against the source.
- **Truncated table cells** ("Northstar Offic...", and its OCR variants such as
  "Northstar Office . „") match when the source value starts with the visible text (at least 4
  characters). If two rows could match that way, the run stops for manual review.
- **Item numbers are compared with OCR look-alikes folded on both sides** (O/0, I/l/1, S/5, B/8,
  Z/2). Live, OCR read "CHR-ERG-01" as "CHR-ERG-OI". If two real SKUs differ only by such a
  character, both rows match, so the run stops for manual review.
- **An existing VAT's code isn't re-checked.** The VATs list has no VAT-code column, so reuse
  checks Name and Value only. A newly created VAT always gets code S.
- **Item-line cells are edited in place.** The run double-clicks the cell (column from the header
  grid lines, row from the OCR box), types into the inline `Edit` that UIA exposes inside the table,
  and presses Enter (then Esc if the editor stays open). Every value is checked by OCR. A line
  discount is shown as negative ("-10.00 %"), so the check ignores its sign.
- **The run never types without verified keyboard focus.** In one live run, keystrokes meant for a
  dialog's search box reached the order's Items table, which accepts a typed item number and
  added a product line by itself. Before any keystroke, the target control must report keyboard
  focus through UIA (set by UIA focus, else by clicking into it); otherwise the step stops. Keys
  are sent without forcing the main window to the foreground. Selector dialogs count as open only
  once their search box exists.
- **The UI tree is read with one UIA cache request.** Reading the tree property by property cost
  about 1–2.5 s per read on Fakturama's window, several times per step. One cache request now
  returns the whole control-view subtree with the six properties used (same tree as before,
  checked against the old walk). An editor that's already active reuses its snapshot.
- **"Select a product" can pick by itself.** Live, typing a complete item number made the dialog
  add the product and close. The run accepts that only if exactly one line for that SKU then
  appears in the Items table; otherwise it stops.
- **The mouse is parked on the title bar after each click.** Hover tooltips covered the Items rows
  that are read by OCR.
- **Polling uses cheap reads.** While waiting for a cell value, the run does one OCR pass of the
  already known table rectangle (about 0.1–0.2 s) instead of a full UIA walk of the window (about
  1.5 s). The final verification still uses a stable double read.
- **The unit ("pcs") is extracted but not entered.** Fakturama's item line has no unit field.
- **Fakturama save errors are detected, and one was traced to Fakturama itself.** If a dialog
  appears after Save, the run stops and reports its text instead of waiting. In one live session
  the order editor failed to save with `NullPointerException ... DocumentEditor.doSave
  (DocumentEditor.java:412)` (from `~/.fakturama2/.metadata/.log`). That editor had had item
  lines deleted and re-added by hand several times; the order's data had passed every check. A
  clean end-to-end run on a fresh order is the check (result recorded under Status).
- **The Debtor's payment method is on the Miscellaneous tab.** There is no Payment tab.

## Skipped / not implemented

_To be filled in as the work goes on._

## If I had 3 more hours

_To be written at M8._
