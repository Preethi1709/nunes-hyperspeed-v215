"""Visible Windows launcher for V21.5 SURREAL HYPERSPEED.

Unlike older .pyw launchers, this intentionally opens START_NUNES.py in a real
console so a startup problem can never disappear silently.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "START_NUNES.py"

if os.name == "nt":
    python_exe = Path(sys.executable)
    if python_exe.name.lower() == "pythonw.exe":
        python_exe = python_exe.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([str(python_exe), str(SCRIPT)], cwd=ROOT, creationflags=flags)
else:
    subprocess.Popen([sys.executable, str(SCRIPT)], cwd=ROOT)
