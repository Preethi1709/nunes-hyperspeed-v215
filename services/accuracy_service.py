import re
from urllib.parse import quote

PRICE_RE = re.compile(r"(?i)(?P<currency>₹|rs\.?|inr|usd|\$|eur|€|gbp|£|aed)?\s*[:.-]?\s*(?P<value>\d[\d,]*(?:\.\d+)?)")
GOOD_PRICE_LABELS = (
    "unit price", "unit rate", "basic price", "basic rate", "net price", "net rate",
    "our price", "offered price", "offer price", "special price", "each", "per unit",
    "rate", "price", "ex works", "ex-works"
)
BAD_PRICE_LABELS = (
    "grand total", "subtotal", "sub total", "line total", "total amount", "amount payable",
    "gst", "tax", "freight", "packing", "installation", "commissioning", "discount",
    "advance", "balance", "round off", "rounding"
)
BUYER_TARGET_LABELS = (
    "target price", "target rate", "desired price", "requested price", "our target",
    "please reduce", "can you offer", "can you give", "we need price", "expected price"
)
SUPPLIER_ACCEPT_LABELS = (
    "final price", "final rate", "revised price", "revised rate", "our final",
    "accepted", "agreed price", "best price", "special price", "offered price"
)


def normalize_model(value: str) -> str:
    value = (value or "").strip().upper()
    # Canonical model is compact for matching, but original is preserved separately.
    return re.sub(r"[^A-Z0-9]", "", value)


def normalize_product(value: str) -> str:
    value = " ".join((value or "").split()).strip()
    return value


def normalize_supplier(value: str) -> str:
    value = " ".join((value or "").split()).strip(" .,-")
    return value


def detect_currency(text: str, default="INR") -> str:
    s = (text or "").lower()
    if "usd" in s or "$" in s:
        return "USD"
    if "eur" in s or "€" in s:
        return "EUR"
    if "gbp" in s or "£" in s:
        return "GBP"
    if "aed" in s:
        return "AED"
    if "₹" in s or "inr" in s or re.search(r"\brs\.?\b", s):
        return "INR"
    return default


def _number(value):
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _matching_lines(text: str, needles, max_lines=4):
    out = []
    needles = [n.lower() for n in needles if n]
    for line in (text or "").splitlines():
        clean = " ".join(line.split()).strip()
        low = clean.lower()
        if clean and any(n in low for n in needles):
            out.append(clean[:700])
            if len(out) >= max_lines:
                break
    return out


def _price_lines(text: str, expected_price, max_lines=8):
    expected = _number(expected_price)
    if expected is None:
        return []
    out = []
    for line in (text or "").splitlines():
        clean = " ".join(line.split()).strip()
        if not clean:
            continue
        for m in PRICE_RE.finditer(clean):
            val = _number(m.group("value"))
            if val is not None and abs(val - expected) <= max(0.01, abs(expected) * 0.000001):
                out.append(clean[:700])
                break
        if len(out) >= max_lines:
            break
    return out



def _has_currency_marker(line: str) -> bool:
    low = (line or "").lower()
    return bool(re.search(r"(?:₹|\$|€|£|\binr\b|\brs\.?\b|\busd\b|\beur\b|\bgbp\b|\baed\b)", low))


def _identifier_number_conflict(name: str, model: str, expected_price, price_lines) -> bool:
    """Reject the classic UMF-250 -> ₹250 failure unless real price context exists."""
    expected = _number(expected_price)
    if expected is None or not price_lines:
        return False
    token = str(int(expected)) if float(expected).is_integer() else str(expected)
    identifiers = f"{name} {model}"
    if not re.search(rf"(?<!\d){re.escape(token)}(?!\d)", identifiers, flags=re.I):
        return False
    for line in price_lines:
        low = line.lower()
        if _has_currency_marker(line) or any(x in low for x in GOOD_PRICE_LABELS + SUPPLIER_ACCEPT_LABELS):
            return False
    return True


def _supplier_looks_like_sentence(value: str) -> bool:
    s = " ".join((value or "").split()).strip()
    if not s:
        return False
    low = s.lower()
    bad_phrases = ("any further information", "further information", "clarification", "please contact",
                   "please feel free", "thank you", "regards", "kindly", "if you need")
    if any(x in low for x in bad_phrases):
        return True
    if re.match(r"^\s*\d+[.)]\s*", s):
        return True
    if len(s) > 90 and not re.search(r"\b(pvt|private|ltd|limited|llp|inc|corp|corporation|company|co\.)\b", low):
        return True
    return False

