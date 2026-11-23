from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

BUILD_ID = "V21_5_SURREAL_HYPERSPEED"

# Stage 0 is deliberately conservative: a false positive here would hide a useful
# supplier email.  Sender/category noise is only terminal when there is no
# independent procurement/commercial signal and no plausible business document.
_OBVIOUS_IRRELEVANT = (
    r"\bone[- ]time password\b",
    r"\botp\b.{0,40}\b(?:code|login|sign[- ]?in|verification|valid)\b",
    r"\bverification code\b",
    r"\bsecurity alert\b",
    r"\bnew sign[- ]?in\b",
    r"\blogin attempt\b",
    r"\bpassword (?:changed|reset|expired)\b",
    r"\bcalendar (?:notification|reminder)\b",
    r"\bmeeting reminder\b",
    r"\bsocial (?:media )?notification\b",
    r"\b(?:instagram|facebook|twitter|linkedin)\b.{0,80}\b(?:notification|liked|commented|followed|mentioned|reel|post)\b",
    r"\bnewsletter\b.{0,180}\bunsubscribe\b",
    r"\bpromotional?\b.{0,180}\bunsubscribe\b",
)

_PROCUREMENT_SIGNAL = re.compile(
    r"(?i)\b(?:quotation|quote|qtn|commercial\s+offer|offer|proforma|pro\s*forma|"
    r"price\s*list|unit\s*price|unit\s*rate|purchase\s*price|net\s*price|best\s*price|"
    r"rfq|request\s+for\s+quotation|purchase\s+order|supplier|vendor|enquiry|inquiry|requirement)\b"
)

