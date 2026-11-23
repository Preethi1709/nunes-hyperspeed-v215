from __future__ import annotations

import base64
import io
import json
import os
import re
import requests
import threading
from typing import Any

OLLAMA_URL = (os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
LOCAL_AI_ENABLED = (os.getenv("LOCAL_AI_ENABLED") or "true").strip().lower() in {"1","true","yes","on"}
LOCAL_AI_FAST_MODEL = (os.getenv("LOCAL_AI_FAST_MODEL") or "qwen3:1.7b").strip()
LOCAL_AI_DEEP_MODEL = (os.getenv("LOCAL_AI_DEEP_MODEL") or "qwen3:4b").strip()
LOCAL_AI_VISION_MODEL = (os.getenv("LOCAL_AI_VISION_MODEL") or "qwen3-vl:4b").strip()
LOCAL_AI_TIMEOUT = max(15, min(300, int(os.getenv("LOCAL_AI_TIMEOUT_SECONDS", "120"))))
LOCAL_AI_FAST_KEEP_ALIVE = (os.getenv("LOCAL_AI_FAST_KEEP_ALIVE") or "5m").strip()
LOCAL_AI_HEAVY_KEEP_ALIVE = (os.getenv("LOCAL_AI_HEAVY_KEEP_ALIVE") or "0").strip()

_LOCAL_MODEL_GATE = threading.RLock()
_LOCAL_ACTIVE_MODEL = ""


def _ollama_unload(model: str) -> None:
    if not model:
        return
    try:
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": 0}, timeout=8)
    except Exception:
        pass


def unload_all_v214_models() -> None:
    for model in dict.fromkeys([LOCAL_AI_FAST_MODEL, LOCAL_AI_DEEP_MODEL, LOCAL_AI_VISION_MODEL]):
        _ollama_unload(model)


def _activate_only(model: str) -> None:
    global _LOCAL_ACTIVE_MODEL
    if _LOCAL_ACTIVE_MODEL and _LOCAL_ACTIVE_MODEL != model:
        _ollama_unload(_LOCAL_ACTIVE_MODEL)
        _LOCAL_ACTIVE_MODEL = ""
    _LOCAL_ACTIVE_MODEL = model


def _keep_alive_for_model(model: str) -> str:
    return LOCAL_AI_FAST_KEEP_ALIVE if model == LOCAL_AI_FAST_MODEL else LOCAL_AI_HEAVY_KEEP_ALIVE


def _strip_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end+1]
    return json.loads(text)


def _model_present(names: list[str], requested: str) -> bool:
    req = (requested or "").strip()
    if not req:
        return False
    base = req.split(":", 1)[0]
    return any((n == req) or n.startswith(req + ":") or n.split(":", 1)[0] == base for n in names)


def health() -> dict:
    if not LOCAL_AI_ENABLED:
        return {"enabled": False, "online": False, "models": [], "all_required_ready": False, "rag_removed": True}
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
        fast = _model_present(names, LOCAL_AI_FAST_MODEL)
        deep = _model_present(names, LOCAL_AI_DEEP_MODEL)
        vision = _model_present(names, LOCAL_AI_VISION_MODEL)
        return {
            "enabled": True, "online": True, "models": names,
            "fast_model": LOCAL_AI_FAST_MODEL, "fast_model_ready": fast,
            "deep_model": LOCAL_AI_DEEP_MODEL, "deep_model_ready": deep,
            "vision_model": LOCAL_AI_VISION_MODEL, "vision_model_ready": vision,
            "embedding_model": "REMOVED", "embedding_ready": False, "rag_removed": True,
            "all_required_ready": bool(fast and deep and vision),
        }
    except Exception as exc:
        return {"enabled": True, "online": False, "models": [], "all_required_ready": False, "rag_removed": True, "error": str(exc)}


def fast_text_model_name() -> str:
    h = health()
    if h.get("online") and h.get("fast_model_ready"):
        return LOCAL_AI_FAST_MODEL
    if h.get("online") and h.get("deep_model_ready"):
        return LOCAL_AI_DEEP_MODEL
    return ""


def deep_text_model_name() -> str:
    h = health()
    if h.get("online") and h.get("deep_model_ready"):
        return LOCAL_AI_DEEP_MODEL
    return fast_text_model_name()


def vision_model_name() -> str:
    h = health()
    return LOCAL_AI_VISION_MODEL if h.get("online") and h.get("vision_model_ready") else ""


def _chat_json(model: str, system: str, user: str, images: list[str] | None = None) -> dict:
    global _LOCAL_ACTIVE_MODEL
    with _LOCAL_MODEL_GATE:
        _activate_only(model)
        msg = {"role": "user", "content": user}
        if images:
            msg["images"] = images
        payload = {
            "model": model, "stream": False, "format": "json",
            "messages": [{"role": "system", "content": system}, msg],
            "options": {"temperature": 0},
            "keep_alive": _keep_alive_for_model(model),
        }
        try:
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=LOCAL_AI_TIMEOUT)
            r.raise_for_status()
            return _strip_json(((r.json().get("message") or {}).get("content") or ""))
        finally:
            if str(_keep_alive_for_model(model)).strip().lower() in {"0","0s","0m","false","off"}:
                _ollama_unload(model)
                _LOCAL_ACTIVE_MODEL = ""


