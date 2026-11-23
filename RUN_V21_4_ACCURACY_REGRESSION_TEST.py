from __future__ import annotations

from services.v214_python_engine import extract_main, extract_rescue, infer_supplier, relevance_gate
from services.accuracy_service import verify_product


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"{status} {name}" + (f" :: {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def verified_first(subject, body, sender="sales@example.com", attachments=None):
    rows = extract_main(subject, body, sender)
    if not rows:
        return None, None
    v = verify_product(rows[0], {"subject": subject, "body": body, "sender": sender}, attachments or [])
    return rows[0], v


failures = []

# Stage-0 conservative relevance: obvious noise may be eradicated, but documents win.
check("OTP is high-confidence irrelevant", relevance_gate("Your verification code", "OTP code: 384992 for login", "")["decision"] == "IRRELEVANT")
check("Newsletter is high-confidence irrelevant", relevance_gate("Weekly newsletter", "News and updates\nUnsubscribe", "")["decision"] == "IRRELEVANT")
check("Generic attachment is never eradicated", relevance_gate("Re: discussion", "Please see attached", "document.pdf")["decision"] != "IRRELEVANT")
check("Offer attachment is relevant", relevance_gate("Re: discussion", "Please find attached our offer", "offer.pdf")["decision"] == "RELEVANT")

# Known regressions supplied during V21.3 hardening.
known = [
    ("UMF 70 exact price", "Quotation UMF 70", "Supplier: Acme Instruments Pvt Ltd\nProduct Name: UMF 70\nUnit Price: ₹17,500\nAcme Instruments Pvt Ltd", 17500.0),
    ("UMF-150 exact price", "Quotation UMF-150", "Supplier: Acme Instruments Pvt Ltd\nProduct Name: UMF-150\nPrice: ₹35,000\nAcme Instruments Pvt Ltd", 35000.0),
    ("UMF-250 exact price", "Quotation UMF-250", "Supplier: Acme Instruments Pvt Ltd\nProduct Name: UMF-250\nPrice: ₹65,000\nAcme Instruments Pvt Ltd", 65000.0),
]
for label, subject, body, expected in known:
    row, v = verified_first(subject, body, "Sales Team <sales@acme.example>")
    check(label, bool(row and abs(float(row.get("purchase_price") or 0)-expected)<0.01 and v and v.get("export_eligible")), f"row={row} verify={v}")

fruit_body = """Supplier: V Tech Instruments Pvt Ltd
Product Name: Digital Fruit Penetrometer
Model: Digital 53205
Qty: 01
Accuracy: 0.02
Unit Price: ₹85,000
V Tech Instruments Pvt Ltd"""
row, v = verified_first("Quotation", fruit_body, "V Tech <sales@vtech.example>")
check("Digital Fruit Penetrometer uses ₹85,000", bool(row and row.get("purchase_price") == 85000.0 and v.get("export_eligible")), str(row))
check("Qty 01 never becomes purchase price", not any(float(x.get("purchase_price") or 0) == 1 for x in extract_main("Quotation", fruit_body, "sales@vtech.example")))
check("Model 53205 never becomes purchase price", not any(float(x.get("purchase_price") or 0) == 53205 for x in extract_main("Quotation", fruit_body, "sales@vtech.example")))
check("Accuracy 0.02 never becomes purchase price", not any(abs(float(x.get("purchase_price") or 0)-0.02)<1e-9 for x in extract_main("Quotation", fruit_body, "sales@vtech.example")))

# Deliberately malicious/wrong proposals must be rejected by Python even if an AI suggested them.
source = {"subject":"Quotation UMF 70", "sender":"Acme Instruments Pvt Ltd <sales@acme.example>", "body":"UMF 70\nUnit Price: ₹17,500\nAcme Instruments Pvt Ltd"}
for bad, label in [(70, "identifier 70"), (1, "quantity 01"), (0.02, "accuracy 0.02")]:
    candidate={"product_name":"UMF 70", "model":"UMF70", "purchase_price":bad, "supplier_name":"Acme Instruments Pvt Ltd", "confidence":0.99}
    vr=verify_product(candidate, source, [])
    check(f"Python firewall rejects AI {label} as price", not vr.get("export_eligible"), str(vr.get("quality_flags")))

# Product/spec/date/supplier false-positive exclusions.
check("Range -120 ~ +120 is not a product", extract_main("Specifications", "-120 ~ +120 °C\nResolution: 0.02", "sales@example.com") == [])
check("Date 10-Apr-23 is not a product", extract_main("Price date", "10-Apr-23\nGST: 18%", "sales@example.com") == [])
check("Numbered heading is not supplier", infer_supplier("sales@gmail.com", "6. Heating Systems\nProduct Name: Heater\nUnit Price: ₹12,000") == "")

# Legitimate product names that begin with a specification word must NOT be rejected.
pressure_body = """Supplier: Nunes Instruments Pvt Ltd
Product Name: Pressure Gauge PG100
Unit Price: ₹12,000
Nunes Instruments Pvt Ltd"""
row, v = verified_first("Pressure Gauge quotation", pressure_body, "Nunes <sales@nunes.example>")
check("Pressure Gauge remains a valid product name", bool(row and v.get("export_eligible")), str(v.get("quality_flags") if v else None))

# Two-price deterministic math.
disc_body = """Supplier: Nunes Instruments Pvt Ltd
Product Name: Pressure Gauge PG100
Original Price: ₹100,000
Discount: 10%
Nunes Instruments Pvt Ltd"""
row, v = verified_first("Discount offer", disc_body, "Nunes <sales@nunes.example>")
check("Original price retained", bool(row and row.get("original_price") == 100000.0), str(row))
check("Discount retained", bool(row and row.get("discount_percent") == 10.0), str(row))
check("Purchase price deterministically calculated", bool(row and row.get("purchase_price") == 90000.0 and v.get("export_eligible")), str(v))

# Table rescue: Python should solve readable attachments without AI.
table = """Product | Model | Qty | Unit Price
Digital Pressure Gauge | DPG-100 | 1 | ₹8,500
"""
rows = extract_rescue("Quotation", "Supplier: Test Instruments Pvt Ltd\nTest Instruments Pvt Ltd", [{"name":"quote.pdf","text":table}], [], "Test Instruments <sales@test.example>")
vr = verify_product(rows[0], {"subject":"Quotation","body":"Supplier: Test Instruments Pvt Ltd\nTest Instruments Pvt Ltd","sender":"Test Instruments <sales@test.example>"}, [{"name":"quote.pdf","text":table}]) if rows else {}
check("Readable table solved by Python Rescue", bool(rows and rows[0].get("purchase_price") == 8500.0 and vr.get("export_eligible")), f"rows={rows} verify={vr}")

# Additional commercial false-value firewall cases.
flow_body = """Supplier: Flow Systems Pvt Ltd
Product Name: Flow Meter FM-100
Qty: 2
HSN: 90262000
Unit Price: ₹14,999
Freight: ₹500
GST: 18%
Grand Total: ₹30,998
Flow Systems Pvt Ltd"""
row, v = verified_first("Quotation Flow Meter", flow_body, "Flow Systems <sales@flowsystems.example>")
check("Unit price wins over Qty/HSN/Freight/GST/Grand Total", bool(row and row.get("purchase_price") == 14999.0 and v.get("export_eligible")), str(row))

noise_money = """Supplier: Flow Systems Pvt Ltd
Product Name: Flow Meter FM-100
Qty: 2
HSN: 90262000
Freight: ₹500
GST: 18%
Grand Total: ₹30,998
Flow Systems Pvt Ltd"""
check("Freight and grand total alone cannot create purchase price", extract_main("Commercial details", noise_money, "sales@flowsystems.example") == [])

compact = """Supplier: Test Instruments Pvt Ltd
Product Name: Temperature Indicator TI-20
Offer: 12,500/- each
Test Instruments Pvt Ltd"""
row, v = verified_first("Offer", compact, "Test Instruments <sales@test.example>")
check("Indian compact each-price supported", bool(row and row.get("purchase_price") == 12500.0 and v.get("export_eligible")), str(row))

check("Generic Gmail sender is not invented as supplier", infer_supplier("sales@gmail.com", "Product Name: Meter\nUnit Price: ₹5,000") == "")

# Multi-product readable table must link each price to its own row, never cross-link rows.
multi_table = """Product | Model | Qty | Unit Price
Gauge A | GA-1 | 1 | ₹8,500
Gauge B | GB-2 | 2 | ₹9,500
"""
rows = extract_rescue("Quotation", "Supplier: Test Instruments Pvt Ltd\nTest Instruments Pvt Ltd", [{"name":"quote.pdf","text":multi_table}], [], "Test Instruments <sales@test.example>")
pairs={(r.get("product_name"),r.get("model"),r.get("purchase_price")) for r in rows}
check("Multi-row table keeps product-price row relationships", {("Gauge A","GA-1",8500.0),("Gauge B","GB-2",9500.0)}.issubset(pairs), str(rows))

# Missing supplier evidence must never auto-confirm.
candidate={"product_name":"Laser Source", "purchase_price":8000, "supplier_name":"Invented Supplier Pvt Ltd", "confidence":0.99, "price_basis":"EXPLICIT_UNIT_PRICE", "price_evidence":"Unit Price ₹8,000"}
vr=verify_product(candidate,{"subject":"Quotation Laser Source","body":"Laser Source\nUnit Price ₹8,000","sender":"unknown@gmail.com"},[])
check("Invented supplier cannot auto-confirm", not vr.get("export_eligible"), str(vr.get("quality_flags")))

print("\nV21.4 ACCURACY REGRESSION TEST:", "PASSED" if not failures else "FAILED")
if failures:
    print("Failures:", ", ".join(failures))
raise SystemExit(0 if not failures else 1)
