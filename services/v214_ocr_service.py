from __future__ import annotations

import io
import os
import shutil
import threading
from typing import Any

OCR_ENABLED = (os.getenv("V214_OCR_ENABLED") or "true").strip().lower() in {"1","true","yes","on"}
OCR_MAX_PAGES = max(1, min(20, int(os.getenv("V214_OCR_MAX_PAGES", "8"))))
OCR_RENDER_SCALE = max(1.0, min(2.5, float(os.getenv("V214_OCR_RENDER_SCALE", "1.55"))))
OCR_WORKERS = max(1, min(2, int(os.getenv("V214_OCR_WORKERS", "1"))))
_OCR_GATE = threading.BoundedSemaphore(OCR_WORKERS)
_ENGINE = None
_ENGINE_NAME = ""


def _load_engine():
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is not None:
        return _ENGINE, _ENGINE_NAME
    if not OCR_ENABLED:
        return None, "disabled"
    # RapidOCR is preferred because it is a small dedicated OCR runtime and does
    # not keep a multi-billion-parameter VLM resident in RAM.
    for modname in ("rapidocr", "rapidocr_onnxruntime"):
        try:
            mod = __import__(modname, fromlist=["RapidOCR"])
            cls = getattr(mod, "RapidOCR")
            _ENGINE = cls()
            _ENGINE_NAME = modname
            return _ENGINE, _ENGINE_NAME
        except Exception:
            continue
    # Optional Tesseract fallback when already installed on the Windows PC.
    try:
        import pytesseract  # type: ignore
        if shutil.which("tesseract"):
            _ENGINE = pytesseract
            _ENGINE_NAME = "tesseract"
            return _ENGINE, _ENGINE_NAME
    except Exception:
        pass
    return None, "unavailable"


def health() -> dict:
    engine, name = _load_engine()
    return {
        "enabled": bool(OCR_ENABLED),
        "available": engine is not None,
        "engine": name,
        "max_pages": OCR_MAX_PAGES,
        "workers": OCR_WORKERS,
        "role": "scan-to-text only; never confirms product/price",
    }


def _rapid_lines(engine, image) -> tuple[list[str], float]:
    """Run RapidOCR and normalize both legacy v1 and modern v3 outputs."""
    try:
        result = engine(image)
    except TypeError:
        result = engine.run(image)

    # RapidOCR 3.x returns RapidOCROutput with txts/scores/boxes.
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if txts is not None:
        lines: list[str] = []
        score_seq = list(scores) if scores is not None else [1.0] * len(txts)
        for idx, raw_text in enumerate(txts):
            text = str(raw_text or "").strip()
            try:
                score = float(score_seq[idx]) if idx < len(score_seq) else 1.0
            except Exception:
                score = 0.0
            if text and score >= 0.35:
                lines.append(text)

        elapsed = 0.0
        elapse_list = getattr(result, "elapse_list", None)
        if elapse_list:
            try:
                elapsed = float(sum(float(x or 0) for x in elapse_list))
            except Exception:
                elapsed = 0.0
        return lines, elapsed

    # rapidocr-onnxruntime 1.x usually returns (rows, elapsed).
    elapsed = 0.0
    payload = result
    if isinstance(result, tuple) and len(result) >= 1:
        payload = result[0]
        try:
            elapsed = float(result[1] or 0)
        except Exception:
            elapsed = 0.0

    lines: list[str] = []
    for row in payload or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            text = str(row[1] or "").strip()
            score = 1.0
            if len(row) >= 3:
                try:
                    score = float(row[2] or 0)
                except Exception:
                    score = 0.0
            if text and score >= 0.35:
                lines.append(text)
        elif isinstance(row, dict):
            text = str(row.get("text") or row.get("rec_text") or "").strip()
            try:
                score = float(row.get("score") or row.get("rec_score") or 1.0)
            except Exception:
                score = 0.0
            if text and score >= 0.35:
                lines.append(text)
    return lines, elapsed