def self_test() -> dict:
    h = health()
    result = {"ok": False, "ollama_url": OLLAMA_URL, "health": h, "checks": {}, "rag_removed": True}
    if not h.get("online"):
        result["error"] = h.get("error") or "Ollama offline"
        return result
    for model in dict.fromkeys([LOCAL_AI_FAST_MODEL, LOCAL_AI_DEEP_MODEL, LOCAL_AI_VISION_MODEL]):
        ready = _model_present(h.get("models") or [], model)
        result["checks"][model] = {"installed": ready}
    result["ok"] = all(x.get("installed") for x in result["checks"].values())
    return result


def _normalize_products(rows: list[dict] | None) -> list[dict]:
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        p = dict(raw)
        for k in ("original_price", "discount_percent", "purchase_price"):
            if p.get(k) in ("", "null", "None"):
                p[k] = None
        try: p["confidence"] = float(p.get("confidence") or 0)
        except Exception: p["confidence"] = 0.0
        out.append(p)
    return out


_TEXT_SYSTEM = r'''
You are an EXCEPTION-ONLY procurement quotation interpreter.
Python already tried deterministic extraction.  Your job is only to propose a
possible product-price relationship from EXPLICIT CURRENT-SOURCE evidence.
Never invent. Never use historical memory as current evidence.

purchase_price = final/net/offered PRE-TAX unit procurement price.
original_price = explicit pre-discount/list/MRP/basic unit rate only when discount exists.
discount_percent = supplier discount only; NEVER GST/tax.
Reject quantity, model digits, serials, dates, HSN/SAC, ranges, accuracy,
resolution, capacity, GST/tax, freight, packing, subtotal and grand total as price.
If uncertain, return products=[] rather than guessing.
Return JSON only:
{"relevant":true,"confidence":0.99,"products":[{"product_name":"","model":"","specifications":"","original_price":null,"discount_percent":null,"purchase_price":null,"currency":"INR","supplier_name":"","price_date":"","confidence":0.99,"price_basis":"EXPLICIT_UNIT_PRICE","price_evidence":""}],"notes":""}
'''


def extract_message_local(email_payload: dict, attachment_texts: list[dict] | None = None, model: str | None = None) -> dict:
    if not LOCAL_AI_ENABLED:
        return {"relevant": True, "confidence": 0.0, "products": [], "notes": "Local AI disabled"}
    chosen = (model or fast_text_model_name()).strip()
    h = health()
    if not h.get("online") or not chosen or not _model_present(h.get("models") or [], chosen):
        return {"relevant": True, "confidence": 0.0, "products": [], "notes": f"Local text model unavailable: {chosen or 'none'}"}
    source = "\n\n".join(
        f"FILE: {a.get('name','')}\n{(a.get('text') or '')[:14000]}" for a in (attachment_texts or []) if a.get("text")
    )[:32000]
    user = f'''FROM: {email_payload.get("sender","")}
DATE: {email_payload.get("received_at","")}
SUBJECT: {email_payload.get("subject","")}
BODY:\n{(email_payload.get("body") or "")[:10000]}

ATTACHMENT/OCR TEXT:\n{source}

SQLITE VERIFIED PATTERN MEMORY (advisory only; NOT current evidence):\n{(email_payload.get("_memory_context") or "")[:3500]}'''
    try:
        parsed = _chat_json(chosen, _TEXT_SYSTEM, user)
        return {"relevant": parsed.get("relevant") is not False, "confidence": float(parsed.get("confidence") or 0),
                "products": _normalize_products(parsed.get("products") or []), "notes": str(parsed.get("notes") or "")[:800], "model": chosen}
    except Exception as exc:
        return {"relevant": True, "confidence": 0.0, "products": [], "notes": f"Local text extraction failed: {exc}"[:800], "model": chosen}


def _vision_images(files: list[dict] | None, max_images: int = 4, max_total_bytes: int = 8_000_000) -> list[str]:
    out = []
    total = 0
    for f in files or []:
        if len(out) >= max_images:
            break
        data = f.get("data") or b""
        mime = str(f.get("mime_type") or "").lower()
        if not data:
            continue
        if mime.startswith("image/"):
            if total + len(data) <= max_total_bytes:
                out.append(base64.b64encode(data).decode("ascii")); total += len(data)
        elif mime == "application/pdf":
            try:
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(data)
                for idx in range(min(len(pdf), max_images-len(out))):
                    pil = pdf[idx].render(scale=1.35).to_pil()
                    buf = io.BytesIO(); pil.save(buf, format="PNG", optimize=True)
                    raw = buf.getvalue()
                    if total + len(raw) <= max_total_bytes:
                        out.append(base64.b64encode(raw).decode("ascii")); total += len(raw)
            except Exception:
                pass
    return out


