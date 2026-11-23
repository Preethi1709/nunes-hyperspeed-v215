from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / (os.getenv("NUNES_DB_FILE", "nunes_v213_fresh.db").strip() or "nunes_v213_fresh.db")
BACKUP_DIR = Path(os.getenv("NUNES_BACKUP_DIR", str(BASE_DIR / "backups")))
DB_SCHEMA_VERSION = 215
_DB_INIT_LOCK = threading.RLock()

# IMPORTANT: table creation and index creation are intentionally separate.
# Older NUNES databases can be missing newer columns. Creating an index on a
# not-yet-migrated column causes SQLite to abort startup with "no such column".
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mailboxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    access_token TEXT,
    refresh_token TEXT,
    token_uri TEXT,
    client_id TEXT,
    client_secret TEXT,
    scopes TEXT,
    connected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_sync_at TEXT,
    is_active INTEGER DEFAULT 1,
    inbox_query TEXT DEFAULT 'in:inbox',
    history_page_token TEXT,
    history_complete INTEGER DEFAULT 0,
    history_scanned_count INTEGER DEFAULT 0,
    inbox_total_estimate INTEGER DEFAULT 0,
    last_history_at TEXT,
    gmail_history_id TEXT,
    last_live_poll_at TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL,
    gmail_message_id TEXT NOT NULL,
    gmail_thread_id TEXT,
    rfc_message_id TEXT,
    received_at TEXT,
    sender TEXT,
    recipients TEXT,
    cc TEXT,
    subject TEXT,
    body TEXT,
    attachment_names TEXT,
    status TEXT DEFAULT 'RAW',
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    priority INTEGER DEFAULT 0,
    analysis_attempts INTEGER DEFAULT 0,
    analysis_started_at TEXT,
    analysis_completed_at TEXT,
    relevance_score REAL,
    pipeline_stage TEXT DEFAULT 'MAIN',
    rescue_attempts INTEGER DEFAULT 0,
    ai_attempts INTEGER DEFAULT 0,
    source_hash TEXT,
    in_inbox INTEGER DEFAULT 1,
    UNIQUE(mailbox_id, gmail_message_id),
    FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL,
    email_id INTEGER NOT NULL,
    product_key TEXT NOT NULL,
    marketing_person TEXT,
    price_date TEXT,
    image_link TEXT,
    product_name TEXT NOT NULL,
    canonical_product_name TEXT,
    model TEXT,
    canonical_model TEXT,
    specifications TEXT,
    original_price REAL,
    discount_percent REAL,
    purchase_price REAL,
    currency TEXT DEFAULT 'INR',
    purchase_owner TEXT,
    supplier_name TEXT,
    canonical_supplier_name TEXT,
    source_type TEXT,
    source_filename TEXT,
    confidence REAL,
    verification_status TEXT DEFAULT 'CONFIRMED',
    verification_confidence REAL,
    export_eligible INTEGER DEFAULT 0,
    quality_flags TEXT,
    price_basis TEXT,
    price_evidence TEXT,
    evidence_summary TEXT,
    source_gmail_message_id TEXT,
    source_gmail_thread_id TEXT,
    source_rfc_message_id TEXT,
    sheet_exported_at TEXT,
    sheet_export_error TEXT,
    sheet_export_target TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mailbox_id, product_key),
    FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id),
    FOREIGN KEY(email_id) REFERENCES emails(id)
);

CREATE TABLE IF NOT EXISTS product_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    evidence_text TEXT,
    source_type TEXT,
    source_filename TEXT,
    source_page TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL,
    email_id INTEGER,
    product_id INTEGER,
    reason TEXT NOT NULL,
    payload_json TEXT,
    status TEXT DEFAULT 'OPEN',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id),
    FOREIGN KEY(email_id) REFERENCES emails(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL,
    email_id INTEGER NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'ANALYZE',
    state TEXT NOT NULL DEFAULT 'READY',
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    leased_at TEXT,
    lease_owner TEXT,
    next_retry_at TEXT,
    last_error TEXT,
    last_stage TEXT,
    quarantine_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(email_id, job_type),
    FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id),
    FOREIGN KEY(email_id) REFERENCES emails(id)
);