UNIT_WORDS = (
    "lpm", "rpm", "kg", "g", "mg", "mm", "cm", "meter", "metre", "hz", "khz", "mhz",
    "volt", "volts", " v", "ma", "amp", "amps", "bar", "psi", "pa", "mpa", "°c", "deg c",
    "lux", "ppm", "ml", "ltr", "litre", "liter", "%rh"
)
SPEC_LABELS = ("range", "accuracy", "resolution", "capacity", "flow rate", "temperature", "pressure", "load limit", "frequency")


def _corruption_score(value: str) -> float:
    s = re.sub(r"\s+", "", value or "")
    if len(s) < 10:
        return 0.0
    pairs = sum(1 for i in range(1, len(s)) if s[i].lower() == s[i-1].lower() and s[i].isalpha())
    return pairs / max(1, len(s)-1)


def _product_quality_flags(name: str) -> list[str]:
    s = " ".join((name or "").split()).strip()
    low = s.lower()
    flags = []
    if not s or len(s) < 3:
        flags.append("PRODUCT_NAME_EMPTY_OR_SHORT")
        return flags
    alpha = len(re.findall(r"[A-Za-z]", s))
    digits = len(re.findall(r"\d", s))
    if alpha < 3:
        flags.append("PRODUCT_NAME_NOT_TEXTUAL")
    if re.fullmatch(r"[+\-~–—\d.,/%°\s]+", s):
        flags.append("PRODUCT_NAME_IS_NUMERIC_SPEC")
    # A real product may legitimately start with words such as "Pressure",
    # "Temperature", "Capacity" or "Flow" (Pressure Gauge, Temperature
    # Controller, Flow Meter...).  Treat it as a specification label only when
    # the label is followed by a numeric/range value, not merely because the
    # product name begins with the same English word.
    spec_prefix = r"(?:" + "|".join(re.escape(x) for x in SPEC_LABELS) + r")"
    if re.match(rf"(?i)^\s*{spec_prefix}\s*(?:[:=~-]|to\b)?\s*[-+]?\d", s):
        flags.append("PRODUCT_NAME_IS_SPEC_LABEL")

    unit_re = r"(?:" + "|".join(re.escape(u.strip()) for u in UNIT_WORDS if u.strip()) + r")"
    if alpha <= max(8, digits + 3) and len(s) <= 50 and re.search(rf"(?i)(?<![A-Za-z]){unit_re}(?![A-Za-z])", s):
        flags.append("PRODUCT_NAME_LOOKS_MEASUREMENT")
    if _corruption_score(s) >= 0.20:
        flags.append("PRODUCT_NAME_OCR_CORRUPTION")
    if len(s) > 220:
        flags.append("PRODUCT_NAME_EXCESSIVE_DESCRIPTION")
    return flags


def _measurement_price_conflict(name: str, model: str, expected_price, lines: list[str]) -> bool:
    expected = _number(expected_price)
    if expected is None:
        return False
    token = str(int(expected)) if float(expected).is_integer() else str(expected)
    for line in lines or []:
        low = line.lower()
        # e.g. UMF 70 / 70LPM or Accuracy 0.02. Currency/price labels override.
        if _has_currency_marker(line) or any(x in low for x in GOOD_PRICE_LABELS + SUPPLIER_ACCEPT_LABELS):
            continue
        if re.search(rf"(?<!\d){re.escape(token)}(?:\.0+)?\s*(?:lpm|rpm|kg|g|mg|mm|cm|ml|ltr|hz|khz|mhz|v|ma|a|bar|psi|pa|mpa|lux|ppm|%|°c)\b", low, flags=re.I):
            return True
        if any(lbl in low for lbl in SPEC_LABELS) and re.search(rf"(?<!\d){re.escape(token)}(?!\d)", low):
            return True
    return False


