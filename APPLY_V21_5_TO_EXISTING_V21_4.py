from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent
PREFERRED = Path(r"C:\Users\Nunes\Documents\Hyperspeed Email Automation AI\V21.3\NUNES_PRODUCT_INTELLIGENCE_UNIFIED_PRO_V21_3_HYPERSPEED_FRESH_FINAL")

ROOT_FILES = [
    "app.py", "db.py", "START_NUNES.py", "START_NUNES.pyw", "START_V21_5.bat",
    "requirements.txt", "requirements-ocr.txt", "README.md", ".env.example",
    "SETUP_V21_5_OCR.py", "SETUP_V21_5_MODELS.py", "APPLY_V21_5_ENV.py",
    "APPLY_V21_5_TO_EXISTING_V21_4.py", "APPLY_V21_5_TO_EXISTING_V21_4.ps1", "INSTALL_V21_5_UPGRADE.bat",
    "RUN_V21_5_ARCHITECTURE_TEST.py", "RUN_V21_5_STAGE0_STAGE1_TEST.py",
    "RUN_V21_5_MIGRATION_TEST.py", "RUN_V21_5_ACCURACY_REGRESSION_TEST.py",
    "RUN_V21_5_INSTALLED_TEST.py", "RUN_V21_5_FINAL_MASTER_TEST.py",
    "RUN_V21_4_ACCURACY_REGRESSION_TEST.py", "ARCHITECTURE_FLOW.txt", "V21_5_BUILD_REPORT.txt",
]
BACKUP_ITEMS = [
    "app.py", "db.py", "START_NUNES.py", "START_NUNES.pyw", "requirements.txt", "requirements-ocr.txt",
    "README.md", ".env.example", "services", "static", "templates",
]


def step(text: str) -> None:
    print(f"\n=== {text} ===", flush=True)


def venv_python(folder: Path) -> Path:
    for p in (folder/".venv"/"Scripts"/"python.exe", folder/".venv"/"bin"/"python"):
        if p.exists(): return p
    return folder/".venv"/"Scripts"/"python.exe"


def find_targets() -> list[Path]:
    if (PREFERRED/"nunes_v213_fresh.db").exists() and venv_python(PREFERRED).exists():
        return [PREFERRED]
    roots=[Path.home()/"Documents"/"Hyperspeed Email Automation AI", Path(r"F:\Hyperspeed Email Automation AI")]
    hits=[]
    for root in roots:
        if not root.exists(): continue
        try:
            for dbp in root.rglob("nunes_v213_fresh.db"):
                f=dbp.parent
                if venv_python(f).exists() and (f/"START_NUNES.py").exists(): hits.append(f.resolve())
        except (PermissionError,OSError): pass
    out=[]; seen=set()
    for h in hits:
        k=str(h).lower()
        if k not in seen: seen.add(k); out.append(h)
    return out


def read_port(target: Path) -> int:
    import re
    env=target/".env"
    if not env.exists(): return 5067
    m=re.search(r'(?mi)^\s*APP_PORT\s*=\s*["\']?(\d+)',env.read_text(encoding="utf-8",errors="replace"))
    return int(m.group(1)) if m else 5067


def port_listening(port: int) -> bool:
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(.4)
    try: return s.connect_ex(("127.0.0.1",int(port)))==0
    finally: s.close()


