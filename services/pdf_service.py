from io import BytesIO
from datetime import datetime
import math
import re

try:
    from PyPDF2 import PdfReader
except Exception:  # modern fallback used by current Python environments
    from pypdf import PdfReader

MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)


def _clean(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return "\n".join(" ".join(x.split()) for x in str(value).splitlines() if " ".join(x.split())).strip()


def _cell_lines(value):
    return [" ".join(x.split()) for x in str(value or "").splitlines() if " ".join(x.split())]


def _parse_number(value):
    text = " ".join(str(value or "").split())
    if not text or text == "-":
        return None
    cleaned = re.sub(r"(?i)\b(?:rs\.?|inr)\b|₹|/-|each|ex[- ]?works|per\s+unit|pcs?|sets?", " ", text)
    cleaned = cleaned.replace("`", " ").replace("’", " ").replace("'", " ")
    m = re.search(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)", cleaned)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def _parse_percent(value):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value or ""))
    return float(m.group(1)) if m else None


def _extract_date(text: str) -> str:
    if not text:
        return ""
    m = re.search(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b", text, flags=re.I)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            pass
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def _extract_supplier(text: str) -> str:
    if not text:
        return ""
    lines = [_clean_cell(x).replace("\n", " ") for x in text.splitlines() if _clean_cell(x)]
    # Strong letterhead/branding before generic company-word matching.
    for line in lines[:25]:
        clean = line.strip(" .:-")
        compact_letters = re.sub(r"\s+", "", clean)
        if re.fullmatch(r"[A-Z ]{8,}", clean) and "FILTRATION" in compact_letters:
            prefix = compact_letters.split("FILTRATION", 1)[0]
            if prefix:
                return f"{prefix} FILTRATION"
        low = clean.lower()
        if re.match(r"^\d+[.)\s]", clean):
            continue
        if 4 <= len(clean) <= 80 and any(x in low for x in ("filtration", "engineering", "instruments", "scientific", "technologies", "machineries")):
            if "nunes" not in low and not any(x in low for x in ("product", "application", "website", "email", "quotation", "www.", ".com", "http")):
                return re.sub(r"(?i)^for\s+", "", clean).strip()
    # Explicit sign-off company lines are strongest (e.g. "For SES Instruments Pvt. Ltd.").
    for line in lines:
        embedded = re.search(r"(?i)\bfor\s+([A-Z0-9&.,() /-]{3,}?(?:pvt\.?\s*ltd\.?|private\s+limited|limited|llp))\b", line)
        if embedded:
            candidate = re.sub(r"(?i)^for\s+", "", embedded.group(1)).strip(" .:-")
            if "nunes" not in candidate.lower():
                return candidate
        m = re.search(r"(?i)^for\s+(.+)$", line)
        if m:
            candidate = re.sub(r"(?i)^for\s+", "", m.group(1)).strip(" .:-")
            if len(candidate) >= 4 and "nunes" not in candidate.lower() and not candidate.lower().startswith(("further", "your")):
                return candidate
    company_words = (
        "pvt. ltd", "pvt ltd", "private limited", "limited", "llp", "corporation",
        "industries", "electronics", "instruments", "engineering", "technologies",
        "scientific", "systems", "solutions", "company"
    )
    for line in lines:
        low = line.lower()
        if re.match(r"^\s*\d+[.)]\s*", line):
            continue
        if "nunes instrumentation" in low or "nunes instruments" in low:
            continue
        if any(x in low for x in company_words) and 4 <= len(line) <= 120:
            # Avoid URLs/page footers and prose that merely contains company words.
            if "page " in low or ".com" in low or "www." in low:
                continue
            if not any(x in low for x in ("please", "further information", "clarification", "terms", "warranty")):
                return line.strip(" .:-")
    return ""


def _technical_spec_summary(full_text: str, max_len: int = 900) -> str:
    preferred_labels = (
        "test voltage", "unit capacity", "supply voltage", "resolution", "operating",
        "humidity range", "display", "interface", "safety", "printer", "vessel and electrode",
        "range", "accuracy", "capacity", "temperature", "pressure", "measurement"
    )
    specs, seen = [], set()
    for raw_line in (full_text or "").splitlines():
        line = " ".join(raw_line.split())
        low = line.lower()
        if any(low.startswith(label) for label in preferred_labels):
            if low not in seen:
                seen.add(low); specs.append(line)
    return "; ".join(specs)[:max_len]


def _find_col(header, keywords, excludes=()):
    for i, cell in enumerate(header):
        low = " ".join(str(cell or "").lower().split())
        if excludes and any(x in low for x in excludes):
            continue
        if any(k in low for k in keywords):
            return i
    return None


def _find_price_col(header):
    """Choose an actual commercial price column, never a measurement rate column."""
    best = (None, -1)
    for i, cell in enumerate(header):
        low = " ".join(str(cell or "").lower().split())
        if not low:
            continue
        score = -1
        if "amount" in low and "without gst" in low:
            score = 96
        elif any(x in low for x in ("flow rate", "sampling rate", "baud rate", "tax rate", "discount", "cgst", "sgst", "igst", "taxable", "grand total")):
            continue
        elif "unit price" in low or "unit rate" in low:
            score = 100
        elif "price in" in low or low == "price":
            score = 98
        elif low in ("rate", "basic rate", "basic price", "net price", "amount"):
            score = 92
        elif "price" in low:
            score = 88
        elif "rate" in low and not any(u in low for u in ("flow", "sample", "baud")):
            score = 80
        if score > best[1]:
            best = (i, score)
    return best[0]


def _explicit_model(text: str) -> str:
    text = " ".join((text or "").split())
    patterns = [
        r"(?i)\bmodel\s*(?:no\.?\s*)?[:#]\s*([A-Za-z0-9][A-Za-z0-9 ._/-]{1,32}?)(?=\s+(?:European|For|It|The|Range|Load|Accuracy|Unit|Sensor|Working|Pressure|$))",
        r"(?i)\bmodel\s*(?:no\.?\s*)?[:#]\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,24})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return " ".join(m.group(1).split()).strip(" .,-")
    return ""


def _clean_product_name(text: str) -> str:
    value = " ".join((text or "").split()).strip(" .:-")
    if not value:
        return ""
    # Keep the commercial item name and move model/specification prose elsewhere.
    value = re.split(r"(?i)\s+(?:model\s*[:#-]|for\s+(?:apple|apples|pear|pears|industrial|laboratory)|european brand|it\s+(?:is|measure)|the instrument|range\s*[:#-]|load limit\s*[:#-]|indication scale\s*[:#-])", value, maxsplit=1)[0]
    value = re.sub(r"\s*\((?:Research|Model)?$", "", value, flags=re.I).strip(" .:-")
    # Long description cells often append specifications after the real item name.
    # Keep the compact commercial identity in PRODUCT NAME and leave the rest for SPECIFICATIONS.
    if len(value) > 100:
        compact = re.split(r"(?i)\s+for\s+(?:measuring|measurement|testing|determining|determination)\b", value, maxsplit=1)[0].strip()
        if len(compact) >= 8:
            value = compact
    # Avoid enormous descriptions in the Product Name column.
    if len(value) > 180:
        cut = re.split(r"[.;]", value, maxsplit=1)[0].strip()
        value = cut if len(cut) >= 12 else value[:180].rsplit(" ", 1)[0]
    value = re.sub(r"([A-Za-z]{2,})-\s+(\d)", r"\1-\2", value)
    return value


def _infer_model(text: str):
    text = " ".join((text or "").split())
    text = re.sub(r"([A-Za-z0-9])[-/]\s+(\d)", r"\1-\2", text)
    # Prefer alpha-numeric codes containing a hyphen or multiple letters+digits.
    matches = re.findall(r"\b[A-Z]{1,8}[A-Z0-9]*[-/]?[A-Z0-9]*\d[A-Z0-9-]*\b", text.upper())
    for m in matches:
        if len(m) >= 4 and not re.fullmatch(r"\d+", m):
            return m
    return ""


def _infer_product_family(full_text: str) -> str:
    """Infer a real product family when the table provides only a product code."""
    top = "\n".join((full_text or "").splitlines()[:90])
    if re.search(r"(?i)\bmagnetic filters?\b", top):
        return "Magnetic Filter"
    if re.search(r"(?i)\bfruit penetrometers?\b", top):
        return "Fruit Penetrometer"
    return ""


def _group_text_lines(lines, n):
    lines = [x for x in lines if x]
    if n <= 1:
        return [" ".join(lines)] if lines else [""]
    if len(lines) == n:
        return lines
    if len(lines) >= n and len(lines) % n == 0:
        width = len(lines) // n
        return [" ".join(lines[i*width:(i+1)*width]) for i in range(n)]
    # Product cells often begin each product with a model-like token. Split there.
    starts = [i for i, x in enumerate(lines) if _infer_model(x)]
    if len(starts) >= n:
        groups = []
        for j in range(n):
            a = starts[j]
            b = starts[j+1] if j + 1 < n else len(lines)
            groups.append(" ".join(lines[a:b]))
        return groups
    # Conservative fallback: distribute lines as evenly as possible.
    groups, pos = [], 0
    for i in range(n):
        remaining = len(lines) - pos
        take = max(1, math.ceil(remaining / (n - i)))
        groups.append(" ".join(lines[pos:pos+take]))
        pos += take
    return groups


def _make_product(product_name, model, purchase_price, supplier, price_date, filename,
                  price_evidence, price_basis="EXPLICIT_UNIT_PRICE", list_price=None,
                  discount_percent=None, specs="", source_page="", source_row="", source_column=""):
    if not product_name or purchase_price is None:
        return None
    return {
        "product_name": " ".join(product_name.split()),
        "model": re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", " ".join((model or "").split())),
        "specifications": specs or "",
        "purchase_price": float(purchase_price),
        "supplier_name": supplier,
        "marketing_person": "",
        "purchase_owner": "",
        "image_link": "",
        "price_date": price_date,
        "source_type": "PDF",
        "source_filename": filename,
        "confidence": 0.995,
        # Transient evidence fields consumed by the verifier; they do not require DB columns.
        "price_evidence": price_evidence,
        "price_basis": price_basis,
        "list_price": list_price,
        "discount_percent": discount_percent,
        "source_page": str(source_page or ""),
        "source_row": str(source_row or ""),
        "source_column": str(source_column or ""),
    }


def _parse_structured_table(table, full_text, filename, supplier, price_date, source_page="", table_no=""):
    """Parse well-formed quotation tables, including merged multi-product cells."""
    if not table or len(table) < 2:
        return []
    header_idx = None
    cols = {}
    for ri, row in enumerate(table[:8]):
        cells = [_clean_cell(c).replace("\n", " ") for c in (row or [])]
        product_idx = _find_col(cells, ("name of product", "product /", "product", "service", "item description", "description", "equipment"))
        price_idx = _find_price_col(cells)
        if product_idx is None or price_idx is None:
            continue
        # A collapsed header where the whole header sits in one cell is unsafe. Let wide-row fallback handle it.
        if product_idx == price_idx:
            continue
        header_idx = ri
        cols = {
            "product": product_idx,
            "model": _find_col(cells, ("model no", "model")),
            "qty": _find_col(cells, ("qty", "quantity")),
            "price": price_idx,
            "discount": _find_col(cells, ("discount",)),
            "total": _find_col(cells, ("total",), excludes=("taxable",)),
            "taxable": _find_col(cells, ("taxable",)),
            "serial": _find_col(cells, ("sr.", "sr no", "no.", "s.no", "sl no")),
        }
        # Presence of SGST/CGST/IGST means Total is tax-inclusive; never use it as purchase price.
        joined = " ".join(cells).lower()
        cols["tax_columns"] = any(x in joined for x in ("sgst", "cgst", "igst", "gst", "tax"))
        break
    if header_idx is None:
        return []

    header_cells = [_clean_cell(c).replace("\n", " ") for c in (table[header_idx] or [])]
    product_header = header_cells[cols["product"]] if cols.get("product") is not None and cols["product"] < len(header_cells) else "Product"
    price_header = header_cells[cols["price"]] if cols.get("price") is not None and cols["price"] < len(header_cells) else "Price"
    products = []
    for row_offset, row in enumerate(table[header_idx + 1:], start=header_idx + 2):
        cells = [_clean_cell(c) for c in (row or [])]
        def at(idx): return cells[idx] if idx is not None and idx < len(cells) else ""
        product_lines = _cell_lines(at(cols["product"]))
        price_lines = _cell_lines(at(cols["price"]))
        qty_lines = _cell_lines(at(cols["qty"]))
        serial_lines = _cell_lines(at(cols["serial"]))
        if not product_lines or not price_lines:
            continue
        prices = [_parse_number(x) for x in price_lines]
        prices = [x for x in prices if x is not None]
        if not prices:
            continue
        n = max(len(prices), len(qty_lines), len(serial_lines), 1)
        names = _group_text_lines(product_lines, n)
        models = _group_text_lines(_cell_lines(at(cols["model"])), n) if cols["model"] is not None else [""] * n
        qty_vals = [_parse_number(x) for x in qty_lines]
        discount_vals = [_parse_percent(x) for x in _cell_lines(at(cols["discount"]))]
        total_vals = [_parse_number(x) for x in _cell_lines(at(cols["total"]))]
        # Align lists; missing values stay None.
        for i in range(n):
            name = names[i] if i < len(names) else ""
            if not name or name.lower().startswith("total"):
                continue
            base_price = prices[i] if i < len(prices) else (prices[0] if n == 1 else None)
            if base_price is None:
                continue
            qty = qty_vals[i] if i < len(qty_vals) and qty_vals[i] else 1.0
            disc = discount_vals[i] if i < len(discount_vals) else None
            line_total = total_vals[i] if i < len(total_vals) else None
            chosen = base_price
            basis = "EXPLICIT_UNIT_PRICE"
            evidence = f"{price_header or 'Price/Rate'} {base_price:g}"
            # When the row explicitly has a discount and a non-tax total, the total is the net offered line value.
            # Convert to net unit only because Qty is explicit in the same row.
            if disc is not None and line_total is not None and not cols["tax_columns"] and qty:
                chosen = line_total / qty
                basis = "DISCOUNTED_NET_UNIT"
                evidence = f"Rate {base_price:g} | Qty {qty:g} | Discount {disc:g}% | Net line total {line_total:g} | Net unit {chosen:g}"
            elif cols["tax_columns"]:
                evidence = f"{price_header or 'Price'} {base_price:g}; tax-inclusive Total ignored"
            model = models[i] if i < len(models) else ""
            explicit_model = _explicit_model(name)
            product_name = _clean_product_name(name)
            # Product-code tables (e.g. UMF 70) must not use the code itself as
            # the human product name. Preserve the code as model and derive the
            # family from the document heading.
            if "code" in (product_header or "").lower():
                model = model or _clean_product_name(name)
                product_name = _infer_product_family(full_text) or _clean_product_name(name)
            elif not model:
                model = explicit_model or _infer_model(name)
            elif explicit_model and len(str(model)) > 40:
                model = explicit_model
            p = _make_product(product_name, model, chosen, supplier, price_date, filename, evidence, basis, base_price, disc,
                              source_page=source_page, source_row=row_offset, source_column=price_header)
            if p:
                products.append(p)
    return products


def _parse_wide_collapsed_rows(table, full_text, filename, supplier, price_date, source_page="", table_no=""):
    """Recover tables where pdfplumber collapses the header into one cell but data columns remain separated.

    This specifically prevents serial/quantity values (1,2,3...) from being mistaken for purchase prices.
    """
    products = []
    for row_offset, row in enumerate(table or [], start=1):
        cells = [_clean_cell(c) for c in (row or [])]
        if len(cells) < 9:
            continue
        serial = _parse_number(cells[0])
        sku = cells[1].replace("\n", " ").strip() if len(cells) > 1 else ""
        desc = cells[2].replace("\n", " ").strip() if len(cells) > 2 else ""
        if serial is None or not desc or len(desc) < 8:
            continue
        # Common no-header quotation row: serial ... Qty | Price | optional picture.
        # Detect this before the older Rate/Discount/Total layout so Qty=01 can
        # never become the purchase price (Fruit Penetrometer regression).
        qty_candidate = _parse_number(cells[-3]) if len(cells) >= 3 else None
        price_candidate = _parse_number(cells[-2]) if len(cells) >= 2 else None
        if price_candidate is not None and qty_candidate is not None and price_candidate > qty_candidate:
            qty, rate, discount, net_total = qty_candidate, price_candidate, None, None
            chosen, basis = rate, "EXPLICIT_UNIT_PRICE"
            evidence = f"Qty {qty:g} | Price {rate:g}; quantity kept separate"
        else:
            # Older layout: HSN | Qty | Unit | Rate | Discount | Total.
            rate = _parse_number(cells[-3])
            discount = _parse_percent(cells[-2])
            net_total = _parse_number(cells[-1])
            qty = _parse_number(cells[-5]) or 1.0
            if rate is None:
                continue
            chosen, basis = rate, "EXPLICIT_UNIT_RATE"
            evidence = f"Rate {rate:g}"
            if discount is not None and net_total is not None and qty:
                chosen = net_total / qty
                basis = "DISCOUNTED_NET_UNIT"
                evidence = f"SKU {sku} | Rate {rate:g} | Qty {qty:g} | Discount {discount:g}% | Net line total {net_total:g} | Net unit {chosen:g}"
        name = _clean_product_name(desc or sku)
        row_text = " ".join(cells)
        model = (sku if sku and len(sku) <= 40 else "") or _explicit_model(row_text) or _infer_model(row_text)
        p = _make_product(name, model, chosen, supplier, price_date, filename, evidence, basis, rate, discount, source_page=source_page, source_row=row_offset, source_column="Rate/Price")
        if p:
            products.append(p)
    return products


def _money_cell(value: str) -> bool:
    raw = " ".join(str(value or "").split()).strip()
    if not raw:
        return False
    # Currency words are okay; engineering units make this a measurement, not money.
    cleaned = re.sub(r"(?i)(?:₹|rs\.?|inr|usd|eur|gbp|aed|/-)", "", raw)
    if re.search(r"[A-Za-z]", cleaned):
        return False
    return _parse_number(raw) is not None


def _qty_cell(value: str) -> bool:
    raw = " ".join(str(value or "").split()).strip()
    if not raw:
        return False
    cleaned = re.sub(r"(?i)\b(?:pcs?|sets?|nos?|units?)\b", "", raw).strip()
    return bool(re.fullmatch(r"\d+(?:\.0+)?", cleaned))


def _parse_loose_qty_price_rows(table, full_text, filename, supplier, price_date, source_page="", table_no=""):
    """Recover irregular tables with explicit adjacent Qty + Price cells.

    Used for continuation-page quotations where the header was rendered on the
    previous page. It deliberately refuses cells containing engineering units,
    preventing Qty/flow/range values from becoming prices.
    """
    products = []
    rows = table or []
    for ri, row in enumerate(rows):
        cells = [_clean_cell(c) for c in (row or [])]
        price_idx = qty_idx = None
        for i in range(max(0, len(cells)-1)):
            if _qty_cell(cells[i]) and _money_cell(cells[i+1]):
                q = _parse_number(cells[i]); pr = _parse_number(cells[i+1])
                if q is not None and pr is not None and pr >= max(10, q):
                    qty_idx, price_idx = i, i+1
                    break
        if price_idx is None:
            continue
        serial = _parse_number(cells[0]) if cells else None
        # Gather descriptive cells before Qty, excluding the serial number.
        candidates=[]
        for ci, cell in enumerate(cells[1:qty_idx], start=1):
            text=" ".join(cell.split()).strip()
            if not text or not re.search(r"[A-Za-z]", text):
                continue
            if text.lower().startswith(("model :", "range :", "accuracy :", "load ")):
                continue
            candidates.append((ci,text))
        if not candidates:
            continue
        # Prefer a compact item-name cell. If only a long description exists,
        # _clean_product_name extracts its leading commercial name.
        ci, raw_name = min(candidates, key=lambda x: (0 if len(x[1]) <= 90 else 1, len(x[1])))
        name = _clean_product_name(raw_name)
        if not name:
            continue
        # Continuation rows often contain the model/specs below the priced row.
        continuation = [" ".join(cells)]
        for nxt in rows[ri+1:ri+8]:
            nc=[_clean_cell(c) for c in (nxt or [])]
            if nc and _parse_number(nc[0]) is not None:
                break
            # stop on another explicit Qty+Price row
            if any(_qty_cell(nc[j]) and j+1 < len(nc) and _money_cell(nc[j+1]) for j in range(max(0,len(nc)-1))):
                break
            continuation.append(" ".join(nc))
        row_text=" ".join(continuation)
        model=_explicit_model(row_text) or _infer_model(row_text)
        price=_parse_number(cells[price_idx])
        qty=_parse_number(cells[qty_idx]) or 1.0
        evidence=f"Qty {qty:g} | Price {price:g}; explicit adjacent quotation cells"
        p=_make_product(name, model, price, supplier, price_date, filename, evidence,
                        "EXPLICIT_UNIT_PRICE", price, None,
                        source_page=source_page, source_row=ri+1, source_column="Price")
        if p:
            products.append(p)
    return products


def _dedupe_products(products):
    out, seen = [], set()
    for p in products:
        key = (
            re.sub(r"\s+", " ", p.get("product_name", "").strip()).casefold(),
            re.sub(r"\s+", " ", p.get("model", "").strip()).casefold(),
            round(float(p.get("purchase_price") or 0), 4),
        )
        if key in seen:
            continue
        seen.add(key); out.append(p)
    return out


def _parse_product_tables(tables, full_text, filename):
    supplier = _extract_supplier(full_text)
    price_date = _extract_date(full_text)
    products = []
    for entry in tables:
        if isinstance(entry, dict):
            table = entry.get("table") or []
            source_page = entry.get("page") or ""
            table_no = entry.get("table_no") or ""
        else:
            table, source_page, table_no = entry, "", ""
        structured = _parse_structured_table(table, full_text, filename, supplier, price_date, source_page, table_no)
        if structured:
            products.extend(structured)
        else:
            products.extend(_parse_wide_collapsed_rows(table, full_text, filename, supplier, price_date, source_page, table_no))
        products.extend(_parse_loose_qty_price_rows(table, full_text, filename, supplier, price_date, source_page, table_no))
    products = _dedupe_products(products)
    if len(products) == 1:
        products[0]["specifications"] = _technical_spec_summary(full_text)
    return products


def extract_pdf_bundle(content: bytes, filename: str = "", max_chars: int = 24000):
    """Evidence-first PDF quotation extraction.

    V21.4 strict rules:
      - never treat serial/HSN/Qty/GST/Total as price simply because it is numeric;
      - prefer explicit pre-tax unit Price/Rate;
      - when an explicit discount and net line total are present, compute the exact discounted net unit;
      - split merged multi-product table cells into separate products/prices;
      - preserve source price evidence for the verifier and View Details.
    """
    chunks, tables_all = [], []
    try:
        import pdfplumber
        with pdfplumber.open(BytesIO(content)) as doc:
            for page_no, page in enumerate(doc.pages, start=1):
                text = page.extract_text(x_tolerance=1, y_tolerance=3, layout=False) or ""
                if text:
                    chunks.append(f"--- PDF PAGE {page_no} ---\n{_clean(text)}")
                try:
                    for ti, table in enumerate(page.extract_tables() or [], start=1):
                        tables_all.append({"page": page_no, "table_no": ti, "table": table})
                        rows = []
                        for row in table or []:
                            cells = [_clean_cell(c).replace("\n", " / ") for c in (row or [])]
                            if any(cells): rows.append(" | ".join(cells))
                        if rows:
                            chunks.append(f"--- PDF PAGE {page_no} TABLE {ti} ---\n" + "\n".join(rows))
                except Exception:
                    pass
                if len("\n\n".join(chunks)) >= max_chars:
                    break
    except Exception:
        chunks, tables_all = [], []

    if not chunks:
        try:
            reader = PdfReader(BytesIO(content))
            for page_no, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text:
                    chunks.append(f"--- PDF PAGE {page_no} ---\n{_clean(text)}")
                if len("\n\n".join(chunks)) >= max_chars:
                    break
        except Exception:
            return {"text": "", "products": []}

    full_text = "\n\n".join(chunks)[:max_chars]
    return {"text": full_text, "products": _parse_product_tables(tables_all, full_text, filename)}


def extract_pdf_text(content: bytes, max_chars: int = 24000) -> str:
    return extract_pdf_bundle(content, "", max_chars)["text"]
