from pathlib import Path
import os,sqlite3,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parent
fail=[]
def ck(name,cond):
    print(('PASS' if cond else 'FAIL'),name)
    if not cond: fail.append(name)
with tempfile.TemporaryDirectory(prefix='nunes_v215_migration_') as td:
    dbp=Path(td)/'test.db'; backups=Path(td)/'backups'
    env=os.environ.copy(); env['NUNES_DB_FILE']=str(dbp); env['NUNES_BACKUP_DIR']=str(backups)
    # Build a structurally complete predecessor DB, then mark it as schema 214 and
    # remove the V21.5-only task table. This avoids unrealistic hand-made old tables.
    r=subprocess.run([sys.executable,'-c','from db import init_db; init_db()'],cwd=ROOT,env=env)
    ck('Fixture database created',r.returncode==0)
    con=sqlite3.connect(dbp)
    con.execute('DROP TABLE IF EXISTS v215_stage_tasks')
    con.execute('PRAGMA user_version=214')
    con.execute("INSERT INTO app_settings(key,value) VALUES('db_schema_version','214') ON CONFLICT(key) DO UPDATE SET value='214'")
    con.commit(); con.close()
    # Remove the fresh-schema backup directory so the upgrade itself must create
    # the PRE_V21_5 snapshot.
    if backups.exists():
        import shutil; shutil.rmtree(backups)
    r=subprocess.run([sys.executable,'-c','from db import init_db; init_db()'],cwd=ROOT,env=env)
    ck('Migration process exits cleanly',r.returncode==0)
    con=sqlite3.connect(dbp)
    version=con.execute('PRAGMA user_version').fetchone()[0]
    task=con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='v215_stage_tasks'").fetchone()
    cols={r[1] for r in con.execute('PRAGMA table_info(emails)')}
    con.close()
    ck('Schema migrated to 215',version==215)
    ck('V21.5 task table created',bool(task))
    ck('Stage decision columns present',{'stage0_decision','stage1_summary','pipeline_stage'}.issubset(cols))
    ck('Pre-V21.5 safety backup created',(backups/'PRE_V21_5_MIGRATION.db').exists())
print('\nV21.5 MIGRATION TEST:', 'PASSED' if not fail else 'FAILED')
raise SystemExit(1 if fail else 0)
