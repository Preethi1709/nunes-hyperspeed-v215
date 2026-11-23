from __future__ import annotations

import hashlib
import re
from typing import Iterable

# NUNES Product Intelligence V21.4 REAL HYPERSPEED
# -------------------------------------------------
# Deterministic, evidence-first processing. This module performs no network
# calls and has no LLM, vector, embedding or semantic-retrieval dependency.
# AI is an exception service outside this module; Python remains final authority.

PROCUREMENT_WORDS = (
    "quotation", "quote", "commercial offer", "offer", "price list", "price",
    "unit price", "unit rate", "rate", "rfq", "requirement", "enquiry", "inquiry",
    "purchase", "supplier", "vendor", "proforma", "proposal", "revised quotation",
    "best price", "net price", "discount", "model", "specification", "datasheet",
    "technical commercial", "technical-commercial", "our offer", "please find attached",
)

COMMERCIAL_ATTACHMENT_WORDS = (
    "quote", "quotation", "offer", "price", "commercial", "proposal", "proforma",
    "rfq", "requirement", "rate", "product", "catalog", "catalogue", "estimate",
)

DOCUMENT_EXTENSIONS = (".pdf", ".xls", ".xlsx", ".csv", ".tsv", ".doc", ".docx", ".txt")

# Eradication is intentionally conservative. These are high-confidence classes
# that cannot contain a current supplier product-price quotation unless there is
# independent commercial evidence or a business attachment.
STRONG_IRRELEVANT_PATTERNS = (
    r"\bone[- ]time password\b",
    r"\botp\b.{0,30}\b(?:code|login|sign[- ]?in|verification)\b",
    r"\bverification code\b",
    r"\bsecurity alert\b",
    r"\bnew sign[- ]?in\b",
    r"\bpassword (?:changed|reset|expired)\b",
    r"\blogin attempt\b",
    r"\bcalendar (?:notification|reminder)\b",
    r"\bmeeting reminder\b",
    r"\bjob alert\b",
    r"\bnew job(?:s)? for you\b",
    r"\bapplication (?:received|status|update)\b",
    r"\bsocial (?:media )?notification\b",
    r"\b(?:facebook|instagram|linkedin|x|twitter) notification\b",
    r"\bnewsletter\b.{0,80}\bunsubscribe\b",
)

CURRENCY_RE = re.compile(
    r"(?i)(?P<currency>₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)\s*[:@=-]?\s*(?P<value>\d[\d,]*(?:\.\d{1,2})?)"
)
PRICE_LABEL_RE = re.compile(
    r"(?i)\b(?:unit\s*price|unit\s*rate|purchase\s*price|net\s*price|net\s*rate|"
    r"offer(?:ed)?\s*price|discounted\s*price|final\s*(?:price|rate)|best\s*price|"
    r"special\s*price|our\s*price|price|rate)\b\s*[:@=-]?\s*"
    r"(?:₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)?\s*(?P<value>\d[\d,]*(?:\.\d{1,2})?)"
)
FINAL_PRICE_LABEL_RE = re.compile(
    r"(?i)\b(?:discounted\s*price|final\s*(?:price|rate)|net\s*(?:price|rate)|purchase\s*price|"
    r"offer(?:ed)?\s*price|best\s*price|special\s*price|our\s*price|unit\s*(?:price|rate)|price|rate)\b"
    r"\s*[:@=-]?\s*(?:₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)?\s*(?P<value>\d[\d,]*(?:\.\d{1,2})?)"
)
ORIGINAL_LABEL_RE = re.compile(
    r"(?i)\b(?:original\s*price|list\s*price|mrp|basic\s*(?:price|rate)|quoted\s*rate)\b"
    r"\s*[:@=-]?\s*(?:₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)?\s*(?P<value>\d[\d,]*(?:\.\d{1,2})?)"
)
DISCOUNT_RE = re.compile(r"(?i)\bdiscount(?:ed)?(?:\s*(?:percent|%))?\b\s*[:@=-]?\s*(?P<value>\d+(?:\.\d+)?)\s*%")