CREATE TABLE IF NOT EXISTS v215_stage_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER NOT NULL,
    email_id INTEGER NOT NULL,
    task_key TEXT NOT NULL,
    lane TEXT NOT NULL,
    filename TEXT,
    attachment_id TEXT,
    mime_type TEXT,
    state TEXT NOT NULL DEFAULT 'QUEUED',
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    commercial_score REAL DEFAULT 0,
    probe_reason TEXT,
    evidence_text TEXT,
    result_json TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(email_id, task_key),
    FOREIGN KEY(mailbox_id) REFERENCES mailboxes(id) ON DELETE CASCADE,
    FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS supplier_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    domain TEXT,
    aliases TEXT,
    last_seen_at TEXT,
    quotation_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_master_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_product_name TEXT,
    canonical_model TEXT,
    alias_name TEXT,
    alias_model TEXT,
    UNIQUE(alias_name, alias_model)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_verification_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mailbox_id INTEGER,
    email_id INTEGER,
    product_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    verdict TEXT,
    confidence REAL,
    evidence TEXT,
    latency_ms INTEGER,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL,
    product_id INTEGER,
    value REAL,
    currency TEXT,
    price_type TEXT,
    source_filename TEXT,
    source_page TEXT,
    source_row TEXT,
    source_column TEXT,
    evidence_text TEXT,
    accepted INTEGER DEFAULT 0,
    rejection_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);



CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_emails_mailbox_status ON emails(mailbox_id, status);
CREATE INDEX IF NOT EXISTS idx_emails_queue ON emails(mailbox_id, status, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_emails_rfc_message_id ON emails(rfc_message_id);
CREATE INDEX IF NOT EXISTS idx_emails_inbox_status ON emails(mailbox_id, in_inbox, status);
CREATE INDEX IF NOT EXISTS idx_emails_analysis_completed ON emails(mailbox_id, analysis_completed_at);
CREATE INDEX IF NOT EXISTS idx_emails_v214_stage ON emails(mailbox_id, pipeline_stage, status, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_products_mailbox ON products(mailbox_id);
CREATE INDEX IF NOT EXISTS idx_products_model ON products(canonical_model, model);
CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(canonical_supplier_name, supplier_name);
CREATE INDEX IF NOT EXISTS idx_products_created ON products(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_sheet_pending ON products(mailbox_id, export_eligible, sheet_exported_at, verification_status);
CREATE INDEX IF NOT EXISTS idx_products_export_gate ON products(mailbox_id, export_eligible, verification_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_state_priority ON processing_jobs(state, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_v215_tasks_lane_state ON v215_stage_tasks(lane, state, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_v215_tasks_email ON v215_stage_tasks(email_id, state, id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_runs_email ON ai_verification_runs(email_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_candidates_email ON price_candidates(email_id, accepted);
"""

MIGRATION_COLUMNS = {
    "mailboxes": [
        ("inbox_query", "inbox_query TEXT DEFAULT 'in:inbox'"),
        ("history_page_token", "history_page_token TEXT"),
        ("history_complete", "history_complete INTEGER DEFAULT 0"),
        ("history_scanned_count", "history_scanned_count INTEGER DEFAULT 0"),
        ("inbox_total_estimate", "inbox_total_estimate INTEGER DEFAULT 0"),
        ("last_history_at", "last_history_at TEXT"),
        ("gmail_history_id", "gmail_history_id TEXT"),
        ("last_live_poll_at", "last_live_poll_at TEXT"),
    ],
    "emails": [
        ("priority", "priority INTEGER DEFAULT 0"),
        ("analysis_attempts", "analysis_attempts INTEGER DEFAULT 0"),
        ("analysis_started_at", "analysis_started_at TEXT"),
        ("analysis_completed_at", "analysis_completed_at TEXT"),
        ("rfc_message_id", "rfc_message_id TEXT"),
        ("relevance_score", "relevance_score REAL"),
        ("pipeline_stage", "pipeline_stage TEXT DEFAULT 'MAIN'"),
        ("rescue_attempts", "rescue_attempts INTEGER DEFAULT 0"),
        ("ai_attempts", "ai_attempts INTEGER DEFAULT 0"),
        ("source_hash", "source_hash TEXT"),
        ("in_inbox", "in_inbox INTEGER DEFAULT 1"),
        ("stage0_decision", "stage0_decision TEXT"),
        ("stage1_summary", "stage1_summary TEXT"),
    ],
    "products": [
        ("sheet_exported_at", "sheet_exported_at TEXT"),
        ("sheet_export_error", "sheet_export_error TEXT"),
        ("sheet_export_target", "sheet_export_target TEXT"),
        ("canonical_product_name", "canonical_product_name TEXT"),
        ("canonical_model", "canonical_model TEXT"),
        ("original_price", "original_price REAL"),
        ("discount_percent", "discount_percent REAL"),
        ("currency", "currency TEXT DEFAULT 'INR'"),
        ("canonical_supplier_name", "canonical_supplier_name TEXT"),
        ("verification_status", "verification_status TEXT DEFAULT 'CONFIRMED'"),
        ("verification_confidence", "verification_confidence REAL"),
        ("export_eligible", "export_eligible INTEGER DEFAULT 0"),
        ("quality_flags", "quality_flags TEXT"),
        ("price_basis", "price_basis TEXT"),
        ("price_evidence", "price_evidence TEXT"),
        ("evidence_summary", "evidence_summary TEXT"),
        ("source_gmail_message_id", "source_gmail_message_id TEXT"),
        ("source_gmail_thread_id", "source_gmail_thread_id TEXT"),
        ("source_rfc_message_id", "source_rfc_message_id TEXT"),
        ("updated_at", "updated_at TEXT"),
    ],
}


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")


def _quick_check_conn(conn: sqlite3.Connection) -> tuple[bool, str]:
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
        result = "; ".join(str(r[0]) for r in rows[:10]) if rows else "unknown"
        return result.lower() == "ok", result
    except Exception as exc:
        return False, str(exc)


def database_health() -> dict:
    if not DB_PATH.exists():
        return {"ok": True, "detail": "Database will be created on startup", "journal_mode": "WAL"}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        _configure_connection(conn)
        ok, detail = _quick_check_conn(conn)
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.close()
        return {"ok": ok, "detail": detail, "journal_mode": mode, "schema_version": version}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "journal_mode": "unknown", "schema_version": 0}


def _backup_before_migration(conn: sqlite3.Connection, old_version: int) -> None:
    if not DB_PATH.exists() or old_version >= DB_SCHEMA_VERSION:
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    # Exactly one pre-V21.5 safety snapshot is enough. It intentionally never
    # overwrites an earlier snapshot.
    target = BACKUP_DIR / "PRE_V21_5_MIGRATION.db"
    if target.exists():
        return
    dst = sqlite3.connect(target)
    try:
        conn.backup(dst)
        dst.commit()
    finally:
        dst.close()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _ensure_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS product_search USING fts5(
            product_id UNINDEXED, mailbox_id UNINDEXED, product_name, model, supplier_name, specifications, tokenize='unicode61'
        )""")
        conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
          INSERT INTO product_search(product_id,mailbox_id,product_name,model,supplier_name,specifications)
          VALUES(new.id,new.mailbox_id,new.product_name,new.model,new.supplier_name,new.specifications);
        END;
        CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
          DELETE FROM product_search WHERE product_id=old.id;
        END;
        CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
          DELETE FROM product_search WHERE product_id=old.id;
          INSERT INTO product_search(product_id,mailbox_id,product_name,model,supplier_name,specifications)
          VALUES(new.id,new.mailbox_id,new.product_name,new.model,new.supplier_name,new.specifications);
        END;
        """)
        conn.execute("""INSERT INTO product_search(product_id,mailbox_id,product_name,model,supplier_name,specifications)
                      SELECT p.id,p.mailbox_id,p.product_name,p.model,p.supplier_name,p.specifications FROM products p
                      WHERE NOT EXISTS(SELECT 1 FROM product_search f WHERE f.product_id=p.id)""")
    except sqlite3.OperationalError:
        # FTS5 is an optimization only; the normal indexed LIKE search remains usable.
        pass


def init_db() -> None:
    """Create/migrate the persistent database without the V20.3 startup-order bug."""
    with _DB_INIT_LOCK:
        BACKUP_DIR.mkdir(exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=60.0)
        try:
            _configure_connection(conn)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                # A temporarily busy DB should not make startup disappear. The busy
                # timeout remains active and the existing journal mode is safe to use.
                pass

            # If an existing database is structurally corrupt, stop before changing it.
            # The launcher will keep the window open and point the user to the log.
            if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
                ok, detail = _quick_check_conn(conn)
                if not ok:
                    raise RuntimeError(f"DATABASE_INTEGRITY_ERROR: {detail}")

            old_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            _backup_before_migration(conn, old_version)

            # 1) Create any missing tables.
            conn.executescript(BASE_SCHEMA)

            # 2) Add all missing columns to older tables.
            for table, columns in MIGRATION_COLUMNS.items():
                for column, ddl in columns:
                    ensure_column(conn, table, column, ddl)

            # Preserve any compatible price column created by an older experiment.
            # Extra historical columns are left harmlessly in place; V21.5 never
            # relies on them unless the current evidence firewall proves the result.
            product_cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
            if "quoted_rate" in product_cols and "original_price" in product_cols:
                conn.execute(
                    """UPDATE products
                       SET original_price=quoted_rate
                       WHERE (original_price IS NULL OR original_price<=0)
                         AND quoted_rate IS NOT NULL AND quoted_rate>0"""
                )

            # 3) Only NOW create indexes that may reference migrated columns.
            conn.executescript(INDEX_SCHEMA)
            _ensure_fts(conn)

            conn.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES('db_schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(DB_SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES('last_db_migration_at',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
