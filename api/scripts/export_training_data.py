#!/usr/bin/env python
"""Export (line-crop image -> ground-truth text) pairs for TrOCR fine-tuning.

A line is exportable when EVERY word in it is user-verified — either corrected
in the Entry view (corrections set verified=true) or blessed whole via the
"line is correct" button. Crops are cut from the ORIGINAL photo (EXIF-uprighted,
full resolution) using the normalized bbox stored by the local OCR backend.

Usage:
    .venv/bin/python scripts/export_training_data.py [--out ../training_data]

Output:
    {out}/{entry_id}_{line}.jpg          one crop per verified line
    {out}/manifest.jsonl                 {"image", "text", "entry_id", "line"} per crop

The manifest is rewritten each run (idempotent). Per OCR_PIPELINE.md this set is
the seed data for fine-tuning; a few hundred lines is the realistic floor.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import STORAGE_DIR  # noqa: E402
from app.db import get_conn  # noqa: E402

CROP_PAD = 6  # px of context around each line crop (at full resolution)


def exportable_lines(words: list[dict]) -> dict[int, dict]:
    """Group words by line; keep lines where every word is verified and a bbox
    exists. Returns {line_index: {"text": ..., "bbox": [...]}}."""
    lines: dict[int, list[dict]] = {}
    for w in words:
        if w.get("line") is None:
            continue
        lines.setdefault(w["line"], []).append(w)
    return {
        idx: {"text": " ".join(w["text"] for w in ws), "bbox": ws[0].get("bbox")}
        for idx, ws in lines.items()
        if ws and ws[0].get("bbox") and all(w.get("verified") for w in ws)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=Path(__file__).resolve().parents[2] / "training_data",
        type=Path,
        help="output directory (default: <repo>/training_data)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.transcript_json,
                      (SELECT ei.storage_key FROM entry_images ei
                       WHERE ei.entry_id = e.id ORDER BY ei.page_order LIMIT 1) AS storage_key
               FROM entries e
               WHERE e.transcript_json IS NOT NULL"""
        )
        rows = cur.fetchall()

    manifest = []
    entries_hit = 0
    for row in rows:
        lines = exportable_lines(row["transcript_json"])
        if not lines or not row["storage_key"]:
            continue
        src = STORAGE_DIR / row["storage_key"]
        if not src.exists():
            print(f"skip {row['id']}: missing image {src}", file=sys.stderr)
            continue

        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            for idx, line in sorted(lines.items()):
                x0, y0, x1, y1 = line["bbox"]
                box = (
                    max(0, int(x0 * img.width) - CROP_PAD),
                    max(0, int(y0 * img.height) - CROP_PAD),
                    min(img.width, int(x1 * img.width) + CROP_PAD),
                    min(img.height, int(y1 * img.height) + CROP_PAD),
                )
                name = f"{row['id']}_{idx}.jpg"
                img.crop(box).save(args.out / name, format="JPEG", quality=92)
                manifest.append(
                    {"image": name, "text": line["text"], "entry_id": str(row["id"]), "line": idx}
                )
        entries_hit += 1

    with open(args.out / "manifest.jsonl", "w") as f:
        for m in manifest:
            f.write(json.dumps(m) + "\n")

    print(f"exported {len(manifest)} verified lines from {entries_hit} entries -> {args.out}")


if __name__ == "__main__":
    main()