# Common Indian quotation shorthand such as "12500/-" or "12500/- each".
# It is considered a commercial signal, but deterministic confirmation still
# requires the independent evidence firewall to accept the surrounding context.
COMPACT_PRICE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9.])(?P<value>\d{2,3}(?:,\d{3})+|\d{4,9})(?:\.\d{1,2})?\s*(?:/-|/\s*-|each\b|per\s*unit\b)"
)

FALSE_PRICE_CONTEXT = (
    "qty", "quantity", "gst", "cgst", "sgst", "igst", "tax", "freight", "packing",
    "installation", "commissioning", "subtotal", "grand total", "total amount",
    "hsn", "sac", "serial", "phone", "mobile", "account", "resolution", "accuracy",
    "range", "capacity", "flow", "voltage", "current", "frequency", "warranty",
)

PRODUCT_BAD_PATTERNS = (
    r"^\s*\d+\s*$",
    r"^\s*[-+]?\d+(?:\.\d+)?\s*(?:~|to|-)\s*[-+]?\d+(?:\.\d+)?",
    r"^\s*\d+\.\s*(?:heating systems?|terms?|conditions?|tax|gst|freight|packing)\b",
    r"^\s*(?:subtotal|grand total|total|gst|cgst|sgst|igst|freight|packing|installation)\b",
    r"^\s*\d{1,2}[-/](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[-/]\d{2,4}\s*$",
)

GENERIC_MAIL_DOMAINS = {
    "gmail", "googlemail", "yahoo", "ymail", "outlook", "hotmail", "live",
    "icloud", "protonmail", "rediffmail", "aol", "zoho",
}

GENERIC_NON_PRODUCT_NAMES = {
    "original", "original price", "list", "list price", "mrp", "basic",
    "basic price", "basic rate", "quoted", "quoted rate", "price", "rate",
    "unit price", "unit rate", "net price", "net rate", "discount",
    "offer", "quote", "quotation", "proposal",
    "subtotal", "grand total", "total", "amount", "tax", "gst",
}

COMPANY_LINE_RE = re.compile(
    r"(?i)\b(?:pvt\.?\s*ltd\.?|private\s+limited|limited|ltd\.?|llp|inc\.?|corp\.?|corporation|"
    r"enterprises?|instruments?|instrumentation|technolog(?:y|ies)|scientific|engineering|"
    r"systems?|solutions?|industries|traders?|equipments?|equipment|automation)\b"
)


def _norm(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip())


def _norm_key(v: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _norm(v).lower()).strip()


def _price(v: object):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return x if x > 0 else None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v).replace("₹", ""), re.I)
    if not m:
        return None
    try:
        x = float(m.group(0).replace(",", ""))
        return x if x > 0 else None
    except Exception:
        return None


def _percent(v: object):
    if v in (None, ""):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        x = float(m.group())
        return x if 0 <= x < 100 else None
    except Exception:
        return None


def _currency_from(text: str) -> str:
    low = (text or "").lower()
    if "usd" in low or "$" in text:
        return "USD"
    if "eur" in low or "€" in text:
        return "EUR"
    if "gbp" in low or "£" in text:
        return "GBP"
    if "aed" in low:
        return "AED"
    return "INR"


def _valid_product_name(name: str) -> bool:
    name = _norm(name).strip("|:-–—•\t ")
    if len(name) < 3 or len(name) > 220:
        return False
    if not re.search(r"[A-Za-z]", name):
        return False
    if any(re.search(p, name, re.I) for p in PRODUCT_BAD_PATTERNS):
        return False
    low = name.lower()
    if low in GENERIC_NON_PRODUCT_NAMES:
        return False
    if any(x in low for x in ("terms & conditions", "terms and conditions", "bank details", "payment terms")):
        return False
    if re.match(r"(?i)^\s*(?:range|accuracy|resolution|capacity|voltage|frequency|qty|quantity)\s*[:=-]", name):
        return False
    return True


