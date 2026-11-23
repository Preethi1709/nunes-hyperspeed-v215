from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime

from db import db

_MODEL_KEY = "learning_router_model_v1"
_MODEL_META_KEY = "learning_router_model_meta_v1"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+\-/]{1,32}", re.I)

_DEFAULT_NEGATIVE_RULES = [
    ("quantity", "Quantity/Qty is never a unit purchase price."),
    ("gst_tax", "GST/CGST/SGST/IGST/tax percentages are never supplier discount percentages or unit prices."),
    ("grand_total", "Subtotal/grand total is not a unit purchase price unless deterministic row math explicitly proves a unit value."),
    ("freight_packing", "Freight, packing, installation and transport charges are not product unit prices."),
    ("model_digits", "Model/serial/HSN/SAC digits are identifiers, not prices."),
    ("range_accuracy", "Range, capacity, flow, resolution and accuracy numbers are specifications, not prices."),
    ("dates_phones_accounts", "Dates, phone numbers and account numbers are never purchase prices."),
]


def ensure_learning_schema() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS learning_negative_memory (
            pattern_key TEXT PRIMARY KEY,
            rule_text TEXT NOT NULL,
            hit_count INTEGER DEFAULT 0,
            last_seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS learning_source_cache (
            source_hash TEXT PRIMARY KEY,
            mailbox_id INTEGER NOT NULL,
            email_id INTEGER NOT NULL,
            final_status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS learning_supplier_stats (
            supplier_key TEXT PRIMARY KEY,
            canonical_name TEXT,
            domain TEXT,
            confirmed_count INTEGER DEFAULT 0,
            human_approved_count INTEGER DEFAULT 0,
            common_price_basis TEXT,
            last_seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS learning_verified_examples (
            product_id INTEGER PRIMARY KEY,
            mailbox_id INTEGER,
            supplier_key TEXT,
            supplier_name TEXT,
            product_name TEXT,
            model TEXT,
            price_basis TEXT,
            evidence_label TEXT,
            human_approved INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_learning_examples_supplier ON learning_verified_examples(supplier_key,updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_learning_cache_mailbox ON learning_source_cache(mailbox_id,email_id);
        """)
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        for key, rule in _DEFAULT_NEGATIVE_RULES:
            conn.execute(
                """INSERT INTO learning_negative_memory(pattern_key,rule_text,hit_count,last_seen_at)
                   VALUES(?,?,0,?) ON CONFLICT(pattern_key) DO UPDATE SET rule_text=excluded.rule_text""",
                (key, rule, now),
            )


def _tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN_RE.findall((text or "").lower()):
        if len(t) < 3 or t.isdigit():
            continue
        out.append(t[:40])
    return out


def source_fingerprint(sender: str, subject: str, body: str, attachment_names: str = "") -> str:
    raw = "\n".join([
        re.sub(r"\s+", " ", (sender or "").strip().lower()),
        re.sub(r"\s+", " ", (subject or "").strip().lower()),
        re.sub(r"\s+", " ", (body or "").strip()),
        re.sub(r"\s+", " ", (attachment_names or "").strip().lower()),
    ])
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def duplicate_final_status(mailbox_id: int, email_id: int, source_hash: str) -> dict | None:
    if not source_hash:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT e.id,e.status,e.analysis_completed_at
               FROM emails e
               WHERE e.mailbox_id=? AND e.id<>? AND e.source_hash=?
                 AND e.analysis_completed_at IS NOT NULL
                 AND e.status NOT IN ('NEEDS REVIEW','ERROR','FAILED')
               ORDER BY e.id DESC LIMIT 1""",
            (mailbox_id, email_id, source_hash),
        ).fetchone()
        return dict(row) if row else None


def remember_source_result(mailbox_id: int, email_id: int, source_hash: str, final_status: str) -> None:
    if not source_hash:
        return
    with db() as conn:
        conn.execute(
            """INSERT INTO learning_source_cache(source_hash,mailbox_id,email_id,final_status,updated_at)
               VALUES(?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(source_hash) DO UPDATE SET mailbox_id=excluded.mailbox_id,
                 email_id=excluded.email_id,final_status=excluded.final_status,updated_at=CURRENT_TIMESTAMP""",
            (source_hash, mailbox_id, email_id, final_status),
        )


def record_negative_flags(flags, evidence: str = "") -> int:
    flags = flags or []
    if isinstance(flags, str):
        try: flags = json.loads(flags)
        except Exception: flags = [flags]
    text = (" ".join(map(str, flags)) + " " + (evidence or "")).lower()
    mapping = {
        "quantity": ("qty", "quantity"),
        "gst_tax": ("gst", "cgst", "sgst", "igst", "tax"),
        "grand_total": ("grand total", "subtotal", "total_only", "total price"),
        "freight_packing": ("freight", "packing", "installation", "transport"),
        "model_digits": ("model", "serial", "hsn", "sac"),
        "range_accuracy": ("range", "accuracy", "resolution", "capacity", "flow"),
        "dates_phones_accounts": ("date", "phone", "account"),
    }
    hits = [key for key, needles in mapping.items() if any(n in text for n in needles)]
    if not hits: return 0
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    with db() as conn:
        for key in hits:
            conn.execute(
                "UPDATE learning_negative_memory SET hit_count=COALESCE(hit_count,0)+1,last_seen_at=? WHERE pattern_key=?",
                (now, key),
            )
    return len(hits)


def _sender_domain(sender: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    return (m.group(1).lower().strip(". ") if m else "")


def learn_confirmed_product(product_id: int, human_approved: bool = False) -> None:
    """Learn only from evidence-confirmed or explicitly human-approved records.

    V21.4 deliberately stores compact relational examples in SQLite.  No RAG,
    vector, embedding or semantic-retrieval table is used by the runtime.
    """
    with db() as conn:
        p = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not p: return
        if not human_approved and (p["verification_status"] or "").upper() != "CONFIRMED": return
        email = conn.execute("SELECT sender FROM emails WHERE id=?", (p["email_id"],)).fetchone()
        domain = _sender_domain(email["sender"] if email else "")
        canonical = (p["canonical_supplier_name"] or p["supplier_name"] or domain or "unknown").strip()
        key = canonical.lower()
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        conn.execute(
            """INSERT INTO learning_supplier_stats(
                   supplier_key,canonical_name,domain,confirmed_count,human_approved_count,common_price_basis,last_seen_at
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(supplier_key) DO UPDATE SET
                 canonical_name=excluded.canonical_name,
                 domain=COALESCE(NULLIF(excluded.domain,''),learning_supplier_stats.domain),
                 confirmed_count=learning_supplier_stats.confirmed_count+1,
                 human_approved_count=learning_supplier_stats.human_approved_count+excluded.human_approved_count,
                 common_price_basis=COALESCE(NULLIF(excluded.common_price_basis,''),learning_supplier_stats.common_price_basis),
                 last_seen_at=excluded.last_seen_at""",
            (key, canonical, domain, 1, 1 if human_approved else 0, p["price_basis"] or "", now),
        )
        conn.execute(
            """INSERT INTO learning_verified_examples(
                   product_id,mailbox_id,supplier_key,supplier_name,product_name,model,price_basis,evidence_label,human_approved,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(product_id) DO UPDATE SET
                 mailbox_id=excluded.mailbox_id,supplier_key=excluded.supplier_key,supplier_name=excluded.supplier_name,
                 product_name=excluded.product_name,model=excluded.model,price_basis=excluded.price_basis,
                 evidence_label=excluded.evidence_label,human_approved=MAX(learning_verified_examples.human_approved,excluded.human_approved),
                 updated_at=CURRENT_TIMESTAMP""",
            (product_id, p["mailbox_id"], key, canonical, p["product_name"] or "", p["model"] or "",
             p["price_basis"] or "", str(p["price_evidence"] or p["evidence_summary"] or "")[:600], 1 if human_approved else 0),
        )

def build_memory_context(mailbox_id: int, msg: dict, rag_results=None, max_chars: int = 5000) -> str:
    """Verified history is ADVISORY pattern memory, never evidence for the current record."""
    sender = str((msg or {}).get("sender") or "")
    domain = _sender_domain(sender)
    parts = []
    with db() as conn:
        supplier = None
        if domain:
            supplier = conn.execute(
                """SELECT canonical_name,confirmed_count,human_approved_count,common_price_basis
                   FROM learning_supplier_stats WHERE domain=? ORDER BY confirmed_count DESC LIMIT 1""",
                (domain,),
            ).fetchone()
        if supplier:
            parts.append(
                f"SUPPLIER MEMORY: {supplier['canonical_name']} has {supplier['confirmed_count']} confirmed and "
                f"{supplier['human_approved_count']} human-approved examples. Common price basis: {supplier['common_price_basis'] or 'varies'}."
            )
            recent = conn.execute(
                """SELECT product_name,model,price_basis,evidence_label FROM learning_verified_examples
                   WHERE supplier_key=? ORDER BY updated_at DESC LIMIT 3""",
                ((supplier["canonical_name"] or "").strip().lower(),),
            ).fetchall()
            for r in recent:
                parts.append(
                    f"VERIFIED SUPPLIER PATTERN: product={r['product_name'] or ''}; model={r['model'] or ''}; "
                    f"price_basis={r['price_basis'] or ''}; evidence_label={str(r['evidence_label'] or '')[:240]}"
                )
        negatives = conn.execute(
            "SELECT rule_text,hit_count FROM learning_negative_memory ORDER BY hit_count DESC,pattern_key LIMIT 7"
        ).fetchall()
        if negatives:
            parts.append("NEGATIVE MEMORY: " + " | ".join(str(r["rule_text"]) for r in negatives))

    if not parts: return ""
    header = (
        "HISTORICAL MEMORY — ADVISORY ONLY. Never copy a historical product or price into the current result. "
        "Current-email evidence is always required. Use memory only to understand supplier layout/labels and known mistakes.\n"
    )
    return (header + "\n".join(parts))[:max_chars]


def train_router_model(limit: int = 5000) -> dict:
    """Tiny lexical ML router trained only from verified outcomes.

    It never confirms/rejects a product; it only helps route work. Final accuracy is
    always controlled by the deterministic evidence firewall.
    """
    ensure_learning_schema()
    pos=Counter(); neg=Counter(); pos_docs=0; neg_docs=0
    with db() as conn:
        rows=conn.execute(
            """SELECT e.id,e.sender,e.subject,e.body,e.status,
                      EXISTS(SELECT 1 FROM products p WHERE p.email_id=e.id AND p.verification_status='CONFIRMED' AND p.export_eligible=1) AS has_product
               FROM emails e WHERE e.analysis_completed_at IS NOT NULL
                 AND (e.status IN ('NO PRODUCT','IRRELEVANT','DUPLICATE') OR
                      EXISTS(SELECT 1 FROM products p2 WHERE p2.email_id=e.id AND p2.verification_status='CONFIRMED' AND p2.export_eligible=1))
               ORDER BY e.id DESC LIMIT ?""", (int(limit),)
        ).fetchall()
        for r in rows:
            toks=set(_tokens(" ".join([r["sender"] or "",r["subject"] or "",(r["body"] or "")[:6000]])))
            if not toks: continue
            if int(r["has_product"] or 0): pos_docs+=1; pos.update(toks)
            else: neg_docs+=1; neg.update(toks)
        if pos_docs<15 or neg_docs<15:
            return {"trained":False,"positive_docs":pos_docs,"negative_docs":neg_docs,"reason":"need >=15 positive and >=15 negative verified emails"}
        weights={}
        for t in set(pos)|set(neg):
            pp=(pos[t]+1.0)/(pos_docs+2.0); pn=(neg[t]+1.0)/(neg_docs+2.0)
            w=math.log(pp/pn)
            if abs(w)>=0.18: weights[t]=round(max(-4.0,min(4.0,w)),5)
        weights=dict(sorted(weights.items(),key=lambda kv:abs(kv[1]),reverse=True)[:1600])
        model={"pos_docs":pos_docs,"neg_docs":neg_docs,"weights":weights}
        meta={"trained_at":datetime.now().astimezone().isoformat(),"features":len(weights),"positive_docs":pos_docs,"negative_docs":neg_docs}
        conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(_MODEL_KEY,json.dumps(model,separators=(",",":"))))
        conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(_MODEL_META_KEY,json.dumps(meta,separators=(",",":"))))
    return {"trained":True,**meta}


def _load_router_model() -> dict:
    with db() as conn:
        row=conn.execute("SELECT value FROM app_settings WHERE key=?",(_MODEL_KEY,)).fetchone()
    if not row or not row["value"]: return {}
    try: return json.loads(row["value"])
    except Exception: return {}


def router_score(sender: str, subject: str, body: str) -> float | None:
    model=_load_router_model(); weights=model.get("weights") or {}
    if not weights: return None
    pos_docs=float(model.get("pos_docs") or 1); neg_docs=float(model.get("neg_docs") or 1)
    logit=math.log((pos_docs+1.0)/(neg_docs+1.0))
    for t in set(_tokens(" ".join([sender or "",subject or "",(body or "")[:6000]]))):
        logit+=float(weights.get(t,0.0))
    logit=max(-12.0,min(12.0,logit))
    return 1.0/(1.0+math.exp(-logit))


def health() -> dict:
    ensure_learning_schema()
    with db() as conn:
        meta=conn.execute("SELECT value FROM app_settings WHERE key=?",(_MODEL_META_KEY,)).fetchone()
        gold=int(conn.execute("SELECT COUNT(*) c FROM learning_verified_examples WHERE human_approved=1").fetchone()["c"])
        confirmed=int(conn.execute("SELECT COUNT(*) c FROM learning_verified_examples").fetchone()["c"])
        suppliers=int(conn.execute("SELECT COUNT(*) c FROM learning_supplier_stats").fetchone()["c"])
        negative_hits=int(conn.execute("SELECT COALESCE(SUM(hit_count),0) c FROM learning_negative_memory").fetchone()["c"])
        cached=int(conn.execute("SELECT COUNT(*) c FROM learning_source_cache").fetchone()["c"])
    try: model_meta=json.loads(meta["value"]) if meta and meta["value"] else {}
    except Exception: model_meta={}
    return {"enabled":True,"storage":"SQLite relational memory (no RAG / no embeddings)",
            "gold_memory":gold,"confirmed_memory":confirmed,"supplier_profiles":suppliers,
            "negative_memory_hits":negative_hits,"duplicate_cache_entries":cached,"router_model":model_meta,
            "rag_removed":True,"final_authority":"Python deterministic evidence firewall"}