def _ocr_pil(pil_image) -> tuple[str, dict]:
    engine, name = _load_engine()
    if engine is None:
        return "", {"ok": False, "engine": name, "error": "Dedicated OCR runtime unavailable"}
    if name == "tesseract":
        try:
            text = engine.image_to_string(pil_image)
            return str(text or ""), {"ok": bool(text), "engine": name}
        except Exception as exc:
            return "", {"ok": False, "engine": name, "error": str(exc)[:500]}
    try:
        import numpy as np  # type: ignore
        arr = np.asarray(pil_image.convert("RGB"))
        lines, elapsed = _rapid_lines(engine, arr)
        text = "\n".join(lines)
        return text, {"ok": bool(text), "engine": name, "elapsed": elapsed, "lines": len(lines)}
    except Exception as exc:
        return "", {"ok": False, "engine": name, "error": str(exc)[:500]}


def ocr_image_bytes(data: bytes, name: str = "image") -> dict:
    if not data:
        return {"ok": False, "text": "", "engine": health().get("engine"), "error": "empty image"}
    try:
        from PIL import Image
        pil = Image.open(io.BytesIO(data))
        text, meta = _ocr_pil(pil)
        return {**meta, "text": text, "name": name, "pages": 1 if text else 0}
    except Exception as exc:
        return {"ok": False, "text": "", "engine": health().get("engine"), "error": str(exc)[:500]}


def ocr_pdf_bytes(data: bytes, name: str = "document.pdf", max_pages: int | None = None, start_page: int = 0) -> dict:
    if not data:
        return {"ok": False, "text": "", "engine": health().get("engine"), "error": "empty pdf"}
    engine, engine_name = _load_engine()
    if engine is None:
        return {"ok": False, "text": "", "engine": engine_name, "error": "Dedicated OCR runtime unavailable"}
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        start = max(0, min(int(start_page or 0), max(0, len(pdf)-1))) if len(pdf) else 0
        limit = min(len(pdf), start + int(max_pages or OCR_MAX_PAGES))
        chunks = []
        pages_done = 0
        pages_with_text = 0
        for idx in range(start, limit):
            page = pdf[idx]
            bitmap = page.render(scale=OCR_RENDER_SCALE)
            pil = bitmap.to_pil()
            text, meta = _ocr_pil(pil)
            if text.strip():
                chunks.append(f"[PAGE {idx+1}]\n{text.strip()}")
                pages_with_text += 1
            pages_done += 1
        joined = "\n\n".join(chunks)
        return {
            "ok": bool(joined), "text": joined, "engine": engine_name,
            "pages": pages_done, "pages_with_text": pages_with_text, "page_count": len(pdf),
            "start_page": start + 1 if len(pdf) else 0,
            "end_page": limit,
            "truncated": limit < len(pdf),
        }
    except Exception as exc:
        return {"ok": False, "text": "", "engine": engine_name, "error": str(exc)[:500]}


def ocr_multimodal_files(files: list[dict] | None) -> dict:
    # OCR is intentionally bounded independently from the two Rescue workers.
    # This lets one Rescue worker continue parsing text/Excel while one CPU-heavy
    # scan is being recognized, without launching multiple OCR engines at once.
    with _OCR_GATE:
        texts = []
        results = []
        for f in files or []:
            data = f.get("data") or b""
            mime = str(f.get("mime_type") or "").lower()
            name = str(f.get("name") or "file")
            if not data:
                continue
            if mime == "application/pdf" or name.lower().endswith(".pdf"):
                r = ocr_pdf_bytes(data, name)
            elif mime.startswith("image/"):
                r = ocr_image_bytes(data, name)
            else:
                continue
            results.append(r)
            if r.get("text"):
                texts.append({"name": name, "text": r.get("text") or "", "ocr_engine": r.get("engine")})
        return {"ok": any(r.get("ok") for r in results), "texts": texts, "results": results, "health": health()}