def consistent_backup(src: Path,dst: Path) -> None:
    dst.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(src,timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        qc=str(con.execute("PRAGMA quick_check").fetchone()[0])
        if qc.lower()!="ok": raise RuntimeError(f"Source DB quick_check failed: {qc}")
        out=sqlite3.connect(dst)
        try:
            con.backup(out); out.commit()
            qc2=str(out.execute("PRAGMA quick_check").fetchone()[0])
            if qc2.lower()!="ok": raise RuntimeError(f"Backup DB quick_check failed: {qc2}")
        finally: out.close()
    finally: con.close()


def backup_code(target: Path,backup: Path) -> None:
    dstroot=backup/"previous_code"; dstroot.mkdir(parents=True,exist_ok=True)
    for rel in BACKUP_ITEMS:
        src=target/rel
        if not src.exists(): continue
        dst=dstroot/rel
        if src.is_dir(): shutil.copytree(src,dst,dirs_exist_ok=True)
        else: dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    if (target/".env").exists(): shutil.copy2(target/".env",backup/".env.before_v215")


def install_code(target: Path) -> None:
    for rel in ROOT_FILES:
        src=SRC/rel
        if not src.exists(): raise RuntimeError(f"V21.5 package incomplete: {src}")
        dst=target/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    for folder in ("services","static","templates"):
        shutil.copytree(SRC/folder,target/folder,dirs_exist_ok=True)
    for old_ui in (target/"static"/"app_v214.js", target/"static"/"styles_v214.css"):
        if old_ui.exists(): old_ui.unlink()
    for p in (target/"__pycache__",target/"services"/"__pycache__"):
        if p.exists(): shutil.rmtree(p,ignore_errors=True)


def run_checked(cmd:list[str],cwd:Path,label:str) -> None:
    print(f"\n-- {label} --",flush=True)
    r=subprocess.run(cmd,cwd=cwd)
    if r.returncode: raise RuntimeError(f"{label} failed with exit code {r.returncode}")


def main() -> int:
    ap=argparse.ArgumentParser(description="Safe no-loss V21.4 -> V21.5 SURREAL HYPERSPEED upgrade")
    ap.add_argument("--target",default="",help="Existing live NUNES project folder")
    args=ap.parse_args()
    print("="*72); print(" NUNES PRODUCT INTELLIGENCE V21.5 SURREAL HYPERSPEED - SAFE UPGRADE"); print("="*72)
    print("Source package:",SRC)
    target=Path(args.target).expanduser() if args.target else None
    if target is None:
        step("LOCATING LIVE PROJECT")
        hits=find_targets()
        if not hits:
            print("LIVE PROJECT NOT FOUND. Re-run with --target \"FULL_PROJECT_PATH\""); return 2
        if len(hits)>1:
            print("MULTIPLE LIVE PROJECTS FOUND; installer will not guess:")
            for h in hits: print(" -",h)
            return 2
        target=hits[0]
    target=target.resolve()
    if target==SRC.resolve(): raise RuntimeError("Source package and live target cannot be the same folder")

    step("VALIDATING LIVE PROJECT")
    dbp=target/"nunes_v213_fresh.db"; py=venv_python(target)
    for req in (dbp,py,target/"START_NUNES.py"):
        if not req.exists(): raise RuntimeError(f"Required live file missing: {req}")
    port=read_port(target)
    if port_listening(port): raise RuntimeError(f"Port {port} is listening. Stop NUNES first. No files were changed.")
    print("Live target:",target); print(f"Server check: stopped (port {port} free)"); print("Existing venv:",py)

    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    backup=target/"backups"/f"V21_5_UPGRADE_{stamp}"; backup.mkdir(parents=True,exist_ok=True)
    safety=backup/"nunes_v213_fresh.pre_v215.db"
    try:
        step("DATABASE SAFETY BACKUP"); consistent_backup(dbp,safety); print("SAFETY BACKUP OK:",safety)
        step("BACKING UP CURRENT CODE"); backup_code(target,backup); print("Previous code backup:",backup/"previous_code")
        step("INSTALLING V21.5 CODE"); install_code(target)
        step("UPDATING V21.5 SETTINGS"); run_checked([str(py),str(target/"APPLY_V21_5_ENV.py"),str(target/".env")],target,"Environment update")
        step("MIGRATING DATABASE TO SCHEMA 215"); run_checked([str(py),"-c","from db import init_db; init_db(); print('DATABASE MIGRATION: OK')"],target,"Database migration")
        step("VALIDATING V21.5 ARCHITECTURE"); run_checked([str(py),str(target/"RUN_V21_5_ARCHITECTURE_TEST.py")],target,"Architecture test")
        step("VALIDATING STAGE 0/1 ROUTING"); run_checked([str(py),str(target/"RUN_V21_5_STAGE0_STAGE1_TEST.py")],target,"Stage 0/1 test")
        step("VALIDATING ACCURACY FIREWALL"); run_checked([str(py),str(target/"RUN_V21_5_ACCURACY_REGRESSION_TEST.py")],target,"Accuracy regression test")
        step("VALIDATING INSTALLED PROJECT"); run_checked([str(py),str(target/"RUN_V21_5_INSTALLED_TEST.py")],target,"Installed-project test")
        report={"ok":True,"installed_at":datetime.now().astimezone().isoformat(),"target":str(target),"backup":str(backup),"database_backup":str(safety),"build":"V21_5_SURREAL_HYPERSPEED","schema":215}
        (backup/"UPGRADE_RESULT.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        (target/"V21_5_UPGRADE_SUCCESS.txt").write_text(f"V21.5 SURREAL HYPERSPEED upgrade completed successfully.\nTime: {report['installed_at']}\nSafety backup: {safety}\n",encoding="utf-8")
        step("UPGRADE COMPLETE")
        print("V21.5 SURREAL HYPERSPEED installed successfully.")
        print("Live project:",target); print("Safety backup:",backup)
        print(".env / .venv / Gmail / Sheets / database / logs / backups were preserved.")
        print("Start:  .\\.venv\\Scripts\\python.exe .\\START_NUNES.py")
        return 0
    except Exception as exc:
        (backup/"UPGRADE_FAILED.txt").write_text(f"V21.5 upgrade stopped safely.\nError: {type(exc).__name__}: {exc}\nSafety backup: {safety}\n",encoding="utf-8")
        print("\nUPGRADE STOPPED SAFELY:",type(exc).__name__,exc); print("Safety backup folder:",backup)
        return 1

if __name__=="__main__": raise SystemExit(main())
