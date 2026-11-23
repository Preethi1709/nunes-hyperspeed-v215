@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo  NUNES V21.5 SURREAL HYPERSPEED - SAFE UPGRADE
echo ================================================================
echo.
py ".\APPLY_V21_5_TO_EXISTING_V21_4.py"
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Upgrade did not complete. Read the message above; your safety backup is preserved.
) else (
  echo Upgrade completed successfully.
)
pause
exit /b %RC%
