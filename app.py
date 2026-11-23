import os, json, hashlib, traceback, asyncio, threading, re, shutil, uuid, base64, sqlite3, logging, sys, time, ctypes
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from dotenv import load_dotenv, dotenv_values

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

# Local desktop development uses a loopback HTTP callback.
# This must NOT be used for a public production deployment.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from db import init_db, db, DB_PATH, database_health
from services.gmail_service import SCOPES, list_message_ids, list_message_page, list_all_message_ids, get_inbox_total, fetch_message, download_attachment, get_gmail_profile, list_history_message_ids, fetch_thread_context, start_mailbox_watch
from services.pdf_service import extract_pdf_text, extract_pdf_bundle
from services.pdf_isolation_service import extract_pdf_bundle_isolated
from services.attachment_service import extract_attachment_text
from services.v214_python_engine import (
    local_relevance, definitely_irrelevant, extract_main, extract_rescue,
    candidate_score, make_key, relevance_gate, has_commercial_price_signal,
    architecture_summary as v214_architecture_summary,
)
from services.v215_surrealspeed_engine import (
    stage0_filter, classify_stage1_tasks, commercial_probe_text, probe_pdf_bytes,
    target_pdf_bytes, stage1_summary, task_result, filename_priority,
    architecture_summary as v215_architecture_summary,
)
from services.accuracy_service import verify_product, gmail_links
from services.local_ai_service import (
    health as local_ai_health, self_test as local_ai_self_test,
    extract_message_local, extract_visual_local,
    fast_text_model_name, deep_text_model_name, vision_model_name,
)
from services.v214_ocr_service import health as ocr_health, ocr_multimodal_files, ocr_image_bytes, ocr_pdf_bytes
from services.learning_service import (
    ensure_learning_schema, source_fingerprint, duplicate_final_status, remember_source_result,
    record_negative_flags, learn_confirmed_product, build_memory_context,
    train_router_model, router_score, health as learning_health
)
from services.sheets_service import (
    HEADERS as SHEET_HEADERS, SHEET_SCHEMA_VERSION, append_product, append_products_batch,
    create_product_spreadsheet, create_product_spreadsheet_fast, ensure_sheet_ready,
    ensure_tabs_bulk, normalize_sheet_id, sheet_url, validate_tab_name, rename_tab,
    rebuild_tab_from_products
)

APP_PORT = int(os.getenv("APP_PORT", "5067"))
BUILD_ID = "V21_5_SURREAL_HYPERSPEED"

# LIVE mode:
# Gmail scanning is frequent because direct email/PDF parsing is local.
# AI fallback is kept separate so it cannot block normal results.
LIVE_SCAN_SECONDS = max(2, int(os.getenv("LIVE_SCAN_SECONDS", "2")))
DIRECT_ANALYSIS_SECONDS = max(1, int(os.getenv("DIRECT_ANALYSIS_SECONDS", "1")))
_DEFAULT_DIRECT_WORKERS = min(10, max(6, int(os.cpu_count() or 8)))
DIRECT_WORKERS = max(4, min(12, int(os.getenv("DIRECT_WORKERS", str(_DEFAULT_DIRECT_WORKERS)))))
DIRECT_BATCH_SIZE = max(12, min(80, int(os.getenv("DIRECT_BATCH_SIZE", "32"))))
DIRECT_SLICE_SIZE = max(4, min(12, int(os.getenv("DIRECT_SLICE_SIZE", "8"))))
AI_BATCH_SIZE = 1  # compatibility only; V21.4 exception AI is single-case
HISTORY_SCAN_SECONDS = max(5, int(os.getenv("HISTORY_SCAN_SECONDS", "8")))
HISTORY_PAGE_SIZE = 500
HISTORY_PAGES_PER_CYCLE = max(1, min(3, int(os.getenv("HISTORY_PAGES_PER_CYCLE", "2"))))
UI_PUSH_SECONDS = max(2, int(os.getenv("UI_PUSH_SECONDS", "2")))
SHEET_EXPORT_SECONDS = max(3, int(os.getenv("SHEET_EXPORT_SECONDS", "5")))
SHEET_EXPORT_BATCH_SIZE = max(50, min(500, int(os.getenv("SHEET_EXPORT_BATCH_SIZE", "250"))))
COVERAGE_VERIFY_SECONDS = max(300, int(os.getenv("COVERAGE_VERIFY_SECONDS", "900")))

BACKUP_SECONDS = max(900, int(os.getenv("BACKUP_SECONDS", "21600")))
BACKUP_KEEP = max(3, min(60, int(os.getenv("BACKUP_KEEP", "14"))))
JOB_LEASE_SECONDS = max(60, int(os.getenv("JOB_LEASE_SECONDS", "300")))
STALE_EMAIL_SECONDS = max(180, int(os.getenv("STALE_EMAIL_SECONDS", "600")))
FAST_LANE_PRIORITY = max(100, int(os.getenv("FAST_LANE_PRIORITY", "120")))
INBOX_COUNT_REFRESH_SECONDS = max(15, int(os.getenv("INBOX_COUNT_REFRESH_SECONDS", "30")))
PDF_PARSE_TIMEOUT_SECONDS = max(15, min(120, int(os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "45"))))
MAX_JOB_ATTEMPTS = max(2, min(8, int(os.getenv("MAX_JOB_ATTEMPTS", "3"))))
DIRECT_DRAIN_BUDGET_SECONDS = max(10, min(90, int(os.getenv("DIRECT_DRAIN_BUDGET_SECONDS", "30"))))
# V21.4 REAL HYPERSPEED: three runtime lanes only.
# MAIN = fast deterministic Python, RESCUE = deeper Python + attachment/OCR,
# AI = exception-only local models.  RAG/embeddings are not in the runtime.
V214_MAIN_WORKERS = max(2, min(10, int(os.getenv("V214_MAIN_WORKERS", "6"))))
V214_RESCUE_WORKERS = max(1, min(4, int(os.getenv("V214_RESCUE_WORKERS", "2"))))
V214_RESCUE_BATCH = max(1, min(8, int(os.getenv("V214_RESCUE_BATCH", "2"))))
V214_AI_WORKERS = 1
V214_AI_BATCH = 1
V214_RESCUE_SECONDS = max(1, min(10, int(os.getenv("V214_RESCUE_SECONDS", "1"))))
V214_AI_SECONDS = max(1, min(10, int(os.getenv("V214_AI_SECONDS", "1"))))
V214_MAX_RESCUE_ATTEMPTS = max(1, min(5, int(os.getenv("V214_MAX_RESCUE_ATTEMPTS", "2"))))
V214_MAX_AI_ATTEMPTS = max(1, min(5, int(os.getenv("V214_MAX_AI_ATTEMPTS", "3"))))
DIRECT_WORKERS = V214_MAIN_WORKERS
V214_MAIN_GATE = threading.BoundedSemaphore(V214_MAIN_WORKERS)
V214_RESCUE_GATE = threading.BoundedSemaphore(V214_RESCUE_WORKERS)
V214_AI_GATE = threading.BoundedSemaphore(V214_AI_WORKERS)

# V21.5 Stage-2 specialist lanes.  These are independent so a slow scan cannot
# hold text/PDF/table/document work behind one Rescue queue.
V215_TEXT_WORKERS = max(1, min(10, int(os.getenv("V215_TEXT_WORKERS", "6"))))
V215_PDF_WORKERS = max(1, min(4, int(os.getenv("V215_PDF_WORKERS", "2"))))
V215_TABLE_WORKERS = max(1, min(4, int(os.getenv("V215_TABLE_WORKERS", "2"))))
V215_DOC_WORKERS = max(1, min(4, int(os.getenv("V215_DOC_WORKERS", "2"))))
V215_OCR_WORKERS = 1
V215_STAGE2_BATCH_MULTIPLIER = max(1, min(4, int(os.getenv("V215_STAGE2_BATCH_MULTIPLIER", "2"))))
V215_TASK_STALE_SECONDS = max(300, min(3600, int(os.getenv("V215_TASK_STALE_SECONDS", "1200"))))
V215_OCR_PROBE_PAGES = max(1, min(4, int(os.getenv("V215_OCR_PROBE_PAGES", "2"))))
V215_OCR_MAX_PAGES = max(V215_OCR_PROBE_PAGES, min(20, int(os.getenv("V215_OCR_MAX_PAGES", "8"))))
V215_TEXT_GATE = threading.BoundedSemaphore(V215_TEXT_WORKERS)
V215_PDF_GATE = threading.BoundedSemaphore(V215_PDF_WORKERS)
V215_TABLE_GATE = threading.BoundedSemaphore(V215_TABLE_WORKERS)
V215_DOC_GATE = threading.BoundedSemaphore(V215_DOC_WORKERS)
V215_OCR_GATE = threading.BoundedSemaphore(V215_OCR_WORKERS)

# Verified-outcome learning is cheap Python/SQLite only.
LEARNING_TRAIN_SECONDS = max(300, min(7200, int(os.getenv("LEARNING_TRAIN_SECONDS", "900"))))

# PC-safe HyperSpeed resource controller.
# The purpose is to protect Windows/Chrome while local AI is active and to
# prevent PDF-isolation child processes from failing with MemoryError.
HYPERSPEED_MIN_FREE_MEMORY_MB = max(768, min(8192, int(os.getenv("HYPERSPEED_MIN_FREE_MEMORY_MB", "1536"))))
HYPERSPEED_MIN_FREE_MEMORY_PERCENT = max(5, min(40, int(os.getenv("HYPERSPEED_MIN_FREE_MEMORY_PERCENT", "12"))))
PDF_PARSE_SEMAPHORE = threading.BoundedSemaphore(1)

HYPERSPEED_FULL_SPEED = (os.getenv("HYPERSPEED_FULL_SPEED") or "true").strip().lower() in {"1","true","yes","on"}
HYPERSPEED_OFFICE_ADAPTIVE = (not HYPERSPEED_FULL_SPEED) and (os.getenv("HYPERSPEED_OFFICE_ADAPTIVE") or "true").strip().lower() in {"1","true","yes","on"}
HYPERSPEED_TEXT_IDLE_SECONDS = max(15, min(600, int(os.getenv("HYPERSPEED_TEXT_IDLE_SECONDS", "60"))))
HYPERSPEED_VISION_IDLE_SECONDS = max(30, min(1200, int(os.getenv("HYPERSPEED_VISION_IDLE_SECONDS", "120"))))


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def _windows_user_idle_seconds():
    """Seconds since keyboard/mouse input. Non-Windows returns a large idle value."""
    if os.name != "nt":
        return 999999.0
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        now = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
        elapsed_ms = (now - int(lii.dwTime)) & 0xFFFFFFFF
        return max(0.0, elapsed_ms / 1000.0)
    except Exception:
        # Failure is conservative on the office PC: do not start a heavy local model.
        return 0.0


def _office_local_ai_allowed(kind="text"):
    """Memory guard always applies; idle-time throttling applies only when full-speed mode is disabled."""
    memory_ok, memory_info = _local_ai_memory_headroom()
    idle_seconds = _windows_user_idle_seconds()
    required_idle = (
        HYPERSPEED_VISION_IDLE_SECONDS if kind == "vision"
        else HYPERSPEED_TEXT_IDLE_SECONDS
    )
    if not memory_ok:
        return False, {
            "reason": "low_memory",
            "idle_seconds": round(idle_seconds, 1),
            "required_idle_seconds": required_idle,
            "memory": memory_info,
        }
    if HYPERSPEED_OFFICE_ADAPTIVE and idle_seconds < required_idle:
        return False, {
            "reason": "user_active",
            "idle_seconds": round(idle_seconds, 1),
            "required_idle_seconds": required_idle,
            "memory": memory_info,
        }
    return True, {
        "reason": "allowed",
        "idle_seconds": round(idle_seconds, 1),
        "required_idle_seconds": required_idle,
        "memory": memory_info,
    }


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _system_memory_snapshot():
    """Return safe cross-platform memory facts without adding a dependency."""
    if os.name == "nt":
        try:
            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                total_mb = int(st.ullTotalPhys / (1024 * 1024))
                available_mb = int(st.ullAvailPhys / (1024 * 1024))
                available_percent = round((available_mb / total_mb) * 100.0, 1) if total_mb else 0.0
                return {
                    "ok": True,
                    "total_mb": total_mb,
                    "available_mb": available_mb,
                    "available_percent": available_percent,
                    "memory_load_percent": int(st.dwMemoryLoad),
                }
        except Exception:
            pass
    return {"ok": False, "total_mb": 0, "available_mb": 0, "available_percent": 100.0, "memory_load_percent": 0}


def _local_ai_memory_headroom():
    snap = _system_memory_snapshot()
    if not snap.get("ok"):
        return True, snap
    ok = (
        int(snap.get("available_mb") or 0) >= HYPERSPEED_MIN_FREE_MEMORY_MB
        and float(snap.get("available_percent") or 0) >= HYPERSPEED_MIN_FREE_MEMORY_PERCENT
    )
    return bool(ok), snap


_BACKGROUND_STOP = threading.Event()
_SUPERVISOR_THREADS = {}
_SUPERVISOR_LOCK = threading.Lock()

@asynccontextmanager
async def _lifespan(app):
    startup_runtime()
    try:
        yield
    finally:
        shutdown_runtime()

