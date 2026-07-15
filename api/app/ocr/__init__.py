"""OCR pipeline entry point.

`transcribe_page` is the single public call: it normalizes the photo, then
dispatches to the configured backend. Every backend returns the same
PAGE_SCHEMA-shaped dict — that contract is what `routes/entries.py`, the
correction UI, and `entries.transcript_json` all depend on.
"""

import io
import re
import threading
from datetime import date

from PIL import Image, ImageOps

from ..config import OCR_BACKEND, OCR_MAX_DIM

# The output contract shared by all backends (and sent verbatim to the hosted
# backend as a structured-output schema).
PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "detected_date": {"type": ["string", "null"], "format": "date"},
        "date_confidence": {"type": "string", "enum": ["high", "med", "low", "none"]},
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "med", "low"]},
                    "alternates": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "confidence", "alternates"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["detected_date", "date_confidence", "words"],
    "additionalProperties": False,
}

# Serialize model calls: a batch upload schedules many background OCR jobs at
# once, but local models on a modest machine can only do one page at a time.
# Running them concurrently thrashes memory and times the queued ones out.
_OCR_LOCK = threading.Lock()

# A date written at the top of a page, e.g. "7/3/26" or "7.3.2026" (day/month/year).
_DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")


def _guess_date_from_text(text: str) -> tuple[str | None, str]:
    """Best-effort date from the first few lines, read as day/month/year. Returns
    (iso_date_or_None, confidence). Always 'med' at most so local backends route
    to the one-click review step rather than silently applying a guess."""
    head = "\n".join(text.splitlines()[:3])
    m = _DATE_RE.search(head)
    if not m:
        return None, "none"
    day, month, year = (int(g) for g in m.groups())
    if len(m.group(3)) <= 2:
        year += 2000
    try:
        return date(year, month, day).isoformat(), "med"
    except ValueError:
        return None, "none"


def _prepare_image(image_bytes: bytes) -> bytes:
    """Downscale an uploaded photo to OCR_MAX_DIM on its longest edge and
    re-encode as JPEG. Phone photos store rotation in EXIF (e.g. orientation=6 =
    "rotate 90°") — bake it into the pixels so the models see an upright page,
    not a sideways one. Re-encoding also normalizes odd containers (iPhone MPO,
    PNG). The original full-res image on disk is untouched — this only shrinks
    the in-memory copy sent to the model."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((OCR_MAX_DIM, OCR_MAX_DIM))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def transcribe_page(image_bytes: bytes, media_type: str) -> dict:
    """Transcribe a photographed journal page into the ordered word list
    (per-word confidence + alternates, per OCR_PIPELINE.md) plus the detected
    written date and its confidence. The image is normalized first (EXIF
    rotation + downscale, see _prepare_image); backends are imported lazily so
    e.g. torch never loads when the hosted backend is configured."""
    jpeg_bytes = _prepare_image(image_bytes)
    with _OCR_LOCK:
        if OCR_BACKEND == "anthropic":
            from . import anthropic_backend

            return anthropic_backend.transcribe(jpeg_bytes, "image/jpeg")
        from . import local_backend

        return local_backend.transcribe(jpeg_bytes)
