# CLAUDE.md — Fakturama Image-to-Cash (job-application take-home)

You are helping implement a take-home project. The full agreed plan is in `PLAN.md` — read it
completely before writing code, and follow it. Do not redesign agreed decisions without asking.

## The task in one paragraph
Given one order image (`samples/order_01.png`), extract the order data with an LLM, then drive the
Fakturama desktop app (Java/SWT, English UI, Windows) via Microsoft UI Automation (pywinauto, UIA
backend) to: open a New Order, select-or-create the Debtor, select-or-create each Product (and its
VAT), fill the item lines, save the Order, create the linked Invoice via the Order's
"Create a follow-up document > Invoice", set the paid status, and verify every step. Ambiguous or
conflicting matches stop the run for manual review.

## Environment
- Windows 10/11, Fakturama installed with English UI, IntelliJ IDEA with the Python plugin.
- Python 3.12, virtualenv at `.venv`, `src/` layout (package `i2c`), installed with `pip install -e .[dev]`.
- Company stack is Python. LLM: Anthropic (default, `claude-haiku-5-5`, configurable) or Gemini
  (free tier fallback). Keys come from `.env` (never commit it).

## Hard rules
1. **No hardcoded screen coordinates.** Locate controls via UIA (control type + name/label), or via
   Windows OCR inside a rectangle obtained from UIA. Clicks on table rows use OCR word boxes.
2. **LLM is used exactly once per image** (extraction), cached by image hash. No LLM in matching,
   verification or navigation — those are deterministic Python.
3. **Every step = action + verification.** Use `wait_until(condition, timeout)` polling, never bare
   `time.sleep()` for synchronisation.
4. **Save controls are clicked exactly once** per record, then verified.
5. Ambiguity/conflict → raise `ManualReviewRequired(reason, step)`; write summary, leave app as-is.
6. Money is `Decimal`, rounded `ROUND_HALF_UP` to 2 places. Never float.
7. Every flow step saves an annotated screenshot to `artifacts/<run-id>/` (deliverable).
8. Keep UI label strings in `config/settings.yaml`, not scattered in code.

## How we work (important)
- I (the user) run anything that touches Fakturama. You cannot see my screen.
- **Probe first:** before writing any UI code for a screen, ask me to run `tools/dump_tree.py`
  for that screen and paste the output. Build selectors from real output, not guesses.
  Key unknown: whether NatTable tables (selector dialogs, Items, Data > Documents) expose rows/cells
  via UIA. Expected: they don't → OCR path (`ui/table.py`).
- Develop stage by stage using `--stop-after <stage>` and `--from-json <extracted.json>` so we
  don't re-extract or re-run finished stages.
- Keep offline unit tests green (`pytest`): calc, validate, match, table grouping, extraction
  service with a fake provider.
- Commit after each working milestone with a clear message.
- Record skipped items and decisions in README as you go (the README is graded).

## Commands
- `python -m i2c extract samples/order_01.png` — extraction only, prints/saves JSON
- `python tools/dump_tree.py --window "<title regex>" --out artifacts/probe/<name>` — UIA probe
- `python -m i2c run samples/order_01.png [--from-json PATH] [--stop-after STAGE]`
- `pytest -q`
