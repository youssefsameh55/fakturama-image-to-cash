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
| M5–M7 Flow stages | not started |
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
- **Text input tries UIA ValuePattern first, then the keyboard, and every value is read back.**
  The order is configurable (`input.text_methods`), because SWT data binding may only commit
  values entered with real key input.
- **The fresh Fakturama database has only VAT "Tax-free 0%" and payment term "Cash".** So creating
  the VAT rate and the payment method is required for the sample order, not an optional extra.
- **The Debtor's payment method is on the Miscellaneous tab.** There is no Payment tab.

## Skipped / not implemented

_To be filled in as the work goes on._

## If I had 3 more hours

_To be written at M8._
