# NUNES PRODUCT INTELLIGENCE V21.5 — SURREAL HYPERSPEED

V21.5 replaces the single V21.4 Rescue funnel with an explicit **Stage 0 → Stage 1 → specialist Stage 2** architecture. The goal is sustained mailbox throughput without relaxing the existing deterministic evidence firewall.

## Final runtime architecture

### Stage 0 — obvious irrelevant email filter
A very conservative Python filter runs before document work. High-confidence OTP/login/security/social/newsletter noise can finish immediately. Any procurement signal, quotation-like filename, or plausible business attachment overrides the skip and advances the email.

### Stage 1 — format classification and segregation
No deep extraction happens here. Surviving mail is split into independent tasks:

- `TEXT` — body/HTML
- `PDF` — PDF with embedded text or unknown PDF
- `TABLE` — XLS/XLSX/CSV/TSV
- `DOC` — DOCX/TXT/RTF-style readable documents
- `OCR` — images and PDFs discovered to contain no readable embedded text
- `OTHER` — bounded exception handling for unsupported attachment types

A multi-attachment email can therefore feed several lanes simultaneously instead of entering one Rescue queue.

### Stage 2 — independent specialist processors
Default bounded workers:

- TEXT: 6
- PDF: 2
- TABLE: 2
- DOC: 2
- OCR: 1
- local AI: 1 request/model at a time

Each lane performs a cheap commercial probe before expensive work. Product catalogues/company profiles/spec sheets with no explicit commercial price evidence stop early. Quotation filenames increase priority but **never prove a price by themselves**.

#### PDF short-circuit
PDF processing first performs an embedded-text sweep only. If no explicit monetary evidence exists, the PDF is skipped from deep product-price extraction. When price evidence exists, V21.5 identifies hit pages and builds a small page window for the expensive quotation/table parser rather than parsing unrelated pages whenever possible.

#### Scan/OCR circuit breaker
A PDF without embedded text is routed to OCR. OCR is progressive and capped. If the first scan window returns no readable text, V21.5 stops repeating empty OCR work and routes the unreadable visual case to the exception path instead of burning every page. If readable OCR text exists but contains no price evidence across the bounded complete document, it is skipped from deep price extraction.

### Cross-document Python Rescue
After all specialist tasks for an email finish, Python correlates body + attachment evidence once. This is no longer the primary queue; it is a bounded correlation step.

### Exception AI
Only unresolved current-source evidence reaches local AI:

1. `qwen3:1.7b` — fast readable-text exception
2. `qwen3:4b` — difficult readable-text exception
3. `qwen3-vl:4b` — final visual exception

AI can propose candidates only. The existing Python source-evidence firewall remains final authority.

## Accuracy rules retained

- `purchase_price` remains the authoritative effective pre-tax procurement price.
- Original Price + explicit Discount may derive Discounted/Purchase Price only when both are evidenced.
- Qty, model digits, HSN/SAC, dates, GST/tax, freight, totals, ranges, accuracy/resolution and capacities cannot silently become unit price.
- Uncertain evidence becomes Review; V21.5 does not guess.
- Only `CONFIRMED + export_eligible=1` reaches Google Sheets.
- RAG/embeddings/Gemini/cloud extraction remain removed.

The unchanged regression firewall includes UMF 70 → ₹17,500, UMF-150 → ₹35,000, UMF-250 → ₹65,000, Fruit Penetrometer/model 53205 → ₹85,000, Qty 01 rejection, 0.02 accuracy rejection, range/date/heading rejection, two-price logic, GST/freight/total protection, and multi-row table relationships.

## Database

The persistent filename remains `nunes_v213_fresh.db` to preserve the live system. V21.5 migrates SQLite to **schema 215** and adds `v215_stage_tasks` plus Stage-0/Stage-1 audit columns. Existing products, Review decisions, Gmail data, Sheets state, logs and backups are preserved.

On the first V21.5 startup, unfinished V21.4 work is requeued through Stage 0/1 once so the old Rescue backlog can be redistributed by format. Future restarts preserve genuine V21.5 specialist queue states.

## Safe V21.4 → V21.5 installation

1. Stop NUNES completely (`Ctrl+C`) and make sure port 5067 is free.
2. Extract the V21.5 ZIP to a separate distribution folder.
3. Double-click `INSTALL_V21_5_UPGRADE.bat`.
4. The installer locates the live project, creates a consistent SQLite safety backup, backs up current code, preserves `.env`, `.venv`, Gmail/Sheets credentials, DB/logs/backups, copies V21.5, migrates schema 215 and runs architecture/routing/accuracy/installed tests.
5. If more than one live project is found, run:

```powershell
py .\APPLY_V21_5_TO_EXISTING_V21_4.py --target "FULL_LIVE_PROJECT_PATH"
```

After a successful upgrade start with:

```powershell
.\.venv\Scripts\python.exe .\START_NUNES.py
```

Do **not** run the old V21.4 backlog-rebalance script after V21.5 is installed. V21.5 has its own one-time migration.

## OCR and model checks

Existing working OCR/model installations are preserved. If needed:

```powershell
.\.venv\Scripts\python.exe .\SETUP_V21_5_OCR.py
.\.venv\Scripts\python.exe .\SETUP_V21_5_MODELS.py --check-only
```

## Diagnostics

`GET /api/diagnostics` now exposes separate counters for TEXT, PDF, TABLE, DOC, OCR and OTHER tasks (queued / processing / done / skipped / failed), plus Stage 0/1, cross-document rescue and AI exception counts. This makes the real bottleneck visible instead of hiding everything under one Rescue number.

## Included validation

- `RUN_V21_5_ARCHITECTURE_TEST.py`
- `RUN_V21_5_STAGE0_STAGE1_TEST.py`
- `RUN_V21_5_MIGRATION_TEST.py`
- `RUN_V21_5_ACCURACY_REGRESSION_TEST.py`
- `RUN_V21_5_INSTALLED_TEST.py`
- `RUN_V21_5_FINAL_MASTER_TEST.py`

The local build passed the V21.5 master suite before packaging. Throughput figures discussed during design are **engineering targets, not guaranteed benchmarks**; real speed depends on the proportion of OCR-heavy scans and the local PC.
