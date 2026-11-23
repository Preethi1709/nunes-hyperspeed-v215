from __future__ import annotations
import subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REQ=ROOT/'requirements-ocr.txt'

print('NUNES V21.5 dedicated OCR setup - Python 3.13 compatible')
print('Python:', sys.executable)
print('Version:', sys.version.split()[0])

# Upgrade pip first so environment markers and modern wheels resolve correctly.
subprocess.run(
    [sys.executable,'-m','pip','install','--disable-pip-version-check','--upgrade','pip'],
    cwd=ROOT
)

r=subprocess.run(
    [sys.executable,'-m','pip','install','--disable-pip-version-check','-r',str(REQ)],
    cwd=ROOT
)
if r.returncode:
    print('\nOCR package installation was not completed. Core V21.5 remains safe; scanned exceptions can fall back to Qwen-VL.')
    raise SystemExit(r.returncode)

try:
    from services.v214_ocr_service import health
    state=health()
    print('\nOCR HEALTH:', state)
    if not state.get('available'):
        print('OCR packages installed, but the OCR engine did not initialize.')
        raise SystemExit(2)
except Exception as exc:
    print('OCR health check failed:', exc)
    raise SystemExit(2)

print('\nV21.5 dedicated OCR setup completed successfully.')