app = FastAPI(title="NUNES Mail Product Intelligence", lifespan=_lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_STATS_CACHE = {}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_CACHE_TTL = 2.0

# ---------------------------------------------------------------------------
# V21.4 crash visibility + runtime diagnostics
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
APP_LOG = LOG_DIR / "nunes_app.log"
APP_STARTED_AT = datetime.now().astimezone()

logger = logging.getLogger("nunes")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = RotatingFileHandler(APP_LOG, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"))
    logger.addHandler(_handler)
    _console = logging.StreamHandler(sys.stdout)
    _console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(_console)


def _global_exception_hook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        return sys.__excepthook__(exc_type, exc_value, exc_tb)
    logger.critical("UNHANDLED MAIN EXCEPTION", exc_info=(exc_type, exc_value, exc_tb))


def _thread_exception_hook(args):
    logger.error(
        "UNHANDLED THREAD EXCEPTION in %s",
        getattr(args.thread, "name", "unknown"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )

sys.excepthook = _global_exception_hook
try:
    threading.excepthook = _thread_exception_hook
except Exception:
    pass

_WORKER_LOCKS = {}
_WORKER_LOCKS_GUARD = threading.Lock()
_DISPATCH_THREADS = {}
_DISPATCH_DEFERRALS = {}
_DISPATCH_GUARD = threading.Lock()
_MAILBOX_CURSOR = {}
_MAILBOX_CURSOR_GUARD = threading.Lock()

def _worker_lock(name):
    with _WORKER_LOCKS_GUARD:
        return _WORKER_LOCKS.setdefault(name, threading.Lock())

def _active_mailbox_ids():
    with db() as conn:
        return [int(r["id"]) for r in conn.execute(
            "SELECT id FROM mailboxes WHERE is_active=1 ORDER BY id"
        ).fetchall()]

def _mailbox_slice(worker_name, count=1):
    """Return a fair round-robin mailbox slice so one large account cannot monopolize a worker."""
    ids = _active_mailbox_ids()
    if not ids:
        return []
    count = max(1, min(int(count), len(ids)))
    with _MAILBOX_CURSOR_GUARD:
        start = int(_MAILBOX_CURSOR.get(worker_name, 0)) % len(ids)
        out = [ids[(start + i) % len(ids)] for i in range(count)]
        _MAILBOX_CURSOR[worker_name] = (start + count) % len(ids)
    return out

def _dispatch_worker(name, fn, count_deferral=True):
    """Start work on a daemon thread and return immediately.

    Top-level scheduler dispatchers count deferrals because repeated busy
    dispatches may indicate a real control-plane problem. Per-mailbox workers
    intentionally do not count a normal "still busy" state as an error.
    """
    with _DISPATCH_GUARD:
        current = _DISPATCH_THREADS.get(name)
        if current is not None and current.is_alive():
            if count_deferral:
                _DISPATCH_DEFERRALS[name] = int(_DISPATCH_DEFERRALS.get(name, 0)) + 1
            return {"ok": True, "dispatched": False, "reason": "busy"}
        t = threading.Thread(
            target=_run_worker_safe,
            args=(name, fn),
            daemon=True,
            name=f"nunes-{name}",
        )
        _DISPATCH_THREADS[name] = t
        t.start()
    return {"ok": True, "dispatched": True}


def system_local_now_text():
    """Return this Windows PC's current local time, including its timezone offset."""
    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


def system_timezone_text():
    now = datetime.now().astimezone()
    name = now.tzname() or "Local"
    offset = now.strftime("%z")
    if len(offset) == 5:
        offset = offset[:3] + ":" + offset[3:]
    return f"{name} (UTC{offset})" if offset else name


history_mailbox_locks = {}
_history_lock_guard = threading.Lock()

def _history_lock(mailbox_id):
    with _history_lock_guard:
        return history_mailbox_locks.setdefault(int(mailbox_id), threading.Lock())

def _placeholder(value):
    s = (value or "").strip().lower()
    return (not s) or ("your-client" in s) or ("your_client" in s) or ("your-client-secret" in s) or ("paste_" in s)


def oauth_configured():
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    secret = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    redirect = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()
    return (
        not _placeholder(client_id)
        and client_id.endswith(".apps.googleusercontent.com")
        and not _placeholder(secret)
        and redirect == "http://127.0.0.1:5067/auth/google/callback"
    )


def external_ai_disabled():
    return True


def setup_required():
    # V21.4 requires Google OAuth for Gmail; extraction AI is local and exception-only.
    return not oauth_configured()


def _env_quote(value):
    value = str(value or "")
    value = value.replace("\\", "\\\\").replace(chr(34), "\\\"").replace("\n", "\\n")
    return chr(34) + value + chr(34)


def _save_local_config(values):
    client_id = (values.get("GOOGLE_CLIENT_ID") or "").strip()
    secret = (values.get("GOOGLE_CLIENT_SECRET") or "").strip()
    sheet_id = (values.get("GOOGLE_SHEET_ID") or "").strip()

    if _placeholder(client_id) or not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("Enter the real Google OAuth Web Client ID ending in .apps.googleusercontent.com")
    if _placeholder(secret):
        raise ValueError("Enter the real Google OAuth Client Secret")
    config = {
        "LOCAL_AI_ENABLED": "true",
        "HYPERSPEED_FULL_SPEED": "true",
        "GOOGLE_CLIENT_ID": client_id,
        "GOOGLE_CLIENT_SECRET": secret,
        "GOOGLE_REDIRECT_URI": "http://127.0.0.1:5067/auth/google/callback",
        "GOOGLE_SHEET_ID": sheet_id,
        "GOOGLE_SHEET_TAB": (values.get("GOOGLE_SHEET_TAB") or "PRODUCT_DATA").strip(),
        "APP_PORT": "5067",
        "LIVE_SCAN_SECONDS": (values.get("LIVE_SCAN_SECONDS") or "2").strip(),
        "V214_MAIN_WORKERS": (values.get("V214_MAIN_WORKERS") or "6").strip(),
        "V214_RESCUE_WORKERS": (values.get("V214_RESCUE_WORKERS") or "2").strip(),
        "V214_RESCUE_BATCH": (values.get("V214_RESCUE_BATCH") or "2").strip(),
        "V214_MAX_RESCUE_ATTEMPTS": (values.get("V214_MAX_RESCUE_ATTEMPTS") or "2").strip(),
        "V214_MAX_AI_ATTEMPTS": (values.get("V214_MAX_AI_ATTEMPTS") or "3").strip(),
        "V214_OCR_ENABLED": (values.get("V214_OCR_ENABLED") or "true").strip(),
        "LOCAL_AI_FAST_MODEL": (values.get("LOCAL_AI_FAST_MODEL") or "qwen3:1.7b").strip(),
        "LOCAL_AI_DEEP_MODEL": (values.get("LOCAL_AI_DEEP_MODEL") or "qwen3:4b").strip(),
        "LOCAL_AI_VISION_MODEL": (values.get("LOCAL_AI_VISION_MODEL") or "qwen3-vl:4b").strip(),
        "DIRECT_ANALYSIS_SECONDS": (values.get("DIRECT_ANALYSIS_SECONDS") or "1").strip(),
        "DIRECT_BATCH_SIZE": (values.get("DIRECT_BATCH_SIZE") or "60").strip(),
        "HISTORY_SCAN_SECONDS": (values.get("HISTORY_SCAN_SECONDS") or "8").strip(),
        "HISTORY_PAGE_SIZE": "500",
        "HISTORY_PAGES_PER_CYCLE": (values.get("HISTORY_PAGES_PER_CYCLE") or "2").strip(),
        "UI_PUSH_SECONDS": (values.get("UI_PUSH_SECONDS") or "2").strip(),
        "COVERAGE_VERIFY_SECONDS": (values.get("COVERAGE_VERIFY_SECONDS") or "900").strip(),
        "SHEET_EXPORT_SECONDS": (values.get("SHEET_EXPORT_SECONDS") or "5").strip(),
        "SHEET_EXPORT_BATCH_SIZE": (values.get("SHEET_EXPORT_BATCH_SIZE") or "250").strip(),
        "BACKUP_SECONDS": (values.get("BACKUP_SECONDS") or "21600").strip(),
        "BACKUP_KEEP": (values.get("BACKUP_KEEP") or "14").strip(),
        "JOB_LEASE_SECONDS": (values.get("JOB_LEASE_SECONDS") or "300").strip(),
        "GMAIL_PUBSUB_TOPIC": (values.get("GMAIL_PUBSUB_TOPIC") or "").strip(),
        "GMAIL_PUSH_TOKEN": (values.get("GMAIL_PUSH_TOKEN") or "").strip(),
    }

    lines = [
        "# NUNES Mail Product Intelligence - local configuration",
        "# Generated by the first-run setup screen. Keep this file private.",
        "",
    ]
    for key, value in config.items():
        lines.append(f"{key}={_env_quote(value)}")
    lines.append("")
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")

    # Apply immediately; no restart is required after the setup form is saved.
    for key, value in config.items():
        os.environ[key] = value

    return config

def make_flow(state=None):
    cfg = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")],
        }
    }
    flow = Flow.from_client_config(cfg, scopes=SCOPES, state=state)
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    return flow

def mailbox_row(mailbox_id):
    with db() as conn:
        return conn.execute("SELECT * FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()

def _setting_get(key, default=""):
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _setting_set(key, value):
    with db() as conn:
        conn.execute(
            """INSERT INTO app_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, str(value or ""))
        )



def _run_worker_safe(name, fn):
    """Run one background worker without allowing a worker exception to kill the server."""
    lock = _worker_lock(name)
    if not lock.acquire(blocking=False):
        return {"ok": True, "skipped": "already_running"}
    try:
        _setting_set(f"worker:{name}:last_start", system_local_now_text())
        result = fn()
        _setting_set(f"worker:{name}:last_success", system_local_now_text())
        _setting_set(f"worker:{name}:last_error", "")
        return result if result is not None else {"ok": True}
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"[:1200]
        logger.exception("Worker %s failed: %s", name, msg)
        try:
            _setting_set(f"worker:{name}:last_error", msg)
            _setting_set(f"worker:{name}:last_error_at", system_local_now_text())
            _setting_set("last_worker_error", f"{name}: {msg}"[:1400])
        except Exception:
            logger.exception("Could not persist worker error state")
        return {"ok": False, "error": msg}
    finally:
        lock.release()


def _worker_status_snapshot():
    names = ["live_gmail", "history", "direct_analysis", "rescue", "ai", "sheets", "self_heal", "coverage", "learning", "backup", "gmail_watch"]
    out = {}
    for name in names:
        out[name] = {
            "last_start": _setting_get(f"worker:{name}:last_start", ""),
            "last_success": _setting_get(f"worker:{name}:last_success", ""),
            "last_error": _setting_get(f"worker:{name}:last_error", ""),
            "last_error_at": _setting_get(f"worker:{name}:last_error_at", ""),
            "dispatch_skips": int(_setting_get(f"worker:{name}:dispatch_skips", "0") or 0),
            "running": bool(_worker_lock(name).locked()),
        }
    return out

def _default_tab_name(mailbox_id):
    mb = mailbox_row(mailbox_id)
    email = (mb["email"] if mb else f"MAILBOX_{mailbox_id}") or f"MAILBOX_{mailbox_id}"
    local = email.split("@", 1)[0].strip() or f"MAILBOX_{mailbox_id}"
    local = re.sub(r"[\\/\?\*\[\]:]+", "_", local)
    return local[:80] or f"MAILBOX_{mailbox_id}"


def get_shared_sheet_id():
    sheet_id = normalize_sheet_id(_setting_get("shared_sheet_id", ""))
    if sheet_id:
        return sheet_id
    env_sheet = normalize_sheet_id(os.getenv("GOOGLE_SHEET_ID", ""))
    if env_sheet:
        return env_sheet
    # Backward compatibility: promote the first old per-mailbox Sheet mapping.
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key LIKE 'sheet_id:%' AND value<>'' ORDER BY key LIMIT 1"
        ).fetchone()
    return normalize_sheet_id(row["value"]) if row else ""


def get_sheet_owner_mailbox_id():
    raw = _setting_get("sheet_owner_mailbox_id", "")
    try:
        owner_id = int(raw)
        if mailbox_row(owner_id):
            return owner_id
    except Exception:
        pass
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM mailboxes WHERE is_active=1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else None


def get_sheet_writer_row(fallback_mailbox_id=None):
    owner_id = get_sheet_owner_mailbox_id()
    mb = mailbox_row(owner_id) if owner_id else None
    return mb or (mailbox_row(fallback_mailbox_id) if fallback_mailbox_id else None)


def get_sheet_config(mailbox_id):
    sheet_id = get_shared_sheet_id()
    tab = (_setting_get(f"sheet_tab:{mailbox_id}", "") or _default_tab_name(mailbox_id)).strip()
    return sheet_id, tab


def set_shared_sheet(sheet_id, owner_mailbox_id):
    sheet_id = normalize_sheet_id(sheet_id)
    if not sheet_id:
        raise ValueError("Google Sheet ID is empty")
    _setting_set("shared_sheet_id", sheet_id)
    _setting_set("sheet_owner_mailbox_id", owner_mailbox_id)
    return sheet_id


def _ensure_unique_tab(mailbox_id, tab):
    tab = validate_tab_name(tab)
    with db() as conn:
        rows = conn.execute("SELECT id,email FROM mailboxes WHERE is_active=1 AND id<>?", (mailbox_id,)).fetchall()
    for row in rows:
        other_tab = (_setting_get(f"sheet_tab:{row['id']}", "") or _default_tab_name(row["id"])).strip()
        if other_tab.casefold() == tab.casefold():
            raise ValueError(f"Tab name '{tab}' is already assigned to {row['email']}")
    return tab


def set_mailbox_tab(mailbox_id, tab):
    tab = _ensure_unique_tab(mailbox_id, tab)
    _setting_set(f"sheet_tab:{mailbox_id}", tab)
    return tab


def set_sheet_config(mailbox_id, sheet_id, tab=None):
    sheet_id = set_shared_sheet(sheet_id, mailbox_id)
    tab = set_mailbox_tab(mailbox_id, tab or _default_tab_name(mailbox_id))
    return sheet_id, tab


def ensure_all_mailbox_tabs():
    """Ensure all connected Gmail tabs using bulk Google API operations."""
    sheet_id = get_shared_sheet_id()
    writer = get_sheet_writer_row()
    if not sheet_id or not writer:
        return []
    with db() as conn:
        boxes = conn.execute("SELECT id,email FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()
    specs = []
    for box in boxes:
        tab = (_setting_get(f"sheet_tab:{box['id']}", "") or _default_tab_name(box["id"])).strip()
        tab = set_mailbox_tab(box["id"], tab)
        specs.append((int(box["id"]), box["email"], tab))
    ids = ensure_tabs_bulk(writer, sheet_id, [x[2] for x in specs])
    results = []
    for mailbox_id,email,tab in specs:
        _setting_set(f"sheet_tab_id:{mailbox_id}", ids.get(tab, ""))
        results.append({"mailbox_id": mailbox_id, "email": email, "tab": tab})
    return results


def _sheet_target(sheet_id, tab):
    return f"{sheet_id}:{tab}" if sheet_id else ""


def _ensure_sheet_schema_current(mailbox_id, writer, sheet_id, tab):
    """Run the V21.4 two-price Sheet migration exactly when the schema changes."""
    key = f"sheet_schema_version:{mailbox_id}"
    if _setting_get(key, "") == SHEET_SCHEMA_VERSION and _setting_get(f"sheet_tab_id:{mailbox_id}", ""):
        return
    ready = ensure_sheet_ready(writer, sheet_id, tab)
    _setting_set(f"sheet_tab_id:{mailbox_id}", ready.get("tab_id", ""))
    _setting_set(key, SHEET_SCHEMA_VERSION)


def export_product_to_sheet(mailbox_id, product_id, force=False):
    sheet_id, tab = get_sheet_config(mailbox_id)
    if not sheet_id:
        return {"ok": False, "reason": "not_configured"}

    target = _sheet_target(sheet_id, tab)
    with db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=? AND mailbox_id=?", (product_id, mailbox_id)).fetchone()
    if not row:
        return {"ok": False, "reason": "product_not_found"}

    if not force and row["sheet_exported_at"] and row["sheet_export_target"] == target:
        return {"ok": True, "already_exported": True}

    writer = get_sheet_writer_row(mailbox_id)
    if not writer:
        return {"ok": False, "reason": "sheet_writer_not_found"}
    try:
        _ensure_sheet_schema_current(mailbox_id, writer, sheet_id, tab)
        append_product(writer, dict(row), sheet_id, tab)
        with db() as conn:
            conn.execute(
                """UPDATE products
                   SET sheet_exported_at=CURRENT_TIMESTAMP,
                       sheet_export_error=NULL,
                       sheet_export_target=?
                   WHERE id=?""",
                (target, product_id)
            )
        return {"ok": True}
    except Exception as exc:
        with db() as conn:
            conn.execute(
                """UPDATE products
                   SET sheet_export_error=?, sheet_export_target=?
                   WHERE id=?""",
                (str(exc)[:800], target, product_id)
            )
        return {"ok": False, "error": str(exc)}


def export_pending_products(mailbox_id, limit=SHEET_EXPORT_BATCH_SIZE):
    """Batch Sheet export: many product rows in one Google API call."""
    sheet_id, tab = get_sheet_config(mailbox_id)
    if not sheet_id:
        return {"ok": False, "configured": False, "exported": 0, "failed": 0}
    writer = get_sheet_writer_row(mailbox_id)
    if not writer:
        return {"ok": False, "configured": True, "exported": 0, "failed": 1, "error": "Sheet writer mailbox not found"}
    target = _sheet_target(sheet_id, tab)
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM products WHERE mailbox_id=?
               AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
               AND (sheet_exported_at IS NULL OR COALESCE(sheet_export_target,'') != ?)
               ORDER BY id ASC LIMIT ?""",
            (mailbox_id, target, int(limit))
        ).fetchall()
    if not rows:
        return {"ok": True, "configured": True, "exported": 0, "failed": 0}
    try:
        # On the first export after this patch, migrate any existing six-column
        # V21.4 tab to the seven-column two-price schema before appending rows.
        _ensure_sheet_schema_current(mailbox_id, writer, sheet_id, tab)
        count = append_products_batch(writer, [dict(r) for r in rows], sheet_id, tab)
        ids = [int(r["id"]) for r in rows]
        now = system_local_now_text()
        with db() as conn:
            conn.executemany(
                """UPDATE products SET sheet_exported_at=?,sheet_export_error=NULL,sheet_export_target=? WHERE id=?""",
                [(now, target, pid) for pid in ids]
            )
        _setting_set(f"last_sheet_sync_at:{mailbox_id}", now)
        return {"ok": True, "configured": True, "exported": count, "failed": 0}
    except Exception as exc:
        with db() as conn:
            conn.executemany(
                "UPDATE products SET sheet_export_error=?,sheet_export_target=? WHERE id=?",
                [(str(exc)[:800], target, int(r["id"])) for r in rows]
            )
        return {"ok": False, "configured": True, "exported": 0, "failed": len(rows), "error": str(exc)}


def rebuild_verified_sheet(mailbox_id):
    """Replace the assigned Gmail tab with only current CONFIRMED products.

    This removes legacy/wrong rows after a price re-audit instead of merely
    appending corrected values beside them.
    """
    sheet_id, tab = get_sheet_config(mailbox_id)
    if not sheet_id:
        return {"ok": False, "configured": False, "rows": 0}
    writer = get_sheet_writer_row(mailbox_id)
    if not writer:
        return {"ok": False, "configured": True, "rows": 0, "error": "Sheet writer mailbox not found"}
    target = _sheet_target(sheet_id, tab)
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM products WHERE mailbox_id=?
               AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
               ORDER BY price_date ASC,id ASC""",
            (mailbox_id,)
        ).fetchall()
    try:
        ready = ensure_sheet_ready(writer, sheet_id, tab)
        _setting_set(f"sheet_tab_id:{mailbox_id}", ready.get("tab_id", ""))
        _setting_set(f"sheet_schema_version:{mailbox_id}", SHEET_SCHEMA_VERSION)
        count = rebuild_tab_from_products(writer, [dict(r) for r in rows], sheet_id, tab)
        now = system_local_now_text()
        with db() as conn:
            conn.execute(
                """UPDATE products SET sheet_exported_at=NULL,sheet_export_target=NULL
                   WHERE mailbox_id=? AND (COALESCE(verification_status,'')<>'CONFIRMED' OR COALESCE(export_eligible,0)<>1)""",
                (mailbox_id,)
            )
            conn.execute(
                """UPDATE products SET sheet_exported_at=?,sheet_export_error=NULL,sheet_export_target=?
                   WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1""",
                (now, target, mailbox_id)
            )
        _setting_set(f"last_sheet_sync_at:{mailbox_id}", now)
        _setting_set(f"sheet_rebuild_required:{mailbox_id}", "0")
        return {"ok": True, "configured": True, "rows": count, "url": sheet_url(sheet_id)}
    except Exception as exc:
        _setting_set(f"sheet_rebuild_error:{mailbox_id}", str(exc)[:700])
        return {"ok": False, "configured": True, "rows": 0, "error": str(exc)}


def _upsert_mailbox_defaults(mailbox_id):
    with db() as conn:
        conn.execute(
            """UPDATE mailboxes
               SET inbox_query=COALESCE(NULLIF(inbox_query,''),'in:inbox'),
                   history_complete=COALESCE(history_complete,0),
                   history_scanned_count=COALESCE(history_scanned_count,0),
                   inbox_total_estimate=COALESCE(inbox_total_estimate,0)
               WHERE id=?""",
            (mailbox_id,)
        )


TERMINAL_STATUSES = (
    "IRRELEVANT", "NO PRODUCT", "NEEDS REVIEW", "NEEDS OCR",
    "DUPLICATE / ALREADY EXISTS"
)

ACTIVE_STATUSES = (
    "DISCOVERED", "NEW", "RAW", "PROCESSING", "PDF READING",
    "RESCUE QUEUED", "RESCUE PROCESSING", "AI QUEUED", "AI PROCESSING"
)


def _status_terminal(status: str) -> bool:
    return (status or "").startswith("EXTRACTED ") or status in TERMINAL_STATUSES


def _set_email_status(email_id, status, error=None, completed=None):
    if completed is None:
        completed = _status_terminal(status)
    with db() as conn:
        conn.execute(
            """UPDATE emails
               SET status=?, error=?, updated_at=CURRENT_TIMESTAMP,
                   analysis_completed_at=CASE WHEN ? THEN COALESCE(analysis_completed_at,CURRENT_TIMESTAMP) ELSE analysis_completed_at END
               WHERE id=?""",
            (status, error, 1 if completed else 0, email_id)
        )
    if completed:
        _route_done(email_id)
        try:
            with db() as conn:
                row=conn.execute("SELECT mailbox_id,source_hash FROM emails WHERE id=?",(email_id,)).fetchone()
            if row and row["source_hash"]:
                remember_source_result(int(row["mailbox_id"]),int(email_id),row["source_hash"],status)
        except Exception:
            logger.debug("Learning cache update skipped for email %s",email_id,exc_info=True)


def _index_message_ids(mailbox_id, ids, priority=10):
    """Fast coverage indexing: store Gmail IDs only and create durable analysis jobs."""
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return 0
    touched = 0
    with db() as conn:
        for mid in ids:
            conn.execute(
                """INSERT INTO emails(mailbox_id,gmail_message_id,status,priority)
                   VALUES(?,?,'DISCOVERED',?)
                   ON CONFLICT(mailbox_id,gmail_message_id) DO UPDATE SET
                     priority=MAX(COALESCE(emails.priority,0), excluded.priority), in_inbox=1""",
                (mailbox_id, mid, int(priority))
            )
            row=conn.execute("SELECT id,status FROM emails WHERE mailbox_id=? AND gmail_message_id=?", (mailbox_id,mid)).fetchone()
            if row and row["status"] in ('DISCOVERED','NEW','RAW'):
                conn.execute(
                    """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority)
                       VALUES(?,?,'ANALYZE','READY',?)
                       ON CONFLICT(email_id,job_type) DO UPDATE SET priority=MAX(processing_jobs.priority,excluded.priority),
                   state=CASE WHEN processing_jobs.state='DONE' THEN 'READY' ELSE processing_jobs.state END""",
                    (mailbox_id,row["id"],int(priority))
                )
            touched += 1
    return touched


def _hydrate_and_process(mailbox_id, email_id, gmail_message_id, allow_ai=False):
    mb = mailbox_row(mailbox_id)
    if not mb:
        return False
    try:
        msg = fetch_message(mb, gmail_message_id)
        names = " | ".join(x.get("filename", "") for x in msg.get("attachments", []))
        prio = candidate_score(msg.get("subject", ""), msg.get("body", ""), names)
        src_hash = ""
        if not msg.get("attachments"):
            src_hash=source_fingerprint(msg.get("sender",""),msg.get("subject",""),msg.get("body",""),"")
        with db() as conn:
            conn.execute(
                """UPDATE emails SET
                     gmail_thread_id=?, rfc_message_id=?, received_at=?, sender=?, recipients=?, cc=?, subject=?, body=?,
                     attachment_names=?, source_hash=?, status='NEW', priority=?,
                     analysis_started_at=COALESCE(analysis_started_at,CURRENT_TIMESTAMP),
                     analysis_attempts=COALESCE(analysis_attempts,0)+1,
                     error=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (msg.get("threadId"),msg.get("rfc_message_id"),msg.get("received_at"),msg.get("sender"),msg.get("recipients"),
                 msg.get("cc"),msg.get("subject"),msg.get("body"),names,src_hash,prio,email_id)
            )
        if src_hash:
            dup=duplicate_final_status(mailbox_id,email_id,src_hash)
            if dup:
                _set_email_status(email_id,"DUPLICATE",f"Exact body-email duplicate of email {dup['id']} · reused terminal classification {dup['status']}",completed=True)
                return True
        process_email(mailbox_id,email_id,msg,allow_ai=allow_ai)
        return True
    except Exception as exc:
        message = str(exc)
        temporary = _is_temporary_error(message) or "database is locked" in message.lower()
        if temporary:
            _set_email_status(email_id, "RAW", message[:800], completed=False)
        else:
            # Quarantine the one bad message; never let it block the mailbox.
            _set_email_status(email_id, "NEEDS REVIEW", f"Quarantined processing failure: {message[:700]}", completed=True)
            try:
                _create_review_item(mailbox_id, email_id, None, f"Quarantined processing failure: {message[:700]}")
            except Exception:
                pass
        return False


def _ensure_processing_job(mailbox_id, email_id, priority=0):
    with db() as conn:
        conn.execute(
            """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority)
               VALUES(?,?,'ANALYZE','READY',?)
               ON CONFLICT(email_id,job_type) DO UPDATE SET
                 priority=MAX(COALESCE(processing_jobs.priority,0),excluded.priority),
                 state=CASE WHEN processing_jobs.state='DONE' THEN processing_jobs.state ELSE processing_jobs.state END""",
            (mailbox_id, email_id, int(priority))
        )


def _recover_stale_jobs():
    with db() as conn:
        conn.execute(
            """UPDATE processing_jobs SET state='READY',lease_owner=NULL,leased_at=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE state='LEASED' AND (leased_at IS NULL OR leased_at < datetime('now', ?))""",
            (f'-{int(JOB_LEASE_SECONDS)} seconds',)
        )


def _recover_stale_email_states():
    """Recover abandoned V21.4 stage leases without changing terminal results."""
    cutoff = f'-{int(STALE_EMAIL_SECONDS)} seconds'
    with db() as conn:
        cur1 = conn.execute(
            """UPDATE emails SET status='RAW',pipeline_stage='MAIN',
                   error=CASE WHEN error IS NULL OR error='' THEN 'Recovered stale Python-main state' ELSE error END,
                   analysis_started_at=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE status IN ('PROCESSING','PDF READING')
                 AND COALESCE(updated_at,created_at) < datetime('now', ?)""", (cutoff,))
        cur2 = conn.execute(
            """UPDATE emails SET status='RESCUE QUEUED',pipeline_stage='RESCUE',
                   error=CASE WHEN error IS NULL OR error='' THEN 'Recovered stale Python-rescue state' ELSE error END,
                   analysis_started_at=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE status='RESCUE PROCESSING'
                 AND COALESCE(updated_at,created_at) < datetime('now', ?)""", (cutoff,))
        cur3 = conn.execute(
            """UPDATE emails SET status='AI QUEUED',pipeline_stage='AI',
                   error=CASE WHEN error IS NULL OR error='' THEN 'Recovered stale AI-exception state' ELSE error END,
                   analysis_started_at=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE status='AI PROCESSING'
                 AND COALESCE(updated_at,created_at) < datetime('now', ?)""", (cutoff,))
    recovered = int(cur1.rowcount or 0) + int(cur2.rowcount or 0) + int(cur3.rowcount or 0)
    if recovered:
        logger.warning('V21.4 recovered %s stale processing state(s)', recovered)
        _setting_set('last_stale_recovery', f'{system_local_now_text()} · {recovered} email(s)')
    return recovered

def _lease_analysis_jobs(mailbox_id, limit):
    _recover_stale_jobs()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with db() as conn:
        # Ensure older pending rows also have durable jobs after an upgrade.
        rows = conn.execute(
            """SELECT id,COALESCE(priority,0) priority FROM emails
               WHERE mailbox_id=? AND status IN ('DISCOVERED','NEW','RAW') ORDER BY priority DESC,id DESC LIMIT ?""",
            (mailbox_id, int(limit))
        ).fetchall()
        for r in rows:
            conn.execute(
                """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority)
                   VALUES(?,?,'ANALYZE','READY',?)
                   ON CONFLICT(email_id,job_type) DO UPDATE SET priority=MAX(processing_jobs.priority,excluded.priority),
                   state=CASE WHEN processing_jobs.state='DONE' THEN 'READY' ELSE processing_jobs.state END""",
                (mailbox_id, r["id"], r["priority"])
            )
        jobs = conn.execute(
            """SELECT j.id job_id,j.email_id,e.gmail_message_id,e.status,COALESCE(j.priority,0) priority
               FROM processing_jobs j JOIN emails e ON e.id=j.email_id
               WHERE j.mailbox_id=? AND j.job_type='ANALYZE' AND j.state IN ('READY','RETRY')
                 AND (j.next_retry_at IS NULL OR j.next_retry_at <= CURRENT_TIMESTAMP)
                 AND e.status IN ('DISCOVERED','NEW','RAW')
               ORDER BY j.priority DESC,j.id ASC LIMIT ?""",
            (mailbox_id, int(limit))
        ).fetchall()
        ids=[int(r["job_id"]) for r in jobs]
        if ids:
            conn.executemany(
                "UPDATE processing_jobs SET state='LEASED',lease_owner=?,leased_at=CURRENT_TIMESTAMP,attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [(owner, jid) for jid in ids]
            )
    return [dict(r) for r in jobs]


def _finish_analysis_job(job_id, email_id):
    with db() as conn:
        row = conn.execute("SELECT status,error,mailbox_id FROM emails WHERE id=?", (email_id,)).fetchone()
        job = conn.execute("SELECT attempts FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
        status = (row["status"] if row else "ERROR") or "ERROR"
        attempts = int(job["attempts"] if job else 0)
        if status in ('DISCOVERED','NEW','RAW'):
            if attempts >= MAX_JOB_ATTEMPTS:
                reason = ((row["error"] if row else "") or f"No completion after {attempts} attempts")[:800]
                conn.execute(
                    "UPDATE processing_jobs SET state='QUARANTINED',quarantine_reason=?,last_error=?,lease_owner=NULL,leased_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (reason, reason, job_id)
                )
                conn.execute(
                    "UPDATE emails SET status='NEEDS REVIEW',error=?,analysis_completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (f"Quarantined after {attempts} attempts: {reason}"[:800], email_id)
                )
            else:
                conn.execute(
                    "UPDATE processing_jobs SET state='RETRY',next_retry_at=datetime('now','+15 seconds'),last_error=?,lease_owner=NULL,leased_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    ((row["error"] if row else "Retry") or "Retry", job_id)
                )
        else:
            conn.execute(
                "UPDATE processing_jobs SET state='DONE',next_retry_at=NULL,last_error=NULL,lease_owner=NULL,leased_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,)
            )


def analyze_discovered_mailbox(mailbox_id, limit=DIRECT_BATCH_SIZE):
    jobs = _lease_analysis_jobs(mailbox_id, limit)
    if not jobs:
        return {"processed": 0}
    done = 0
    def run(job):
        with V214_MAIN_GATE:
            ok = _hydrate_and_process(mailbox_id, job["email_id"], job["gmail_message_id"], False)
            _finish_analysis_job(job["job_id"], job["email_id"])
            return ok
    with ThreadPoolExecutor(max_workers=min(DIRECT_WORKERS, len(jobs))) as pool:
        futures = [pool.submit(run, j) for j in jobs]
        for fut in as_completed(futures):
            try:
                done += 1 if fut.result() else 0
            except Exception:
                traceback.print_exc()
    return {"processed": done}


def _store_message_if_new(mailbox_id, mid, msg=None, allow_ai=False):
    with db() as conn:
        exists = conn.execute(
            """SELECT id
               FROM emails
               WHERE mailbox_id=? AND gmail_message_id=?""",
            (mailbox_id, mid)
        ).fetchone()

    if exists:
        return {"inserted": False, "processed": False}

    mb = mailbox_row(mailbox_id)
    if not msg:
        msg = fetch_message(mb, mid)

    attachment_names = " | ".join(x["filename"] for x in msg["attachments"])

    with db() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO emails(
                mailbox_id,
                gmail_message_id,
                gmail_thread_id,
                rfc_message_id,
                received_at,
                sender,
                recipients,
                cc,
                subject,
                body,
                attachment_names,
                status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            mailbox_id,
            msg["id"],
            msg["threadId"],
            msg.get("rfc_message_id", ""),
            msg["received_at"],
            msg["sender"],
            msg["recipients"],
            msg["cc"],
            msg["subject"],
            msg["body"],
            attachment_names,
            "NEW"
        ))
        email_id = cur.lastrowid

    if not email_id:
        return {"inserted": False, "processed": False}

    try:
        process_email(mailbox_id, email_id, msg, allow_ai=allow_ai)
        return {"inserted": True, "processed": True}
    except Exception as exc:
        message = str(exc)
        if _is_ai_config_error(message):
            _record_ai_error(message)
        temporary = _is_temporary_error(message)
        with db() as conn:
            conn.execute(
                """UPDATE emails
                   SET status=?,
                       error=?,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (
                    "AI QUEUED" if temporary else "ERROR",
                    message[:500],
                    email_id
                )
            )
        return {"inserted": True, "processed": False}


def _is_ai_config_error(message: str) -> bool:
    s = (message or "").lower()
    return any(x in s for x in ("model unavailable", "ollama", "connection refused", "local ai disabled"))


def _record_ai_error(message: str):
    _setting_set("ai_provider_error", (message or "")[:700])
    _setting_set("ai_provider_error_at", system_local_now_text())


def _clear_ai_error():
    _setting_set("ai_provider_error", "")
    _setting_set("ai_provider_error_at", "")
    _setting_set("last_local_ai_success_at", system_local_now_text())


def _is_temporary_error(message: str) -> bool:
    s = (message or "").lower()
    tokens = (
        "ai_temporary_error",
        "429",
        "rate limit",
        "timed out",
        "timeout",
        "connection",
        "temporarily unavailable",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return any(x in s for x in tokens)


def cleanup_exact_product_duplicates(mailbox_id=None):
    """
    Keep the newest exact product row for each mailbox.
    Exact identity = product name + model + purchase price + supplier.
    """
    with db() as conn:
        if mailbox_id is None:
            rows = conn.execute(
                "SELECT * FROM products ORDER BY id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products WHERE mailbox_id=? ORDER BY id DESC",
                (mailbox_id,)
            ).fetchall()

        seen = {}
        delete_ids = []
        keep_updates = []

        for row in rows:
            p = {
                "product_name": row["product_name"],
                "model": row["model"],
                "purchase_price": row["purchase_price"],
                "supplier_name": row["supplier_name"],
            }
            key = make_key("", p)
            group = (row["mailbox_id"], key)

            if group in seen:
                delete_ids.append(row["id"])
            else:
                seen[group] = row["id"]
                keep_updates.append((key, row["id"]))

        if delete_ids:
            conn.executemany(
                "DELETE FROM products WHERE id=?",
                [(x,) for x in delete_ids]
            )

        for key, product_id in keep_updates:
            conn.execute(
                "UPDATE products SET product_key=? WHERE id=?",
                (key, product_id)
            )

        return len(delete_ids)


def retry_errors(mailbox_id):
    with db() as conn:
        rows = conn.execute(
            "SELECT id FROM emails WHERE mailbox_id=? AND status='ERROR'",
            (mailbox_id,)
        ).fetchall()
        for row in rows:
            conn.execute(
                """UPDATE emails
                   SET status='RAW', error=NULL, analysis_completed_at=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (row["id"],)
            )
            conn.execute(
                """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority,next_retry_at,last_error)
                   VALUES(?,?,'ANALYZE','READY',80,NULL,NULL)
                   ON CONFLICT(email_id,job_type) DO UPDATE SET state='READY',priority=MAX(processing_jobs.priority,80),next_retry_at=NULL,last_error=NULL,leased_at=NULL,lease_owner=NULL,updated_at=CURRENT_TIMESTAMP""",
                (mailbox_id,row["id"])
            )
        return len(rows)

