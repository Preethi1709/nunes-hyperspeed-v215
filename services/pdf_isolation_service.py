from __future__ import annotations

import multiprocessing as mp
import queue
import traceback
from typing import Any


def _pdf_child(content: bytes, filename: str, max_chars: int, out_q) -> None:
    try:
        from services.pdf_service import extract_pdf_bundle
        out_q.put({"ok": True, "bundle": extract_pdf_bundle(content, filename, max_chars)})
    except BaseException as exc:  # process boundary: capture parser crashes safely
        out_q.put({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-3000:]})


def extract_pdf_bundle_isolated(content: bytes, filename: str = "", max_chars: int = 48000, timeout_seconds: int = 45) -> dict[str, Any]:
    """Run PDF parsing in a disposable process.

    Malformed/slow PDFs cannot monopolize a FastAPI worker or mailbox lane. On
    timeout the parser process is terminated and the caller may route the
    original PDF to multimodal AI or Review without blocking other messages.
    """
    timeout_seconds = max(10, min(int(timeout_seconds or 45), 180))
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_pdf_child, args=(content, filename, int(max_chars), out_q), daemon=True)
    proc.start()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        return {"ok": False, "timeout": True, "error": f"PDF parser timeout after {timeout_seconds}s", "bundle": {"text": "", "products": []}}
    try:
        result = out_q.get_nowait()
    except queue.Empty:
        return {"ok": False, "error": f"PDF parser exited with code {proc.exitcode} without a result", "bundle": {"text": "", "products": []}}
    if result.get("ok"):
        return {"ok": True, "timeout": False, "error": "", "bundle": result.get("bundle") or {"text": "", "products": []}}
    return {"ok": False, "timeout": False, "error": result.get("error") or "PDF parser failed", "bundle": {"text": "", "products": []}, "trace": result.get("trace", "")}
