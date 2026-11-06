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
| M2 Extraction + offline tests | in progress |
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

See `PLAN.md` §9 (G1–G6). This list will be completed as the implementation goes.

## Skipped / not implemented

_To be filled in as the work goes on._

## If I had 3 more hours

_To be written at M8._
