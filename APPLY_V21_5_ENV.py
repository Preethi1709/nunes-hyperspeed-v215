from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULTS = {
    "V215_TEXT_WORKERS": "6",
    "V215_PDF_WORKERS": "2",
    "V215_TABLE_WORKERS": "2",
    "V215_DOC_WORKERS": "2",
    "V215_STAGE2_BATCH_MULTIPLIER": "2",
    "V215_TASK_STALE_SECONDS": "1200",
    "V215_OCR_PROBE_PAGES": "2",
    "V215_OCR_MAX_PAGES": "8",
    # Keep the proven V21.4 Python/OCR/AI defaults that V21.5 reuses internally.
    "V214_MAIN_WORKERS": "6",
    "V214_OCR_ENABLED": "true",
    "V214_OCR_WORKERS": "1",
    "V214_OCR_MAX_PAGES": "8",
    "V214_MAX_AI_ATTEMPTS": "3",
    "LOCAL_AI_ENABLED": "true",
    "LOCAL_AI_FAST_MODEL": "qwen3:1.7b",
    "LOCAL_AI_DEEP_MODEL": "qwen3:4b",
    "LOCAL_AI_VISION_MODEL": "qwen3-vl:4b",
    "LOCAL_AI_HEAVY_KEEP_ALIVE": "0",
}


def update_env(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    lines = text.splitlines()
    keys_present = set()
    key_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in lines:
        m = key_re.match(line)
        if m:
            keys_present.add(m.group(1))
    lines.append("")
    lines.append("# V21.5 SURREAL HYPERSPEED specialist-lane settings")
    for key, value in DEFAULTS.items():
        if key not in keys_present:
            lines.append(f'{key}="{value}"')
    path.write_text("\n".join(lines).rstrip()+"\n", encoding="utf-8")
    print(f"V21.5 ENV UPDATED SAFELY: {path}")
    print("Existing Gmail / Sheets / OAuth / Ollama secrets were preserved.")


if __name__ == "__main__":
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(__file__).resolve().parent / ".env"
    update_env(target)