_BUSINESS_EXTENSIONS = {".pdf", ".xls", ".xlsx", ".csv", ".tsv", ".doc", ".docx", ".txt"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

_FILENAME_COMMERCIAL = re.compile(
    r"(?i)(?:^|[ _\-.])(quotation|quote|qtn|offer|commercial|proforma|pro[- _]?forma|pi|"
    r"price[- _]?list|rate[- _]?list|estimate|proposal|rfq)(?:[ _\-.]|$)"
)

# Strong current-source price evidence.  Technical labels such as sampling rate,
# baud rate, flow rate, accuracy, resolution, range, quantity and totals alone do
# not qualify as a unit purchase price signal.
_CURRENCY_AMOUNT = re.compile(
    r"(?i)(?:₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)\s*[:@=\-]?\s*\d[\d,]*(?:\.\d{1,2})?"
)
_PRICE_LABEL_AMOUNT = re.compile(
    r"(?ix)\b(?:unit\s*price|unit\s*rate|purchase\s*price|net\s*price|net\s*rate|"
    r"offer(?:ed)?\s*price|best\s*price|special\s*price|our\s*price|sales\s*price|"
    r"basic\s*price|basic\s*rate|list\s*price|mrp|price|amount)\b"
    r"(?!\s*(?:range|resolution|accuracy))[^\n\r\d]{0,36}"
    r"(?:₹|rs\.?|inr|usd|eur|gbp|aed|\$|€|£)?\s*\d[\d,]*(?:\.\d{1,2})?"
)
_COMPACT_COMMERCIAL = re.compile(r"(?i)(?<![A-Za-z0-9.])(?:\d{2,3}(?:,\d{3})+|\d{4,9})(?:\.\d{1,2})?\s*(?:/-|each\b|per\s+unit\b)")
_COMMERCIAL_HEADERS = re.compile(r"(?i)\b(?:quotation|commercial\s+offer|proforma|price\s*list|sales\s*price|unit\s*price|unit\s*rate|subtotal|grand\s*total|gst|igst|cgst|sgst)\b")
_TECHNICAL_FALSE_PRICE_LINE = re.compile(
    r"(?i)\b(?:sampling\s+rate|flow\s+rate|baud\s+rate|refresh\s+rate|accuracy|resolution|"
    r"measuring\s+range|measurement\s+range|temperature\s+range|humidity\s+range|frequency|"
    r"capacity|speed|rpm|lpm|ppm|mmhg|lux|db|\%\s*rh)\b"
)

TERMINAL_TASK_STATES = {"DONE", "SKIPPED", "FAILED"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split())


def _attachment_names_text(attachments: list[dict] | None) -> str:
    return " | ".join(str(a.get("filename") or "") for a in (attachments or []))


def stage0_filter(sender: str, subject: str, body: str, attachments: list[dict] | None = None) -> dict:
    """Return a conservative Stage-0 decision.

    Only CERTAIN_IRRELEVANT is terminal.  Anything ambiguous advances to Stage 1.
    """
    names = _attachment_names_text(attachments)
    text = "\n".join([_norm(sender), _norm(subject), _norm(body)[:8000], names])
    low = text.lower()
    has_procurement = bool(_PROCUREMENT_SIGNAL.search(text))
    has_business_attachment = any(Path(str(a.get("filename") or "")).suffix.lower() in _BUSINESS_EXTENSIONS for a in (attachments or []))
    filename_commercial = bool(_FILENAME_COMMERCIAL.search(names))

    # Explicit commercial context always wins over social/newsletter wording.
    if has_procurement or filename_commercial:
        return {"decision": "CONTINUE", "reason": "commercial/procurement signal present", "score": 1.0}

    for pattern in _OBVIOUS_IRRELEVANT:
        if re.search(pattern, text, flags=re.I | re.S):
            if has_business_attachment:
                return {"decision": "CONTINUE", "reason": "irrelevant pattern overridden by business attachment", "score": 0.25}
            return {"decision": "CERTAIN_IRRELEVANT", "reason": f"Stage 0 matched: {pattern}", "score": 0.0}

    # High-confidence sender/category-like noise, but never discard when a business
    # document is attached.
    social_domains = ("instagram.com", "facebookmail.com", "facebook.com", "twitter.com", "x.com")
    if any(d in low for d in social_domains) and not has_business_attachment:
        return {"decision": "CERTAIN_IRRELEVANT", "reason": "Stage 0 social-network notification", "score": 0.0}

    return {"decision": "CONTINUE", "reason": "not certainly irrelevant", "score": 0.5}


def lane_for_filename(filename: str) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    if ext == ".pdf":
        return "PDF"
    if ext in {".xls", ".xlsx", ".csv", ".tsv"}:
        return "TABLE"
    if ext in {".doc", ".docx", ".txt", ".rtf"}:
        return "DOC"
    if ext in _IMAGE_EXTENSIONS:
        return "OCR"
    return "OTHER"


def classify_stage1_tasks(msg: dict) -> list[dict]:
    """Stage 1 only classifies/segregates. It performs no deep extraction."""
    tasks: list[dict] = []
    # Every surviving email gets a cheap body lane. It can finish in milliseconds
    # and never waits behind PDF/OCR work.
    tasks.append({
        "task_key": "BODY",
        "lane": "TEXT",
        "filename": "",
        "attachment_id": "",
        "mime_type": "text/html",
    })
    for idx, a in enumerate(msg.get("attachments") or []):
        filename = str(a.get("filename") or f"attachment_{idx+1}")
        lane = lane_for_filename(filename)
        tasks.append({
            "task_key": f"ATT:{idx}:{a.get('attachmentId') or filename}",
            "lane": lane,
            "filename": filename,
            "attachment_id": str(a.get("attachmentId") or ""),
            "mime_type": str(a.get("mimeType") or a.get("mime_type") or ""),
        })
    return tasks


def filename_priority(filename: str) -> int:
    return 40 if _FILENAME_COMMERCIAL.search(str(filename or "")) else 0


def _commercial_lines(text: str) -> list[str]:
    hits: list[str] = []
    for raw in str(text or "").splitlines():
        line = _norm(raw)
        if not line:
            continue
        # Currency itself is strong even if the same line contains a technical word,
        # because values such as "INR 39,312" are explicit money evidence.
        if _CURRENCY_AMOUNT.search(line) or _COMPACT_COMMERCIAL.search(line):
            hits.append(line)
            continue
        if _PRICE_LABEL_AMOUNT.search(line) and not _TECHNICAL_FALSE_PRICE_LINE.search(line):
            hits.append(line)
    return hits


def commercial_probe_text(text: str, filename: str = "") -> dict:
    text = str(text or "")
    hits = _commercial_lines(text)
    headers = list(dict.fromkeys(m.group(0) for m in _COMMERCIAL_HEADERS.finditer(text)))[:12]
    score = filename_priority(filename)
    if hits:
        score += 70
    if headers:
        score += 15
    # A filename/heading saying quotation is useful for prioritization, but without
    # any monetary evidence the document is still skipped from product-price deep work.
    has_money = bool(hits)
    return {
        "commercial": has_money,
        "score": min(100, score),
        "money_hits": hits[:30],
        "headers": headers,
        "filename_priority": filename_priority(filename),
        "reason": "explicit commercial price evidence" if has_money else "no explicit commercial price evidence",
    }


def probe_pdf_bytes(content: bytes, filename: str = "", max_pages: int = 120, max_chars: int = 180000) -> dict:
    """Cheap PDF sweep before table parsing/OCR.

    Extracts embedded text only. It does not run OCR and does not parse quotation
    tables.  It records pages with explicit monetary evidence so Stage 2 can deep
    parse only a narrow page window.
    """
    try:
        try:
            from pypdf import PdfReader
        except Exception:
            from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = min(len(reader.pages), int(max_pages))
        chunks: list[str] = []
        hit_pages: list[int] = []
        page_probes: list[dict] = []
        total_chars = 0
        for idx in range(pages):
            try:
                text = reader.pages[idx].extract_text() or ""
            except Exception:
                text = ""
            text = text[:30000]
            total_chars += len(text)
            p = commercial_probe_text(text, filename if idx == 0 else "")
            if p["commercial"]:
                hit_pages.append(idx + 1)
            if text.strip():
                chunks.append(f"[PAGE {idx+1}]\n{text}")
            page_probes.append({"page": idx + 1, "commercial": p["commercial"], "score": p["score"], "hits": p["money_hits"][:5]})
            if sum(len(x) for x in chunks) >= max_chars:
                break
        joined = "\n\n".join(chunks)[:max_chars]
        full_probe = commercial_probe_text(joined, filename)
        image_only = total_chars < 40
        return {
            "ok": True,
            "page_count": len(reader.pages),
            "pages_scanned": len(page_probes),
            "text": joined,
            "text_chars": total_chars,
            "image_only": image_only,
            "commercial": bool(hit_pages or full_probe["commercial"]),
            "score": full_probe["score"],
            "hit_pages": sorted(set(hit_pages)),
            "page_probes": page_probes,
            "reason": "image/scanned PDF" if image_only else full_probe["reason"],
        }
    except Exception as exc:
        return {"ok": False, "commercial": False, "image_only": False, "text": "", "hit_pages": [], "error": str(exc)[:500]}


def target_pdf_bytes(content: bytes, hit_pages: list[int] | None, radius: int = 1, max_target_pages: int = 12) -> bytes:
    """Create a small PDF containing price-hit pages plus nearby context."""
    hit_pages = [int(x) for x in (hit_pages or []) if int(x) > 0]
    if not hit_pages:
        return content
    try:
        try:
            from pypdf import PdfReader, PdfWriter
        except Exception:
            from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(content))
        selected: set[int] = set()
        for p in hit_pages:
            zero = p - 1
            for idx in range(max(0, zero-radius), min(len(reader.pages), zero+radius+1)):
                selected.add(idx)
        ordered = sorted(selected)[:max_target_pages]
        if not ordered or len(ordered) >= len(reader.pages):
            return content
        writer = PdfWriter()
        for idx in ordered:
            writer.add_page(reader.pages[idx])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:
        return content


def stage1_summary(tasks: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for t in tasks:
        lane = str(t.get("lane") or "OTHER")
        counts[lane] = counts.get(lane, 0) + 1
    return {"task_count": len(tasks), "lanes": counts}


def task_result(products: list[dict] | None = None, evidence_text: str = "", commercial: bool = False,
                needs_ai: bool = False, reason: str = "", meta: dict | None = None) -> str:
    payload = {
        "products": products or [],
        "commercial": bool(commercial),
        "needs_ai": bool(needs_ai),
        "reason": reason,
        "meta": meta or {},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def architecture_summary() -> dict:
    return {
        "version": "21.5",
        "mode": "SURREAL_HYPERSPEED",
        "stage0": "Conservative obvious-irrelevant email filter",
        "stage1": "Format classification and independent queue segregation",
        "stage2": "TEXT/PDF/TABLE/DOC/OCR specialist processors with cheap commercial pre-probes",
        "rescue": "Cross-document deterministic Python rescue only after specialist lanes",
        "ai": "Exception-only local Qwen models",
        "final_authority": "Python deterministic current-source evidence firewall",
        "principle": "Do not deeply read a document until cheap evidence justifies it",
    }