_VISION_SYSTEM = r'''
You are the LAST-RESORT visual quotation interpreter. Python and dedicated OCR
could not prove the relationship. Read row/column layout from the original page.
Return only explicit supplier product + PRE-TAX UNIT price evidence. Never guess.
Reject Qty, HSN/SAC, GST/tax, freight, totals, model digits, ranges, capacity,
accuracy, dates and serial numbers as prices. If unreadable, products=[].
Return JSON only using the same schema as the text exception model.
'''


def extract_visual_local(email_payload: dict, attachment_texts: list[dict] | None = None,
                         multimodal_files: list[dict] | None = None, model: str | None = None) -> dict:
    chosen = (model or vision_model_name()).strip()
    h = health()
    if not h.get("online") or not chosen or not _model_present(h.get("models") or [], chosen):
        return {"relevant": True, "confidence": 0.0, "products": [], "notes": "Local vision model unavailable", "model": chosen}
    images = _vision_images(multimodal_files or [])
    source = "\n\n".join(f"FILE: {a.get('name','')}\n{(a.get('text') or '')[:10000]}" for a in (attachment_texts or []) if a.get("text"))[:24000]
    user = f'''FROM: {email_payload.get("sender","")}
DATE: {email_payload.get("received_at","")}
SUBJECT: {email_payload.get("subject","")}
BODY:\n{(email_payload.get("body") or "")[:5000]}
READABLE/OCR TEXT:\n{source}
The attached image(s) are original quotation evidence.'''
    try:
        parsed = _chat_json(chosen, _VISION_SYSTEM, user, images=images)
        return {"relevant": parsed.get("relevant") is not False, "confidence": float(parsed.get("confidence") or 0),
                "products": _normalize_products(parsed.get("products") or []), "notes": str(parsed.get("notes") or "")[:800], "model": chosen}
    except Exception as exc:
        return {"relevant": True, "confidence": 0.0, "products": [], "notes": f"Local visual extraction failed: {exc}"[:800], "model": chosen}


def verify_products_with_model(model: str, email_payload: dict, products: list[dict], attachment_texts: list[dict] | None = None, multimodal_files: list[dict] | None = None) -> dict:
    # Kept for API compatibility.  The answer is advisory; app.py always runs
    # accuracy_service.verify_product afterwards and only Python can confirm.
    if not products:
        return {"ok": False, "products": [], "model": model}
    source = "\n\n".join(f"FILE: {a.get('name','')}\n{(a.get('text') or '')[:12000]}" for a in (attachment_texts or []) if a.get("text"))[:28000]
    proposed = [{"index": i, **{k:p.get(k) for k in ("product_name","model","original_price","discount_percent","purchase_price","supplier_name")}} for i,p in enumerate(products)]
    system = r'''You are an advisory second opinion. Verify only explicit current-source evidence. Never guess. Reject Qty, tax/GST, totals, freight, model digits and specifications as price. Return JSON only: {"products":[{"index":0,"accept":true,"original_price":null,"discount_percent":null,"purchase_price":1000,"confidence":0.99,"evidence":""}]}'''
    user = f"SUBJECT: {email_payload.get('subject','')}\nBODY:\n{(email_payload.get('body') or '')[:9000]}\nATTACHMENTS:\n{source}\nPROPOSED:\n{json.dumps(proposed, ensure_ascii=False)}"
    try:
        images = _vision_images(multimodal_files) if model == LOCAL_AI_VISION_MODEL else []
        parsed = _chat_json(model, system, user, images=images)
        return {"ok": True, "model": model, "products": parsed.get("products") or []}
    except Exception as exc:
        return {"ok": False, "model": model, "products": [], "error": str(exc)}


def verify_uncertain_local(email_payload: dict, products: list[dict], attachment_texts: list[dict] | None = None,
                           multimodal_files: list[dict] | None = None, use_vision: bool = False, prefer_fast: bool = False) -> dict:
    if use_vision:
        model = vision_model_name()
    else:
        model = fast_text_model_name() if prefer_fast else deep_text_model_name()
    if not model:
        return {"ok": False, "products": [], "model": "", "error": "model unavailable"}
    return verify_products_with_model(model, email_payload, products, attachment_texts, multimodal_files)


def extract_batch_local(items: list[dict], model: str | None = None) -> dict[str, dict]:
    # Compatibility helper. V21.4 no longer makes batch AI part of the main lane.
    out = {}
    for item in (items or [])[:6]:
        ref = str(item.get("ref"))
        msg = item.get("email") or {}
        out[ref] = extract_message_local(msg, [], model=model or fast_text_model_name())
    return out


def run_local_ensemble(*args: Any, **kwargs: Any) -> list[dict]:
    # Ensembles are intentionally removed from V21.4 normal processing.
    return []