def _price_context_ok(line: str, match_start: int) -> bool:
    context = (line[max(0, match_start - 45): match_start + 20] or "").lower()
    if re.search(r"\b(?:unit\s*(?:price|rate)|net\s*(?:price|rate)|offer(?:ed)?\s*price|purchase\s*price|price|rate|each|per\s*unit)\b", context, re.I):
        return True
    return not any(tok in context for tok in FALSE_PRICE_CONTEXT)


def _finalize(p: dict) -> dict:
    p = dict(p or {})
    original = _price(p.get("original_price"))
    discount = _percent(p.get("discount_percent"))
    final = _price(p.get("purchase_price"))
    p["original_price"] = original
    p["discount_percent"] = discount
    p["purchase_price"] = final
    if final is None and original is not None and discount is not None:
        p["purchase_price"] = round(original * (1.0 - discount / 100.0), 2)
        p["price_basis"] = p.get("price_basis") or "CALCULATED_DISCOUNTED_UNIT"
    return p


def _clean_supplier_name(value: str) -> str:
    value = _norm(value).strip("<>|,;:- ")
    value = re.sub(r"(?i)^(?:from|supplier|vendor|company)\s*[:=-]?\s*", "", value).strip()
    if "@" in value and " " not in value:
        return ""
    if len(value) < 3 or len(value) > 100:
        return ""
    if re.match(r"^\d+[.)-]", value):
        return ""
    return value


def infer_supplier(sender: str = "", body: str = "") -> str:
    """Deterministically derive a supplier string that remains source-evidenced.

    Priority: explicit Supplier/Company label -> sender display name -> signature
    company line -> non-generic sender domain token. No historical memory is used.
    """
    text = body or ""
    for label in (r"supplier(?:\s*name)?", r"vendor(?:\s*name)?", r"company(?:\s*name)?"):
        m = re.search(rf"(?im)^\s*{label}\s*[:=-]\s*(.+?)\s*$", text)
        if m:
            value = _clean_supplier_name(m.group(1))
            if value:
                return value

    s = _norm(sender)
    if s:
        m = re.match(r"^\s*([^<]{3,100})\s*<[^>]+>\s*$", s)
        if m:
            value = _clean_supplier_name(m.group(1).strip('"\''))
            if value and value.lower() not in {"sales", "info", "purchase", "support", "admin"}:
                return value

    # Search only the last signature-like block to avoid taking a numbered
    # product heading (e.g. "6. Heating Systems") as supplier.
    lines = [_norm(x) for x in text.splitlines() if _norm(x)]
    for line in reversed(lines[-18:]):
        if len(line) <= 100 and COMPANY_LINE_RE.search(line) and not re.match(r"^\d+[.)-]", line):
            value = _clean_supplier_name(line)
            if value:
                return value

    email_m = re.search(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+)", s, re.I)
    if email_m:
        host = email_m.group(1).lower().split(":", 1)[0]
        parts = [p for p in host.split(".") if p]
        if len(parts) >= 2:
            stem = parts[-2]
            if stem not in GENERIC_MAIL_DOMAINS and len(stem) >= 3:
                return stem.replace("-", " ").replace("_", " ").title()
    return ""


def definitely_irrelevant(subject: str, body: str, attachment_names: str = "") -> bool:
    return relevance_gate(subject, body, attachment_names)["decision"] == "IRRELEVANT"


