# Commonplace

Turn photographed handwritten journal pages into a searchable personal knowledge base —
transcribed entries, recurring people and themes, mood and insight tracking.

The full design + build spec lives in a sibling handoff bundle
(`../design_handoff_commonplace/`: README, ARCHITECTURE, DATA_MODEL, OCR_PIPELINE, NLP_PIPELINE,
ROADMAP). `CLAUDE.md` in this repo summarizes the architecture and conventions.

## Status

**Milestone 2 — real transcription + correction loop, plus per-entry mood.**

- Capture: photograph one or more pages (batch upload) → stored to disk + Postgres.
- OCR: photos are EXIF-rotated upright, downscaled, and transcribed in the background;
  failures surface their actual error on the entry card.
- Date auto-detection: read off the page; anything uncertain routes to a one-click confirm.
- Corrections: fixing a word updates the transcript and logs to the `corrections` table —
  the future fine-tuning dataset.
- Sentiment: each transcript gets a `mood` label + `mood_score` (VADER + a small local
  emotion classifier), shown as a pill on the entry card.

Not built yet (Milestones 3–6): people/theme extraction, knowledge graph, search, insights,
real React/React Native clients. The `app/static/*.html` pages are throwaway scaffolding.

## Prerequisites

- **Python 3.12** — exactly. The local OCR stack (PaddlePaddle) does not support 3.13 yet.
- **PostgreSQL 16** running locally.
- ~2 GB disk for local model caches (downloaded automatically on first OCR).

## Setup

```bash
# 1. Database
createdb commonplace
psql -d commonplace -f api/db/schema.sql

# 2. Python deps (Python 3.12!)
cd api
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Config
cp ../.env.example .env    # defaults to the local OCR backend; set DATABASE_URL if yours differs
```

## OCR backends (in `api/.env`)

**`local` (default — free, fully on-device):** PaddleOCR's detector finds the handwritten
text lines; Microsoft **TrOCR** (`trocr-base-handwritten`) transcribes each line crop.
TrOCR is a dedicated recognition model, not a generative VLM — when it can't read a line it
produces a visible garble rather than inventing plausible prose. Line-level confidence comes
from the model's beam scores and drives the correction UI tiers. Dates are parsed from the
transcript and always routed to the one-click confirm. Expect imperfect accuracy on stylized
multi-color pages — every correction you make accumulates ground truth for the fine-tuning
path in `OCR_PIPELINE.md`. Knobs: `TROCR_MODEL` (`-small` faster / `-large` more accurate)
and `OCR_MAX_DIM`.

**`anthropic` (hosted, most accurate):** Claude vision with structured output — true per-word
confidence + alternate readings, seconds per page, ~1–2¢/page. Requires `ANTHROPIC_API_KEY`.

Both backends return the same transcript contract, so switching is a one-line `.env` change.

## Run

```bash
cd api && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** — the Capture page. Select one *or several* page photos
(PNG/JPG; HEIC not yet supported) and upload. Transcription runs in the background, one page
at a time; entries flip from `processing` to `ready` (or `error` with the reason shown).

CLI upload:

```bash
curl -X POST http://localhost:8000/entries/upload -F "file=@/path/to/page.jpg"
# optionally pin the date and skip detection:  -F "written_date=2026-03-07"
```

## API quick reference

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/entries/upload` | Upload a page photo (multipart `file`, optional `written_date`) |
| `GET`  | `/entries` | List entries (newest first) |
| `GET`  | `/entries/{id}` | One entry + images + transcript |
| `POST` | `/entries/{id}/date` | Confirm/override the written date |
| `POST` | `/entries/{id}/corrections` | Correct one transcribed word (logs to `corrections`) |

## Layout

```
api/
  app/
    main.py               FastAPI app + static UI mount
    config.py             env/config (OCR backend selection)
    ocr/
      __init__.py         transcribe_page dispatch, image prep (EXIF rotate + downscale),
                          shared PAGE_SCHEMA contract, OCR job serialization
      local_backend.py    Paddle-detect + TrOCR-read (free, on-device)
      anthropic_backend.py  hosted Claude vision
    nlp/
      sentiment.py        mood + mood_score (VADER + emotion classifier)
    storage.py            image storage seam (local disk today, S3/R2 later)
    routes/entries.py     upload, OCR background job, date + word corrections
    static/               throwaway HTML UI (capture page)
  db/
    schema.sql            canonical schema for a fresh database
    migrations/           incremental, idempotent migrations (fold back into schema.sql)
storage/entries/          uploaded page images (gitignored)
```

`api/.env` is gitignored — each machine keeps its own DB URL, backend choice, and any API key.