def _process_ids_parallel(mailbox_id, ids, allow_ai=False, workers=DIRECT_WORKERS):
    """Fetch/process independent Gmail messages concurrently.

    Gmail message IDs are still protected by the DB UNIQUE constraint, so the
    fast worker cannot create duplicate local emails.
    """
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return {"inserted": 0, "processed": 0}

    inserted = 0
    processed = 0
    with ThreadPoolExecutor(max_workers=min(workers, len(ids))) as pool:
        futures = {
            pool.submit(_store_message_if_new, mailbox_id, mid, None, allow_ai): mid
            for mid in ids
        }
        for fut in as_completed(futures):
            try:
                result = fut.result()
                inserted += 1 if result.get("inserted") else 0
                processed += 1 if result.get("processed") else 0
            except Exception:
                traceback.print_exc()
    return {"inserted": inserted, "processed": processed}


def _refresh_inbox_count_if_due(mailbox_id, mb):
    key = f"inbox_count_checked_at:{mailbox_id}"
    raw = _setting_get(key, "")
    due = True
    if raw:
        try:
            due = (datetime.now() - datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")).total_seconds() >= INBOX_COUNT_REFRESH_SECONDS
        except Exception:
            due = True
    if not due:
        return None
    try:
        total = int(get_inbox_total(mb))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with db() as conn:
            local_count = int(conn.execute(
                "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1",
                (mailbox_id,)
            ).fetchone()["c"])
            conn.execute("UPDATE mailboxes SET inbox_total_estimate=? WHERE id=?", (total, mailbox_id))
        _setting_set(key, now)
        # A count mismatch means Gmail membership changed in a way the incremental
        # tracker did not fully reconcile. Start an exact ID audit in the
        # background; this never blocks the live scanner.
        if local_count != total:
            try:
                threading.Thread(
                    target=verify_inbox_mailbox, args=(mailbox_id, True), daemon=True
                ).start()
            except NameError:
                pass
        return total
    except Exception:
        return None


def sync_mailbox(mailbox_id, query="in:inbox newer_than:2d", max_results=100):
    """Near-live Gmail sync using incremental History events with safe fallback.

    New INBOX messages enter the fast lane immediately. INBOX label removals are
    also applied locally so dashboard counts do not wait for the next full audit.
    """
    _upsert_mailbox_defaults(mailbox_id)
    mb = mailbox_row(mailbox_id)
    if not mb:
        return {"ok": False, "error": "Mailbox not found"}
    now = system_local_now_text()
    ids = []
    removed_ids = []
    next_history_id = str(mb["gmail_history_id"] or "") if "gmail_history_id" in mb.keys() else ""
    mode = "history"
    try:
        if next_history_id:
            delta = list_history_message_ids(mb, next_history_id, max_results=500)
            ids = delta.get("inbox_added_ids") or delta.get("ids", [])
            removed_ids = delta.get("inbox_removed_ids", [])
            next_history_id = delta.get("history_id") or next_history_id
        else:
            # Seed history BEFORE the recent scan so messages arriving during
            # setup are still returned by the next history poll.
            profile = get_gmail_profile(mb)
            next_history_id = profile.get("history_id") or ""
            ids = list_message_ids(mb, query=query, max_results=max_results)
            mode = "seed"
    except Exception:
        # Expired/invalid Gmail history IDs safely recover with a recent Inbox scan.
        profile = get_gmail_profile(mb)
        next_history_id = profile.get("history_id") or ""
        ids = list_message_ids(mb, query=query, max_results=max_results)
        removed_ids = []
        mode = "fallback"

    _index_message_ids(mailbox_id, ids, priority=FAST_LANE_PRIORITY)
    if removed_ids:
        with db() as conn:
            conn.executemany(
                "UPDATE emails SET in_inbox=0,updated_at=CURRENT_TIMESTAMP WHERE mailbox_id=? AND gmail_message_id=?",
                [(mailbox_id, mid) for mid in removed_ids]
            )
            # Keep the displayed Inbox count responsive between exact count refreshes.
            conn.execute(
                """UPDATE mailboxes SET inbox_total_estimate=MAX(0,COALESCE(inbox_total_estimate,0)+?-?)
                   WHERE id=?""",
                (len(set(ids)), len(set(removed_ids)), mailbox_id)
            )
    elif ids:
        with db() as conn:
            conn.execute(
                """UPDATE mailboxes SET inbox_total_estimate=MAX(COALESCE(inbox_total_estimate,0),
                       (SELECT COUNT(*) FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1)) WHERE id=?""",
                (mailbox_id, mailbox_id)
            )

    _refresh_inbox_count_if_due(mailbox_id, mailbox_row(mailbox_id) or mb)
    with db() as conn:
        conn.execute(
            "UPDATE mailboxes SET last_sync_at=?,last_live_poll_at=?,gmail_history_id=? WHERE id=?",
            (now, now, next_history_id, mailbox_id)
        )
    return {
        "ok": True, "indexed": len(ids), "removed_from_inbox": len(removed_ids),
        "mode": mode, "server_time": now
    }


def history_backfill_mailbox(mailbox_id, page_size=HISTORY_PAGE_SIZE, pages_per_cycle=HISTORY_PAGES_PER_CYCLE):
    """Fast full-inbox coverage: enumerate/store IDs only. Content analysis is independent."""
    lock = _history_lock(mailbox_id)
    if not lock.acquire(blocking=False):
        return {"ok": True, "busy": True, "pages": 0, "scanned": 0}
    try:
        _upsert_mailbox_defaults(mailbox_id)
        mb = mailbox_row(mailbox_id)
        if not mb:
            return {"ok": False, "error": "Mailbox not found"}
        query = (mb["inbox_query"] or "in:inbox").strip() or "in:inbox"
        page_token = mb["history_page_token"]
        if int(mb["history_complete"] or 0) and not page_token:
            return {"ok": True, "complete": True, "pages": 0, "scanned": 0}
        try:
            exact_total = get_inbox_total(mb)
        except Exception:
            exact_total = int(mb["inbox_total_estimate"] or 0)
        pages = 0
        scanned = 0
        while pages < pages_per_cycle:
            data = list_message_page(mb, query=query, max_results=page_size, page_token=page_token or None)
            ids = data["ids"]
            next_token = data.get("next_page_token")
            if data.get("result_size_estimate"):
                exact_total = max(exact_total, int(data["result_size_estimate"]))
            _index_message_ids(mailbox_id, ids, priority=10)
            scanned += len(ids)
            pages += 1
            with db() as conn:
                current = conn.execute("SELECT COALESCE(history_scanned_count,0) c FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()
                new_count = int(current["c"] or 0) + len(ids)
                if exact_total:
                    new_count = min(new_count, exact_total)
                now = system_local_now_text()
                conn.execute(
                    """UPDATE mailboxes SET history_page_token=?,history_scanned_count=?,inbox_total_estimate=?,
                       last_history_at=?,last_sync_at=?,history_complete=? WHERE id=?""",
                    (next_token, new_count, exact_total, now, now, 0 if next_token else 1, mailbox_id)
                )
            page_token = next_token
            if not page_token:
                break
        return {"ok": True, "pages": pages, "scanned": scanned, "complete": not bool(page_token)}
    finally:
        lock.release()

def _route_done(email_id):
    # V21.4 has no private hyperspeed_routes runtime. Legacy table rows are
    # removed once during startup migration and otherwise ignored.
    return None


def _normalize_candidate_prices(product):
    """Derive net price only from explicit original price + explicit supplier discount."""
    p = dict(product or {})
    if p.get("purchase_price") not in (None, ""):
        return p
    try:
        original = float(str(p.get("original_price")).replace(",", ""))
        discount = float(str(p.get("discount_percent")).replace("%", "").strip())
        if original > 0 and 0 < discount < 100:
            p["purchase_price"] = round(original * (1.0 - discount / 100.0), 2)
            p["price_basis"] = p.get("price_basis") or "DERIVED_FROM_EXPLICIT_DISCOUNT"
    except Exception:
        pass
    return p


def _apply_local_verifier_result(proposed, indices, local_result):
    rows = (local_result or {}).get("products") or []
    by_index = {}
    for row in rows:
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        if row.get("accept") is True:
            by_index[idx] = row
    for local_idx, original_idx in enumerate(indices):
        row = by_index.get(local_idx)
        if not row:
            continue
        base = dict(proposed[original_idx])
        for key in ("product_name","model","specifications","supplier_name","price_date","original_price","discount_percent","purchase_price"):
            if row.get(key) not in (None, ""):
                base[key] = row.get(key)
        try:
            base["confidence"] = max(float(base.get("confidence") or 0), float(row.get("confidence") or 0))
        except Exception:
            pass
        if row.get("evidence"):
            base["local_verifier_evidence"] = row.get("evidence")
        proposed[original_idx] = _normalize_candidate_prices(base)
    return proposed

def _create_review_item(mailbox_id, email_id, product_id, reason, payload=None):
    with db() as conn:
        existing = conn.execute(
            """SELECT id FROM review_items
               WHERE mailbox_id=? AND COALESCE(email_id,0)=COALESCE(?,0)
                 AND COALESCE(product_id,0)=COALESCE(?,0) AND status='OPEN'
               ORDER BY id DESC LIMIT 1""",
            (mailbox_id, email_id, product_id)
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO review_items(mailbox_id,email_id,product_id,reason,payload_json,status)
               VALUES(?,?,?,?,?,'OPEN')""",
            (mailbox_id, email_id, product_id, (reason or "Needs verification")[:1000], json.dumps(payload or {}, ensure_ascii=False)[:12000])
        )
        return int(cur.lastrowid)


def _save_products_and_finish(mailbox_id, email_id, msg, products, source_default="EMAIL", source_filename="",
                              attachment_texts=None, multimodal_files=None):
    """Persist only candidates accepted by the Python source-evidence firewall.

    V21.4 never invokes an AI model from this persistence function. AI may
    propose a candidate in the exception lane, but Python verify_product is the
    only authority that can make a row export-eligible.
    """
    attachment_texts = attachment_texts or []
    multimodal_files = multimodal_files or []
    proposed = []
    for raw in products or []:
        if not raw or not raw.get("product_name"):
            continue
        p = _normalize_candidate_prices(raw)
        if p.get("purchase_price") is not None:
            proposed.append(p)

    if not proposed:
        _set_email_status(email_id, "NEEDS REVIEW", "Extraction did not contain a safely evidenced product-price pair.", completed=True)
        _create_review_item(mailbox_id, email_id, None, "No safely evidenced product-price candidate after bounded extraction")
        return 0

    # Pre-compute only to keep negative-memory bookkeeping consistent. Final
    # verification is repeated immediately before every database write below.
    checks = [verify_product(p, msg, attachment_texts) for p in proposed]

    deferred_products = []
    deferred_ids = set()

    created = 0
    confirmed_created = 0
    review_created = 0
    processed_keys = []

    for i, p in enumerate(proposed):
        if i in deferred_ids:
            continue
        verification = verify_product(p, msg, attachment_texts)
        try:
            record_negative_flags(verification.get("quality_flags") or [],verification.get("price_evidence") or p.get("price_evidence", ""))
        except Exception:
            pass
        if not p.get("price_date"):
            p["price_date"] = (msg.get("received_at") or "")[:10]
        p["source_type"] = p.get("source_type") or source_default
        if p["source_type"] != "EMAIL" and not p.get("source_filename"):
            p["source_filename"] = source_filename
        key = make_key(msg.get("id", ""), p)
        processed_keys.append(key)

        with db() as conn:
            values = (
                mailbox_id,email_id,key,p.get("marketing_person",""),p.get("price_date",""),p.get("image_link",""),
                p.get("product_name",""),verification.get("canonical_product_name",""),p.get("model",""),verification.get("canonical_model",""),
                p.get("specifications",""),verification.get("original_price", p.get("original_price")),verification.get("discount_percent", p.get("discount_percent")),verification.get("purchase_price", p.get("purchase_price")),verification.get("currency") or "INR",p.get("purchase_owner",""),
                p.get("supplier_name",""),verification.get("canonical_supplier_name",""),p.get("source_type",source_default),
                p.get("source_filename",source_filename),p.get("confidence",0),verification.get("status","NEEDS REVIEW"),
                verification.get("confidence",0),1 if verification.get("export_eligible") else 0,json.dumps(verification.get("quality_flags") or []),
                verification.get("price_basis") or p.get("price_basis", ""),verification.get("price_evidence") or p.get("price_evidence", ""),
                verification.get("evidence_summary",""),msg.get("id","") or "",
                msg.get("threadId","") or "",msg.get("rfc_message_id","") or ""
            )
            cur = conn.execute(
                """INSERT OR IGNORE INTO products(
                    mailbox_id,email_id,product_key,marketing_person,price_date,image_link,
                    product_name,canonical_product_name,model,canonical_model,specifications,original_price,discount_percent,purchase_price,currency,
                    purchase_owner,supplier_name,canonical_supplier_name,source_type,source_filename,confidence,
                    verification_status,verification_confidence,export_eligible,quality_flags,price_basis,price_evidence,evidence_summary,
                    source_gmail_message_id,source_gmail_thread_id,source_rfc_message_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values
            )
            if cur.rowcount:
                created += 1
                product_id = int(cur.lastrowid)
            else:
                existing = conn.execute("SELECT id FROM products WHERE mailbox_id=? AND product_key=?", (mailbox_id, key)).fetchone()
                if not existing:
                    continue
                product_id = int(existing["id"])
                conn.execute(
                    """UPDATE products SET email_id=?,price_date=?,product_name=?,canonical_product_name=?,model=?,canonical_model=?,
                       specifications=?,original_price=?,discount_percent=?,purchase_price=?,currency=?,supplier_name=?,canonical_supplier_name=?,source_type=?,source_filename=?,
                       confidence=?,verification_status=?,verification_confidence=?,export_eligible=?,quality_flags=?,price_basis=?,price_evidence=?,evidence_summary=?,source_gmail_message_id=?,
                       source_gmail_thread_id=?,source_rfc_message_id=?,sheet_exported_at=NULL,sheet_export_error=NULL,sheet_export_target=NULL,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        email_id,p.get("price_date",""),p.get("product_name",""),verification.get("canonical_product_name",""),
                        p.get("model",""),verification.get("canonical_model",""),p.get("specifications",""),
                        verification.get("original_price", p.get("original_price")),verification.get("discount_percent", p.get("discount_percent")),
                        verification.get("purchase_price", p.get("purchase_price")),verification.get("currency") or "INR",p.get("supplier_name",""),verification.get("canonical_supplier_name",""),
                        p.get("source_type",source_default),p.get("source_filename",source_filename),p.get("confidence",0),
                        verification.get("status","NEEDS REVIEW"),verification.get("confidence",0),1 if verification.get("export_eligible") else 0,
                        json.dumps(verification.get("quality_flags") or []),verification.get("price_basis") or p.get("price_basis", ""),
                        verification.get("price_evidence") or p.get("price_evidence", ""),verification.get("evidence_summary",""),
                        msg.get("id","") or "",msg.get("threadId","") or "",msg.get("rfc_message_id","") or "",product_id
                    )
                )
                conn.execute("DELETE FROM product_evidence WHERE product_id=?", (product_id,))
                created += 1

            for ev in verification.get("evidence", []) or []:
                conn.execute(
                    """INSERT INTO product_evidence(product_id,field_name,evidence_text,source_type,source_filename,source_page,confidence)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        product_id, ev.get("field_name",""), ev.get("evidence_text",""), p.get("source_type",source_default),
                        p.get("source_filename",source_filename), ev.get("source_page", ""), ev.get("confidence",0)
                    )
                )

        try:
            sender_domain = ""
            m = re.search(r"@([A-Za-z0-9.-]+)", msg.get("sender", "") or "")
            if m:
                sender_domain = m.group(1).lower()
            with db() as conn:
                supplier = verification.get("canonical_supplier_name") or (p.get("supplier_name") or "").strip()
                if supplier:
                    conn.execute(
                        """INSERT INTO supplier_master(canonical_name,domain,aliases,last_seen_at,quotation_count) VALUES(?,?,?,?,1)
                           ON CONFLICT(canonical_name) DO UPDATE SET domain=COALESCE(NULLIF(excluded.domain,''),supplier_master.domain),
                           last_seen_at=excluded.last_seen_at,quotation_count=supplier_master.quotation_count+1""",
                        (supplier, sender_domain, p.get("supplier_name", ""), system_local_now_text())
                    )
                conn.execute(
                    """INSERT OR IGNORE INTO product_master_aliases(canonical_product_name,canonical_model,alias_name,alias_model) VALUES(?,?,?,?)""",
                    (
                        verification.get("canonical_product_name", ""), verification.get("canonical_model", ""),
                        p.get("product_name", ""), p.get("model", "")
                    )
                )
        except Exception:
            pass

        status = verification.get("status", "NEEDS REVIEW")
        if status == "CONFIRMED":
            confirmed_created += 1
            try:
                learn_confirmed_product(product_id,human_approved=False)
            except Exception:
                logger.debug("Confirmed learning update skipped for product %s",product_id,exc_info=True)
        else:
            review_created += 1
            _create_review_item(
                mailbox_id, email_id, product_id,
                f"{status}: blocked from Sheet export — {', '.join(verification.get('quality_flags') or ['evidence threshold not met'])}",
                {"verification": verification, "product": p}
            )

    if processed_keys and not deferred_products:
        with db() as conn:
            placeholders = ",".join("?" for _ in processed_keys)
            conn.execute(
                f"""UPDATE products SET verification_status='SUPERSEDED',sheet_exported_at=NULL,sheet_export_target=NULL,updated_at=CURRENT_TIMESTAMP
                    WHERE mailbox_id=? AND email_id=? AND verification_status='REVERIFYING'
                      AND product_key NOT IN ({placeholders})""",
                [mailbox_id, email_id, *processed_keys]
            )

    if confirmed_created:
        _set_email_status(email_id, f"EXTRACTED {confirmed_created}", None, completed=True)
    elif review_created:
        _set_email_status(email_id, "NEEDS REVIEW", "Product candidate found but evidence did not reach confirmed threshold.", completed=True)
    else:
        _set_email_status(email_id, "DUPLICATE / ALREADY EXISTS", None, completed=True)
    return created

def _collect_attachment_evidence(mailbox_id, msg, download_visual=False):
    pdf_products = []
    attachment_texts = []
    multimodal_files = []
    pdf_detected = False
    readable_attachment = False
    visual_only = False
    first_source_file = ""
    if not msg.get("attachments"):
        return {
            "pdf_products": [], "attachment_texts": [], "multimodal_files": [],
            "pdf_detected": False, "readable_attachment": False, "visual_only": False,
            "first_source_file": ""
        }
    mb = mailbox_row(mailbox_id)
    for attachment in msg.get("attachments", []):
        filename = attachment.get("filename", "")
        low = filename.lower()
        if not first_source_file:
            first_source_file = filename
        try:
            if low.endswith(".pdf"):
                pdf_detected = True
                content = download_attachment(mb, msg["id"], attachment["attachmentId"])
                memory_ok, memory_info = _local_ai_memory_headroom()
                if not memory_ok:
                    pdf_result = {
                        "ok": False,
                        "error": f"LOW_MEMORY_DEFERRED: {memory_info.get('available_mb',0)} MB free",
                        "bundle": {"text": "", "products": []},
                    }
                else:
                    with PDF_PARSE_SEMAPHORE:
                        pdf_result = extract_pdf_bundle_isolated(
                            content, filename, max_chars=80000,
                            timeout_seconds=PDF_PARSE_TIMEOUT_SECONDS
                        )
                bundle = pdf_result.get("bundle") or {"text":"","products":[]}
                if not pdf_result.get("ok"):
                    attachment_texts.append({"name": filename, "text": f"[Local PDF parser deferred: {pdf_result.get('error','unknown error')}]"})
                text = bundle.get("text") or ""
                if text:
                    readable_attachment = True
                    attachment_texts.append({"name": filename, "text": text})
                if bundle.get("products"):
                    pdf_products.extend(bundle.get("products") or [])
                if download_visual:
                    multimodal_files.append({"name": filename, "mime_type": "application/pdf", "data": content})
            elif low.endswith((".xlsx", ".csv", ".tsv", ".docx", ".txt")):
                content = download_attachment(mb, msg["id"], attachment["attachmentId"])
                text = extract_attachment_text(filename, content)
                if text:
                    readable_attachment = True
                    attachment_texts.append({"name": filename, "text": text})
            elif low.endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")):
                visual_only = True
                if download_visual:
                    content = download_attachment(mb, msg["id"], attachment["attachmentId"])
                    ext_mime = {
                        ".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
                        ".webp":"image/webp", ".tif":"image/tiff", ".tiff":"image/tiff"
                    }
                    suffix = next((x for x in ext_mime if low.endswith(x)), "")
                    multimodal_files.append({"name": filename, "mime_type": ext_mime.get(suffix, "image/jpeg"), "data": content})
        except Exception as exc:
            attachment_texts.append({"name": filename, "text": f"[Attachment could not be read locally: {exc}]"})
    return {
        "pdf_products": pdf_products,
        "attachment_texts": attachment_texts,
        "multimodal_files": multimodal_files,
        "pdf_detected": pdf_detected,
        "readable_attachment": readable_attachment,
        "visual_only": visual_only,
        "first_source_file": first_source_file,
    }


def _merge_pdf_products(body_products, pdf_products):
    if not pdf_products:
        return list(body_products or [])
    def _compact(v):
        return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())
    pdf_models = {_compact(p.get("model")) for p in pdf_products if _compact(p.get("model"))}
    pdf_names = [_compact(p.get("product_name")) for p in pdf_products]
    body_only = []
    for p in body_products or []:
        model = _compact(p.get("model"))
        name = _compact(p.get("product_name"))
        same = bool(model and model in pdf_models)
        if not same and name:
            same = any(name in pn or pn in name for pn in pdf_names if pn and min(len(name), len(pn)) >= 8)
        if not same:
            body_only.append(p)
    return body_only + list(pdf_products)


def _learning_memory_context(mailbox_id, msg):
    try:
        return build_memory_context(mailbox_id, msg, [], max_chars=3500)
    except Exception:
        return ""

def _with_learning_context(mailbox_id,msg):
    enriched=dict(msg or {})
    enriched["_memory_context"]=_learning_memory_context(mailbox_id,msg)
    try:
        enriched["_ml_router_score"] = router_score(enriched.get("sender",""),enriched.get("subject",""),enriched.get("body",""))
    except Exception:
        enriched["_ml_router_score"] = None
    return enriched

def _set_v214_stage(email_id, status, stage, error=None, completed=False):
    with db() as conn:
        conn.execute("UPDATE emails SET pipeline_stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (stage, int(email_id)))
    _set_email_status(email_id, status, error, completed=completed)


def _v214_all_confirmed(products, msg, attachment_texts=None):
    rows=[p for p in (products or []) if p and p.get("product_name") and p.get("purchase_price") not in (None,"")]
    if not rows:
        return False
    checks=[verify_product(p,msg,attachment_texts or []) for p in rows]
    return bool(checks) and all(v.get("status") == "CONFIRMED" and v.get("export_eligible") for v in checks)


def _route_v214_rescue(email_id, reason="Python main unresolved"):
    _set_v214_stage(email_id, "RESCUE QUEUED", "RESCUE", reason[:800], completed=False)


def _route_v214_ai(email_id, reason="Python rescue unresolved"):
    _set_v214_stage(email_id, "AI QUEUED", "AI", reason[:800], completed=False)


def _claim_v214_rows(mailbox_id, queued_status, processing_status, stage, limit):
    with db() as conn:
        rows=conn.execute(
            """SELECT id,gmail_message_id,COALESCE(rescue_attempts,0) rescue_attempts,COALESCE(ai_attempts,0) ai_attempts
               FROM emails WHERE mailbox_id=? AND status=? ORDER BY priority DESC,id ASC LIMIT ?""",
            (int(mailbox_id), queued_status, int(limit))).fetchall()
        claimed=[]
        for raw in rows:
            r=dict(raw)
            if stage == "RESCUE":
                cur=conn.execute("UPDATE emails SET status=?,pipeline_stage='RESCUE',rescue_attempts=COALESCE(rescue_attempts,0)+1,analysis_started_at=COALESCE(analysis_started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
                                 (processing_status,int(r["id"]),queued_status))
            else:
                cur=conn.execute("UPDATE emails SET status=?,pipeline_stage='AI',ai_attempts=COALESCE(ai_attempts,0)+1,analysis_started_at=COALESCE(analysis_started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
                                 (processing_status,int(r["id"]),queued_status))
            if cur.rowcount:
                claimed.append(r)
    return claimed


def _v214_augment_ocr(evidence):
    evidence=dict(evidence or {})
    need_ocr=bool(evidence.get("visual_only") or (evidence.get("pdf_detected") and not evidence.get("readable_attachment")))
    if not need_ocr or not evidence.get("multimodal_files"):
        evidence["ocr"]={"ok":False,"skipped":True,"health":ocr_health()}
        return evidence
    result=ocr_multimodal_files(evidence.get("multimodal_files") or [])
    evidence["ocr"]=result
    if result.get("texts"):
        evidence["attachment_texts"] = list(evidence.get("attachment_texts") or []) + list(result.get("texts") or [])
        evidence["readable_attachment"] = True
        _setting_set("last_v214_ocr_at", system_local_now_text())
        _setting_set("last_v214_ocr_files", str(len(result.get("texts") or [])))
    return evidence


def process_email(mailbox_id, email_id, msg, allow_ai=True):
    """V21.5 Stage 0 + Stage 1.

    Stage 0 discards only certainly irrelevant mail. Stage 1 performs format
    classification/segregation only. Deep product-price analysis belongs to the
    independent Stage-2 specialist lanes below.
    """
    _set_v214_stage(email_id, "STAGE0 FILTER", "STAGE0", None, completed=False)
    stage0 = stage0_filter(msg.get("sender", ""), msg.get("subject", ""), msg.get("body", ""), msg.get("attachments") or [])
    with db() as conn:
        conn.execute(
            "UPDATE emails SET relevance_score=?,stage0_decision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (float(stage0.get("score") or 0), str(stage0.get("decision") or ""), int(email_id)),
        )

    if stage0.get("decision") == "CERTAIN_IRRELEVANT":
        _set_v214_stage(email_id, "IRRELEVANT", "DONE", stage0.get("reason"), completed=True)
        return 0

    _set_v214_stage(email_id, "STAGE1 CLASSIFY", "STAGE1", stage0.get("reason"), completed=False)
    tasks = classify_stage1_tasks(msg)
    summary = stage1_summary(tasks)
    with db() as conn:
        # A retried RAW email may already have stale Stage-2 rows. Rebuild only
        # unfinished tasks; terminal historical email/product results are untouched.
        conn.execute("DELETE FROM v215_stage_tasks WHERE email_id=?", (int(email_id),))
        for t in tasks:
            lane = str(t.get("lane") or "OTHER")
            base_priority = int(candidate_score(msg.get("subject", ""), msg.get("body", ""), t.get("filename", "")))
            base_priority += int(filename_priority(t.get("filename", "")))
            conn.execute(
                """INSERT INTO v215_stage_tasks(
                       mailbox_id,email_id,task_key,lane,filename,attachment_id,mime_type,state,priority
                   ) VALUES(?,?,?,?,?,?,?,'QUEUED',?)""",
                (
                    int(mailbox_id), int(email_id), str(t.get("task_key") or ""), lane,
                    str(t.get("filename") or ""), str(t.get("attachment_id") or ""),
                    str(t.get("mime_type") or ""), base_priority,
                ),
            )
        conn.execute(
            "UPDATE emails SET status='STAGE2 QUEUED',pipeline_stage='STAGE2',stage1_summary=?,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(summary, ensure_ascii=False), int(email_id)),
        )
    return 0


_V215_FINALIZE_LOCK = threading.RLock()
_V215_TERMINAL_TASK_STATES = ("DONE", "SKIPPED", "FAILED")


def _v215_task_update(task_id, *, state=None, lane=None, score=None, reason=None, evidence=None, result=None, completed=False):
    sets = ["updated_at=CURRENT_TIMESTAMP"]
    args = []
    if state is not None:
        sets.append("state=?"); args.append(str(state))
    if lane is not None:
        sets.append("lane=?"); args.append(str(lane))
    if score is not None:
        sets.append("commercial_score=?"); args.append(float(score))
    if reason is not None:
        sets.append("probe_reason=?"); args.append(str(reason)[:1000])
    if evidence is not None:
        sets.append("evidence_text=?"); args.append(str(evidence)[:120000])
    if result is not None:
        sets.append("result_json=?"); args.append(str(result)[:250000])
    if completed:
        sets.append("completed_at=CURRENT_TIMESTAMP")
    args.append(int(task_id))
    with db() as conn:
        conn.execute(f"UPDATE v215_stage_tasks SET {','.join(sets)} WHERE id=?", args)


def _v215_claim_tasks(lane, limit):
    claimed = []
    with db() as conn:
        rows = conn.execute(
            """SELECT t.*,e.gmail_message_id,e.subject,e.body,e.sender,e.received_at,e.gmail_thread_id,e.rfc_message_id
               FROM v215_stage_tasks t JOIN emails e ON e.id=t.email_id
               WHERE t.lane=? AND t.state='QUEUED' AND e.analysis_completed_at IS NULL
               ORDER BY t.priority DESC,t.id ASC LIMIT ?""",
            (str(lane), int(limit)),
        ).fetchall()
        for raw in rows:
            r = dict(raw)
            cur = conn.execute(
                """UPDATE v215_stage_tasks SET state='PROCESSING',attempts=COALESCE(attempts,0)+1,
                          started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND state='QUEUED'""",
                (int(r["id"]),),
            )
            if cur.rowcount:
                claimed.append(r)
    return claimed


def _v215_email_msg_from_row(row):
    return {
        "id": str(row.get("gmail_message_id") or ""),
        "threadId": str(row.get("gmail_thread_id") or ""),
        "rfc_message_id": str(row.get("rfc_message_id") or ""),
        "received_at": str(row.get("received_at") or ""),
        "sender": str(row.get("sender") or ""),
        "subject": str(row.get("subject") or ""),
        "body": str(row.get("body") or ""),
    }


def _v215_download_task_attachment(row):
    mb = mailbox_row(int(row["mailbox_id"]))
    if not mb:
        raise RuntimeError("Mailbox not found")
    return download_attachment(mb, str(row.get("gmail_message_id") or ""), str(row.get("attachment_id") or ""))


def _v215_store_result(row, products, evidence_text, commercial, needs_ai=False, reason="", score=0, meta=None):
    _v215_task_update(
        row["id"], state="DONE", score=score, reason=reason, evidence=evidence_text,
        result=task_result(products, evidence_text, commercial, needs_ai, reason, meta), completed=True,
    )


def _v215_process_text_task(row):
    with V215_TEXT_GATE:
        msg = _v215_email_msg_from_row(row)
        source = "\n".join([msg.get("subject", ""), msg.get("body", "")])
        probe = commercial_probe_text(source, "")
        if not probe.get("commercial"):
            _v215_task_update(row["id"], state="SKIPPED", score=probe.get("score", 0), reason=probe.get("reason"), evidence=source[:30000],
                              result=task_result([], source[:30000], False, False, probe.get("reason", ""), probe), completed=True)
            return
        products = extract_main(msg.get("subject", ""), msg.get("body", ""), msg.get("sender", ""))
        _v215_store_result(row, products, source, True, not bool(products), "TEXT commercial analysis complete", probe.get("score", 0), probe)


def _v215_process_pdf_task(row):
    with V215_PDF_GATE:
        content = _v215_download_task_attachment(row)
        filename = str(row.get("filename") or "document.pdf")
        probe = probe_pdf_bytes(content, filename)
        if not probe.get("ok"):
            _v215_store_result(row, [], "", True, True, f"PDF pre-probe failed: {probe.get('error','unknown')}", 0, probe)
            return
        if probe.get("image_only"):
            # Stage 1 classified by extension; Stage 2 discovers whether the PDF
            # actually needs OCR and moves only that task into the OCR lane.
            _v215_task_update(row["id"], state="QUEUED", lane="OCR", score=filename_priority(filename),
                              reason="PDF has no readable embedded text; routed to OCR", evidence="", result="")
            return
        if not probe.get("commercial"):
            _v215_task_update(row["id"], state="SKIPPED", score=probe.get("score", 0), reason="PDF skipped before deep parse: no explicit price evidence",
                              evidence=(probe.get("text") or "")[:50000],
                              result=task_result([], probe.get("text") or "", False, False, "PDF no explicit price evidence", probe), completed=True)
            return
        target = target_pdf_bytes(content, probe.get("hit_pages") or [], radius=1, max_target_pages=12)
        with PDF_PARSE_SEMAPHORE:
            parsed = extract_pdf_bundle_isolated(target, filename, max_chars=90000, timeout_seconds=PDF_PARSE_TIMEOUT_SECONDS)
        bundle = parsed.get("bundle") or {"text": "", "products": []}
        text = bundle.get("text") or probe.get("text") or ""
        msg = _v215_email_msg_from_row(row)
        attachment_texts = [{"name": filename, "text": text}]
        products = extract_rescue(msg.get("subject", ""), msg.get("body", ""), attachment_texts, bundle.get("products") or [], sender=msg.get("sender", ""))
        _v215_store_result(row, products, text, True, not bool(products), "PDF targeted commercial analysis complete", probe.get("score", 0), probe)


def _v215_process_document_task(row, lane):
    gate = V215_TABLE_GATE if lane == "TABLE" else V215_DOC_GATE
    with gate:
        content = _v215_download_task_attachment(row)
        filename = str(row.get("filename") or "attachment")
        text = extract_attachment_text(filename, content, max_chars=90000)
        probe = commercial_probe_text(text, filename)
        if not text:
            _v215_store_result(row, [], "", bool(filename_priority(filename)), True,
                               f"{lane} document could not be read locally", probe.get("score", 0), probe)
            return
        if not probe.get("commercial"):
            _v215_task_update(row["id"], state="SKIPPED", score=probe.get("score", 0), reason=f"{lane} skipped: no explicit price evidence",
                              evidence=text[:50000], result=task_result([], text, False, False, f"{lane} no explicit price evidence", probe), completed=True)
            return
        msg = _v215_email_msg_from_row(row)
        att = [{"name": filename, "text": text}]
        products = extract_rescue(msg.get("subject", ""), msg.get("body", ""), att, [], sender=msg.get("sender", ""))
        _v215_store_result(row, products, text, True, not bool(products), f"{lane} commercial analysis complete", probe.get("score", 0), probe)


def _v215_process_ocr_task(row):
    with V215_OCR_GATE:
        content = _v215_download_task_attachment(row)
        filename = str(row.get("filename") or "scan")
        low = filename.lower()
        msg = _v215_email_msg_from_row(row)
        chunks = []
        meta = {"filename": filename, "probe_pages": V215_OCR_PROBE_PAGES, "max_pages": V215_OCR_MAX_PAGES}
        if low.endswith(".pdf"):
            offset = 0
            page_count = None
            empty_first_chunk = False
            commercial_probe = None
            while offset < V215_OCR_MAX_PAGES:
                r = ocr_pdf_bytes(content, filename, max_pages=V215_OCR_PROBE_PAGES, start_page=offset)
                page_count = int(r.get("page_count") or 0)
                text = str(r.get("text") or "")
                if text:
                    chunks.append(text)
                    combined = "\n\n".join(chunks)
                    commercial_probe = commercial_probe_text(combined, filename)
                    if commercial_probe.get("commercial"):
                        break
                elif offset == 0:
                    empty_first_chunk = True
                    # Empty-page circuit breaker: do not burn eight pages repeating
                    # the same empty OCR result. Unreadable visual evidence is an AI/
                    # Review exception, not proof that no price exists.
                    break
                offset += V215_OCR_PROBE_PAGES
                if page_count and offset >= page_count:
                    break
            text = "\n\n".join(chunks)
            meta.update({"page_count": page_count or 0, "pages_attempted": min(V215_OCR_MAX_PAGES, offset + (0 if empty_first_chunk else V215_OCR_PROBE_PAGES)), "empty_first_chunk": empty_first_chunk})
            if empty_first_chunk:
                _v215_store_result(row, [], "", True, True, "OCR returned no text in the initial scan window; visual exception required", filename_priority(filename), meta)
                return
            probe = commercial_probe or commercial_probe_text(text, filename)
            if not probe.get("commercial"):
                if page_count and page_count <= V215_OCR_MAX_PAGES:
                    _v215_task_update(row["id"], state="SKIPPED", score=probe.get("score", 0), reason="Scanned PDF OCR found no explicit price evidence",
                                      evidence=text[:60000], result=task_result([], text, False, False, "Scanned PDF no explicit price evidence", {**meta, **probe}), completed=True)
                else:
                    _v215_store_result(row, [], text, True, True, "Scanned PDF exceeded bounded OCR window without provable price evidence", probe.get("score", 0), {**meta, **probe})
                return
        else:
            r = ocr_image_bytes(content, filename)
            text = str(r.get("text") or "")
            meta.update(r)
            if not text:
                _v215_store_result(row, [], "", True, True, "Image OCR returned no readable text; visual exception required", filename_priority(filename), meta)
                return
            probe = commercial_probe_text(text, filename)
            if not probe.get("commercial"):
                _v215_task_update(row["id"], state="SKIPPED", score=probe.get("score", 0), reason="Image OCR found no explicit price evidence",
                                  evidence=text[:60000], result=task_result([], text, False, False, "Image no explicit price evidence", {**meta, **probe}), completed=True)
                return
        att = [{"name": filename, "text": text}]
        products = extract_rescue(msg.get("subject", ""), msg.get("body", ""), att, [], sender=msg.get("sender", ""))
        _v215_store_result(row, products, text, True, not bool(products), "OCR commercial analysis complete", probe.get("score", 0), {**meta, **probe})


def _v215_process_other_task(row):
    filename = str(row.get("filename") or "")
    if filename_priority(filename):
        _v215_store_result(row, [], "", True, True, "Commercial-looking unsupported attachment requires exception handling", filename_priority(filename), {"filename": filename})
    else:
        _v215_task_update(row["id"], state="SKIPPED", score=0, reason="Unsupported non-commercial attachment skipped",
                          result=task_result([], "", False, False, "unsupported non-commercial attachment", {"filename": filename}), completed=True)


def _v215_finalize_email_if_ready(email_id):
    with _V215_FINALIZE_LOCK:
        with db() as conn:
            email = conn.execute("SELECT * FROM emails WHERE id=?", (int(email_id),)).fetchone()
            if not email or email["analysis_completed_at"]:
                return False
            active = int(conn.execute("SELECT COUNT(*) c FROM v215_stage_tasks WHERE email_id=? AND state NOT IN ('DONE','SKIPPED','FAILED')", (int(email_id),)).fetchone()["c"])
            if active:
                return False
            tasks = [dict(r) for r in conn.execute("SELECT * FROM v215_stage_tasks WHERE email_id=? ORDER BY id", (int(email_id),)).fetchall()]
            erow = dict(email)

        msg = {
            "id": erow.get("gmail_message_id") or "",
            "threadId": erow.get("gmail_thread_id") or "",
            "rfc_message_id": erow.get("rfc_message_id") or "",
            "received_at": erow.get("received_at") or "",
            "sender": erow.get("sender") or "",
            "subject": erow.get("subject") or "",
            "body": erow.get("body") or "",
        }
        candidates = []
        attachment_texts = []
        commercial_any = False
        needs_ai = False
        failed_any = False
        first_source = ""
        for t in tasks:
            if t.get("filename") and not first_source:
                first_source = t.get("filename") or ""
            evidence = str(t.get("evidence_text") or "")
            if evidence and t.get("task_key") != "BODY":
                attachment_texts.append({"name": t.get("filename") or t.get("lane") or "attachment", "text": evidence})
            if t.get("state") == "FAILED":
                failed_any = True
            try:
                payload = json.loads(t.get("result_json") or "{}")
            except Exception:
                payload = {}
            commercial_any = commercial_any or bool(payload.get("commercial")) or float(t.get("commercial_score") or 0) >= 60
            needs_ai = needs_ai or bool(payload.get("needs_ai"))
            for p in payload.get("products") or []:
                if p and p.get("product_name") and p.get("purchase_price") not in (None, ""):
                    if t.get("filename"):
                        p = dict(p)
                        p.setdefault("source_filename", t.get("filename") or "")
                    candidates.append(p)

        if candidates and _v214_all_confirmed(candidates, msg, attachment_texts):
            _set_v214_stage(email_id, "PYTHON VERIFY", "VERIFY", "Stage-2 candidates proven by current-source evidence", completed=False)
            _save_products_and_finish(int(erow["mailbox_id"]), int(email_id), msg, candidates,
                                      "DOCUMENT" if first_source else "EMAIL", first_source, attachment_texts, [])
            return True

        # Cross-document deterministic rescue happens only after all independent
        # specialist lanes finish. It can correlate body + multiple attachments,
        # but still cannot override the Python evidence firewall.
        _set_v214_stage(email_id, "PYTHON RESCUE", "RESCUE", "Cross-document deterministic rescue", completed=False)
        rescued = extract_rescue(msg.get("subject", ""), msg.get("body", ""), attachment_texts, candidates, sender=msg.get("sender", ""))
        if rescued and _v214_all_confirmed(rescued, msg, attachment_texts):
            _save_products_and_finish(int(erow["mailbox_id"]), int(email_id), msg, rescued,
                                      "DOCUMENT" if first_source else "EMAIL", first_source, attachment_texts, [])
            return True

        if not commercial_any and not needs_ai and not failed_any:
            _set_v214_stage(email_id, "NO PRODUCT", "DONE", "V21.5 specialist lanes found no explicit current-source commercial price evidence.", completed=True)
            return True

        _route_v214_ai(email_id, "V21.5 specialist lanes + Python cross-document rescue could not prove the current-source product-price relationship")
        return True


def _v215_process_task(row, lane):
    try:
        if lane == "TEXT":
            _v215_process_text_task(row)
        elif lane == "PDF":
            _v215_process_pdf_task(row)
        elif lane in ("TABLE", "DOC"):
            _v215_process_document_task(row, lane)
        elif lane == "OCR":
            _v215_process_ocr_task(row)
        else:
            _v215_process_other_task(row)
    except Exception as exc:
        attempts = int(row.get("attempts") or 0) + 1
        if attempts < 3:
            _v215_task_update(row["id"], state="QUEUED", reason=f"Stage-2 retry {attempts}/3: {exc}")
        else:
            _v215_task_update(row["id"], state="FAILED", reason=f"Stage-2 failed safely after {attempts} attempt(s): {exc}",
                              result=task_result([], "", True, True, str(exc), {"lane": lane}), completed=True)
    finally:
        _v215_finalize_email_if_ready(int(row["email_id"]))


def _v215_process_lane(lane, workers):
    rows = _v215_claim_tasks(lane, max(1, int(workers) * V215_STAGE2_BATCH_MULTIPLIER))
    if not rows:
        return {"lane": lane, "claimed": 0, "processed": 0}
    with ThreadPoolExecutor(max_workers=min(int(workers), len(rows))) as pool:
        futures = [pool.submit(_v215_process_task, r, lane) for r in rows]
        for f in futures:
            try:
                f.result()
            except Exception:
                logger.exception("V21.5 %s task worker failure", lane)
    return {"lane": lane, "claimed": len(rows), "processed": len(rows)}


def _v215_recover_stale_tasks():
    cutoff = f'-{int(V215_TASK_STALE_SECONDS)} seconds'
    with db() as conn:
        cur = conn.execute(
            """UPDATE v215_stage_tasks SET state='QUEUED',probe_reason=COALESCE(probe_reason,'') || ' | recovered stale Stage-2 lease',
                      updated_at=CURRENT_TIMESTAMP
               WHERE state='PROCESSING' AND COALESCE(updated_at,started_at,created_at) < datetime('now', ?)""",
            (cutoff,),
        )
        return int(cur.rowcount or 0)


def v215_stage2_supervisor():
    recovered = _v215_recover_stale_tasks()
    if recovered:
        logger.warning("V21.5 recovered %s stale Stage-2 task(s)", recovered)
    lanes = [
        ("TEXT", V215_TEXT_WORKERS),
        ("PDF", V215_PDF_WORKERS),
        ("TABLE", V215_TABLE_WORKERS),
        ("DOC", V215_DOC_WORKERS),
        ("OCR", V215_OCR_WORKERS),
        ("OTHER", 1),
    ]
    dispatched = {}
    for lane, workers in lanes:
        dispatched[lane] = _dispatch_worker(
            f"v215_{lane.lower()}_lane",
            lambda lane=lane, workers=workers: _v215_process_lane(lane, workers),
            count_deferral=False,
        )
    return {"mode": "v215_stage2_specialists", "dispatched": dispatched, "recovered": recovered}


def _fetch_for_ai(mailbox_id, row):
    mb = mailbox_row(mailbox_id)
    msg = fetch_message(mb, row["gmail_message_id"])
    return row, msg




_PRICE_SIGNAL_RE = re.compile(
    r"""(?ix)
    (?:
        (?:₹|rs\.?|inr|usd|eur|gbp|\$|€|£)\s*[\d,]+(?:\.\d{1,2})?
        |
        \b(?:price|unit\s*price|unit\s*rate|rate|net\s*price|net\s*rate|offer(?:ed)?\s*price|
              purchase\s*price|amount|mrp|list\s*price|basic\s*(?:price|rate))\b
        [^\n\r\d]{0,30}
        (?:₹|rs\.?|inr|usd|eur|gbp|\$|€|£)?\s*[\d,]+(?:\.\d{1,2})?
    )
    """
)

def _has_explicit_body_price_signal(msg):
    """Conservative body-only gate.

    A body-only email cannot yield a product+price record if the body contains
    no explicit commercial price signal. Such rows are safe to finish as
    NO PRODUCT without spending exception-model time.
    """
    text = "\n".join([
        str((msg or {}).get("subject") or ""),
        str((msg or {}).get("body") or ""),
    ])
    return bool(_PRICE_SIGNAL_RE.search(text))


def local_queue_drain_worker():
    """V21.5 Stage-2 supervisor: independent TEXT/PDF/TABLE/DOC/OCR lanes."""
    return v215_stage2_supervisor()

def local_verify_worker():
    return {"mode":"removed_in_v214","reason":"deep verification is part of exception-only AI lane"}

def _process_v214_rescue_mailbox(mailbox_id, limit=V214_RESCUE_BATCH):
    rows=_claim_v214_rows(mailbox_id,"RESCUE QUEUED","RESCUE PROCESSING","RESCUE",limit)
    processed=0
    mb=mailbox_row(mailbox_id)
    for row in rows:
        eid=int(row["id"])
        try:
            with V214_RESCUE_GATE:
                msg=fetch_message(mb,row["gmail_message_id"])
                _set_v214_stage(eid,"RESCUE PROCESSING","RESCUE",None,completed=False)
                evidence=_collect_attachment_evidence(mailbox_id,msg,download_visual=True)
                evidence=_v214_augment_ocr(evidence)
                products=extract_rescue(
                    msg.get("subject",""),msg.get("body",""),
                    evidence.get("attachment_texts") or [],evidence.get("pdf_products") or [],
                    sender=msg.get("sender","")
                )
                if products and _v214_all_confirmed(products,msg,evidence.get("attachment_texts") or []):
                    _save_products_and_finish(
                        mailbox_id,eid,msg,products,
                        "PDF" if evidence.get("pdf_detected") else ("IMAGE" if evidence.get("visual_only") else "EMAIL"),
                        evidence.get("first_source_file") or "",evidence.get("attachment_texts") or [],evidence.get("multimodal_files") or []
                    )
                    processed += 1
                    continue

                combined="\n".join(
                    [msg.get("subject","") or "",msg.get("body","") or ""] +
                    [str(x.get("text") or "") for x in evidence.get("attachment_texts") or []]
                )
                if not msg.get("attachments") and not has_commercial_price_signal(combined):
                    _set_v214_stage(eid,"NO PRODUCT","DONE","Python Rescue found no explicit current-source commercial price evidence.",completed=True)
                    processed += 1
                    continue
                if msg.get("attachments") and evidence.get("readable_attachment") and not has_commercial_price_signal(combined) and not evidence.get("visual_only"):
                    _set_v214_stage(eid,"NO PRODUCT","DONE","Readable attachment contained no explicit current-source commercial price evidence.",completed=True)
                    processed += 1
                    continue
                _route_v214_ai(eid,"Python Rescue could not prove the current-source product-price relationship")
        except Exception as exc:
            attempts=int(row.get("rescue_attempts") or 0)+1
            if attempts < V214_MAX_RESCUE_ATTEMPTS:
                _route_v214_rescue(eid,f"Python Rescue retry {attempts}/{V214_MAX_RESCUE_ATTEMPTS}: {exc}")
            else:
                _route_v214_ai(eid,f"Python Rescue exhausted after {attempts} attempt(s): {exc}")
    return {"processed":processed,"claimed":len(rows)}


def _v214_model_failure(result):
    note=str((result or {}).get("notes") or "").lower()
    return bool(note.startswith("local text extraction failed") or note.startswith("local visual extraction failed") or "model unavailable" in note)


def _v214_process_one_ai_row(mailbox_id, mb, row):
    eid=int(row["id"])
    msg=fetch_message(mb,row["gmail_message_id"])
    _set_v214_stage(eid,"AI PROCESSING","AI",None,completed=False)
    evidence=_collect_attachment_evidence(mailbox_id,msg,download_visual=True)
    evidence=_v214_augment_ocr(evidence)
    learned=_with_learning_context(mailbox_id,msg)
    attachments=evidence.get("attachment_texts") or []
    files=evidence.get("multimodal_files") or []
    visual_case=bool(evidence.get("visual_only") or (evidence.get("pdf_detected") and not evidence.get("readable_attachment")))
    results=[]

    # Tiny text model first when Python/OCR produced readable text.
    if not visual_case:
        fast_model=fast_text_model_name()
        fast=extract_message_local(learned,attachments,model=fast_model) if fast_model else {"products":[],"notes":"Local fast text model unavailable","model":""}
        results.append(fast)
        if fast.get("products") and _v214_all_confirmed(fast.get("products"),msg,attachments):
            _save_products_and_finish(mailbox_id,eid,msg,fast.get("products") or [],
                "PDF" if evidence.get("pdf_detected") else "EMAIL",evidence.get("first_source_file") or "",attachments,files)
            return "completed"

        deep_model=deep_text_model_name()
        if deep_model and deep_model != fast.get("model"):
            deep=extract_message_local(learned,attachments,model=deep_model)
            results.append(deep)
            if deep.get("products") and _v214_all_confirmed(deep.get("products"),msg,attachments):
                _save_products_and_finish(mailbox_id,eid,msg,deep.get("products") or [],
                    "PDF" if evidence.get("pdf_detected") else "EMAIL",evidence.get("first_source_file") or "",attachments,files)
                return "completed"

    # Vision is last resort only. Dedicated OCR was already attempted.
    if files and (visual_case or not any(r.get("products") for r in results)):
        vision_model=vision_model_name()
        vr=extract_visual_local(learned,attachments,files,model=vision_model) if vision_model else {"products":[],"notes":"Local vision model unavailable","model":""}
        results.append(vr)
        if vr.get("products") and _v214_all_confirmed(vr.get("products"),msg,attachments):
            _save_products_and_finish(mailbox_id,eid,msg,vr.get("products") or [],
                "PDF" if evidence.get("pdf_detected") else "IMAGE",evidence.get("first_source_file") or "",attachments,files)
            return "completed"

    # If AI proposed anything, persist through Python firewall. It can only become
    # CONFIRMED if the current source independently proves every required field.
    candidates=[]
    for r in reversed(results):
        if r.get("products"):
            candidates=r.get("products") or []
            break
    if candidates:
        _save_products_and_finish(mailbox_id,eid,msg,candidates,
            "PDF" if evidence.get("pdf_detected") else ("IMAGE" if evidence.get("visual_only") else "EMAIL"),
            evidence.get("first_source_file") or "",attachments,files)
        return "completed"

    failed=any(_v214_model_failure(r) for r in results)
    attempts=int(row.get("ai_attempts") or 0)+1
    if failed and attempts < V214_MAX_AI_ATTEMPTS:
        _route_v214_ai(eid,f"Local AI transient retry {attempts}/{V214_MAX_AI_ATTEMPTS}")
        return "requeued"
    reason="Python Main + Python Rescue/OCR + local exception AI could not prove a current-source product-price pair."
    _set_v214_stage(eid,"NEEDS REVIEW","DONE",reason,completed=True)
    _create_review_item(mailbox_id,eid,None,reason,{"models":[r.get("model") for r in results],"notes":[r.get("notes") for r in results]})
    return "completed"


def _process_v214_ai_mailbox(mailbox_id, limit=V214_AI_BATCH):
    rows=_claim_v214_rows(mailbox_id,"AI QUEUED","AI PROCESSING","AI",min(1,limit))
    processed=0
    mb=mailbox_row(mailbox_id)
    for row in rows:
        eid=int(row["id"])
        try:
            with V214_AI_GATE:
                result=_v214_process_one_ai_row(mailbox_id,mb,row)
            if result == "completed":
                processed += 1
        except Exception as exc:
            attempts=int(row.get("ai_attempts") or 0)+1
            if attempts < V214_MAX_AI_ATTEMPTS:
                _route_v214_ai(eid,f"AI exception retry {attempts}/{V214_MAX_AI_ATTEMPTS}: {exc}")
            else:
                reason=f"AI exception exhausted safely: {str(exc)[:650]}"
                _set_v214_stage(eid,"NEEDS REVIEW","DONE",reason,completed=True)
                _create_review_item(mailbox_id,eid,None,reason)
    return {"processed":processed,"claimed":len(rows)}


def process_pending_mailbox(mailbox_id, limit=AI_BATCH_SIZE):
    """Compatibility entrypoint: one Rescue cycle + one exception-AI cycle."""
    rescue=_process_v214_rescue_mailbox(mailbox_id,min(V214_RESCUE_BATCH,max(1,limit)))
    ai=_process_v214_ai_mailbox(mailbox_id,1)
    return {"processed":int(rescue.get("processed",0))+int(ai.get("processed",0)),"rescue":rescue,"ai":ai}

def _full_coverage_verify_due(mailbox_id, seconds=1800):
    raw = _setting_get(f"coverage_full_verified_at:{mailbox_id}", "")
    if not raw:
        return True
    try:
        clean = raw.rsplit(" ", 1)[0] if len(raw.split()) >= 3 else raw
        then = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - then).total_seconds() >= seconds
    except Exception:
        return True


def verify_inbox_mailbox(mailbox_id, force=False):
    """Verify inbox coverage efficiently.

    Fast path: ask Gmail for the exact INBOX count. If a completed mailbox has
    the same verified count, do not enumerate the entire inbox again.
    Full ID enumeration runs only when the count changed, coverage is incomplete,
    or the user explicitly restarts/forces verification.
    """
    lock = _history_lock(mailbox_id)
    if not lock.acquire(blocking=False):
        return {"ok": True, "busy": True}
    try:
        mb = mailbox_row(mailbox_id)
        if not mb:
            return {"ok": False, "error": "Mailbox not found"}

        try:
            exact_total = get_inbox_total(mb)
        except Exception:
            exact_total = int(mb["inbox_total_estimate"] or 0)

        verified_count = int(mb["history_scanned_count"] or 0)
        complete = int(mb["history_complete"] or 0) == 1
        if not force and complete and exact_total == verified_count and not _full_coverage_verify_due(mailbox_id):
            return {"ok": True, "fast_verified": True, "total": exact_total, "missing": 0, "complete": True}

        ids = list_all_message_ids(mb, query="in:inbox", page_size=500)
        ids = list(dict.fromkeys(ids))
        id_set = set(ids)
        with db() as conn:
            existing_rows = conn.execute(
                "SELECT gmail_message_id FROM emails WHERE mailbox_id=?",
                (mailbox_id,)
            ).fetchall()
        existing = {r["gmail_message_id"] for r in existing_rows}
        missing = [mid for mid in ids if mid not in existing]
        _index_message_ids(mailbox_id, missing, priority=20)
        batch = {"inserted": len(missing)}
        # Reconcile current INBOX membership so dashboard counters form an exact partition of the current Inbox.
        with db() as conn:
            conn.execute("UPDATE emails SET in_inbox=0 WHERE mailbox_id=?", (mailbox_id,))
            conn.executemany(
                "UPDATE emails SET in_inbox=1 WHERE mailbox_id=? AND gmail_message_id=?",
                [(mailbox_id, mid) for mid in ids]
            )

        now = system_local_now_text()
        with db() as conn:
            conn.execute(
                """UPDATE mailboxes
                   SET history_scanned_count=?, inbox_total_estimate=?, history_complete=1,
                       history_page_token=NULL, last_history_at=?, last_sync_at=?
                   WHERE id=?""",
                (len(id_set), len(id_set), now, now, mailbox_id)
            )
        _setting_set(f"coverage_full_verified_at:{mailbox_id}", now)
        return {
            "ok": True, "total": len(id_set), "missing": len(missing),
            "inserted": batch["inserted"], "complete": True
        }
    finally:
        lock.release()

def coverage_verify_worker():
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM mailboxes WHERE is_active=1").fetchall()]
    for mailbox_id in ids:
        try:
            verify_inbox_mailbox(mailbox_id)
        except Exception:
            traceback.print_exc()



def learning_worker():
    """Repeated cheap training from verified outcomes only; no LLM is invoked."""
    try:
        result=train_router_model(limit=5000)
        if result.get("trained"):
            _setting_set("last_learning_train_at",system_local_now_text())
        return result
    except Exception as exc:
        logger.warning("Learning worker warning: %s",exc)
        return {"trained":False,"error":str(exc)[:500]}


def _drain_direct_mailbox(mailbox_id):
    deadline = time.monotonic() + DIRECT_DRAIN_BUDGET_SECONDS
    total = 0
    while time.monotonic() < deadline and not _BACKGROUND_STOP.is_set():
        result = analyze_discovered_mailbox(mailbox_id, min(DIRECT_BATCH_SIZE, DIRECT_SLICE_SIZE))
        n = int((result or {}).get("processed", 0) or 0)
        total += n
        if n <= 0:
            break
    return {"processed": total}


def _drain_ai_mailbox(mailbox_id):
    deadline = time.monotonic() + max(10, DIRECT_DRAIN_BUDGET_SECONDS)
    total = 0
    while time.monotonic() < deadline and not _BACKGROUND_STOP.is_set():
        result = process_pending_mailbox(mailbox_id, limit=AI_BATCH_SIZE)
        n = int((result or {}).get("processed", 0) or 0)
        total += n
        if n <= 0:
            break
    return {"processed": total}

def direct_analysis_worker():
    """Lightweight dispatcher: each mailbox gets an independent direct lane.

    A slow PDF in one Gmail account can no longer hold the global direct worker
    open and block every other mailbox.
    """
    _recover_stale_email_states()
    for mailbox_id in _mailbox_slice("direct_analysis", 2):
        mid = int(mailbox_id)
        _dispatch_worker(
            f"direct_mb_{mid}",
            lambda mid=mid: _drain_direct_mailbox(mid),
            count_deferral=False,
        )


def live_gmail_worker():
    """Lightweight near-live dispatcher with an independent lane per Gmail account."""
    for mailbox_id in _mailbox_slice("live_gmail", 3):
        mid = int(mailbox_id)
        _dispatch_worker(
            f"live_mb_{mid}",
            lambda mid=mid: sync_mailbox(mid, query="in:inbox newer_than:2d", max_results=20),
            count_deferral=False,
        )


def history_backfill_worker():
    """Background recovery dispatcher; historical work never blocks live Gmail."""
    for mailbox_id in _mailbox_slice("history", 1):
        mid = int(mailbox_id)
        def _run(mid=mid):
            result = history_backfill_mailbox(mid)
            if result.get("complete"):
                _setting_set(f"history_complete_seen:{mid}", system_local_now_text())
            return result
        _dispatch_worker(f"history_mb_{mid}", _run, count_deferral=False)


def ai_fallback_worker():
    """V21.4 exception-only local-AI lane; at most one model request at a time."""
    _recover_stale_email_states()
    for mailbox_id in _mailbox_slice("ai", 1):
        mid=int(mailbox_id)
        _dispatch_worker(f"ai_mb_{mid}", lambda mid=mid: _process_v214_ai_mailbox(mid,1), count_deferral=False)
    return {"mode":"exception_only_local_ai"}

def sheet_export_worker():
    """Lightweight Sheet dispatcher; Google latency is isolated per mailbox tab."""
    for mailbox_id in _mailbox_slice("sheets", 2):
        mid = int(mailbox_id)
        if not get_sheet_config(mid)[0]:
            continue
        def _run(mid=mid):
            if _setting_get(f"sheet_rebuild_required:{mid}", "0") == "1":
                result = rebuild_verified_sheet(mid)
                if not result.get("ok"):
                    return result
            return export_pending_products(mid, limit=SHEET_EXPORT_BATCH_SIZE)
        _dispatch_worker(f"sheets_mb_{mid}", _run, count_deferral=False)



def engine_self_heal_worker():
    """V21.5 bounded recovery for Main, specialist tasks and exception AI."""
    _recover_stale_jobs()
    recovered_emails=_recover_stale_email_states()
    recovered_tasks=_v215_recover_stale_tasks()
    # Recover an email that crashed after its last Stage-2 task completed but
    # before cross-document finalization.
    with db() as conn:
        ready_finalize=[int(r["id"]) for r in conn.execute(
            """SELECT e.id FROM emails e
               WHERE e.analysis_completed_at IS NULL AND e.status IN ('STAGE2 QUEUED','PYTHON RESCUE','PYTHON VERIFY')
                 AND EXISTS(SELECT 1 FROM v215_stage_tasks t WHERE t.email_id=e.id)
                 AND NOT EXISTS(SELECT 1 FROM v215_stage_tasks t WHERE t.email_id=e.id AND t.state NOT IN ('DONE','SKIPPED','FAILED'))
               LIMIT 50"""
        ).fetchall()]
    for eid in ready_finalize:
        _v215_finalize_email_if_ready(eid)
    healed=0
    for mailbox_id in _active_mailbox_ids():
        with db() as conn:
            raw=int(conn.execute("SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND status IN ('DISCOVERED','NEW','RAW')",(mailbox_id,)).fetchone()["c"])
            stage2=int(conn.execute("SELECT COUNT(*) c FROM v215_stage_tasks WHERE mailbox_id=? AND state='QUEUED'",(mailbox_id,)).fetchone()["c"])
            ai=int(conn.execute("SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND status='AI QUEUED'",(mailbox_id,)).fetchone()["c"])
        if raw:
            _dispatch_worker(f"direct_mb_{mailbox_id}",lambda mid=mailbox_id: analyze_discovered_mailbox(mid,min(DIRECT_SLICE_SIZE,8)),count_deferral=False)
        if stage2:
            _dispatch_worker(f"v215_stage2_wake_{mailbox_id}",v215_stage2_supervisor,count_deferral=False)
        if ai:
            _dispatch_worker(f"ai_mb_{mailbox_id}",lambda mid=mailbox_id: _process_v214_ai_mailbox(mid,1),count_deferral=False)
        healed += 1 if (raw or stage2 or ai) else 0
    return {"ok":True,"recovered_stale_emails":recovered_emails,"recovered_stage2_tasks":recovered_tasks,"finalized_recovered":len(ready_finalize),"mailboxes_woken":healed}

def gmail_watch_renew_worker():
    """Optional production Pub/Sub watch renewal. Desktop History polling remains the fallback."""
    topic = (os.getenv("GMAIL_PUBSUB_TOPIC") or "").strip()
    if not topic:
        return {"ok": False, "configured": False}
    renewed = 0
    with db() as conn:
        boxes = conn.execute("SELECT * FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()
    for mb in boxes:
        try:
            result = start_mailbox_watch(mb, topic)
            if result.get("historyId"):
                with db() as conn:
                    conn.execute("UPDATE mailboxes SET gmail_history_id=? WHERE id=?", (str(result.get("historyId")), mb["id"]))
            _setting_set(f"gmail_watch_expiration:{mb['id']}", result.get("expiration", ""))
            renewed += 1
        except Exception as exc:
            _setting_set(f"gmail_watch_error:{mb['id']}", str(exc)[:700])
    return {"ok": True, "configured": True, "renewed": renewed}


def prepare_v15_recovery():
    """One-time recovery for rows classified by older builds.

    Old ERROR rows get one clean retry. Old IRRELEVANT rows that are not clear
    security/authentication noise get conservative batched relevance review, so
    a prior heuristic cannot permanently hide a useful supplier email.
    """
    if _setting_get("v15_recovery_done", "") == "1":
        return
    try:
        with db() as conn:
            errors = conn.execute(
                "SELECT id FROM emails WHERE status='ERROR' AND COALESCE(analysis_attempts,0)<2"
            ).fetchall()
            for row in errors:
                conn.execute(
                    "UPDATE emails SET status='RAW',error=NULL,priority=50,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],)
                )

            irrelevant = conn.execute(
                """SELECT id,subject,body,attachment_names FROM emails
                   WHERE status='IRRELEVANT' ORDER BY id DESC"""
            ).fetchall()
            for row in irrelevant:
                if definitely_irrelevant(row["subject"] or "", row["body"] or "", row["attachment_names"] or ""):
                    continue
                prio = candidate_score(row["subject"] or "", row["body"] or "", row["attachment_names"] or "")
                conn.execute(
                    "UPDATE emails SET status='RELEVANCE QUEUED',priority=?,error=NULL,analysis_completed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (prio, row["id"])
                )
        _setting_set("v15_recovery_done", "1")
    except Exception:
        traceback.print_exc()


def prepare_v16_recovery():
    """Recover stale processing states after older versions/crashes."""
    if _setting_get("v16_recovery_done", "") == "1":
        return
    with db() as conn:
        conn.execute(
            """UPDATE emails SET status='RAW',error=NULL,analysis_started_at=NULL
               WHERE status IN ('PROCESSING','PDF READING','AI PROCESSING')
                 AND analysis_completed_at IS NULL"""
        )
    _setting_set("v16_recovery_done", "1")


def prepare_v17_recovery():
    """One-time legacy AI migration recovery.

    Emails that failed only because the old Groq key was invalid are re-queued.
    Image/scanned-document cases previously stranded as NEEDS OCR are also sent
    through the V21.4 Rescue/exception pipeline. Existing extracted products are untouched.
    """
    if _setting_get("v17_recovery_done", "") == "1":
        return
    with db() as conn:
        conn.execute(
            """UPDATE emails
               SET status='RAW', error=NULL, analysis_completed_at=NULL,
                   analysis_started_at=NULL, priority=60, updated_at=CURRENT_TIMESTAMP
               WHERE status='ERROR'
                 AND (LOWER(COALESCE(error,'')) LIKE '%groq%'
                      OR LOWER(COALESCE(error,'')) LIKE '%invalid_api_key%'
                      OR LOWER(COALESCE(error,'')) LIKE '%invalid api key%')"""
        )
        conn.execute(
            """UPDATE emails
               SET status='AI QUEUED', error=NULL, analysis_completed_at=NULL,
                   analysis_started_at=NULL, priority=70, updated_at=CURRENT_TIMESTAMP
               WHERE status='NEEDS OCR'"""
        )
    _clear_ai_error()
    _setting_set("v17_recovery_done", "1")


def queue_pdf_price_reaudit(mailbox_id=None, include_reverifying=True):
    """Queue historical PDF-derived product emails for evidence-first re-audit.

    The old product rows are retained as REVERIFYING until their source email is
    parsed again. Correctly reproduced rows are refreshed in place; stale/wrong
    rows from the same source email become SUPERSEDED and therefore never export.
    """
    try:
        backup_database()
    except Exception:
        pass

    where = " AND p.mailbox_id=?" if mailbox_id else ""
    args = (int(mailbox_id),) if mailbox_id else ()
    statuses = ("CONFIRMED", "REVERIFYING") if include_reverifying else ("CONFIRMED",)
    placeholders = ",".join("?" for _ in statuses)
    with db() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT p.mailbox_id,p.email_id
                FROM products p JOIN emails e ON e.id=p.email_id
                WHERE UPPER(COALESCE(p.source_type,''))='PDF'
                  AND COALESCE(p.verification_status,'CONFIRMED') IN ({placeholders}) {where}""",
            (*statuses, *args)
        ).fetchall()

        if mailbox_id:
            conn.execute(
                """UPDATE products SET verification_status='REVERIFYING',sheet_exported_at=NULL,
                   sheet_export_error=NULL,sheet_export_target=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE mailbox_id=? AND UPPER(COALESCE(source_type,''))='PDF'
                     AND COALESCE(verification_status,'CONFIRMED') IN ('CONFIRMED','REVERIFYING')""",
                (int(mailbox_id),)
            )
        elif rows:
            conn.execute(
                """UPDATE products SET verification_status='REVERIFYING',sheet_exported_at=NULL,
                   sheet_export_error=NULL,sheet_export_target=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE UPPER(COALESCE(source_type,''))='PDF'
                     AND COALESCE(verification_status,'CONFIRMED') IN ('CONFIRMED','REVERIFYING')"""
            )

        affected_mailboxes = set()
        for row in rows:
            mid, email_id = int(row["mailbox_id"]), int(row["email_id"])
            affected_mailboxes.add(mid)
            conn.execute(
                """UPDATE emails SET status='RAW',priority=MAX(COALESCE(priority,0),99),error=NULL,
                   analysis_started_at=NULL,analysis_completed_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (email_id,)
            )
            conn.execute(
                """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority,next_retry_at,last_error)
                   VALUES(?,?,'ANALYZE','READY',99,NULL,NULL)
                   ON CONFLICT(email_id,job_type) DO UPDATE SET state='READY',
                     priority=MAX(processing_jobs.priority,99),next_retry_at=NULL,last_error=NULL,
                     lease_owner=NULL,leased_at=NULL,updated_at=CURRENT_TIMESTAMP""",
                (mid,email_id)
            )
        for mid in affected_mailboxes:
            conn.execute(
                """INSERT INTO app_settings(key,value) VALUES(?, '1')
                   ON CONFLICT(key) DO UPDATE SET value='1'""",
                (f"sheet_rebuild_required:{mid}",)
            )

    for mid in sorted({int(r["mailbox_id"]) for r in rows}):
        _dispatch_worker(
            f"direct_mb_{mid}",
            lambda mid=mid: analyze_discovered_mailbox(mid, min(4, DIRECT_SLICE_SIZE)),
            count_deferral=False,
        )
    _dispatch_worker("sheets", sheet_export_worker)
    return {"queued": len(rows), "mailboxes": sorted({int(r["mailbox_id"]) for r in rows})}


def prepare_v207_pdf_price_reaudit():
    """Legacy compatibility routine retained but not executed by V21.4 startup."""
    if _setting_get("v207_pdf_price_reaudit_done", "") == "1":
        return {"queued": 0}
    result = queue_pdf_price_reaudit()
    _setting_set("v207_pdf_price_reaudit_done", "1")
    _setting_set("v207_pdf_price_reaudit_count", str(result.get("queued", 0)))
    _setting_set("v207_pdf_price_reaudit_started_at", system_local_now_text())
    return result


def _supervisor_loop(name, interval_seconds, dispatcher, initial_delay=0):
    """Tiny control-plane loop. Heavy work always runs in mailbox worker threads.

    V21.4 keeps APScheduler removed so a slow job cannot create repeated
    max_instances warnings or serialize unrelated mailboxes.
    """
    if _BACKGROUND_STOP.wait(max(0, float(initial_delay))):
        return
    while not _BACKGROUND_STOP.is_set():
        started = time.perf_counter()
        try:
            dispatcher()
        except Exception as exc:
            logger.exception("Supervisor %s warning: %s", name, exc)
            try:
                _setting_set(f"supervisor:{name}:last_error", f"{type(exc).__name__}: {exc}"[:800])
            except Exception:
                pass
        elapsed = time.perf_counter() - started
        _setting_set(f"supervisor:{name}:last_tick", system_local_now_text())
        _setting_set(f"supervisor:{name}:dispatch_ms", str(round(elapsed * 1000)))
        _BACKGROUND_STOP.wait(max(0.2, float(interval_seconds)))


def _start_supervisor(name, interval_seconds, dispatcher, initial_delay=0):
    with _SUPERVISOR_LOCK:
        cur = _SUPERVISOR_THREADS.get(name)
        if cur and cur.is_alive():
            return
        t = threading.Thread(
            target=_supervisor_loop,
            args=(name, interval_seconds, dispatcher, initial_delay),
            daemon=True,
            name=f"nunes-supervisor-{name}",
        )
        _SUPERVISOR_THREADS[name] = t
        t.start()


def _start_background_supervisors():
    _BACKGROUND_STOP.clear()
    jobs = [
        ("live_gmail", LIVE_SCAN_SECONDS, live_gmail_worker, 1),
        ("direct_analysis", DIRECT_ANALYSIS_SECONDS, direct_analysis_worker, 2),
        ("stage2", V214_RESCUE_SECONDS, local_queue_drain_worker, 3),
        ("ai", V214_AI_SECONDS, ai_fallback_worker, 4),
        ("sheets", SHEET_EXPORT_SECONDS, sheet_export_worker, 5),
        ("history", HISTORY_SCAN_SECONDS, history_backfill_worker, 6),
        ("self_heal", 30, engine_self_heal_worker, 20),
        ("coverage", COVERAGE_VERIFY_SECONDS, lambda: _dispatch_worker("coverage", coverage_verify_worker), 30),
        ("learning", LEARNING_TRAIN_SECONDS, lambda: _dispatch_worker("learning", learning_worker), 35),
        ("backup", BACKUP_SECONDS, lambda: _dispatch_worker("backup", backup_database), 60),
        ("gmail_watch", 86400, lambda: _dispatch_worker("gmail_watch", gmail_watch_renew_worker), 120),
    ]
    for name, seconds, fn, delay in jobs:
        _start_supervisor(name, seconds, fn, delay)
    logger.info("V21.5 SURREAL HYPERSPEED supervisors started: Stage 0/1 + specialist Stage 2 + exception AI")

def _migrate_v215_queue():
    """One-time V21.4 -> V21.5 backlog migration.

    Every unfinished V21.4 Rescue/AI item re-enters Stage 0/1 once so the new
    format router can split it into specialist queues. After the guard is set,
    restarts preserve genuine V21.5 Stage-2 and AI states.
    """
    key = "v215_stage_architecture_migration_done"
    already_done = _setting_get(key, "") == "1"
    moved = 0
    with db() as conn:
        if not already_done:
            rows = conn.execute(
                """SELECT id,mailbox_id FROM emails
                   WHERE analysis_completed_at IS NULL AND status<>'ERROR'"""
            ).fetchall()
            ids = [int(r["id"]) for r in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                moved = conn.execute(
                    f"""UPDATE emails SET status='RAW',pipeline_stage='MAIN',analysis_started_at=NULL,
                               error='V21.5 Surreal HyperSpeed migration: Stage 0 -> Stage 1 -> specialist Stage 2',
                               stage0_decision=NULL,stage1_summary=NULL,updated_at=CURRENT_TIMESTAMP
                         WHERE id IN ({marks})""", ids
                ).rowcount
                conn.execute(f"DELETE FROM v215_stage_tasks WHERE email_id IN ({marks})", ids)
                for r in rows:
                    conn.execute(
                        """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority,leased_at,lease_owner,next_retry_at,last_error)
                           VALUES(?,?,'ANALYZE','READY',100,NULL,NULL,NULL,NULL)
                           ON CONFLICT(email_id,job_type) DO UPDATE SET state='READY',priority=MAX(processing_jobs.priority,100),
                             leased_at=NULL,lease_owner=NULL,next_retry_at=NULL,last_error=NULL,updated_at=CURRENT_TIMESTAMP""",
                        (int(r["mailbox_id"]), int(r["id"])),
                    )
        # Always normalize only pre-Stage-2 active Main states. Specialist tasks
        # and AI exception states survive ordinary restarts.
        raw = conn.execute(
            """UPDATE emails SET pipeline_stage='MAIN'
               WHERE analysis_completed_at IS NULL AND status IN ('DISCOVERED','NEW','RAW','PROCESSING','PDF READING')"""
        ).rowcount
    if not already_done:
        _setting_set(key, "1")
        _setting_set("v215_stage_architecture_migration_at", system_local_now_text())
    return {"requeued_for_stage0": int(moved or 0), "main_normalized": int(raw or 0), "already_done": bool(already_done)}

def startup_runtime():
    logger.info("Starting %s from %s", BUILD_ID, BASE_DIR)
    init_db()
    ensure_learning_schema()
    migration=_migrate_v215_queue()
    logger.info("V21.5 stage migration: %s", migration)
    _setting_set("ai_provider_error", "")
    _setting_set("ai_provider_error_at", "")
    _setting_set("v214_rag_removed", "1")
    _setting_set("v215_architecture_active", "1")
    try:
        cleanup_exact_product_duplicates()
    except Exception:
        logger.exception("Non-fatal startup duplicate cleanup warning")
    _recover_stale_jobs()
    _recover_stale_email_states()
    _start_background_supervisors()

def shutdown_runtime():
    logger.info("Application shutdown requested")
    _BACKGROUND_STOP.set()
    # Worker threads are daemonized and state is durable in processing_jobs.
    for name, t in list(_SUPERVISOR_THREADS.items()):
        try:
            t.join(timeout=1.0)
        except Exception:
            pass


@app.exception_handler(Exception)
async def unhandled_api_exception(request: Request, exc: Exception):
    request_id = uuid.uuid4().hex[:10]
    logger.exception("Unhandled API error [%s] %s %s", request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "The operation failed safely. The NUNES server is still running.",
            "request_id": request_id,
            "detail": str(exc)[:500],
        },
    )


@app.get("/api/ai/local-health")
def api_ai_local_health():
    return {
        "local": local_ai_health(),
        "mode": "v21_4_python_first_exception_ai",
    }

@app.post("/api/ai/local-self-test")
def api_local_ai_self_test():
    result = local_ai_self_test()
    _setting_set("last_local_ai_self_test", json.dumps(result, ensure_ascii=False)[:12000])
    _setting_set("last_local_ai_self_test_at", system_local_now_text())
    return result


@app.get("/api/learning/health")
def api_learning_health():
    return learning_health()

@app.post("/api/learning/train")
def api_learning_train():
    return train_router_model(limit=5000)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if setup_required():
        return RedirectResponse("/setup")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "oauth_ready": oauth_configured(),
        "local_ai_ready": bool(local_ai_health().get("online")),
        "local_ai_only": True,
    })


