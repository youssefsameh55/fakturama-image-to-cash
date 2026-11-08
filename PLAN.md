# Fakturama Image-to-Cash — Final Implementation Plan

LLM-first extraction; Windows OCR used only for reading Fakturama's custom-drawn tables.
OCR-based image extraction is a stretch goal (`EXTRACTOR=ocr|auto`) only if time remains.

> **Updated 2026-11-07 to match the final code.** Where the implementation differs from the
> original plan, the text below describes what the code does; §14 lists every change and why.

---

## 0. Goal & deliverables

**Goal:** `image → saved Order → linked Invoice (paid status) → verified`, every step checked, clean
stop for manual review on ambiguity.

**Deliverables (from the brief)**
- Part 1 — `DESIGN.md`, 1–4 pages, no code (timebox 90 min).
- Part 2 — Git repo (timebox 5 h): source code with clear structure; setup instructions
  (dependencies + how to run against Fakturama); annotated screenshots or a short recording;
  README listing anything skipped; written answer: *"If you had 3 more hours, what would you do?"*

**Required approach (from the brief):** Microsoft UI Automation (UIA), OCR and/or LLMs. No reliance
on hardcoded coordinates or a fixed UI layout.

---

## 1. Environment & tooling

| Item | Choice |
|---|---|
| OS / app | Windows 10/11, Fakturama English UI, normal (non-elevated) user |
| Python | 3.12, `.venv` |
| IDE | IntelliJ + Python plugin; `src/` marked Sources Root or `pip install -e .[dev]` |
| UIA | `pywinauto` (backend="uia") |
| Model | `pydantic` v2, `Decimal` money |
| LLM | `anthropic` (default) + `google-genai` (fallback), via `LLM_PROVIDER` |
| Imaging | `Pillow`, `mss` |
| OCR (Fakturama tables) | Windows.Media.Ocr via `winrt-Windows.Media.Ocr`, `winrt-Windows.Graphics.Imaging`, `winrt-Windows.Storage.Streams`, `winrt-Windows.Foundation` |
| Config | `.env` (keys/provider/model), `config/settings.yaml` (labels, timeouts, mappings) |
| Tests | `pytest` (all non-UI modules offline) |

`.env.example`:
```
LLM_PROVIDER=anthropic          # anthropic | gemini
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-haiku-5-5
GEMINI_API_KEY=
GEMINI_MODEL=gemini-flash-latest
EXTRACTION_MAX_ATTEMPTS=2
FAKTURAMA_WINDOW_TITLE=.*Fakturama.*
```

IntelliJ run configs: `Probe UI`, `Extract only`, `Full run`, `pytest`.
IntelliJ and Fakturama must run at the same privilege level. Don't touch mouse/keyboard during runs.

---

## 2. Architecture

```
order image
   │
   ▼
[Extract]  LLM (1 call, cached) → JSON → Pydantic OrderData → deterministic validation
   │                                     └─ fail → 1 retry with error feedback → ManualReview
   ▼
[Flow runner]  ordered steps: action → verify → evidence → next
   ├─ ui/find.py      UIA lookup by control type + name/label (no coordinates)
   ├─ ui/table.py     table rect (UIA) → screenshot → Windows OCR → rows/cells
   ├─ ui/wait.py      polling waits with timeout
   ├─ ui/input.py     set text / combo / checkbox / date + read-back
   ├─ match.py        deterministic exact / none / ambiguous / conflict
   └─ ui/evidence.py  annotated screenshot + log per step
   ▼
artifacts/<run-id>/   extracted.json, run.log, NN_step.png, summary.json
```

---

## 3. Design decisions

