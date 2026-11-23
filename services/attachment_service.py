from io import BytesIO
from pathlib import Path
import csv


def _decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return content.decode("utf-8", errors="replace")


def extract_attachment_text(filename: str, content: bytes, max_chars: int = 30000) -> str:
    """Extract safe text from common supplier attachments.

    PDF remains handled by pdf_service because it has quotation-table logic.
    This helper improves XLSX/CSV/DOCX/TXT coverage without OCR or guessing.
    """
    ext = Path(filename or "").suffix.lower()

    if ext in (".txt", ".csv", ".tsv"):
        text = _decode_text(content)
        return text[:max_chars]

    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
            wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
            chunks = []
            for ws in wb.worksheets[:8]:
                chunks.append(f"--- SHEET: {ws.title} ---")
                rows_seen = 0
                for row in ws.iter_rows(values_only=True):
                    cells = [str(v).strip() if v is not None else "" for v in row[:30]]
                    if any(cells):
                        chunks.append(" | ".join(cells))
                    rows_seen += 1
                    if rows_seen >= 300 or len("\n".join(chunks)) >= max_chars:
                        break
                if len("\n".join(chunks)) >= max_chars:
                    break
            return "\n".join(chunks)[:max_chars]
        except Exception:
            return ""

    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(BytesIO(content))
            chunks = []
            for p in doc.paragraphs:
                t = " ".join((p.text or "").split())
                if t:
                    chunks.append(t)
            for table in doc.tables[:20]:
                for row in table.rows[:200]:
                    cells = [" ".join((c.text or "").split()) for c in row.cells]
                    if any(cells):
                        chunks.append(" | ".join(cells))
                    if len("\n".join(chunks)) >= max_chars:
                        break
            return "\n".join(chunks)[:max_chars]
        except Exception:
            return ""

    return ""
