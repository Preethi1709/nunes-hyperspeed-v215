from pathlib import Path
import subprocess,sys,shutil
ROOT=Path(__file__).resolve().parent
checks=[
 'RUN_V21_5_ARCHITECTURE_TEST.py',
 'RUN_V21_5_STAGE0_STAGE1_TEST.py',
 'RUN_V21_5_MIGRATION_TEST.py',
 'RUN_V21_5_ACCURACY_REGRESSION_TEST.py',
]
failed=[]
for test in checks:
    print('\n'+'='*72); print('RUNNING',test); print('='*72)
    r=subprocess.run([sys.executable,str(ROOT/test)],cwd=ROOT)
    if r.returncode: failed.append(test)
node=shutil.which('node')
if node:
    r=subprocess.run([node,'--check',str(ROOT/'static/app_v215.js')])
    print(('PASS' if r.returncode==0 else 'FAIL'),'JavaScript syntax')
    if r.returncode: failed.append('JavaScript syntax')
else:
    print('SKIP JavaScript syntax (node not installed)')
print('\nV21.5 FINAL MASTER TEST:', 'PASSED' if not failed else 'FAILED')
if failed: print('Failed:',', '.join(failed))
raise SystemExit(1 if failed else 0)
