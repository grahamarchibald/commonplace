import base64
import io
import json
import re
import threading
from datetime import date

import httpx
from PIL import Image, ImageOps

from .config import (
    ANTHROPIC_MODEL,
    OCR_BACKEND,
    OCR_MAX_DIM,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT,
)

SYSTEM_PROMPT = (
    "You transcribe a single photographed page from someone's handwritten journal. "
    "Return the transcript as an ordered list of words, preserving the original "
    "wording, spelling, and word order exactly as written. For each word, assign "
    "a confidence tier: 'high' if you are confident in the reading, 'med' or 'low' "
    "if the handwriting is ambiguous. Flag honestly rather than guessing silently. "
    "For any word not rated 'high', include 2-3 plausible alternate readings in "
    "'alternates'; for 'high' confidence words, 'alternates' must be an empty list.\n\n"
    "Also detect the entry's written date. It is usually handwritten at the top of "
    "the page, most often in day/month/year order (e.g. '7/3/26' means 7 March 2026). "
    "Return 'detected_date' as an ISO 'YYYY-MM-DD' string, or null if no date is "
    "visible. Assume years are 20xx. Set 'date_confidence' to 'high' only when you "
    "clearly read an unambiguous date; use 'med'/'low' when the digits are unclear or "
    "the day/month order is genuinely ambiguous, and 'none' when no date is present."
)

USER_PROMPT = "Transcribe this journal page and detect its date."

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


def _prepare_image(image_bytes: bytes) -> bytes:
    """Downscale an uploaded photo to OCR_MAX_DIM on its longest edge and
    re-encode as JPEG. A full-res phone photo (e.g. 24MP) otherwise overflows a
    local model's context window and is needlessly large/expensive for the
    hosted API; re-encoding also normalizes odd containers (iPhone MPO, PNG).
    The original full-res image on disk is untouched — this only shrinks the
    in-memory copy sent to the model."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        # Phone photos store rotation in EXIF (e.g. orientation=6 = "rotate 90°").
        # Bake it into the pixels so the model sees an upright page, not a sideways
        # one — otherwise it can't read the handwriting and hallucinates.
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((OCR_MAX_DIM, OCR_MAX_DIM))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def transcribe_page(image_bytes: bytes, media_type: str) -> dict:
    """Transcribe a photographed journal page into the ordered word list
    (per-word confidence + alternates, per OCR_PIPELINE.md) plus the detected
    written date and its confidence. Dispatches to the configured OCR backend;
    both backends return the same PAGE_SCHEMA-shaped dict. The image is
    downscaled and re-encoded to JPEG first (see _prepare_image)."""
    jpeg_bytes = _prepare_image(image_bytes)
    # Serialize model calls: a batch upload schedules many background OCR jobs at
    # once, but one local model on a modest machine can only do one at a time.
    # Running them concurrently thrashes memory and makes the queued ones time out,
    # so hold a process-wide lock and let each page take its turn.
    with _OCR_LOCK:
        if OCR_BACKEND == "ollama":
            return _transcribe_ollama(jpeg_bytes)
        return _transcribe_anthropic(jpeg_bytes, "image/jpeg")


_OCR_LOCK = threading.Lock()


OLLAMA_PROMPT = (
    "Transcribe this handwritten journal page verbatim to plain text. "
    "Preserve the original wording, spelling, and line breaks. "
    "Output only the transcription, with no commentary or preamble."
)

# A date written at the top of a page, e.g. "7/3/26" or "7.3.2026" (day/month/year).
_DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")


def _guess_date_from_text(text: str) -> tuple[str | None, str]:
    """Best-effort date from the first few lines, read as day/month/year. Returns
    (iso_date_or_None, confidence). Always 'med' at most so the local backend
    routes to the one-click review step rather than silently applying a guess."""
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


def _transcribe_ollama(image_bytes: bytes) -> dict:
    """Local vision model via Ollama — no API key, runs on-device. On modest
    hardware the per-word JSON schema is prohibitively slow (grammar-constrained
    decoding stalls), so we ask for a plain-text transcription — the model's
    fastest, most accurate mode — and synthesize the PAGE_SCHEMA shape from it.
    Per-word confidence/alternates aren't available in this mode (a hosted-backend
    feature); words are marked 'high' with no alternates. The date is parsed from
    the text and always routed to review."""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    resp = httpx.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": OLLAMA_PROMPT, "images": [b64]}],
            "stream": False,
            "options": {"temperature": 0, "num_ctx": OLLAMA_NUM_CTX},
        },
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    text = resp.json()["message"]["content"].strip()

    detected_date, date_confidence = _guess_date_from_text(text)
    words = [{"text": w, "confidence": "high", "alternates": []} for w in text.split()]
    return {"detected_date": detected_date, "date_confidence": date_confidence, "words": words}


def _transcribe_anthropic(image_bytes: bytes, media_type: str) -> dict:
    """Hosted Claude vision. Uses structured JSON output (`output_config.format`
    + json_schema) to force the shape — not assistant prefill."""
    import anthropic

    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": PAGE_SCHEMA}},
    )
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)