| # | Area | Decision | Rationale |
|---|---|---|---|
| D1 | Image extraction | Single LLM vision call, schema-constrained JSON | Fast, layout-robust, ~$0.001/order |
| D2 | Provider | Anthropic `claude-haiku-5-5` default (configurable); Gemini Flash free tier fallback | Cheap, fast, swappable |
| D3 | LLM elsewhere | None — matching/verification/navigation deterministic | Speed, cost, predictability |
| D4 | Extraction validation | Recompute line nets, sums, VAT, gross; required fields; date formats | Catches misreads without a 2nd model |
| D5 | Retry | 1 retry with validation errors in prompt → `ManualReviewRequired` | Bounded cost, safe failure |
| D6 | Cache | key = sha256(image) + provider + model + PROMPT_VERSION in `.cache/extractions/` | Zero tokens on dev re-runs |
| D7 | Control grounding | UIA (type + name/label) → Windows OCR inside UIA-obtained rect → stop | No coordinates, resolution-independent |
| D8 | Tables (NatTable) | OCR of the table rect; click row via OCR word box centre | NatTable likely opaque to UIA (confirm by probe) |
| D9 | Sync | `wait_until(cond, timeout, interval)`, polling every 0.1 s; table "stable" = 2 identical OCR reads | Fast and robust |
| D10 | Input | Real keystrokes with proven keyboard focus (UIA ValuePattern only as fallback); dates by arrow keys per field; always read back | ValuePattern text showed on screen but saved empty (§14) |
| D11 | Flow structure | Steps as functions (action + verify) run by a runner | "Verify each step" is structural |
| D12 | Errors | `ManualReviewRequired(reason, step, screenshot)` → summary, app left as-is | Matches brief |
| D13 | Evidence | Annotated screenshot per step (target outlined + caption) | Deliverable + debugging |
| D14 | UI strings | `config/settings.yaml` | Easy to adapt |
| D15 | DPI | Per-monitor DPI awareness at startup | OCR pixels == click coords |
| D16 | Saves | Exactly once per record, then verified | Matches brief |
| D17 | OCR image extraction | Deferred stretch goal | Agreed scope |

---

## 4. Data model (`models.py`)

```
OrderData
├─ external_reference: str          # WEB-2026-0714-A17
├─ order_date: date                 # 2026-07-14
├─ customer_id: str | None          # CUST-1007 (extracted, deliberately NOT used — see G2)
├─ currency: str                    # EUR
├─ debtor: Debtor
│   ├─ company, alias
│   ├─ first_name, last_name
│   ├─ email, phone
│   ├─ billing_address: Address     # name, street, zip, city, country, additional?
│   └─ delivery_address: Address
│      (property) delivery_same_as_billing
├─ payment: Payment
│   ├─ method: str
│   ├─ status: PAID | UNPAID | OTHER
│   └─ payment_date: date | None
├─ items: list[Item]
│   └─ position, sku, description, quantity, unit?, unit_net_price,
│      discount_percent, vat_percent, line_net_total
└─ totals: net, vat, gross
```

**`calc.py`** (Decimal, ROUND_HALF_UP, 2 dp):
- `line_net = qty × unit_net × (1 − discount/100)`
- `product_gross_price = unit_net × (1 + vat/100)` (no discount — brief 3.9)
- `vat_total = Σ line_net × vat/100` (tolerance ±0.01 per line)
- `gross = net + vat`

### Expected extraction for `samples/order_01.png` (use as test fixture)
```json
{
  "external_reference": "WEB-2026-0714-A17",
  "order_date": "2026-07-14",
  "customer_id": "CUST-1007",
  "currency": "EUR",
  "debtor": {
    "company": "Northstar Office GmbH",
    "alias": "NORTHSTAR-BERLIN",
    "first_name": "Marta",
    "last_name": "Klein",
    "email": "marta.klein@example.test",
    "phone": "+49 30 5550 1420",
    "billing_address":  {"name": "Northstar Office GmbH",       "street": "Friedrichstrasse 88", "zip": "10117", "city": "Berlin", "country": "Germany"},
    "delivery_address": {"name": "Northstar Office Warehouse",  "street": "Beusselstrasse 44",   "zip": "10553", "city": "Berlin", "country": "Germany"}
  },
  "payment": {"method": "Bank Transfer", "status": "PAID", "payment_date": "2026-07-18"},
  "items": [
    {"position": 1, "sku": "CHR-ERG-01", "description": "Ergonomic Desk Chair", "quantity": "2", "unit": "pcs",
     "unit_net_price": "250.00", "discount_percent": "10", "vat_percent": "19", "line_net_total": "450.00"},
    {"position": 2, "sku": "MAT-DESK-02", "description": "Anti-Fatigue Desk Mat", "quantity": "3", "unit": "pcs",
     "unit_net_price": "40.00", "discount_percent": "0", "vat_percent": "19", "line_net_total": "120.00"}
  ],
  "totals": {"net": "570.00", "vat": "108.30", "gross": "678.30"}
}
```
Derived: product gross prices CHR-ERG-01 = 297.50, MAT-DESK-02 = 47.60.