@app.get("/api/setup/status")
def api_setup_status():
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    masked_client = ""
    if client_id and not _placeholder(client_id):
        masked_client = client_id[:10] + "…" + client_id[-22:]
    return {
        "oauth_configured": oauth_configured(),
        "local_ai_ready": bool(local_ai_health().get("online")),
        "local_ai_only": True,
        "client_id_hint": masked_client,
        "redirect_uri": "http://127.0.0.1:5067/auth/google/callback",
        "origin": "http://127.0.0.1:5067",
    }


@app.post("/api/setup/save")
async def api_setup_save(request: Request):
    try:
        payload = await request.json()
        _save_local_config(payload)
        return {"ok": True, "redirect": "/"}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Could not save local configuration: {exc}"}, status_code=500)


@app.post("/api/setup/import-env")
async def api_setup_import_env(file: UploadFile = File(...)):
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig", errors="replace")
        temp_path = BASE_DIR / ".env.import.tmp"
        temp_path.write_text(text, encoding="utf-8")
        try:
            values = dict(dotenv_values(temp_path))
        finally:
            try:
                temp_path.unlink()
            except Exception:
                pass
        _save_local_config(values)
        return {"ok": True, "redirect": "/"}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"Could not import .env: {exc}"}, status_code=500)

@app.get("/api/server-time")
def api_server_time():
    now = datetime.now().astimezone()
    return {
        "text": system_local_now_text(),
        "timezone": system_timezone_text(),
        "epoch_ms": int(now.timestamp() * 1000),
    }


