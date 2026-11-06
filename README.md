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
| M2 Extraction + offline tests | done (offline); live call pending |
| M3–M7 UI toolkit and flow | not started (waiting for probe output) |
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
python tools/dump_tree.py --list                       # list Fakturama's top-level windows
python tools/dump_tree.py --window ".*Fakturama.*" --out artifacts/probe/main
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

## Skipped / not implemented

_To be filled in as the work goes on._

## If I had 3 more hours

_To be written at M8._
