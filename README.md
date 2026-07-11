# Commonplace

Turn photographed handwritten journal pages into a searchable personal knowledge base —
transcribed entries, recurring people and themes, mood and insight tracking.

The full design + build spec lives in a sibling handoff bundle
(`../design_handoff_commonplace/`: README, ARCHITECTURE, DATA_MODEL, OCR_PIPELINE, NLP_PIPELINE,
ROADMAP). `CLAUDE.md` in this repo summarizes the architecture and conventions.

## Status

**Milestone 2 — real transcription + correction loop.**

- Capture: photograph a page → stored to disk + Postgres (`entries` / `entry_images`).
- OCR: a vision model transcribes the page in the background into an ordered word list with
  per-word confidence + alternate readings, plus the written date read off the page.
- Date auto-detection: a confident date is applied automatically; an unclear one routes to a
  one-click confirm instead of blocking the upload.
- Corrections: fixing a word updates the transcript and logs the change to a `corrections`
  table (the future fine-tuning dataset).

Not built yet (Milestones 3–6): entity/theme/mood extraction, knowledge graph, search, insights,
and the real React / React Native clients. The current `app/static/*.html` pages are throwaway
scaffolding to exercise the pipeline.

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 16** running locally
- An **OCR backend** — pick one during setup below:
  - **Local (free, no API key):** [Ollama](https://ollama.com) with a Qwen2.5-VL model.
    On Apple Silicon, install the **native** app from ollama.com — the Homebrew `/usr/local`
    build runs under Rosetta with no GPU and is far slower.
  - **Hosted (best quality):** an Anthropic API key (billed per call, separate from any
    Claude subscription).

## Setup

```bash
# 1. Database (any local Postgres 16; example uses a Homebrew install)
createdb commonplace
psql -d commonplace -f api/db/schema.sql          # provision a fresh DB

# 2. Python deps
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Config
cp ../.env.example .env
#    Edit .env: set DATABASE_URL if yours differs, and choose an OCR backend (next section).
```

### Choose an OCR backend (in `api/.env`)

**Local Ollama (default in `.env.example`):**

```bash
# .env
OCR_BACKEND=ollama
OLLAMA_MODEL=qwen2.5vl:3b   # 3B fits an 8 GB machine; use qwen2.5vl:7b if you have more RAM
```

```bash
# then, one-time:
ollama pull qwen2.5vl:3b
```

Expect ~1–2 min per page on a small machine, and rougher transcription than the hosted model.
Because small local models over-claim date confidence, the local backend never auto-applies a
date — you always get the one-click confirm.

**Hosted Anthropic:**

```bash
# .env
OCR_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-opus-4-8   # optional override
```

## Run

```bash
cd api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** — the Capture page. Uploaded photos are saved to
`storage/entries/{entry_id}/` and served back via `/files`.

## Uploading pages

- **In the browser:** open http://localhost:8000, choose a page photo, and upload. The date is
  read off the page automatically; you're only asked to confirm it when the read is unclear.
- **From the command line:**

  ```bash
  curl -X POST http://localhost:8000/entries/upload -F "file=@/path/to/page.jpg"
  # optionally pin the date and skip detection:  -F "written_date=2026-03-07"
  ```

Transcription runs in the background, so the upload returns immediately with `status:processing`;
the entry flips to `ready` (or `error`) once OCR finishes.

## API quick reference

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/entries/upload` | Upload a page photo (multipart `file`, optional `written_date`) |
| `GET`  | `/entries` | List entries (newest first) |
| `GET`  | `/entries/{id}` | One entry + its images and transcript |
| `POST` | `/entries/{id}/date` | Confirm/override the written date |
| `POST` | `/entries/{id}/corrections` | Correct one transcribed word (logs to `corrections`) |

## Layout

```
api/
  app/
    main.py            FastAPI app + static UI mount
    config.py          env/config (OCR backend selection)
    ocr.py             vision-model OCR (ollama | anthropic), returns PAGE_SCHEMA
    storage.py         image storage seam (local disk today, S3/R2 later)
    routes/entries.py  upload, OCR background job, list/get, date + word corrections
    static/            throwaway HTML UI (capture page)
  db/
    schema.sql         canonical schema for a fresh database
    migrations/        incremental, idempotent migrations (fold back into schema.sql)
storage/entries/       uploaded page images (gitignored)
```

`api/.env` is gitignored — each machine keeps its own DB URL, backend choice, and any API key.