@app.get("/api/build")
def api_build():
    return {
        "build_id": BUILD_ID,
        "local_time": system_local_now_text(),
        "timezone": system_timezone_text(),
    }


@app.get("/api/config")
def api_config():
    any_sheet = bool(os.getenv("GOOGLE_SHEET_ID"))
    try:
        with db() as conn:
            any_sheet = any_sheet or bool(conn.execute(
                "SELECT 1 FROM app_settings WHERE (key='shared_sheet_id' OR key LIKE 'sheet_id:%') AND value<>'' LIMIT 1"
            ).fetchone())
    except Exception:
        pass
    health = api_health()
    engine_warning = _setting_get("last_worker_error", "")
    if not engine_warning and not (health.get("engine") or {}).get("ok", True):
        engine_warning = (health.get("engine") or {}).get("detail", "")
    return {
        "oauth_configured": oauth_configured(),
        "local_ai_ready": bool(local_ai_health().get("online")),
        "local_ai_only": True,
        "cloud_ai_removed": True,
        "ai_provider": "Python + Local Ollama",
        "ai_model": fast_text_model_name() or "qwen3:1.7b",
        "ai_deep_model": deep_text_model_name() or "qwen3:4b",
        "ai_vision_model": vision_model_name() or "qwen3-vl:4b",
        "v214_main_workers": V214_MAIN_WORKERS,
        "v214_rescue_workers": V214_RESCUE_WORKERS,
        "ai_provider_error": "",
        "ai_provider_error_at": "",
        "sheet_configured": any_sheet,
        "build_id": BUILD_ID,
        "server_local_time": system_local_now_text(),
        "server_timezone": system_timezone_text(),
        "direct_workers": DIRECT_WORKERS,
        "direct_batch_size": DIRECT_BATCH_SIZE,
        "uptime_seconds": int((datetime.now().astimezone() - APP_STARTED_AT).total_seconds()),
        "last_worker_error": engine_warning,
        "worker_busy_deferrals": (health.get("dispatcher") or {}).get("busy_deferrals", 0),
        "last_manual_recovery": _setting_get("last_manual_recovery", ""),
        "last_auto_recovery": _setting_get("last_auto_recovery", ""),
        "pdf_reaudit_count": _setting_get("v207_pdf_price_reaudit_count", "0"),
        "pdf_reaudit_started_at": _setting_get("v207_pdf_price_reaudit_started_at", ""),
        "sheet_schema_version": SHEET_SCHEMA_VERSION,
        "sheet_columns": list(SHEET_HEADERS),
        "retrieval_memory": "SQLite relational only",
        "local_ai": local_ai_health(),
        "last_local_ai_self_test_at": _setting_get("last_local_ai_self_test_at", ""),
    }

