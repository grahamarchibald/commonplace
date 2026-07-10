from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..db import get_conn
from ..storage import save_entry_image

router = APIRouter(prefix="/entries", tags=["entries"])


@router.post("/upload", status_code=201)
async def upload_entry(
    file: UploadFile = File(...),
    written_date: date = Form(default_factory=date.today),
):
    """Milestone-1 capture endpoint: no OCR yet, just get a real photographed
    page into entries/entry_images with status='processing'."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "file must be an image")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO entries (written_date, status) VALUES (%s, 'processing') RETURNING id",
            (written_date,),
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
