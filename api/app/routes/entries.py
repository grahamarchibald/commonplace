import json
from datetime import date

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import get_conn
from ..ocr import transcribe_page
from ..storage import save_entry_image

router = APIRouter(prefix="/entries", tags=["entries"])


def run_ocr(entry_id: str, image_bytes: bytes, media_type: str, date_locked: bool = False):
    try:
        result = transcribe_page(image_bytes, media_type)
    except Exception:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE entries SET status = 'error', updated_at = now() WHERE id = %s",
                (entry_id,),
            )
            conn.commit()
        return

    words = result["words"]
    raw_text = " ".join(w["text"] for w in words)

    # Only trust the detected date when the model is confident; otherwise flag
    # for a quick user confirmation (prefilled with the model's best guess).
    detected = result.get("detected_date")
    confident = result.get("date_confidence") == "high" and detected

    with get_conn() as conn, conn.cursor() as cur:
        if date_locked:
            # User supplied the date at upload — keep it, just store the guess.
            cur.execute(
                """UPDATE entries
                   SET transcript_json = %s::jsonb, raw_text = %s, status = 'ready',
                       detected_date = %s, needs_date_review = false, updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, detected, entry_id),
            )
        elif confident:
            cur.execute(
                """UPDATE entries
                   SET transcript_json = %s::jsonb, raw_text = %s, status = 'ready',
                       written_date = %s, detected_date = %s, needs_date_review = false,
                       updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, detected, detected, entry_id),
            )
        else:
            cur.execute(
                """UPDATE entries
                   SET transcript_json = %s::jsonb, raw_text = %s, status = 'ready',
                       detected_date = %s, needs_date_review = true, updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, detected, entry_id),
            )
        conn.commit()


@router.post("/upload", status_code=201)
async def upload_entry(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    written_date: date | None = Form(default=None),
):
    """Photograph a page -> store it -> transcribe it in the background
    (few-shot VLM OCR call, per OCR_PIPELINE.md). The written date is detected
    from the page during OCR; pass `written_date` only to override that."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "file must be an image")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    # Placeholder until OCR detects the real date (written_date is NOT NULL).
    # If the user supplied a date explicitly, keep it and skip date review.
    placeholder = written_date or date.today()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entries (written_date, status) VALUES (%s, 'processing') RETURNING id",
            (placeholder,),
        )
        entry_id = str(cur.fetchone()["id"])

        storage_key, width, height = save_entry_image(entry_id, file.filename or "page.jpg", data)

        cur.execute(
            """INSERT INTO entry_images (entry_id, storage_key, page_order, width, height)
               VALUES (%s, %s, 0, %s, %s) RETURNING id""",
            (entry_id, storage_key, width, height),
        )
        image_id = str(cur.fetchone()["id"])
        conn.commit()

    background_tasks.add_task(run_ocr, entry_id, data, file.content_type, written_date is not None)

    return {
        "entry_id": entry_id,
        "image_id": image_id,
        "storage_key": storage_key,
        "status": "processing",
    }


@router.get("")
def list_entries():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.written_date, e.captured_at, e.status, e.mood,
                      e.needs_date_review, e.detected_date,
                      count(ei.id) AS image_count
               FROM entries e
               LEFT JOIN entry_images ei ON ei.entry_id = e.id
               GROUP BY e.id
               ORDER BY e.written_date DESC, e.captured_at DESC"""
        )
        return cur.fetchall()


@router.get("/{entry_id}")
def get_entry(entry_id: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM entries WHERE id = %s", (entry_id,))
        entry = cur.fetchone()
        if not entry:
            raise HTTPException(404, "entry not found")

        cur.execute(
            "SELECT id, storage_key, page_order, width, height FROM entry_images "
            "WHERE entry_id = %s ORDER BY page_order",
            (entry_id,),
        )
        entry["images"] = cur.fetchall()
        return entry


class DateConfirmation(BaseModel):
    written_date: date


@router.post("/{entry_id}/date")
def confirm_date(entry_id: str, body: DateConfirmation):
    """Confirm/override the written date for an entry that needed review
    (the low-friction path: the user only lands here when OCR was unsure)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE entries
               SET written_date = %s, needs_date_review = false, updated_at = now()
               WHERE id = %s""",
            (body.written_date, entry_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "entry not found")
        conn.commit()
    return {"ok": True, "written_date": body.written_date.isoformat()}


class WordCorrection(BaseModel):
    word_index: int
    corrected_text: str


@router.post("/{entry_id}/corrections")
def correct_word(entry_id: str, correction: WordCorrection):
    """Apply a user correction to one transcribed word, logging it to
    `corrections` (the future fine-tuning dataset, per OCR_PIPELINE.md)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT transcript_json FROM entries WHERE id = %s", (entry_id,))
        row = cur.fetchone()
        if not row or row["transcript_json"] is None:
            raise HTTPException(404, "entry not found or not yet transcribed")

        words = row["transcript_json"]
        idx = correction.word_index
        if idx < 0 or idx >= len(words):
            raise HTTPException(400, "word_index out of range")

        original = words[idx]
        cur.execute(
            """INSERT INTO corrections (entry_id, word_index, model_text, corrected_text, model_confidence)
               VALUES (%s, %s, %s, %s, %s)""",
            (entry_id, idx, original["text"], correction.corrected_text, original["confidence"]),
        )

        words[idx] = {"text": correction.corrected_text, "confidence": "high", "alternates": []}
        raw_text = " ".join(w["text"] for w in words)
        cur.execute(
            "UPDATE entries SET transcript_json = %s::jsonb, raw_text = %s, updated_at = now() WHERE id = %s",
            (json.dumps(words), raw_text, entry_id),
        )
        conn.commit()

    return {"ok": True}