---

## 5. Extraction spec (`extract/`)

- **Preprocess:** downscale so the long side ≤ 1568 px (PNG/JPEG), base64.
- **Prompt rules:** transcribe only what's visible, never invent; money as plain decimal strings
  (`"250.00"`); percentages without `%`; dates `YYYY-MM-DD`; split contact name into first/last;
  split each address into name/street/zip/city/country; status uppercase; missing → `null`.
- **Structured output:**
  - Anthropic: forced tool call (`tool_choice={"type":"tool","name":"record_order"}`) with a flat,
    `$ref`-free JSON schema (`extract/schema.py`).
  - Gemini: `response_mime_type="application/json"` + schema embedded in prompt.
  - Both → same Pydantic validation.
- **Validation (`validate.py`)** returns `Issue(code, message, severity)`:

| Check | Severity |
|---|---|
| Required: reference, date, company, billing address, ≥1 item, SKU/qty/price/VAT per item, totals | error |
| Line net = formula (±0.01) | error |
| Σ line nets = net total | error |
| VAT total / gross consistent | error |
| VAT & discount in [0,100], qty > 0 | error |
| PAID ⇒ payment date present | error |
| Not PAID but payment date given | warning (ignore date) |
| Payment method not in code mapping | warning |
| Currency ≠ EUR | warning |
| Duplicate SKUs | warning |

- **Service (`service.py`):** cache lookup → provider call → parse → validate → on errors retry
  once with the issue list appended → still errors → `ManualReviewRequired`.
- **Outputs:** `artifacts/<run>/extracted.json`; log provider, model, input/output tokens, latency,
  cache hit.

---

## 6. UI layer (`ui/`)

- `app.py` — attach to the running Fakturama (title regex); focus, maximise; DPI awareness.
  Launching Fakturama is out of scope.
- `find.py` — by control type + name; by label adjacency (Edit right of / below Text "Cust.Ref.");
  toolbar/left-panel buttons by name or tooltip; tabs by name. Returns pywinauto wrappers.
- `wait.py` — `wait_until(cond, timeout, interval)`; `wait_window`, `wait_editor_tab`,
  `wait_table_stable`.
- `input.py` — `set_text`, `select_combo(exact_text)`, `set_checkbox`, `set_date` — all read back.
- `ocr.py` — Windows OCR on a screen rect → words with boxes. No upscaling (`ocr.upscale: 1`):
  2× made OCR worse on a high-DPI screen.
- `table.py` — column ranges from the header's grid lines; headers fuzzy-matched to configured
  names; words grouped into rows by y; dropped cells re-read with the column header as context;
  returns `[{col: text}]` with each row's screen rect. Pure logic is unit-tested offline.
- `evidence.py` — window capture, outline target rect, caption, save `NN_<step>.png`.

**Probe tool `tools/dump_tree.py`:** for a window matching a title regex (or `--list` to show all
top-level windows of the Fakturama process), dump control type, name, AutomationId, class,
rectangle, supported patterns, value — recursively with depth limit — to a text file, plus a
screenshot.

Probe these screens (in order): main window · New Order · Select the address · Select a product ·
New Debtor (Addresses / Miscellaneous / Payment tabs) · Data > terms of payment + its editor ·
Data > VATs + its editor · New Product · Data > Documents · Invoice editor (+ its payment area).

---

## 7. Flow spec (maps to the brief's steps)

### Stage `order` — Extract & open Order (1.1–1.8)
Steps: `open_order`, `set_header`, `set_modes`, `verify_header`.
1. Extract → `OrderData`.
2. Toolbar **Order** → wait New Order editor. ✔ editor open, No. non-empty (record it).
3. Leave No.; set **Date** = order date; **Cust.Ref.** = external reference.
4. Price mode **Net**; VAT **With VAT**. ✔ read back all.
5. Keep this Order tab open throughout.

### Stage `debtor` — Select or create Debtor (2.1–2.13)
Steps: `find_debtor`, `ensure_payment_method`, `create_debtor`, `select_created_debtor`,
`verify_addresses`.