def relevance_gate(subject: str, body: str, attachment_names: str = "") -> dict:
    text = f"{subject or ''}\n{body or ''}\n{attachment_names or ''}"
    low = text.lower()
    names = (attachment_names or "").lower()
    has_doc = any(ext in names for ext in DOCUMENT_EXTENSIONS)
    proc_hits = [w for w in PROCUREMENT_WORDS if w in low]
    commercial_price = has_commercial_price_signal(text)
    commercial_attachment = has_doc and (any(w in names for w in COMMERCIAL_ATTACHMENT_WORDS) or bool(proc_hits))
    strong_noise = any(re.search(p, low, re.I | re.S) for p in STRONG_IRRELEVANT_PATTERNS)

    # A plausible business document or explicit commercial price always wins
    # over an irrelevant-looking subject/footer.
    if strong_noise and not has_doc and not proc_hits and not commercial_price:
        return {"decision": "IRRELEVANT", "score": -100, "reason": "high-confidence non-procurement system/notification message"}
    if commercial_attachment or commercial_price or proc_hits:
        score = 20 + min(55, len(proc_hits) * 7) + (25 if commercial_price else 0) + (20 if has_doc else 0)
        return {"decision": "RELEVANT", "score": min(100, score), "reason": "procurement/commercial evidence"}
    if has_doc:
        return {"decision": "UNCERTAIN", "score": 30, "reason": "business attachment requires Python Rescue"}
    return {"decision": "UNCERTAIN", "score": 10, "reason": "no explicit product-price evidence in the fast relevance pass"}


def local_relevance(subject: str, body: str, attachment_names: str = "") -> bool:
    return relevance_gate(subject, body, attachment_names)["decision"] != "IRRELEVANT"


def candidate_score(subject: str, body: str, attachment_names: str = "") -> int:
    return int(relevance_gate(subject, body, attachment_names)["score"])


def has_commercial_price_signal(text: str) -> bool:
    text = text or ""
    for m in CURRENCY_RE.finditer(text):
        if _price_context_ok(text, m.start()):
            return True
    for m in PRICE_LABEL_RE.finditer(text):
        if _price_context_ok(text, m.start()):
            return True
    for m in COMPACT_PRICE_RE.finditer(text):
        if _price_context_ok(text, m.start()):
            return True
    return False


def _label_value(text: str, label: str) -> str:
    m = re.search(rf"(?im)^\s*{label}\s*[:\-]\s*(.+?)\s*$", text or "")
    return _norm(m.group(1)) if m else ""


def _explicit_labeled_candidate(text: str, supplier: str = "") -> list[dict]:
    name = _label_value(text, r"product(?:\s*name)?") or _label_value(text, r"item(?:\s*name|\s*description)?")
    if not _valid_product_name(name):
        return []
    model = _label_value(text, r"model(?:\s*(?:no\.?|number))?")
    specs = _label_value(text, r"specifications?")
    supplier = _label_value(text, r"supplier(?:\s*name)?") or supplier
    original_m = ORIGINAL_LABEL_RE.search(text or "")
    discount_m = DISCOUNT_RE.search(text or "")

    # Prefer a final/net/discounted/unit label that is not the original/list/MRP
    # occurrence. If original + discount exists with no separate final price,
    # deterministic arithmetic derives purchase_price.
    purchase_m = None
    for m in FINAL_PRICE_LABEL_RE.finditer(text or ""):
        prefix = (text or "")[max(0, m.start()-18):m.start()].lower()
        full = m.group(0).lower()
        if any(x in prefix + full for x in ("original price", "list price", "mrp", "basic price", "basic rate", "quoted rate")):
            continue
        purchase_m = m
        break
    # Indian quotation shorthand such as "12,500/- each" is still explicit
    # unit evidence when a Product/Item label anchors the product in the same
    # source. Do this here rather than allowing a generic line like "Offer" to
    # become the product name.
    if purchase_m is None:
        purchase_m = COMPACT_PRICE_RE.search(text or "")

    p = {
        "product_name": name,
        "model": model,
        "specifications": specs,
        "supplier_name": supplier,
        "original_price": original_m.group("value") if original_m else None,
        "discount_percent": discount_m.group("value") if discount_m else None,
        "purchase_price": purchase_m.group("value") if purchase_m else None,
        "currency": _currency_from(text),
        "confidence": 0.99,
        "price_basis": "EXPLICIT_UNIT_PRICE" if purchase_m else "CALCULATED_DISCOUNTED_UNIT",
        "price_evidence": _norm(purchase_m.group(0) if purchase_m else "")[:320],
    }
    p = _finalize(p)
    return [p] if p.get("purchase_price") is not None else []