@app.get("/api/mailboxes/{mailbox_id}/sheets/status")
def api_sheet_status(mailbox_id: int):
    sheet_id, tab = get_sheet_config(mailbox_id)
    target = _sheet_target(sheet_id, tab)
    with db() as conn:
        total = int(conn.execute(
            "SELECT COUNT(*) c FROM products WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1",
            (mailbox_id,)
        ).fetchone()["c"])
        extracted_emails = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND status LIKE 'EXTRACTED %'",
            (mailbox_id,)
        ).fetchone()["c"])
        exported = int(conn.execute(
            """SELECT COUNT(*) c FROM products
               WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
                 AND sheet_exported_at IS NOT NULL AND sheet_export_target=?""",
            (mailbox_id, target)
        ).fetchone()["c"]) if sheet_id else 0
        exported_emails = int(conn.execute(
            """SELECT COUNT(DISTINCT email_id) c FROM products
               WHERE mailbox_id=? AND email_id IS NOT NULL
                 AND COALESCE(verification_status,'CONFIRMED')='CONFIRMED'
                 AND sheet_exported_at IS NOT NULL AND sheet_export_target=?""",
            (mailbox_id, target)
        ).fetchone()["c"]) if sheet_id else 0
        errors = int(conn.execute(
            """SELECT COUNT(*) c FROM products
               WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
                 AND sheet_export_error IS NOT NULL AND COALESCE(sheet_export_target,'')=?""",
            (mailbox_id, target)
        ).fetchone()["c"]) if sheet_id else 0
        reverify = int(conn.execute(
            """SELECT COUNT(*) c FROM products
               WHERE mailbox_id=? AND COALESCE(verification_status,'CONFIRMED')='REVERIFYING'""",
            (mailbox_id,)
        ).fetchone()["c"])
    return {
        "configured": bool(sheet_id),
        "sheet_id": sheet_id,
        "tab": tab,
        "url": sheet_url(sheet_id),
        "total_products": total,
        "extracted_emails": extracted_emails,
        "exported": exported,
        "exported_emails": exported_emails,
        "pending": max(0, total - exported),
        "errors": errors,
        "reverifying": reverify,
        "rebuild_required": _setting_get(f"sheet_rebuild_required:{mailbox_id}", "0") == "1",
        "schema_version": SHEET_SCHEMA_VERSION,
        "columns": list(SHEET_HEADERS),
        "last_sheet_sync_at": _setting_get(f"last_sheet_sync_at:{mailbox_id}", ""),
    }


@app.post("/api/mailboxes/{mailbox_id}/sheets/create")
async def api_sheet_create(mailbox_id: int, request: Request):
    mb = mailbox_row(mailbox_id)
    if not mb:
        return JSONResponse({"ok": False, "error": "Mailbox not found"}, status_code=404)
    try:
        try: payload = await request.json()
        except Exception: payload = {}
        selected_tab = _ensure_unique_tab(mailbox_id, (payload.get("tab") or _default_tab_name(mailbox_id)).strip())
        set_mailbox_tab(mailbox_id, selected_tab)
        with db() as conn:
            boxes = conn.execute("SELECT id,email FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()
        specs=[]
        for box in boxes:
            tab = selected_tab if int(box["id"]) == int(mailbox_id) else (_setting_get(f"sheet_tab:{box['id']}", "") or _default_tab_name(box["id"])).strip()
            tab = set_mailbox_tab(box["id"], tab)
            specs.append((int(box["id"]), box["email"], tab))
        # ONE Google create call creates the spreadsheet, all Gmail tabs, headers and frozen rows.
        created = create_product_spreadsheet_fast(mb, [x[2] for x in specs])
        set_shared_sheet(created["sheet_id"], mailbox_id)
        for mid,email,tab in specs:
            _setting_set(f"sheet_tab_id:{mid}", created["tabs"].get(tab, ""))
        threading.Thread(target=sheet_export_worker, daemon=True).start()
        return {"ok": True, "sheet_id": created["sheet_id"], "url": created["url"], "tab": selected_tab, "mappings": [{"mailbox_id":m,"email":e,"tab":t} for m,e,t in specs]}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/mailboxes/{mailbox_id}/sheets/configure")
async def api_sheet_configure(mailbox_id: int, request: Request):
    mb = mailbox_row(mailbox_id)
    if not mb:
        return JSONResponse({"ok": False, "error": "Mailbox not found"}, status_code=404)
    try:
        payload = await request.json()
        sheet_id = normalize_sheet_id(payload.get("sheet") or payload.get("sheet_id") or "")
        if not sheet_id:
            raise ValueError("Paste a Google Sheet URL or Sheet ID")
        selected_tab = _ensure_unique_tab(mailbox_id, (payload.get("tab") or _default_tab_name(mailbox_id)).strip())
        set_mailbox_tab(mailbox_id, selected_tab)
        with db() as conn:
            boxes = conn.execute("SELECT id,email FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()
        specs=[]
        for box in boxes:
            tab = selected_tab if int(box["id"]) == int(mailbox_id) else (_setting_get(f"sheet_tab:{box['id']}", "") or _default_tab_name(box["id"])).strip()
            tab = set_mailbox_tab(box["id"], tab)
            specs.append((int(box["id"]), box["email"], tab))
        ids = ensure_tabs_bulk(mb, sheet_id, [x[2] for x in specs])
        set_shared_sheet(sheet_id, mailbox_id)
        for mid,email,tab in specs:
            _setting_set(f"sheet_tab_id:{mid}", ids.get(tab, ""))
        threading.Thread(target=sheet_export_worker, daemon=True).start()
        return {"ok": True, "sheet_id": sheet_id, "tab": selected_tab, "url": sheet_url(sheet_id), "mappings": [{"mailbox_id":m,"email":e,"tab":t} for m,e,t in specs]}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/mailboxes/{mailbox_id}/sheets/export-all")
def api_sheet_export_all(mailbox_id: int):
    try:
        if not get_shared_sheet_id():
            return JSONResponse({"ok": False, "error": "Google Sheet is not configured"}, status_code=400)
        with db() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()]
        exported = 0
        failed = 0
        for mid in ids:
            result = export_pending_products(mid, limit=5000)
            exported += int(result.get("exported") or 0)
            failed += int(result.get("failed") or 0)
        return {"ok": failed == 0, "configured": True, "exported": exported, "failed": failed, "mailboxes": len(ids)}
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)