**Payment method (every run, before any Debtor editor is opened):** Data > terms of payment →
read the list → one exact → use; multiple/conflict → stop; none → green + : Name = Description =
method; Account blank; code mapping (Bank Transfer → Credit transfer; Credit Card → Credit card;
SEPA Direct Debit → SEPA direct debit); Cash discount / Discount Days / Net Days = 0; Text
'unpaid'/'deposit'/'paid' blank; do NOT set as standard → Save once → ✔ listed. It runs even
when the Debtor exists, because the Invoice needs the method too (§14).

1. Click **upper existing-contact icon** beside Addresses (never the green +).
2. **Select the address**: search company → wait stable → OCR rows → `match_debtor`.
   - EXACT(1 row) → select → OK → ✔ Invoice & Delivery addresses equal source.
   - AMBIGUOUS/CONFLICT → `ManualReviewRequired`.
   - NONE → Cancel → creation:
     1. Left panel **New Contact** → wait New Debtor editor. Keep proposed Customer ID.
     2. Company, First Name, Last Name; Salutation stays `---`.
     3. Addresses > Main address: street, ZIP, city, country, e-mail, telephone; additional name /
        address specification / district only if supplied. Role = Invoice address
        (+ Delivery address if identical).
     4. Delivery differs (sample case) → add second address with Delivery address role (G1).
     5. Miscellaneous: Alias name, Discount 0%, Net or Gross = Net.
     6. Payment method (Miscellaneous tab): select the exact method, which the payment-method
        step above has already ensured exists.
     7. Save Debtor once.
     8. Back to Order → reopen Select the address → search → select new Debtor → OK.
   - ✔ Order Invoice/Delivery address populated & equal source (also proves the Debtor saved).

### Stage `products` — per item, in source order (3.1–3.17)
Steps: `check_net_mode`, `ensure_vats` (every distinct VAT rate of the order, before any line),
then per item `add_line_N` (select or create the product) and `set_line_N`.

1. **Upper Product-selection icon** beside Items (never green +).
2. **Select a product**: search exact SKU → OCR → `match_product`.
   EXACT → select → OK. CONFLICT → stop. NONE → Cancel →
3. **VAT** (3.4–3.6, done up front by `ensure_vats`): Data > VATs → find `VAT <p>%`. Reuse if
   Name and Value match (the VATs list shows no code column, so an existing VAT's code isn't
   re-checked); conflict → stop. Missing → green + : Name = Description = `VAT <p>%`, code S,
   Value = p, leave Standard VAT unchanged → Save once.
4. **New product** (opened with the top-toolbar button "Create a new product", not the left
   panel; only after VAT exists): Item Number = SKU; Name = Description = description;
   Price (gross) = `product_gross_price`; cost price (net) 0.00; VAT = exact VAT; Stock 0.00;
   leave Category, GTIN, supplier code, allowance, picture, user field 1 untouched → Save once.
5. Back to Order → reopen Select a product → search SKU → select → OK. Not found → stop.
   ("Select a product" may pick a typed SKU by itself and close; accepted only if exactly one line
   for that SKU appears.)
6. **Line**: Qty = quantity; ✔/set U.Price = unit net, VAT = p; Discount = item discount;
   ✔ line Price = `line_net` (OCR the Items row).

### Stage `save_order` — Complete & save Order (4.1–4.7)
Steps: `check_order`, `save_order`, `verify_in_documents`, `create_invoice`.
1. ✔ addresses and all lines vs source.
2. Overall Discount 0%; Shipping = Free of shipping costs / 0.00.
3. ✔ Total Net, VAT, Total = source totals (±0.01).
4. Save once.
5. Data > Documents → search by Order number (view maximized while read, then restored) ✔ Date,
   Cust.Ref., open state, Total.
6. In the saved Order: **Create a follow-up document → Invoice** (NOT toolbar Invoice) → wait
   New Invoice editor.

### Stage `invoice` — Complete & verify Invoice (5.1–5.7)
Steps: `check_invoice`, `set_payment`, `save_invoice`, `verify_documents`.
1. Leave Invoice No., Invoice Date, Service date. ✔ copied Cust.Ref., Invoice/Delivery address,
   Order Date, VAT mode, lines, totals.
2. Payment method = extracted method (ensured to exist by the `debtor` stage); still unavailable
   in the combo → stop.
3. PAID → tick paid, payment date = extracted date, Value = Invoice total. Else leave clear,
   invent nothing.
