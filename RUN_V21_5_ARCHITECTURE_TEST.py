from pathlib import Path
import ast
ROOT=Path(__file__).resolve().parent
app=(ROOT/'app.py').read_text(encoding='utf-8')
db=(ROOT/'db.py').read_text(encoding='utf-8')
eng=(ROOT/'services/v215_surrealspeed_engine.py').read_text(encoding='utf-8')
html=(ROOT/'templates/index.html').read_text(encoding='utf-8')
fail=[]
def ck(name,cond):
    print(('PASS' if cond else 'FAIL'),name)
    if not cond: fail.append(name)
ck('V21.5 build identity','BUILD_ID = "V21_5_SURREAL_HYPERSPEED"' in app)
ck('Schema 215','DB_SCHEMA_VERSION = 215' in db)
ck('Stage task table','CREATE TABLE IF NOT EXISTS v215_stage_tasks' in db)
ck('Stage 0 exists','stage0_filter' in eng and 'CERTAIN_IRRELEVANT' in eng)
ck('Stage 1 format router','classify_stage1_tasks' in eng and 'lane_for_filename' in eng)
for lane in ['TEXT','PDF','TABLE','DOC','OCR']:
    ck(f'{lane} specialist lane', f'("{lane}",' in app or f'== "{lane}"' in app)
ck('PDF cheap pre-probe before deep parser','probe_pdf_bytes' in app and 'target_pdf_bytes' in app)
ck('OCR empty-page circuit breaker','empty_first_chunk' in app)
ck('Cross-document Python rescue before AI', app.index('Cross-document deterministic rescue') < app.index('V21.5 specialist lanes + Python cross-document rescue'))
ck('Python evidence firewall retained','verify_product' in app and '_v214_all_confirmed' in app)
ck('AI exception only','_route_v214_ai' in app and 'V215_OCR_WORKERS = 1' in app)
ck('RAG removed marker','"rag":"REMOVED"' in app)
ck('V21.5 UI assets','app_v215.js' in html and 'styles_v215.css' in html)
ck('V21.5 UI identity','V21.5 · SURREAL HYPERSPEED' in html)
for f in [ROOT/'app.py',ROOT/'db.py',ROOT/'services/v215_surrealspeed_engine.py',ROOT/'services/v214_python_engine.py',ROOT/'services/v214_ocr_service.py']:
    try: ast.parse(f.read_text(encoding='utf-8')); ok=True
    except Exception: ok=False
    ck(f'Python syntax {f.name}',ok)
print('\nV21.5 ARCHITECTURE TEST:', 'PASSED' if not fail else 'FAILED')
raise SystemExit(1 if fail else 0)