@app.post("/api/mailboxes/{mailbox_id}/sheets/rebuild")
def api_sheet_rebuild(mailbox_id: int):
    """Rebuild only this Gmail account's assigned tab from current CONFIRMED DB rows."""
    try:
        result = rebuild_verified_sheet(mailbox_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result
    except Exception as exc:
        logger.exception("Sheet rebuild failed for mailbox %s", mailbox_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/mailboxes/{mailbox_id}/accuracy/recheck-pdfs")
def api_accuracy_recheck_pdfs(mailbox_id: int):
    """Re-audit historical PDF prices for one mailbox without restarting Inbox recovery."""
    if not mailbox_row(mailbox_id):
        return JSONResponse({"ok": False, "error": "Mailbox not found"}, status_code=404)
    try:
        result = queue_pdf_price_reaudit(mailbox_id=mailbox_id, include_reverifying=True)
        return {
            "ok": True,
            "queued": int(result.get("queued") or 0),
            "message": "PDF source emails queued for evidence-first price recheck."
        }
    except Exception as exc:
        logger.exception("PDF price recheck failed for mailbox %s", mailbox_id)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/sheets/accounts")
def api_sheet_accounts():
    sheet_id = get_shared_sheet_id()
    owner_id = get_sheet_owner_mailbox_id()
    with db() as conn:
        boxes = conn.execute("SELECT id,email,is_active FROM mailboxes WHERE is_active=1 ORDER BY id").fetchall()
    rows = []
    for box in boxes:
        _, tab = get_sheet_config(box["id"])
        rows.append({"mailbox_id": box["id"], "email": box["email"], "tab": tab, "owner": box["id"] == owner_id})
    return {"configured": bool(sheet_id), "sheet_id": sheet_id, "url": sheet_url(sheet_id), "accounts": rows}


@app.post("/api/mailboxes/{mailbox_id}/sheets/tab")
async def api_sheet_tab(mailbox_id: int, request: Request):
    mb = mailbox_row(mailbox_id)
    if not mb:
        return JSONResponse({"ok": False, "error": "Mailbox not found"}, status_code=404)
    try:
        payload = await request.json()
        new_tab = _ensure_unique_tab(mailbox_id, payload.get("tab") or "")
        sheet_id, old_tab = get_sheet_config(mailbox_id)
        if not sheet_id:
            set_mailbox_tab(mailbox_id, new_tab)
            return {"ok": True, "tab": new_tab, "configured": False}

        writer = get_sheet_writer_row(mailbox_id)
        stored_tab_id = _setting_get(f"sheet_tab_id:{mailbox_id}", "")
        result = rename_tab(
            writer, sheet_id, old_tab, new_tab,
            tab_id=int(stored_tab_id) if str(stored_tab_id).isdigit() else None
        )
        old_target = _sheet_target(sheet_id, old_tab)
        new_target = _sheet_target(sheet_id, new_tab)
        set_mailbox_tab(mailbox_id, new_tab)
        _setting_set(f"sheet_tab_id:{mailbox_id}", result.get("tab_id", ""))
        with db() as conn:
            conn.execute(
                """UPDATE products SET sheet_export_target=?
                   WHERE mailbox_id=? AND sheet_export_target=?""",
                (new_target, mailbox_id, old_target)
            )
        return {"ok": True, "tab": new_tab, "configured": True, "url": sheet_url(sheet_id)}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/auth/google/start")
def auth_start(request: Request):
    """
    Add/reconnect a Gmail mailbox.

    `select_account` is deliberately included every time so Google cannot
    silently reuse the Gmail account that was connected previously. This is
    essential for reliable multi-mailbox connection on one browser/PC.
    """
    if not oauth_configured():
        return RedirectResponse("/setup")

    flow = make_flow()

    # Optional hint can be supplied by a future UI, but the user always gets
    # Google's account chooser because prompt includes select_account.
    login_hint = (request.query_params.get("email") or "").strip().lower()
    auth_kwargs = {
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent",
    }
    if login_hint:
        auth_kwargs["login_hint"] = login_hint

    url, state = flow.authorization_url(**auth_kwargs)

    response = RedirectResponse(url)
    response.set_cookie(
        "nunes_oauth_state", state, httponly=True, samesite="lax", max_age=600
    )
    if login_hint:
        response.set_cookie(
            "nunes_oauth_intended_email", login_hint, httponly=True, samesite="lax", max_age=600
        )
    else:
        response.delete_cookie("nunes_oauth_intended_email")
    return response


@app.get("/auth/google/callback")
def auth_callback(request: Request):
    try:
        returned_state = request.query_params.get("state", "")
        saved_state = request.cookies.get("nunes_oauth_state", "")

        if not returned_state:
            raise RuntimeError("Google callback did not contain an OAuth state value.")

        if saved_state and returned_state != saved_state:
            raise RuntimeError("OAuth state mismatch. Please click Connect Gmail again.")

        flow = make_flow(state=returned_state)

        # Use the exact redirect URI configured in Google Cloud.
        flow.fetch_token(
            authorization_response=str(request.url)
        )

        creds = flow.credentials

        # Use Gmail API itself to identify the authenticated mailbox.
        # This avoids depending on a separate OAuth2 UserInfo discovery service.
        gmail = build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )

        profile = gmail.users().getProfile(userId="me").execute()
        email = (profile.get("emailAddress") or "").strip()

        if not email:
            raise RuntimeError(
                "Google authorized successfully, but Gmail profile did not return an email address."
            )

        intended_email = (request.cookies.get("nunes_oauth_intended_email") or "").strip().lower()
        if intended_email and email.lower() != intended_email:
            raise RuntimeError(
                f"You asked to connect {intended_email}, but Google returned {email}. "
                "Please click Add Gmail again and choose the requested account."
            )

        name = email

        scope_values = creds.scopes or SCOPES
        if isinstance(scope_values, str):
            scope_text = scope_values
        else:
            scope_text = " ".join(scope_values)

        with db() as conn:
            conn.execute("""
                INSERT INTO mailboxes(
                    email,display_name,access_token,refresh_token,token_uri,
                    client_id,client_secret,scopes,is_active,
                    inbox_query,history_page_token,history_complete,history_scanned_count,inbox_total_estimate,gmail_history_id,last_live_poll_at
                ) VALUES(?,?,?,?,?,?,?,?,1,'in:inbox',NULL,0,0,0,?,NULL)
                ON CONFLICT(email) DO UPDATE SET
                  display_name=excluded.display_name,
                  access_token=excluded.access_token,
                  refresh_token=COALESCE(excluded.refresh_token, mailboxes.refresh_token),
                  token_uri=excluded.token_uri,
                  client_id=excluded.client_id,
                  client_secret=excluded.client_secret,
                  scopes=excluded.scopes,
                  is_active=1,
                  inbox_query=COALESCE(mailboxes.inbox_query,'in:inbox'),
                  gmail_history_id=COALESCE(excluded.gmail_history_id,mailboxes.gmail_history_id)
            """, (
                email,
                name,
                creds.token,
                creds.refresh_token,
                creds.token_uri,
                creds.client_id,
                creds.client_secret,
                scope_text,
                str(profile.get("historyId") or ""),
            ))

        with db() as conn:
            mailbox_record = conn.execute(
                "SELECT id FROM mailboxes WHERE email=?",
                (email,)
            ).fetchone()

        if mailbox_record and get_shared_sheet_id():
            try:
                set_mailbox_tab(mailbox_record["id"], _setting_get(f"sheet_tab:{mailbox_record['id']}", "") or _default_tab_name(mailbox_record["id"]))
                threading.Thread(target=ensure_all_mailbox_tabs, daemon=True).start()
            except Exception:
                traceback.print_exc()

        if mailbox_record:
            threading.Thread(
                target=sync_mailbox,
                args=(mailbox_record["id"], "in:inbox newer_than:2d", 30),
                daemon=True
            ).start()
            threading.Thread(
                target=history_backfill_mailbox,
                args=(mailbox_record["id"],),
                daemon=True
            ).start()

        # Redirect with the exact mailbox id so the browser selects the
        # Gmail account that was just added instead of defaulting to another
        # previously connected mailbox.
        from urllib.parse import quote
        mailbox_id = int(mailbox_record["id"]) if mailbox_record else 0
        response = RedirectResponse(
            f"/?gmail_connected=1&mailbox_id={mailbox_id}&connected_email={quote(email)}"
        )
        response.delete_cookie("nunes_oauth_state")
        response.delete_cookie("nunes_oauth_intended_email")
        return response

    except Exception as exc:
        traceback.print_exc()

        # Deliberately do not print tokens/secrets in the browser.
        safe_message = str(exc)
        safe_message = safe_message.replace(
            os.getenv("GOOGLE_CLIENT_SECRET", "") or "__NO_SECRET__",
            "[HIDDEN]"
        )

        html = f"""
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <title>NUNES Gmail Connection Error</title>
          <style>
            body{{font-family:Segoe UI,Arial;background:#f4f6f9;padding:40px;color:#111827}}
            .box{{max-width:850px;margin:auto;background:white;border:1px solid #e5e7eb;
                  border-radius:16px;padding:26px;box-shadow:0 3px 15px rgba(0,0,0,.06)}}
            h1{{margin-top:0}} code{{display:block;background:#f8fafc;border:1px solid #e5e7eb;
                  padding:14px;border-radius:10px;white-space:pre-wrap;color:#b91c1c}}
            a{{display:inline-block;margin-top:16px;background:#111827;color:white;
               padding:10px 14px;border-radius:9px;text-decoration:none}}
          </style>
        </head>
        <body>
          <div class="box">
            <h1>Gmail connection could not finish</h1>
            <p>Google returned to the NUNES app, but the local callback encountered this error:</p>
            <code>{safe_message}</code>
            <p>The full traceback has also been printed in the NUNES CMD window.</p>
            <a href="/">Back to dashboard</a>
          </div>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=500)

@app.get("/api/mailboxes")
def api_mailboxes():
    with db() as conn:
        rows = conn.execute(
            """SELECT id,email,display_name,last_sync_at,is_active,
                      history_complete,history_scanned_count,
                      inbox_total_estimate,last_history_at
               FROM mailboxes
               WHERE is_active=1
               ORDER BY id ASC"""
        ).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/mailboxes/{mailbox_id}/sync")
def api_sync(mailbox_id: int):
    # Optional force-check. Normal operation is fully automatic.
    result = sync_mailbox(
        mailbox_id,
        query="in:inbox newer_than:2d",
        max_results=60
    )
    cleanup_exact_product_duplicates(mailbox_id)
    return result

@app.post("/api/mailboxes/{mailbox_id}/retry-errors")
def api_retry_errors(mailbox_id: int):
    changed = retry_errors(mailbox_id)
    return {"ok": True, "requeued": changed}

@app.post("/api/mailboxes/{mailbox_id}/history/restart")
def api_restart_history(mailbox_id: int):
    with db() as conn:
        conn.execute(
            """UPDATE mailboxes
               SET history_page_token=NULL,
                   history_complete=0,
                   history_scanned_count=0,
                   inbox_total_estimate=0,
                   last_history_at=NULL
               WHERE id=?""",
            (mailbox_id,)
        )
    threading.Thread(target=history_backfill_mailbox, args=(mailbox_id,), daemon=True).start()
    return {"ok": True, "message": "Inbox backfill restarted."}


@app.post("/api/mailboxes/{mailbox_id}/clean-duplicates")
def api_clean_duplicates(mailbox_id: int):
    deleted = cleanup_exact_product_duplicates(mailbox_id)
    return {"ok": True, "deleted": deleted}

@app.delete("/api/mailboxes/{mailbox_id}")
def api_disconnect(mailbox_id: int):
    with db() as conn:
        conn.execute("UPDATE mailboxes SET is_active=0 WHERE id=?", (mailbox_id,))
        active_count = conn.execute(
            "SELECT COUNT(*) c FROM mailboxes WHERE is_active=1"
        ).fetchone()["c"]
    return {"ok": True, "active_count": active_count}

@app.get("/api/mailboxes/{mailbox_id}/stats")
def api_stats(mailbox_id: int):
    """Operational counters designed for a simple, trustworthy dashboard.

    Email pipeline is an exact partition:
      Indexed = Completed + Pending + Failed

    Product totals remain separate because one email can create multiple products.
    The endpoint also exposes recent throughput and ETA so the UI can prove that
    background processing is moving rather than merely showing queue sizes.
    """
    now_mono = time.monotonic()
    with _STATS_CACHE_LOCK:
        cached = _STATS_CACHE.get(int(mailbox_id))
        if cached and (now_mono - cached[0]) < _STATS_CACHE_TTL:
            return cached[1]

    terminal_placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    sheet_id, sheet_tab = get_sheet_config(mailbox_id)
    sheet_target = _sheet_target(sheet_id, sheet_tab) if sheet_id else ""

    with db() as conn:
        indexed = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1",
            (mailbox_id,)
        ).fetchone()["c"])
        failures = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status='ERROR'",
            (mailbox_id,)
        ).fetchone()["c"])
        completed = int(conn.execute(
            f"""SELECT COUNT(*) c FROM emails
                 WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status<>'ERROR'
                   AND (status LIKE 'EXTRACTED %' OR status IN ({terminal_placeholders}))""",
            (mailbox_id, *TERMINAL_STATUSES)
        ).fetchone()["c"])
        pending = max(0, indexed - completed - failures)

        confirmed_products = int(conn.execute(
            "SELECT COUNT(*) c FROM products WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1",
            (mailbox_id,)
        ).fetchone()["c"])
        all_products = int(conn.execute(
            "SELECT COUNT(*) c FROM products WHERE mailbox_id=?", (mailbox_id,)
        ).fetchone()["c"])
        product_emails = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status LIKE 'EXTRACTED %'",
            (mailbox_id,)
        ).fetchone()["c"])
        review_open = int(conn.execute(
            "SELECT COUNT(*) c FROM review_items WHERE mailbox_id=? AND status='OPEN'",
            (mailbox_id,)
        ).fetchone()["c"])
        sheet_pending = int(conn.execute(
            """SELECT COUNT(*) c FROM products WHERE mailbox_id=?
               AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
               AND (sheet_exported_at IS NULL OR COALESCE(sheet_export_target,'')<>?)""",
            (mailbox_id, sheet_target)
        ).fetchone()["c"]) if sheet_id else 0
        sheet_exported_products = int(conn.execute(
            """SELECT COUNT(*) c FROM products WHERE mailbox_id=?
               AND COALESCE(verification_status,'CONFIRMED')='CONFIRMED'
               AND sheet_exported_at IS NOT NULL AND sheet_export_target=?""",
            (mailbox_id, sheet_target)
        ).fetchone()["c"]) if sheet_id else 0
        sheet_exported_emails = int(conn.execute(
            """SELECT COUNT(DISTINCT email_id) c FROM products WHERE mailbox_id=? AND email_id IS NOT NULL
               AND COALESCE(verification_status,'CONFIRMED')='CONFIRMED'
               AND sheet_exported_at IS NOT NULL AND sheet_export_target=?""",
            (mailbox_id, sheet_target)
        ).fetchone()["c"]) if sheet_id else 0

        discovered = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status IN ('DISCOVERED','NEW','RAW')",
            (mailbox_id,)
        ).fetchone()["c"])
        processing = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status IN ('PROCESSING','PDF READING','STAGE0 FILTER','STAGE1 CLASSIFY','PYTHON RESCUE','PYTHON VERIFY','AI PROCESSING')",
            (mailbox_id,)
        ).fetchone()["c"])
        aiq = int(conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 AND status IN ('AI QUEUED','AI PROCESSING')",
            (mailbox_id,)
        ).fetchone()["c"])
        rescueq = int(conn.execute(
            "SELECT COUNT(DISTINCT email_id) c FROM v215_stage_tasks WHERE mailbox_id=? AND state IN ('QUEUED','PROCESSING')",
            (mailbox_id,)
        ).fetchone()["c"])

        recent_5 = int(conn.execute(
            """SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1
               AND analysis_completed_at IS NOT NULL AND analysis_completed_at >= datetime('now','-5 minutes')""",
            (mailbox_id,)
        ).fetchone()["c"])
        recent_15 = int(conn.execute(
            """SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1
               AND analysis_completed_at IS NOT NULL AND analysis_completed_at >= datetime('now','-15 minutes')""",
            (mailbox_id,)
        ).fetchone()["c"])
        recent_60 = int(conn.execute(
            """SELECT COUNT(*) c FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1
               AND analysis_completed_at IS NOT NULL AND analysis_completed_at >= datetime('now','-60 minutes')""",
            (mailbox_id,)
        ).fetchone()["c"])
        last_completed = conn.execute(
            "SELECT MAX(analysis_completed_at) v FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1",
            (mailbox_id,)
        ).fetchone()["v"] or ""
        last_product = conn.execute(
            """SELECT id,product_name,created_at FROM products
               WHERE mailbox_id=? AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1
               ORDER BY id DESC LIMIT 1""",
            (mailbox_id,)
        ).fetchone()
        mb = conn.execute(
            """SELECT COALESCE(inbox_total_estimate,0) inbox_total_estimate,
                      COALESCE(history_scanned_count,0) history_scanned_count,
                      COALESCE(history_complete,0) history_complete,
                      COALESCE(last_history_at,'') last_history_at,
                      COALESCE(last_sync_at,'') last_sync_at,
                      COALESCE(last_live_poll_at,'') last_live_poll_at
               FROM mailboxes WHERE id=?""",
            (mailbox_id,)
        ).fetchone()

    inbox_total = int(mb["inbox_total_estimate"] or 0) if mb else 0
    coverage_count = min(indexed, inbox_total) if inbox_total else indexed
    coverage_pct = min(100, round(coverage_count / inbox_total * 100)) if inbox_total else (100 if indexed else 0)
    analysis_pct = min(100, round(completed / indexed * 100)) if indexed else 0

    # REAL throughput metrics for operations monitoring. Use the shortest window
    # with a useful completion sample and fall back to 60 minutes for sparse
    # exception-AI/scanned-document work.
    if recent_5 >= 3:
        rate_window_minutes = 5
        rate_raw = recent_5 / 5.0
        rate_confidence = "HIGH" if recent_5 >= 10 else "MEDIUM"
    elif recent_15 >= 3:
        rate_window_minutes = 15
        rate_raw = recent_15 / 15.0
        rate_confidence = "MEDIUM" if recent_15 >= 10 else "LOW"
    elif recent_60 > 0:
        rate_window_minutes = 60
        rate_raw = recent_60 / 60.0
        rate_confidence = "LOW"
    else:
        rate_window_minutes = 0
        rate_raw = 0.0
        rate_confidence = "NONE"

    rate_per_min = round(rate_raw, 2)
    rate_per_hour = round(rate_raw * 60.0, 1)
    rate_sample_ready = bool(rate_raw > 0)
    eta_minutes = int((pending / rate_raw) + 0.999) if pending > 0 and rate_raw > 0 else (0 if pending == 0 else None)
    uptime_seconds = int((datetime.now().astimezone() - APP_STARTED_AT).total_seconds())
    # A long OCR/AI request is not a stall while a worker is actively processing.
    # "Stalled" means pending work, no recent completion, and zero active leases.
    stalled = bool(pending > 0 and uptime_seconds >= 900 and recent_15 == 0 and processing == 0)
    degraded = bool(not stalled and pending >= 50 and recent_15 > 0 and rate_per_min > 0 and rate_per_min < 1.0)

    result = {
        "inbox_total": inbox_total,
        "indexed_emails": indexed,
        "completed_emails": completed,
        "analyzed_emails": completed,
        "pending_emails": pending,
        "waiting_emails": pending,
        "failures": failures,
        "pipeline_balanced": indexed == completed + pending + failures,
        "analysis_pct": analysis_pct,
        "coverage_count": coverage_count,
        "coverage_pct": coverage_pct,
        "confirmed_products": confirmed_products,
        "all_product_rows": all_products,
        "product_emails": product_emails,
        "review_open": review_open,
        "sheet_pending": sheet_pending,
        "sheet_exported_products": sheet_exported_products,
        "sheet_exported_emails": sheet_exported_emails,
        "discovered_pending": discovered,
        "processing_now": processing,
        "ai_pending": aiq,
        "rescue_pending": rescueq,
        "stage2_pending": rescueq,
        "processed_last_5m": recent_5,
        "processed_last_15m": recent_15,
        "processed_last_60m": recent_60,
        "processing_rate_per_min": rate_per_min,
        "processing_rate_per_hour": rate_per_hour,
        "rate_window_minutes": rate_window_minutes,
        "rate_confidence": rate_confidence,
        "eta_minutes": eta_minutes,
        "rate_sample_ready": rate_sample_ready,
        "stalled": stalled,
        "degraded": degraded,
        "last_completed_at": last_completed,
        "last_product_at": (last_product["created_at"] if last_product else ""),
        "last_product_name": (last_product["product_name"] if last_product else ""),
        "last_product_id": (int(last_product["id"]) if last_product else None),
        "history_complete": bool(mb["history_complete"]) if mb else False,
        "last_history_at": mb["last_history_at"] if mb else "",
        "last_sync_at": mb["last_sync_at"] if mb else "",
        "last_live_poll_at": mb["last_live_poll_at"] if mb else "",
    }
    with _STATS_CACHE_LOCK:
        _STATS_CACHE[int(mailbox_id)] = (time.monotonic(), result)
    return result


@app.get("/api/mailboxes/{mailbox_id}/activity")
def api_mailbox_activity(mailbox_id: int, limit: int = 12):
    """Small operational activity feed for the Overview page."""
    limit = min(max(int(limit), 1), 30)
    with db() as conn:
        rows = conn.execute(
            """SELECT e.id,e.subject,e.sender,e.status,e.error,
                      COALESCE(e.analysis_completed_at,e.updated_at,e.created_at) event_at,
                      (SELECT COUNT(*) FROM products p WHERE p.email_id=e.id
                         AND COALESCE(p.verification_status,'CONFIRMED')='CONFIRMED') product_count
               FROM emails e
               WHERE e.mailbox_id=? AND COALESCE(e.in_inbox,1)=1
                 AND (e.analysis_completed_at IS NOT NULL OR e.status='ERROR' OR e.status LIKE 'EXTRACTED %')
               ORDER BY COALESCE(e.analysis_completed_at,e.updated_at,e.created_at) DESC,e.id DESC
               LIMIT ?""",
            (mailbox_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def _stream_fingerprint(mailbox_id: int):
    """Small synchronous DB read used by SSE through asyncio.to_thread.

    Keeping SQLite work off the asyncio event loop is critical: multiple open
    browser tabs must never make the FastAPI UI stop responding.
    """
    with db() as conn:
        row = conn.execute(
            """SELECT
                  (SELECT COUNT(*) FROM emails WHERE mailbox_id=?) AS email_count,
                  (SELECT COALESCE(MAX(updated_at),'') FROM emails WHERE mailbox_id=?) AS email_updated,
                  (SELECT COUNT(*) FROM products WHERE mailbox_id=?) AS product_count,
                  (SELECT COALESCE(MAX(id),0) FROM products WHERE mailbox_id=?) AS product_max,
                  (SELECT COALESCE(last_sync_at,'') FROM mailboxes WHERE id=?) AS last_sync,
                  (SELECT COALESCE(last_history_at,'') FROM mailboxes WHERE id=?) AS last_history,
                  (SELECT COALESCE(history_scanned_count,0) FROM mailboxes WHERE id=?) AS history_count
            """,
            (mailbox_id, mailbox_id, mailbox_id, mailbox_id, mailbox_id, mailbox_id, mailbox_id)
        ).fetchone()
    if not row:
        return (0, "", 0, 0, "", "", 0)
    return (
        row["email_count"], row["email_updated"], row["product_count"], row["product_max"],
        row["last_sync"], row["last_history"], row["history_count"]
    )


@app.get("/api/mailboxes/{mailbox_id}/stream")
async def api_live_stream(mailbox_id: int):
    """
    Server-Sent Events stream for immediate status/product UI refresh.
    """

    async def event_generator():
        last_fingerprint = None
        heartbeat = 0

        while True:
            try:
                fingerprint = await asyncio.to_thread(_stream_fingerprint, mailbox_id)

                if fingerprint != last_fingerprint:
                    payload = json.dumps({
                        "type": "update",
                        "mailbox_id": mailbox_id
                    })
                    yield f"data: {payload}\n\n"
                    last_fingerprint = fingerprint
                    heartbeat = 0

                heartbeat += 1

                if heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = 0

                await asyncio.sleep(UI_PUSH_SECONDS)

            except asyncio.CancelledError:
                break

            except Exception as exc:
                payload = json.dumps({
                    "type": "stream_error",
                    "message": str(exc)[:200]
                })
                yield f"data: {payload}\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/mailboxes/{mailbox_id}/emails")
def api_emails(mailbox_id: int, limit: int = 200):
    with db() as conn:
        rows = conn.execute("""
            SELECT id,received_at,sender,subject,attachment_names,status,error
            FROM emails WHERE mailbox_id=? AND COALESCE(in_inbox,1)=1 ORDER BY id DESC LIMIT ?
        """, (mailbox_id, limit)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/mailboxes/{mailbox_id}/products")
def api_products(mailbox_id: int, q: str = "", limit: int = 500, include_review: bool = False):
    """Return trusted products for day-to-day work by default.

    Uncertain candidates remain available in the dedicated Review page instead of
    being mixed into the main Products table. Set include_review=true only for
    diagnostics/migration tools.
    """
    limit = min(max(int(limit), 1), 2000)
    verified_sql = "" if include_review else " AND COALESCE(p.verification_status,'')='CONFIRMED' AND COALESCE(p.export_eligible,0)=1"
    verified_plain_sql = "" if include_review else " AND COALESCE(verification_status,'')='CONFIRMED' AND COALESCE(export_eligible,0)=1"
    with db() as conn:
        if q.strip():
            terms = re.findall(r"[A-Za-z0-9_.+-]+", q.strip())[:12]
            rows = []
            if terms:
                fts_query = " AND ".join(f'"{t.replace(chr(34), "")}"*' for t in terms)
                try:
                    rows = conn.execute(
                        f"""SELECT p.* FROM product_search f JOIN products p ON p.id=f.product_id
                           WHERE f.mailbox_id=? AND product_search MATCH ? {verified_sql}
                           ORDER BY bm25(product_search),p.id DESC LIMIT ?""",
                        (mailbox_id, fts_query, limit)
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                search = f"%{q}%"
                rows = conn.execute(
                    f"""SELECT * FROM products WHERE mailbox_id=? {verified_plain_sql} AND
                       (product_name LIKE ? OR model LIKE ? OR supplier_name LIKE ? OR specifications LIKE ?)
                       ORDER BY id DESC LIMIT ?""",
                    (mailbox_id, search, search, search, search, limit)
                ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM products WHERE mailbox_id=? {verified_plain_sql} ORDER BY id DESC LIMIT ?",
                (mailbox_id, limit)
            ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/products/{product_id}/details")
def api_product_details(product_id: int):
    with db() as conn:
        product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            raise HTTPException(404, "Product not found")
        email = conn.execute("SELECT * FROM emails WHERE id=?", (product["email_id"],)).fetchone()
        mailbox = conn.execute("SELECT id,email FROM mailboxes WHERE id=?", (product["mailbox_id"],)).fetchone()
        evidence = conn.execute(
            "SELECT field_name,evidence_text,source_type,source_filename,source_page,confidence FROM product_evidence WHERE product_id=? ORDER BY id",
            (product_id,)
        ).fetchall()
        if (product["canonical_model"] or "").strip():
            history = conn.execute(
                """SELECT id,price_date,original_price,discount_percent,purchase_price,currency,supplier_name,verification_status,created_at
                   FROM products WHERE canonical_model=? ORDER BY COALESCE(price_date,created_at) DESC,id DESC LIMIT 25""",
                (product["canonical_model"],)
            ).fetchall()
        else:
            history = conn.execute(
                """SELECT id,price_date,original_price,discount_percent,purchase_price,currency,supplier_name,verification_status,created_at
                   FROM products WHERE canonical_product_name=? ORDER BY COALESCE(price_date,created_at) DESC,id DESC LIMIT 25""",
                (product["canonical_product_name"],)
            ).fetchall()
    email_dict = dict(email) if email else {}
    # Backfill source identifiers for rows extracted before V20.1.
    if email and (not email_dict.get("rfc_message_id") or not email_dict.get("gmail_thread_id")):
        try:
            mb = mailbox_row(product["mailbox_id"])
            msg = fetch_message(mb, email_dict.get("gmail_message_id"))
            with db() as conn:
                conn.execute(
                    "UPDATE emails SET gmail_thread_id=?,rfc_message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (msg.get("threadId", ""), msg.get("rfc_message_id", ""), email["id"])
                )
                conn.execute(
                    """UPDATE products SET source_gmail_message_id=?,source_gmail_thread_id=?,source_rfc_message_id=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (msg.get("id", ""), msg.get("threadId", ""), msg.get("rfc_message_id", ""), product_id)
                )
            email_dict["gmail_thread_id"] = msg.get("threadId", "")
            email_dict["rfc_message_id"] = msg.get("rfc_message_id", "")
        except Exception:
            pass
    links = gmail_links(
        mailbox["email"] if mailbox else "",
        email_dict.get("gmail_thread_id") or product["source_gmail_thread_id"] or "",
        email_dict.get("rfc_message_id") or product["source_rfc_message_id"] or "",
    )
    return {
        "product": dict(product),
        "email": {
            "received_at": email_dict.get("received_at", ""),
            "sender": email_dict.get("sender", ""),
            "recipients": email_dict.get("recipients", ""),
            "cc": email_dict.get("cc", ""),
            "subject": email_dict.get("subject", ""),
            "attachment_names": email_dict.get("attachment_names", ""),
            "status": email_dict.get("status", ""),
            "gmail_message_id": email_dict.get("gmail_message_id", ""),
            "gmail_thread_id": email_dict.get("gmail_thread_id", ""),
            "rfc_message_id": email_dict.get("rfc_message_id", ""),
        },
        "mailbox_email": mailbox["email"] if mailbox else "",
        "evidence": [dict(r) for r in evidence],
        "price_history": [dict(r) for r in history],
        "gmail": links,
    }