4. Save once.
5. Data > Documents ✔ Invoice row state + Total; ✔ source Order still open with same Cust.Ref. + Total.
6. Reopen Invoice only if needed to confirm persisted payment method / paid / date / value.
7. End. No Delivery, Correction, or Dunning documents.

**Run output** `summary.json`: order no., invoice no., per-entity `selected|created`
(debtor, payment method, each VAT, each product), verifications passed, durations, issues.

---

## 8. Matching rules (`match.py`)

Normalisation: Unicode NFKC, trim, collapse whitespace, casefold. Money as Decimal; ZIP as string.

| Entity | Search | EXACT | CONFLICT (→ stop) |
|---|---|---|---|
| Debtor | company | Company + First Name + Name + ZIP + City equal | same company but other key fields differ; >1 exact |
| Product | SKU | Item number equal | duplicate SKU rows; description differs (warn by default, configurable to stop) |
| VAT | `VAT p%` | Name + Value (code not shown in the list) | name matches but value differs |
| Payment method | name | Name equal | multiple equal rows; differing definition |

Result type: `NONE | EXACT(row) | AMBIGUOUS | CONFLICT`. Fully unit-tested.

---

## 9. Ambiguities in the brief & decisions (put in README)

- **G1** Billing ≠ delivery in the sample; brief covers only identical → add a second address with
  the Delivery role.
- **G2** `CUST-1007` extracted but Fakturama's proposed Customer ID kept (brief 2.6).
- **G3** Contact split: first token = first name, rest = last name.
- **G4** "Exact" = equal after whitespace/case normalisation.
- **G5** Search results read via OCR because NatTable is opaque to UIA (confirm by probe).
- **G6** Existing SKU with different description → warning (configurable to stop).

---

## 10. Repo layout

```
fakturama-image-to-cash/
├─ README.md  DESIGN.md  PLAN.md  CLAUDE.md  Launch.bat
├─ pyproject.toml  requirements.txt  .env.example  .gitignore
├─ config/settings.yaml
├─ samples/order_01.png  order_02.png  order_03.png
├─ tools/dump_tree.py  read_table.py  bench_uia.py  make_samples.py
├─ src/i2c/
│  ├─ __main__.py  main.py     # CLI: extract | run [--from-json] [--stop-after|--start-at STAGE] | gui
│  ├─ gui.py                   # launcher window (runs `i2c run` as a subprocess)
│  ├─ models.py  calc.py  match.py  errors.py  config.py  logging_setup.py  perf.py
│  ├─ extract/  prompt.py  schema.py  providers.py  validate.py  cache.py  service.py
│  ├─ ui/       app.py  find.py  geom.py  wait.py  input.py  ocr.py  table.py  evidence.py
│  └─ flow/     context.py  runner.py  common.py  order.py  debtor.py  products.py
│               save_order.py  invoice.py
├─ tests/  test_calc.py  test_validate.py  test_match.py  test_table.py  test_models.py
│          test_extract_service.py  test_find.py  test_flow_runner.py  test_products.py
│          test_invoice.py  test_ui_helpers.py  test_ocr_live_images.py  uia_dump.py
│          fixtures/order_01.expected.json  fixtures/uia/  fixtures/ocr/
└─ artifacts/   (gitignored)   .cache/ (gitignored)
```

The payment-method, VAT and product steps live in the stage modules (`debtor.py`, `products.py`);
shared helpers (tabs, open/maximize a view, read a table, selectors, save once) are in
`flow/common.py`; the Documents lookup by number is in `save_order.py`.

---

## 11. Testing strategy

- **Offline unit (137 tests):** calc; validate (golden fixture + mutated variants); match
  (none/exact/ambiguous/conflict); table grouping (synthetic OCR words); extraction service retry +
  cache with a fake provider; plus replays of real UIA dumps (`fixtures/uia/`) and live OCR
  captures (`fixtures/ocr/`) for control finding, tables, products and invoice checks.