def _line_price_matches(line: str):
    matches = list(CURRENCY_RE.finditer(line))
    if matches:
        return matches
    lm = PRICE_LABEL_RE.search(line)
    if lm:
        return [lm]
    cm = COMPACT_PRICE_RE.search(line)
    return [cm] if cm else []


def _same_line_candidates(text: str, supplier: str = "", source_type: str = "EMAIL", source_filename: str = "") -> list[dict]:
    out: list[dict] = []
    seen = set()
    for raw in (text or "").splitlines():
        line = _norm(raw)
        if len(line) < 5 or len(line) > 700:
            continue
        low = line.lower()
        if any(x in low for x in ("grand total", "subtotal", "gst total", "tax total", "bank account")):
            continue
        matches = _line_price_matches(line)
        for m in matches[:2]:
            if not m or not _price_context_ok(line, m.start()):
                continue
            value = _price(m.groupdict().get("value"))
            if value is None:
                continue
            left = line[:m.start()].strip(" |:-–—\t")
            left = re.sub(r"^\s*\d{1,3}\s*[.)-]\s*", "", left)
            left = re.sub(r"(?i)\b(?:unit\s*price|price|rate|net\s*price|offer(?:ed)?\s*price)\s*[:@=-]?\s*$", "", left).strip(" |:-")
            if not _valid_product_name(left):
                continue
            key = (_norm_key(left), value)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "product_name": left[:220], "model": "", "specifications": "",
                "supplier_name": supplier, "original_price": None, "discount_percent": None,
                "purchase_price": value, "currency": _currency_from(line),
                "confidence": 0.90, "price_basis": "EXPLICIT_UNIT_PRICE",
                "price_evidence": line[:360], "source_type": source_type,
                "source_filename": source_filename,
            })
    return out


def _header_table_candidates(text: str, supplier: str = "", source_type: str = "PDF", source_filename: str = "") -> list[dict]:
    lines = [x.rstrip() for x in (text or "").splitlines()]
    out: list[dict] = []
    for i, raw in enumerate(lines):
        header = _norm(raw).lower()
        if not ("price" in header or "rate" in header):
            continue
        if not any(x in header for x in ("product", "item", "description", "particular")):
            continue
        splitter = r"\s*\|\s*" if "|" in raw else (r"\t+" if "\t" in raw else r"\s{2,}")
        cols = [c.strip().lower() for c in re.split(splitter, raw.strip()) if c.strip()]
        if len(cols) < 2:
            continue
        name_idx = next((j for j, c in enumerate(cols) if any(k in c for k in ("product", "item", "description", "particular"))), None)
        price_idx = next((j for j, c in enumerate(cols) if any(k in c for k in ("unit price", "unit rate", "net price", "price", "rate"))), None)
        model_idx = next((j for j, c in enumerate(cols) if "model" in c), None)
        qty_idx = next((j for j, c in enumerate(cols) if c in ("qty", "quantity")), None)
        if name_idx is None or price_idx is None or (qty_idx is not None and price_idx == qty_idx):
            continue
        for row in lines[i+1:i+35]:
            if not _norm(row):
                continue
            cells = [c.strip() for c in re.split(splitter, row.strip())]
            if max(name_idx, price_idx) >= len(cells):
                continue
            name = cells[name_idx]
            if not _valid_product_name(name):
                continue
            price_cell = cells[price_idx]
            value = _price(price_cell)
            if value is None:
                continue
            lowrow = row.lower()
            if any(x in lowrow for x in ("subtotal", "grand total", "gst", "tax total")):
                continue
            out.append({
                "product_name": _norm(name)[:220],
                "model": _norm(cells[model_idx])[:120] if model_idx is not None and model_idx < len(cells) else "",
                "specifications": "", "supplier_name": supplier,
                "original_price": None, "discount_percent": None, "purchase_price": value,
                "currency": _currency_from(price_cell or row), "confidence": 0.99,
                "price_basis": "EXPLICIT_UNIT_PRICE", "price_evidence": _norm(row)[:360],
                "source_type": source_type, "source_filename": source_filename,
            })
        if out:
            break
    return out