@app.get("/api/mailboxes/{mailbox_id}/review")
def api_review(mailbox_id: int, limit: int = 100):
    with db() as conn:
        rows = conn.execute(
            """SELECT r.id,r.reason,r.status,r.created_at,r.email_id,p.id product_id,p.product_name,p.model,p.original_price,p.discount_percent,p.purchase_price,p.currency,
                      e.subject,e.sender,e.received_at,e.status email_status,e.error email_error,e.attachment_names
               FROM review_items r
               LEFT JOIN products p ON p.id=r.product_id
               LEFT JOIN emails e ON e.id=r.email_id
               WHERE r.mailbox_id=? AND r.status='OPEN'
               ORDER BY r.id DESC LIMIT ?""",
            (mailbox_id, min(max(int(limit), 1), 500))
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/review/{review_id}/details")
def api_review_details(review_id: int):
    """Show source and evidence for product-level or email-level review items."""
    with db() as conn:
        row = conn.execute(
            """SELECT r.*,e.mailbox_id AS email_mailbox_id,e.received_at,e.sender,e.recipients,e.cc,
                      e.subject,e.body,e.attachment_names,e.status AS email_status,e.error AS email_error,
                      e.gmail_message_id,e.gmail_thread_id,e.rfc_message_id,m.email AS mailbox_email
               FROM review_items r
               LEFT JOIN emails e ON e.id=r.email_id
               LEFT JOIN mailboxes m ON m.id=e.mailbox_id
               WHERE r.id=?""",
            (review_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Review item not found")
        product = None
        evidence = []
        if row["product_id"]:
            product_row = conn.execute("SELECT * FROM products WHERE id=?", (row["product_id"],)).fetchone()
            if product_row:
                product = dict(product_row)
                evidence = [dict(x) for x in conn.execute(
                    """SELECT field_name,evidence_text,source_type,source_filename,source_page,confidence
                       FROM product_evidence WHERE product_id=? ORDER BY id""",
                    (row["product_id"],)
                ).fetchall()]
    links = gmail_links(row["mailbox_email"] or "", row["gmail_thread_id"] or "", row["rfc_message_id"] or "")
    return {
        "review": {"id": row["id"], "reason": row["reason"] or "", "created_at": row["created_at"] or "", "product_id": row["product_id"]},
        "product": product,
        "email": {
            "received_at": row["received_at"] or "", "sender": row["sender"] or "", "recipients": row["recipients"] or "",
            "cc": row["cc"] or "", "subject": row["subject"] or "", "body": row["body"] or "",
            "attachment_names": row["attachment_names"] or "", "status": row["email_status"] or "", "error": row["email_error"] or "",
            "gmail_message_id": row["gmail_message_id"] or "", "gmail_thread_id": row["gmail_thread_id"] or "", "rfc_message_id": row["rfc_message_id"] or "",
        },
        "mailbox_email": row["mailbox_email"] or "", "evidence": evidence, "gmail": links,
        "can_approve": bool(row["product_id"]), "can_retry": bool(row["email_id"]),
    }


@app.post("/api/review/{review_id}/retry")
def api_retry_review(review_id: int):
    """Retry through the full V21.5 Stage 0 -> Stage 1 -> Stage 2 architecture."""
    with db() as conn:
        row = conn.execute(
            """SELECT r.*,e.mailbox_id,e.attachment_names FROM review_items r
               LEFT JOIN emails e ON e.id=r.email_id WHERE r.id=?""", (review_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Review item not found")
        if not row["email_id"] or not row["mailbox_id"]:
            raise HTTPException(400, "This review item has no source email to retry")
        eid=int(row["email_id"]); mid=int(row["mailbox_id"])
        conn.execute("UPDATE review_items SET status='RESOLVED',resolved_at=CURRENT_TIMESTAMP WHERE id=?", (review_id,))
        conn.execute("DELETE FROM v215_stage_tasks WHERE email_id=?", (eid,))
        conn.execute(
            """UPDATE emails SET status='RAW',pipeline_stage='MAIN',error=NULL,stage0_decision=NULL,stage1_summary=NULL,
               analysis_started_at=NULL,analysis_completed_at=NULL,rescue_attempts=0,ai_attempts=0,
               priority=MAX(COALESCE(priority,0),95),updated_at=CURRENT_TIMESTAMP WHERE id=?""", (eid,)
        )
        conn.execute(
            """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority) VALUES(?,?,'ANALYZE','READY',95)
               ON CONFLICT(email_id,job_type) DO UPDATE SET state='READY',priority=MAX(processing_jobs.priority,95),
                 leased_at=NULL,lease_owner=NULL,next_retry_at=NULL,last_error=NULL,updated_at=CURRENT_TIMESTAMP""", (mid,eid)
        )
        conn.execute("INSERT INTO audit_log(event_type,entity_type,entity_id,details) VALUES('REVIEW_RETRY','review',?,?)",
                     (str(review_id), f"Email {eid} requeued into V21.5 Stage 0/1"))
    _dispatch_worker(f"review_retry_v215_{mid}", lambda mid=mid: analyze_discovered_mailbox(mid, min(4,DIRECT_SLICE_SIZE)), count_deferral=False)
    return {"ok": True, "email_id": eid, "lane": "V21.5_STAGE0_STAGE1"}

@app.post("/api/review/{review_id}/approve")
def api_approve_review(review_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Review item not found")
        if not row["product_id"]:
            raise HTTPException(400, "This review item has no product candidate to approve")
        # Explicit human approval is the only manual override allowed to pass the Sheet firewall.
        # Keep the override auditable instead of silently treating it as AI-confirmed.
        conn.execute(
            """UPDATE products SET verification_status='CONFIRMED',
               verification_confidence=1.0,
               export_eligible=1,
               quality_flags='["HUMAN_APPROVED"]',
               updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (row["product_id"],)
        )
        conn.execute("UPDATE review_items SET status='RESOLVED',resolved_at=CURRENT_TIMESTAMP WHERE id=?", (review_id,))
        if row["email_id"]:
            count = conn.execute(
                "SELECT COUNT(*) c FROM products WHERE email_id=? AND COALESCE(verification_status,'CONFIRMED')='CONFIRMED' AND COALESCE(export_eligible,0)=1",
                (row["email_id"],)
            ).fetchone()["c"]
            if count:
                conn.execute(
                    "UPDATE emails SET status=?,error=NULL,analysis_completed_at=COALESCE(analysis_completed_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (f"EXTRACTED {count}", row["email_id"])
                )
        conn.execute(
            "INSERT INTO audit_log(event_type,entity_type,entity_id,details) VALUES('REVIEW_APPROVED','product',?,?)",
            (str(row["product_id"]), f"Review {review_id} manually approved")
        )
    try:
        learn_confirmed_product(int(row["product_id"]),human_approved=True)
    except Exception:
        logger.debug("Gold learning update skipped for review %s",review_id,exc_info=True)
    return {"ok": True}


@app.post("/api/review/{review_id}/resolve")
def api_resolve_review(review_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Review item not found")
        conn.execute("UPDATE review_items SET status='RESOLVED',resolved_at=CURRENT_TIMESTAMP WHERE id=?", (review_id,))
        conn.execute(
            "INSERT INTO audit_log(event_type,entity_type,entity_id,details) VALUES('REVIEW_DISMISSED','review',?,?)",
            (str(review_id), "Review item dismissed without product confirmation")
        )
    return {"ok": True}


def backup_database():
    """Online SQLite backup; keeps recent snapshots without stopping the live engine."""
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"nunes_mail_{stamp}.db"
    try:
        src = sqlite3.connect(DB_PATH, timeout=30.0)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
        files = sorted(backup_dir.glob("nunes_mail_*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in files[BACKUP_KEEP:]:
            try: old.unlink()
            except Exception: pass
        _setting_set("last_backup_at", system_local_now_text())
        return {"ok": True, "path": str(target)}
    except Exception as exc:
        _setting_set("last_backup_error", str(exc)[:700])
        return {"ok": False, "error": str(exc)}


@app.post("/api/engine/recover")
def api_engine_recover(mailbox_id: int = 0):
    """Safe V21.5 recovery without deleting Gmail/products/reviews."""
    where = " AND mailbox_id=?" if mailbox_id else ""
    args = (int(mailbox_id),) if mailbox_id else ()
    with db() as conn:
        pending_before=int(conn.execute(
            f"SELECT COUNT(*) c FROM emails WHERE COALESCE(in_inbox,1)=1 AND analysis_completed_at IS NULL AND status<>'ERROR' {where}", args
        ).fetchone()["c"])
        # Release stale specialist tasks immediately; queued tasks remain in their
        # format lane and are not collapsed back into one Rescue queue.
        if mailbox_id:
            conn.execute("UPDATE v215_stage_tasks SET state='QUEUED',updated_at=CURRENT_TIMESTAMP WHERE mailbox_id=? AND state='PROCESSING'", (int(mailbox_id),))
        else:
            conn.execute("UPDATE v215_stage_tasks SET state='QUEUED',updated_at=CURRENT_TIMESTAMP WHERE state='PROCESSING'")
        # Main-stage rows regain durable ANALYZE jobs.
        pending_rows=conn.execute(
            f"SELECT id,mailbox_id FROM emails WHERE COALESCE(in_inbox,1)=1 AND analysis_completed_at IS NULL AND status IN ('DISCOVERED','NEW','RAW','PROCESSING','STAGE0 FILTER','STAGE1 CLASSIFY') {where}", args
        ).fetchall()
        for row in pending_rows:
            conn.execute(
                """UPDATE emails SET status='RAW',pipeline_stage='MAIN',analysis_started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (int(row['id']),)
            )
            conn.execute(
                """INSERT INTO processing_jobs(mailbox_id,email_id,job_type,state,priority) VALUES(?,?,'ANALYZE','READY',95)
                   ON CONFLICT(email_id,job_type) DO UPDATE SET state='READY',priority=MAX(processing_jobs.priority,95),
                     leased_at=NULL,lease_owner=NULL,next_retry_at=NULL,last_error=NULL,updated_at=CURRENT_TIMESTAMP""",
                (int(row['mailbox_id']),int(row['id']))
            )
        # Exception AI lease can be safely requeued.
        conn.execute(f"UPDATE emails SET status='AI QUEUED',updated_at=CURRENT_TIMESTAMP WHERE analysis_completed_at IS NULL AND status='AI PROCESSING' {where}", args)
    with _DISPATCH_GUARD:
        for k in list(_DISPATCH_DEFERRALS): _DISPATCH_DEFERRALS[k]=0
    started={
        'direct_analysis': _dispatch_worker('direct_analysis_recovery', direct_analysis_worker),
        'stage2': _dispatch_worker('v215_stage2_recovery', v215_stage2_supervisor),
        'ai': _dispatch_worker('ai_recovery', ai_fallback_worker),
        'sheets': _dispatch_worker('sheets_recovery', sheet_export_worker),
    }
    _setting_set("last_manual_recovery", f"{system_local_now_text()} · mailbox {mailbox_id or 'all'} · {pending_before} pending")
    with _STATS_CACHE_LOCK:
        if mailbox_id: _STATS_CACHE.pop(int(mailbox_id),None)
        else: _STATS_CACHE.clear()
    return {"ok":True,"pending_before":pending_before,"workers":started,
            "message":"V21.5 specialist queues recovered without collapsing format lanes."}

@app.get("/api/health")
def api_health():
    try:
        with db() as conn:
            mailbox_count=int(conn.execute("SELECT COUNT(*) c FROM mailboxes WHERE is_active=1").fetchone()["c"])
            pending_total=int(conn.execute("SELECT COUNT(*) c FROM emails WHERE COALESCE(in_inbox,1)=1 AND analysis_completed_at IS NULL AND status<>'ERROR'").fetchone()["c"])
            active_email=int(conn.execute("SELECT COUNT(*) c FROM emails WHERE COALESCE(in_inbox,1)=1 AND status IN ('PROCESSING','STAGE0 FILTER','STAGE1 CLASSIFY','PYTHON RESCUE','PYTHON VERIFY','AI PROCESSING')").fetchone()["c"])
            active_stage2=int(conn.execute("SELECT COUNT(DISTINCT email_id) c FROM v215_stage_tasks WHERE state='PROCESSING'").fetchone()["c"])
            active_total=active_email+active_stage2
            recent_15=int(conn.execute("SELECT COUNT(*) c FROM emails WHERE COALESCE(in_inbox,1)=1 AND analysis_completed_at IS NOT NULL AND analysis_completed_at>=datetime('now','-15 minutes')").fetchone()["c"])
            last_completed=conn.execute("SELECT MAX(analysis_completed_at) v FROM emails WHERE COALESCE(in_inbox,1)=1").fetchone()["v"] or ""
    except Exception:
        mailbox_count=pending_total=active_total=recent_15=0; last_completed=""
        logger.exception("Health DB query failed")
    sheet_id=get_shared_sheet_id(); dbh=database_health(); workers=_worker_status_snapshot()
    worker_errors=[f"{n}: {v['last_error']}" for n,v in workers.items() if v.get("last_error")]
    uptime_seconds=int((datetime.now().astimezone()-APP_STARTED_AT).total_seconds())
    local=local_ai_health(); ocr=ocr_health()
    stalled=bool(pending_total>0 and recent_15==0 and uptime_seconds>=900 and active_total==0)
    if worker_errors: engine_ok,engine_state,engine_detail=False,"Attention",worker_errors[0][:180]
    elif stalled: engine_ok,engine_state,engine_detail=False,"Stalled",f"{pending_total} pending · no active worker and no completion in 15 min"
    elif pending_total>0: engine_ok,engine_state,engine_detail=True,"Processing",f"{pending_total} pending · {active_total} active · {recent_15} completed in 15 min"
    else: engine_ok,engine_state,engine_detail=True,"Stable","No pending processing backlog"
    return {
        "gmail":{"ok":mailbox_count>0,"state":"Connected" if mailbox_count else "Not connected","detail":f"{mailbox_count} account(s)"},
        "local_ai":{"ok":bool(local.get("online")),"state":"Online" if local.get("online") else "Optional","neutral":not bool(local.get("online")),"detail":f"Exception-only · {local.get('fast_model','qwen3:1.7b')} / {local.get('deep_model','qwen3:4b')} / {local.get('vision_model','qwen3-vl:4b')}"},
        "database":{"ok":bool(dbh.get("ok")),"state":"Healthy" if dbh.get("ok") else "Attention","detail":f"{dbh.get('journal_mode','WAL')} · schema {dbh.get('schema_version',215)}"},
        "sheets":{"ok":bool(sheet_id),"state":"Connected" if sheet_id else "Not configured","neutral":not bool(sheet_id),"detail":"Shared Sheet configured" if sheet_id else "Optional output not configured"},
        "engine":{"ok":engine_ok,"state":engine_state,"detail":engine_detail},
        "backup":{"ok":not bool(_setting_get("last_backup_error","")),"state":"Healthy","detail":_setting_get("last_backup_at","Not run yet")},
        "workers":workers,"queue":{"pending":pending_total,"active":active_total,"recent_15m":recent_15,"last_completed_at":last_completed},
        "ocr":ocr,"learning_engine":learning_health(),"local_ai_runtime":local,
        "uptime_seconds":uptime_seconds,"build_id":BUILD_ID,"server_time":system_local_now_text(),
    }

@app.get("/api/diagnostics")
def api_diagnostics():
    """V21.5 diagnostics: Stage 0/1 plus every independent Stage-2 lane."""
    with db() as conn:
        def c(sql,args=()): return int(conn.execute(sql,args).fetchone()["c"])
        stage2 = {}
        for lane in ("TEXT","PDF","TABLE","DOC","OCR","OTHER"):
            stage2[lane.lower()] = {
                "queued": c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE lane=? AND state='QUEUED'", (lane,)),
                "processing": c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE lane=? AND state='PROCESSING'", (lane,)),
                "done": c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE lane=? AND state='DONE'", (lane,)),
                "skipped": c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE lane=? AND state='SKIPPED'", (lane,)),
                "failed": c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE lane=? AND state='FAILED'", (lane,)),
            }
        queue={
            "emails":c("SELECT COUNT(*) c FROM emails"),
            "pending":c("SELECT COUNT(*) c FROM emails WHERE analysis_completed_at IS NULL AND status<>'ERROR'"),
            "stage0_stage1_main":c("SELECT COUNT(*) c FROM emails WHERE status IN ('DISCOVERED','NEW','RAW','PROCESSING','STAGE0 FILTER','STAGE1 CLASSIFY')"),
            "stage2_emails":c("SELECT COUNT(*) c FROM emails WHERE status='STAGE2 QUEUED'"),
            "stage2_tasks_queued":c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE state='QUEUED'"),
            "stage2_tasks_processing":c("SELECT COUNT(*) c FROM v215_stage_tasks WHERE state='PROCESSING'"),
            "python_rescue":c("SELECT COUNT(*) c FROM emails WHERE status IN ('PYTHON RESCUE','PYTHON VERIFY')"),
            "ai_exception_queued":c("SELECT COUNT(*) c FROM emails WHERE status IN ('AI QUEUED','AI PROCESSING')"),
            "errors":c("SELECT COUNT(*) c FROM emails WHERE status='ERROR'"),
            "products":c("SELECT COUNT(*) c FROM products"),
            "review":c("SELECT COUNT(*) c FROM review_items WHERE status='OPEN'"),
        }
    local=local_ai_health()
    return {
        "build_id":BUILD_ID,"server_time":system_local_now_text(),"timezone":system_timezone_text(),
        "uptime_seconds":int((datetime.now().astimezone()-APP_STARTED_AT).total_seconds()),
        "database":database_health(),"queue":queue,"stage2_lanes":stage2,
        "architecture":{
            **v215_architecture_summary(),
            "stage0_workers":V214_MAIN_WORKERS,
            "stage2_workers":{
                "text":V215_TEXT_WORKERS,"pdf":V215_PDF_WORKERS,"table":V215_TABLE_WORKERS,
                "doc":V215_DOC_WORKERS,"ocr":V215_OCR_WORKERS,
            },
            "ai_workers":1,
            "flow":"Stage 0 irrelevant filter -> Stage 1 format segregation -> independent Stage 2 specialists -> Python cross-document rescue -> exception AI -> Python final evidence firewall",
            "backlog_policy":"One-time V21.4 unfinished backlog re-enters Stage 0/1; later restarts preserve V21.5 specialist tasks",
            "retrieval_memory":"SQLite relational only","rag":"REMOVED","external_ai":"DISABLED",
        },
        "ocr":ocr_health(),
        "models":{"fast_text":local.get("fast_model"),"deep_text":local.get("deep_model"),"vision":local.get("vision_model"),"one_active_model_at_a_time":True},
        "learning":learning_health(),
        "resource_controller":{"memory":_system_memory_snapshot(),"min_free_memory_mb":HYPERSPEED_MIN_FREE_MEMORY_MB,"min_free_memory_percent":HYPERSPEED_MIN_FREE_MEMORY_PERCENT,"pdf_concurrency":V215_PDF_WORKERS,"ocr_concurrency":V215_OCR_WORKERS,"local_model_concurrency":1},
        "workers":_worker_status_snapshot(),"log_file":str(APP_LOG),
    }

@app.post("/api/gmail/push")
async def api_gmail_push(request: Request):
    """Optional Cloud Pub/Sub push receiver.

    Local desktop mode continues to use Gmail History polling. This endpoint becomes useful
    only when the app is exposed through a secure public HTTPS endpoint and Gmail users.watch
    is configured for a Pub/Sub topic.
    """
    expected = (os.getenv("GMAIL_PUSH_TOKEN") or "").strip()
    if expected and request.query_params.get("token", "") != expected:
        raise HTTPException(401, "Invalid push token")
    payload = await request.json()
    encoded = ((payload or {}).get("message") or {}).get("data") or ""
    if not encoded:
        return {"ok": True, "ignored": True}
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        data = json.loads(raw)
        email = (data.get("emailAddress") or "").strip().lower()
    except Exception as exc:
        raise HTTPException(400, f"Invalid Pub/Sub payload: {exc}")
    with db() as conn:
        mb = conn.execute("SELECT id FROM mailboxes WHERE lower(email)=? AND is_active=1", (email,)).fetchone()
    if mb:
        threading.Thread(target=sync_mailbox, args=(int(mb["id"]),), kwargs={"query": "in:inbox newer_than:2d", "max_results": 100}, daemon=True).start()
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    logger.info("Launching Uvicorn on http://127.0.0.1:%s", APP_PORT)
    # One process + one app instance. Passing the object avoids importing app.py
    # a second time during startup.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=APP_PORT,
        reload=False,
        workers=1,
        access_log=False,
        timeout_keep_alive=15,
        log_level="warning",
    )
