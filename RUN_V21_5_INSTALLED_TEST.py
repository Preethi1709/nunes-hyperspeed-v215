from pathlib import Path
import sqlite3,sys
ROOT=Path(__file__).resolve().parent
fail=[]
def ck(name,cond):
    print(('PASS' if cond else 'FAIL'),name)
    if not cond: fail.append(name)
ck('V21.5 app installed','V21_5_SURREAL_HYPERSPEED' in (ROOT/'app.py').read_text(encoding='utf-8'))
ck('V21.5 specialist engine installed',(ROOT/'services/v215_surrealspeed_engine.py').exists())
ck('V21.5 UI installed',(ROOT/'static/app_v215.js').exists() and (ROOT/'static/styles_v215.css').exists())
ck('Launcher installed',(ROOT/'START_NUNES.py').exists() and (ROOT/'START_V21_5.bat').exists())
dbp=ROOT/'nunes_v213_fresh.db'
if dbp.exists():
    con=sqlite3.connect(dbp)
    ck('Live DB schema 215',con.execute('PRAGMA user_version').fetchone()[0]==215)
    ck('Stage task table present',bool(con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='v215_stage_tasks'").fetchone()))
    con.close()
for f in [ROOT/'app.py',ROOT/'db.py',ROOT/'services/v215_surrealspeed_engine.py']:
    try: compile(f.read_text(encoding='utf-8'),str(f),'exec'); ok=True
    except Exception: ok=False
    ck(f'Syntax {f.name}',ok)
print('\nV21.5 INSTALLED TEST:', 'PASSED' if not fail else 'FAILED')
raise SystemExit(1 if fail else 0)
