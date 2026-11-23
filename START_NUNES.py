from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQ = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LAUNCHER_LOG = LOG_DIR / "nunes_launcher.log"
SERVER_LOG = LOG_DIR / "nunes_server_console.log"
LAST_CRASH = LOG_DIR / "LAST_CRASH.txt"
MARKER = VENV_DIR / ".nunes_requirements_v215_ok"
EXPECTED_BUILD = "V21_5_SURREAL_HYPERSPEED"
MAX_AUTO_RESTARTS = 3


def log(message: str) -> None:
    text = f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} | {message}"
    print(text, flush=True)
    try:
        with LAUNCHER_LOG.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def read_port() -> int:
    port = 5067
    try:
        if ENV_FILE.exists():
            text = ENV_FILE.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?m)^\s*APP_PORT\s*=\s*[\"']?(\d+)", text)
            if m:
                port = int(m.group(1))
    except Exception:
        pass
    return max(1024, min(65535, port))


PORT = read_port()


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def running_build(port: int) -> str:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/build", timeout=2.0) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
            return str(data.get("build_id") or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def wait_for_server(port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def ensure_environment() -> None:
    py = venv_python()
    if not py.exists():
        log("Creating local Python environment. This happens once.")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)

    needs_install = not MARKER.exists()
    if REQ.exists() and MARKER.exists():
        try:
            needs_install = REQ.stat().st_mtime > MARKER.stat().st_mtime
        except Exception:
            needs_install = True

    if needs_install:
        log("Checking/installing required Python packages.")
        install_log = LOG_DIR / "requirements_install.log"
        with install_log.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat()} ---\n")
            result = subprocess.run(
                [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)],
                cwd=ROOT,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if result.returncode != 0:
            raise RuntimeError(f"Dependency installation failed. See {install_log}")
        MARKER.write_text("ok", encoding="utf-8")


def preflight() -> None:
    required = [ROOT / "app.py", ROOT / "db.py", ROOT / "templates" / "index.html", REQ]
    missing = [str(x.name) for x in required if not x.exists()]
    if missing:
        raise RuntimeError("Package is incomplete. Missing: " + ", ".join(missing))
    if not ENV_FILE.exists():
        log(".env is not present yet. The setup screen will let you configure credentials.")
        log("V21.5 will create a fresh local database here when none exists. Existing V21.3 databases are migrated in place with a safety backup.")


def write_crash_report(exit_code: int, runtime: float, tail: deque[str]) -> None:
    try:
        content = [
            "NUNES PRODUCT INTELLIGENCE V21.5 SURREAL HYPERSPEED - LAST CRASH REPORT",
            "=" * 64,
            f"Time: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"Exit code: {exit_code}",
            f"Runtime before exit: {runtime:.1f} seconds",
            f"Folder: {ROOT}",
            f"Server log: {SERVER_LOG}",
            "",
            "LAST SERVER OUTPUT",
            "-" * 64,
            *list(tail),
            "",
            "If the app closes again, this file contains the reason instead of losing the error window.",
        ]
        LAST_CRASH.write_text("\n".join(content), encoding="utf-8")
    except Exception:
        pass


def stream_process(proc: subprocess.Popen, tail: deque[str]) -> None:
    with SERVER_LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n\n===== SERVER START {datetime.now().isoformat()} =====\n")
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            print(line, flush=True)
            tail.append(line)
            try:
                f.write(line + "\n")
                f.flush()
            except Exception:
                pass


def run_server_once() -> tuple[int, float, deque[str]]:
    tail: deque[str] = deque(maxlen=120)
    started = time.time()
    proc = subprocess.Popen(
        [str(venv_python()), "-u", str(ROOT / "app.py")],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    reader = threading.Thread(target=stream_process, args=(proc, tail), daemon=True)
    reader.start()

    browser_opened = False
    try:
        while proc.poll() is None:
            if not browser_opened and wait_for_server(PORT, timeout=0.6):
                browser_opened = True
                log(f"Server ready: http://127.0.0.1:{PORT}")
                try:
                    webbrowser.open(f"http://127.0.0.1:{PORT}")
                except Exception:
                    pass
            time.sleep(0.4)
    except KeyboardInterrupt:
        log("Stop requested by user. Shutting down safely...")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return 0, time.time() - started, tail

    reader.join(timeout=2)
    return int(proc.returncode or 0), time.time() - started, tail


def hold_on_fatal(message: str) -> None:
    log(message)
    log(f"Diagnostic logs are in: {LOG_DIR}")
    if os.name == "nt":
        try:
            input("\nPress Enter to close this window...")
        except Exception:
            pass


def main() -> None:
    os.chdir(ROOT)
    print("=" * 72)
    print(" NUNES PRODUCT INTELLIGENCE - V21.5 SURREAL HYPERSPEED")
    print("=" * 72)
    print(f"Folder : {ROOT}")
    print(f"Port   : {PORT}")
    print(f"Logs   : {LOG_DIR}")
    print()

    try:
        preflight()

        if port_open(PORT):
            build = running_build(PORT)
            if build == EXPECTED_BUILD:
                log(f"{EXPECTED_BUILD} is already running.")
                webbrowser.open(f"http://127.0.0.1:{PORT}")
                return
            hold_on_fatal(
                f"Port {PORT} is already used by another server (detected build: {build}). "
                "Close the older NUNES server first; V21.5 will not kill another process automatically."
            )
            return

        ensure_environment()
    except Exception as exc:
        write_crash_report(1, 0, deque([f"Launcher preflight failed: {type(exc).__name__}: {exc}"], maxlen=120))
        hold_on_fatal(f"STARTUP PRECHECK FAILED: {type(exc).__name__}: {exc}")
        return

    log("Starting NUNES server. If a fatal error occurs, this window will remain open and a crash report will be saved.")

    restarts = 0
    while True:
        code, runtime, tail = run_server_once()
        if code == 0:
            log("NUNES server stopped normally.")
            return

        write_crash_report(code, runtime, tail)
        restarts += 1
        log(f"Server exited unexpectedly with code {code} after {runtime:.1f}s.")

        if restarts > MAX_AUTO_RESTARTS:
            hold_on_fatal(
                f"Automatic recovery stopped after {MAX_AUTO_RESTARTS} retries. "
                f"Open {LAST_CRASH} for the exact error."
            )
            return

        delay = min(10, 2 * restarts)
        log(f"Automatic recovery attempt {restarts}/{MAX_AUTO_RESTARTS} in {delay} seconds...")
        time.sleep(delay)


if __name__ == "__main__":
    main()