- **Live (user's machine):** extraction once against the real API; flow stage by stage with
  `--stop-after` on a fresh Fakturama DB (create paths).
- **Second run, same image:** everything should be *selected*, not *created* (select paths).
- **Negative:** manually create a same-name Debtor with a different ZIP → expect manual-review stop.
- **More samples:** `order_02.png` (all create paths, 7 % VAT, Credit Card) and `order_03.png`
  (existing Debtor, mixed products, UNPAID), generated by `tools/make_samples.py`.

---

## 12. Schedule & milestones

**Part 1 — DESIGN.md (≤ 90 min)**, written after probe results. Sections: Overview · Extraction ·
Control grounding · Matching & verification · Failure handling · Tradeoffs & alternatives
(OCR-first, vision-model clicking, direct DB) · Risks.

**Part 2 — Implementation (5 h)**

| # | Time | Milestone | Done when |
|---|---|---|---|
| M1 | 0:30 | Skeleton + `dump_tree.py`; user probes 10 screens | probe outputs in `artifacts/probe/` |
| M2 | 0:30 | Extraction module + tests; one live call | `extract` matches fixture |
| M3 | 0:45 | UI toolkit: find/wait/input/ocr/table/evidence | can read Select-the-address rows |
| M4 | 0:30 | Stage `order` | header set & verified |
| M5 | 0:45 | Stage `debtor` (payment-method creation last) | debtor selected/created & verified |
| M6 | 0:45 | Stage `products` (VAT, product, lines) | both lines verified |
| M7 | 0:30 | Stages `save_order` + `invoice` | Documents shows open Order + paid Invoice |
| M8 | 0:25 | README, recording, "3 more hours" | deliverables checklist complete |

**Cut-line if short:** payment-method creation, VAT creation (Bank Transfer and VAT 19% exist by
default), description-mismatch check → document as "designed, not implemented".

**Draft "3 more hours" answer:** OCR-first extraction with LLM fallback; finish any cut branches;
read-only DB cross-verification; batch processing and resume-from-failure; robustness tests on
photographed/skewed images; CI for offline tests.

---

## 13. Probe questions to answer first

1. Do selector/Documents tables expose rows/cells via UIA? (decides D8)
2. How Date fields accept input (typed text vs DateTime segments; date format).
3. Exact control type/labels of price mode (Net/Gross) and VAT mode.
4. UIA names of toolbar items, New panel items, and the follow-up "Invoice" action.
5. Debtor address sub-tabs and address-role controls.

---

## 14. Changes from the original plan (found during implementation)

| Area | Original plan | Final code | Why |
|---|---|---|---|
| Text input (D10) | UIA ValuePattern, fallback typing | Real keystrokes with proven focus; ValuePattern only as fallback | A Company set by ValuePattern showed on screen but was saved empty |
| Dates | open question (§13) | Arrow keys per date field, field order learned at run time | Text set through UIA reverted to today |
| OCR | upscale 2× | no upscale | 2× made OCR worse on a 3000-px screen |
| Table columns | header text positions | header grid lines + fuzzy header names + context re-read of dropped cells | headers centred in some tables, left-aligned in others; OCR drops short tokens |
| Payment method | created inside the Debtor editor, only for a new Debtor | separate step before the Debtor, **on every run** | no unsaved editor in the background; the Invoice needs the method even for an existing Debtor (seen live after deleting "Bank Transfer") |
| VATs | per product, before it is created | all VAT rates of the order up front (`ensure_vats`) | each rate is checked once, before any product editor is open |
| VAT reuse | Name + Value + code S | Name + Value | the VATs list shows no code column |
| New product | left panel "New product" | top-toolbar button "Create a new product" | the left-panel link stopped opening the editor once the VATs view was open |
| Data > Documents | find the row | search by number, view maximized while read | a narrow layout truncated every cell |
| Invoice Items | Items pane rect | rect cut at the "Remarks" label | the invoice is a scrolled form; Items runs under the totals panel |
| Launching Fakturama | attach or launch | attach only | out of scope; must be running, maximized |
| CLI | `--stop-after` | also `--start-at` (continue in open editors) and `gui` (launcher window) | stage-by-stage development; one-click runs |
| Saved editors | left open | master-data editors closed after their save is verified (Ctrl+F4, verified; never unsaved) | 5 open editors made each UIA read ~2.5× slower (188 s create-everything run). Ctrl+W is unbound in Fakturama and typed a "w" into the field (seen live) |
| Dialog lookup | UIA walk over all desktop windows | main window's children, then Win32 `EnumWindows` by Fakturama's pid | the walk ran on every 0.1 s poll while selectors opened/closed and was not counted |
| Performance | not planned | `perf.py` counters; pane-scoped UIA reads, cached snapshots, `FindFirst` | ~75 % of a run was UI-tree reads; 156 s → 63.5 s with every check kept |