def _dedupe(rows: Iterable[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for raw in rows or []:
        p = _finalize(raw)
        if not _valid_product_name(str(p.get("product_name") or "")) or p.get("purchase_price") is None:
            continue
        key = (_norm_key(p.get("product_name")), _norm_key(p.get("model")), round(float(p.get("purchase_price")), 4))
        old = best.get(key)
        if old is None or float(p.get("confidence") or 0) > float(old.get("confidence") or 0):
            best[key] = p
    return list(best.values())


def extract_main(subject: str, body: str, sender: str = "") -> list[dict]:
    """Ultra-fast body-only deterministic pass. No attachments, OCR or AI."""
    text = body or ""
    supplier = infer_supplier(sender, text)
    rows: list[dict] = []
    rows.extend(_explicit_labeled_candidate(text, supplier))
    rows.extend(_same_line_candidates(text, supplier, "EMAIL", ""))
    return _dedupe(rows)


def local_structured_extract(subject: str, body: str, sender: str = "") -> list[dict]:
    return extract_main(subject, body, sender)


def extract_rescue(subject: str, body: str, attachment_texts: list[dict] | None = None,
                   pdf_products: list[dict] | None = None, sender: str = "") -> list[dict]:
    """Bounded second Python pass after the fast pass is unresolved.

    It considers attachment text, reconstructed OCR text and tables. Every row
    still passes the independent accuracy_service evidence firewall.
    """
    supplier = infer_supplier(sender, body or "")
    rows: list[dict] = []
    rows.extend(extract_main(subject, body, sender))
    for raw in (pdf_products or []):
        p = dict(raw or {})
        if not p.get("supplier_name"):
            p["supplier_name"] = supplier
        rows.append(p)
    for item in attachment_texts or []:
        name = str(item.get("name") or "")
        text = str(item.get("text") or "")
        if not text or text.startswith("["):
            continue
        source = "PDF" if name.lower().endswith(".pdf") else "ATTACHMENT"
        # Prefer an explicit supplier in the attachment; otherwise retain the
        # supplier already evidenced by the email body before falling back to
        # the sender display name.  This prevents a shortened sender label from
        # replacing a stronger legal/company name in the current source.
        attachment_supplier = infer_supplier("", text) or supplier or infer_supplier(sender, text)
        rows.extend(_explicit_labeled_candidate(text, attachment_supplier))
        table_rows = _header_table_candidates(text, attachment_supplier, source, name)
        rows.extend(table_rows)
        # Structured tables already encode row/column relationships. Running the
        # generic same-line parser over those same rows creates inferior duplicate
        # candidates such as "Product | Model | Qty" and needlessly escalates AI.
        if not table_rows:
            rows.extend(_same_line_candidates(text, attachment_supplier, source, name))
    return _dedupe(rows)


def make_key(message_id: str, p: dict) -> str:
    price = _price((p or {}).get("purchase_price"))
    price_key = "" if price is None else f"{price:.4f}"
    raw = "|".join([
        _norm_key((p or {}).get("product_name")),
        _norm_key((p or {}).get("model")),
        price_key,
        _norm_key((p or {}).get("supplier_name")),
    ])
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def architecture_summary() -> dict:
    return {
        "version": "21.4",
        "mode": "REAL_HYPERSPEED_PYTHON_FIRST",
        "main": "Python relevance + fast extraction + evidence verification",
        "rescue": "Python deep rules + attachment parsing + dedicated OCR",
        "ai": "Exception-only qwen3:1.7b -> qwen3:4b / qwen3-vl:4b",
        "final_authority": "Python deterministic source-evidence firewall",
        "retrieval_memory": "SQLite relational learning only",
    }