def _percent(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
        else:
            m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
            if not m:
                return None
            v = float(m.group())
    except Exception:
        return None
    return v if 0 <= v < 100 else None


def _discount_lines(text: str, expected_discount, max_lines=6):
    expected = _percent(expected_discount)
    if expected is None:
        return []
    out = []
    for line in (text or "").splitlines():
        clean = " ".join(line.split()).strip()
        low = clean.lower()
        if not clean or "discount" not in low:
            continue
        for m in re.finditer(r"(?i)discount(?:ed)?(?:\s*(?:percent|%))?\s*[:@=\-]?\s*(\d+(?:\.\d+)?)\s*%?", clean):
            try:
                val = float(m.group(1))
            except Exception:
                continue
            if abs(val - expected) <= 0.001:
                out.append(clean[:700])
                break
        if len(out) >= max_lines:
            break
    return out


def _original_price_line(lines: list[str]) -> str:
    strong = ("original price", "list price", "mrp", "basic price", "basic rate", "unit rate", "rate")
    for line in lines or []:
        low = line.lower()
        if any(x in low for x in strong) and "grand total" not in low and "subtotal" not in low:
            return line
    return (lines or [""])[0]


def _discounted_math(original, discount):
    o, d = _number(original), _percent(discount)
    if o is None or d is None:
        return None
    return round(o * (1.0 - d / 100.0), 2)


def _structured_price_is_strong(product: dict, expected_price) -> bool:
    basis = str(product.get("price_basis") or "").upper()
    evidence = " ".join(str(product.get("price_evidence") or "").split())
    if basis not in ("EXPLICIT_UNIT_PRICE", "EXPLICIT_UNIT_RATE", "DISCOUNTED_NET_UNIT", "EXPLICIT_DISCOUNTED_UNIT", "CALCULATED_DISCOUNTED_UNIT") or not evidence:
        return False
    expected = _number(expected_price)
    if expected is None:
        return False
    return any(abs((_number(m.group("value")) or -1) - expected) <= max(.01, abs(expected)*1e-6) for m in PRICE_RE.finditer(evidence))


def _compact_alnum(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def verify_product(product: dict, email_payload: dict, attachment_texts=None):
    """V21.4 strict evidence firewall.

    CONFIRMED means export-safe, not merely plausible. Product name, price and
    supplier must all be supported by the current email/attachment. Relational
    learning may normalize patterns, but it never substitutes for source evidence.
    """
    attachment_texts = attachment_texts or []
    source_text = "\n".join([
        email_payload.get("subject", "") or "",
        email_payload.get("sender", "") or "",
        email_payload.get("body", "") or "",
        *[(x.get("text") or "") for x in attachment_texts],
    ])
    name = " ".join((product.get("product_name") or "").split()).strip()
    model = " ".join((product.get("model") or "").split()).strip()
    supplier = " ".join((product.get("supplier_name") or "").split()).strip()
    original_price = _number(product.get("original_price"))
    discount_percent = _percent(product.get("discount_percent"))
    price = _number(product.get("purchase_price"))
    basis = str(product.get("price_basis") or "").upper()
    if price is None and original_price is not None and discount_percent is not None:
        price = _discounted_math(original_price, discount_percent)
    has_discount_semantics = bool(
        (discount_percent is not None and discount_percent > 0)
        or "DISCOUNT" in basis
        or (original_price is not None and price is not None and abs(original_price - price) > max(0.01, abs(price) * 1e-6))
    )
    if not has_discount_semantics and original_price is None and price is not None:
        # One explicit unit price with no discount is the normal/original quote.
        # purchase_price remains the internal effective price for compatibility.
        original_price = price
    ai_conf = max(0.0, min(1.0, float(product.get("confidence") or 0.0)))

    flags = _product_quality_flags(name)
    name_lines = _matching_lines(source_text, [name] if len(name) >= 4 else [])
    model_lines = _matching_lines(source_text, [model] if model else [])
    compact_source = _compact_alnum(source_text)
    compact_model = _compact_alnum(model)
    model_compact_match = bool(compact_model and len(compact_model) >= 3 and compact_model in compact_source)
    if model and not model_lines and model_compact_match:
        model_lines = [model]
    if not name_lines and name:
        sig = [t.lower() for t in re.findall(r"[A-Za-z]{4,}|[A-Za-z]+[-/]?\d+[A-Za-z0-9-]*", name)
               if t.lower() not in ("with","from","this","that","measuring","different")]
        for line in source_text.splitlines():
            low = " ".join(line.split()).lower()
            needed = min(3, max(2, len(sig)))
            if low and sig and sum(1 for t in sig[:10] if t in low) >= needed:
                name_lines = [" ".join(line.split())[:700]]
                break
    if not name_lines and model_lines and not flags:
        name_lines = [model_lines[0]]

    price_lines = _price_lines(source_text, price)
    structured_strong = _structured_price_is_strong(product, price)
    structured_evidence = " ".join(str(product.get("price_evidence") or "").split())
    best_price_line = structured_evidence[:700] if structured_strong else ""
    price_context = "UNIT_OR_OFFER" if structured_strong else "UNKNOWN"

    if not structured_strong:
        for line in price_lines:
            low = line.lower()
            if any(x in low for x in BAD_PRICE_LABELS):
                continue
            if any(x in low for x in BUYER_TARGET_LABELS) and not any(x in low for x in SUPPLIER_ACCEPT_LABELS):
                continue
            if any(x in low for x in GOOD_PRICE_LABELS) or any(x in low for x in SUPPLIER_ACCEPT_LABELS):
                best_price_line = line
                price_context = "UNIT_OR_OFFER"
                break
        if not best_price_line and price_lines:
            if all(any(x in line.lower() for x in BAD_PRICE_LABELS + BUYER_TARGET_LABELS) for line in price_lines):
                best_price_line, price_context = price_lines[0], "TOTAL_OR_CHARGE_CONFLICT"
            else:
                best_price_line, price_context = price_lines[0], "UNLABELED"

    if price_context != "UNIT_OR_OFFER" and _identifier_number_conflict(name, model, price, price_lines):
        price_context = "IDENTIFIER_CONFLICT"
    if price_context != "UNIT_OR_OFFER" and _measurement_price_conflict(name, model, price, price_lines):
        price_context = "MEASUREMENT_CONFLICT"

    original_price_lines = _price_lines(source_text, original_price) if original_price is not None else []
    original_price_evidence = _original_price_line(original_price_lines) if original_price_lines else ""
    discount_lines = _discount_lines(source_text, discount_percent) if discount_percent is not None else []
    discount_evidence = discount_lines[0] if discount_lines else ""
    calculated_discounted = _discounted_math(original_price, discount_percent)

    if original_price is not None and not original_price_evidence:
        flags.append("ORIGINAL_PRICE_NOT_EVIDENCED")
    if discount_percent is not None and not discount_evidence:
        flags.append("DISCOUNT_NOT_EVIDENCED")
    if original_price is not None and discount_percent is not None and price is not None:
        tolerance = max(0.05, abs(calculated_discounted or 0) * 0.001)
        if calculated_discounted is None or abs(float(price) - float(calculated_discounted)) > tolerance:
            flags.append("DISCOUNT_MATH_MISMATCH")
    if str(product.get("price_basis") or "").upper() == "CALCULATED_DISCOUNTED_UNIT":
        if original_price is None or discount_percent is None or not original_price_evidence or not discount_evidence:
            flags.append("CALCULATED_DISCOUNT_NOT_PROVEN")
        elif price is not None:
            # The arithmetic is deterministic; explicit source evidence is original + discount.
            price_context = "UNIT_OR_OFFER"
            best_price_line = f"{original_price_evidence} | {discount_evidence} | calculated discounted unit {price:.2f}"[:700]

    supplier_valid = bool(supplier) and not _supplier_looks_like_sentence(supplier)
    supplier_lines = _matching_lines(source_text, [supplier] if supplier_valid and len(supplier) >= 4 else [])
    compact_supplier = _compact_alnum(supplier)
    if supplier_valid and not supplier_lines and len(compact_supplier) >= 5 and compact_supplier in compact_source:
        supplier_lines = [supplier]
    if not supplier_valid:
        flags.append("SUPPLIER_INVALID")
    elif not supplier_lines:
        flags.append("SUPPLIER_NOT_EVIDENCED")

    if not name_lines:
        flags.append("PRODUCT_NAME_NOT_EVIDENCED")
    if price_context != "UNIT_OR_OFFER":
        flags.append(f"PRICE_{price_context}")
    if price is None or _number(price) is None:
        flags.append("PRICE_MISSING")

    # Dedupe flags while preserving order.
    flags = list(dict.fromkeys(flags))
    critical = [f for f in flags if f]

    # Strict confidence is derived from evidence, not the model's self-score.
    field_confidence = {
        "product_name": 0.995 if name_lines and not _product_quality_flags(name) else 0.0,
        "model": 0.99 if (not model or model_lines or model_compact_match) else 0.55,
        "original_price": 0.995 if (original_price is None or original_price_evidence) else 0.0,
        "discount_percent": 0.995 if (discount_percent is None or discount_evidence) else 0.0,
        "purchase_price": 0.995 if price_context == "UNIT_OR_OFFER" else 0.0,
        "supplier_name": 0.99 if supplier_lines and supplier_valid else 0.0,
    }
    required = [field_confidence["product_name"], field_confidence["purchase_price"], field_confidence["supplier_name"]]
    score = min(required) if required else 0.0
    if model:
        score = min(score, max(0.80, field_confidence["model"]))
    score = min(score, max(ai_conf, 0.80))

    export_eligible = not critical and score >= 0.96
    status = "CONFIRMED" if export_eligible else "NEEDS REVIEW"

    evidence = []
    if name_lines:
        evidence.append({"field_name":"product_name","evidence_text":name_lines[0],"source_page":product.get("source_page", ""),"confidence":field_confidence["product_name"]})
    if model and (model_lines or model_compact_match):
        evidence.append({"field_name":"model","evidence_text":model_lines[0] if model_lines else model,"source_page":product.get("source_page", ""),"confidence":field_confidence["model"]})
    if original_price_evidence:
        evidence.append({"field_name":"original_price","evidence_text":original_price_evidence,"source_page":product.get("source_page", ""),"confidence":field_confidence["original_price"]})
    if discount_evidence:
        evidence.append({"field_name":"discount_percent","evidence_text":discount_evidence,"source_page":product.get("source_page", ""),"confidence":field_confidence["discount_percent"]})
    if best_price_line:
        evidence.append({"field_name":"purchase_price","evidence_text":best_price_line,"source_page":product.get("source_page", ""),"confidence":field_confidence["purchase_price"]})
    if supplier_lines:
        evidence.append({"field_name":"supplier_name","evidence_text":supplier_lines[0],"source_page":product.get("source_page", ""),"confidence":field_confidence["supplier_name"]})

    summary = []
    if original_price_evidence: summary.append(f"Original price evidence: {original_price_evidence}")
    if discount_evidence: summary.append(f"Discount evidence: {discount_evidence}")
    if best_price_line: summary.append(f"Final price evidence: {best_price_line}")
    if name_lines: summary.append(f"Product evidence: {name_lines[0]}")
    if supplier_lines: summary.append(f"Supplier evidence: {supplier_lines[0]}")
    if flags: summary.append("Blocked by: " + ", ".join(flags))

    return {
        "status": status,
        "export_eligible": bool(export_eligible),
        "confidence": round(score, 4),
        "field_confidence": field_confidence,
        "quality_flags": flags,
        "price_context": price_context,
        "price_basis": str(product.get("price_basis") or ""),
        "original_price": original_price,
        "discount_percent": discount_percent,
        "purchase_price": price,
        "calculated_discounted_price": calculated_discounted,
        "price_evidence": structured_evidence or best_price_line,
        "currency": detect_currency(best_price_line or source_text),
        "canonical_product_name": normalize_product(name),
        "canonical_model": normalize_model(model),
        "canonical_supplier_name": normalize_supplier(supplier) if supplier_valid else "",
        "supplier_valid": supplier_valid,
        "evidence": evidence,
        "evidence_summary": "\n".join(summary)[:2600],
    }


def gmail_links(mailbox_email: str, thread_id: str = "", rfc_message_id: str = ""):
    """Return a direct thread link plus an official-search-operator fallback."""
    auth = quote((mailbox_email or "").strip(), safe="@._+-")
    base = f"https://mail.google.com/mail/u/?authuser={auth}"
    out = {"thread": "", "search": ""}
    if thread_id:
        out["thread"] = f"{base}#all/{quote(str(thread_id), safe='')}"
    if rfc_message_id:
        mid = rfc_message_id.strip().strip("<>")
        query = quote(f"rfc822msgid:{mid}", safe="")
        out["search"] = f"{base}#search/{query}"
    return out
