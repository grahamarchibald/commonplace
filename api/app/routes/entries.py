import json
import traceback
from datetime import date

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import get_conn
from ..nlp.sentiment import analyze as analyze_sentiment
from ..ocr import transcribe_page
from ..storage import save_entry_image

router = APIRouter(prefix="/entries", tags=["entries"])


def run_ocr(entry_id: str, image_bytes: bytes, media_type: str, date_locked: bool = False):
    try:
        result = transcribe_page(image_bytes, media_type)
    except Exception as e:
        # Surface why OCR failed instead of a bare status='error': store a short
        # message on the entry (shown in the UI) and log the full traceback.
        traceback.print_exc()
        detail = f"{type(e).__name__}: {e}"[:500]
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE entries SET status = 'error', error_detail = %s, updated_at = now() "
                "WHERE id = %s",
                (detail, entry_id),
            )
            conn.commit()
        return

    words = result["words"]
    raw_text = " ".join(w["text"] for w in words)

    # Mood extraction (NLP_PIPELINE.md) — never fails the entry; (None, None) on error.
    mood, mood_score = analyze_sentiment(raw_text)

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
                       mood = %s, mood_score = %s,
                       detected_date = %s, needs_date_review = false, updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, mood, mood_score, detected, entry_id),
            )
        elif confident:
            cur.execute(
                """UPDATE entries
                   SET transcript_json = %s::jsonb, raw_text = %s, status = 'ready',
                       mood = %s, mood_score = %s,
                       written_date = %s, detected_date = %s, needs_date_review = false,
                       updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, mood, mood_score, detected, detected, entry_id),
            )
        else:
            cur.execute(
                """UPDATE entries
                   SET transcript_json = %s::jsonb, raw_text = %s, status = 'ready',
                       mood = %s, mood_score = %s,
                       detected_date = %s, needs_date_review = true, updated_at = now()
                   WHERE id = %s""",
                (json.dumps(words), raw_text, mood, mood_score, detected, entry_id),
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
                      e.needs_date_review, e.detected_date, e.error_detail,
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

        # Merge (don't replace): keep line/bbox geometry — the training-data
        # exporter needs it to crop this line from the original photo.
        # `verified` marks user-blessed ground truth, distinct from the model's
        # own 'high' claims.
        words[idx] = {
            **original,
            "text": correction.corrected_text,
            "confidence": "high",
            "alternates": [],
            "verified": True,
        }
        raw_text = " ".join(w["text"] for w in words)
        # Text changed → keep the mood honest by recomputing it (cheap, local).
        mood, mood_score = analyze_sentiment(raw_text)
        cur.execute(
            """UPDATE entries SET transcript_json = %s::jsonb, raw_text = %s,
                   mood = %s, mood_score = %s, updated_at = now()
               WHERE id = %s""",
            (json.dumps(words), raw_text, mood, mood_score, entry_id),
        )
        conn.commit()

    return {"ok": True}


class LineCorrection(BaseModel):
    line: int
    corrected_text: str


@router.post("/{entry_id}/lines")
def correct_line(entry_id: str, body: LineCorrection):
    """Replace one detected line's words with user-supplied ground truth.
    Handles what word-level corrections can't: deleting spurious tokens,
    merging/splitting words, or (empty text) removing a line entirely — e.g.
    when the detector boxed a drawing. The old->new line pair is logged to
    `corrections` (line-level rows anchor at the line's first word index) and
    the new words are stamped verified for the training exporter."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT transcript_json FROM entries WHERE id = %s", (entry_id,))
        row = cur.fetchone()
        if not row or row["transcript_json"] is None:
            raise HTTPException(404, "entry not found or not yet transcribed")

        words = row["transcript_json"]
        line_words = [(i, w) for i, w in enumerate(words) if w.get("line") == body.line]
        if not line_words:
            raise HTTPException(400, "no words on that line")

        old_text = " ".join(w["text"] for _, w in line_words)
        tiers = [w["confidence"] for _, w in line_words]
        worst = "low" if "low" in tiers else ("med" if "med" in tiers else "high")
        bbox = line_words[0][1].get("bbox")

        cur.execute(
            """INSERT INTO corrections (entry_id, word_index, model_text, corrected_text, model_confidence)
               VALUES (%s, %s, %s, %s, %s)""",
            (entry_id, line_words[0][0], old_text, body.corrected_text.strip(), worst),
        )

        new_words = [
            {"text": t, "confidence": "high", "alternates": [], "line": body.line,
             "bbox": bbox, "verified": True}
            for t in body.corrected_text.split()
        ]
        rebuilt, inserted = [], False
        for w in words:
            if w.get("line") == body.line:
                if not inserted:
                    rebuilt.extend(new_words)
                    inserted = True
                continue
            rebuilt.append(w)

        raw_text = " ".join(w["text"] for w in rebuilt)
        mood, mood_score = analyze_sentiment(raw_text)
        cur.execute(
            """UPDATE entries SET transcript_json = %s::jsonb, raw_text = %s,
                   mood = %s, mood_score = %s, updated_at = now()
               WHERE id = %s""",
            (json.dumps(rebuilt), raw_text, mood, mood_score, entry_id),
        )
        conn.commit()
    return {"ok": True, "line": body.line, "words": len(new_words)}


class LineVerification(BaseModel):
    line: int


@router.post("/{entry_id}/verify-line")
def verify_line(entry_id: str, body: LineVerification):
    """Mark every word of one detected line as user-verified ground truth —
    "this line is actually correct". Verified lines (whether confirmed here or
    fixed word-by-word via corrections) are what the training-data exporter
    turns into (line crop -> text) fine-tuning pairs."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT transcript_json FROM entries WHERE id = %s", (entry_id,))
        row = cur.fetchone()
        if not row or row["transcript_json"] is None:
            raise HTTPException(404, "entry not found or not yet transcribed")

        words = row["transcript_json"]
        hit = False
        for w in words:
            if w.get("line") == body.line:
                w["verified"] = True
                hit = True
        if not hit:
            raise HTTPException(400, "no words on that line")

        cur.execute(
            "UPDATE entries SET transcript_json = %s::jsonb, updated_at = now() WHERE id = %s",
            (json.dumps(words), entry_id),
        )
        conn.commit()
    return {"ok": True, "line": body.line}
