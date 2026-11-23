@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "START_NUNES.py"
) else (
  py "START_NUNES.py"
)
