import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .config import STORAGE_DIR


def save_entry_image(entry_id: str, filename: str, data: bytes) -> tuple[str, int | None, int | None]:
    """Save an uploaded page photo to local disk.

    Returns (storage_key, width, height). storage_key is a path relative to
    STORAGE_DIR — swap this function for an S3/R2 upload later without
    touching callers, since entry_images.storage_key just needs to keep
    meaning "where to find the bytes".
    """
    entry_dir = STORAGE_DIR / entry_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix or ".jpg"
    disk_name = f"{uuid.uuid4()}{ext}"
    dest = entry_dir / disk_name
    dest.write_bytes(data)

    width = height = None
    try:
        with Image.open(dest) as img:
            width, height = img.size
            # Small upright thumbnail for the entry cards / date-confirm UI.
            # Path is a convention ({entry_id}/thumb.jpg, served via /files) —
            # no schema column needed.
            thumb = ImageOps.exif_transpose(img).convert("RGB")
            thumb.thumbnail((480, 480))
            thumb.save(entry_dir / "thumb.jpg", format="JPEG", quality=80)
    except Exception:
        pass

    storage_key = f"{entry_id}/{disk_name}"
    return storage_key, width, height
