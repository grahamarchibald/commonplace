# Commonplace

Handwritten journal → searchable personal knowledge base. Design/build spec lives in the handoff
bundle at `../design_handoff_commonplace` (README, ARCHITECTURE, DATA_MODEL, OCR_PIPELINE,
NLP_PIPELINE, ROADMAP).

## Status: Milestone 1

Postgres schema is up and there's a bare-bones capture flow (upload a photo → stored in
`entries`/`entry_images`, `status='processing'`) — no OCR/extraction yet. Goal per the roadmap:
get real photographed pages flowing into the system before building any ML pipeline.

## Local setup

Postgres 16 is installed via Homebrew and running as a service (`brew services start postgresql@16`),
with a `commonplace` database already created.

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# apply schema (only needed once, or after schema.sql changes)
PATH="/usr/local/opt/postgresql@16/bin:$PATH" psql -d commonplace -f db/schema.sql

cp ../.env.example .env   # defaults are fine for local dev

uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — minimal upload form (photo + written date). Uploaded photos are
saved to `../storage/entries/{entry_id}/` and rows are written to `entries`/`entry_images`.
`GET /entries` lists what's been captured so far.

## Next (Milestone 2, per ROADMAP.md)

Wire the few-shot VLM OCR call into the upload flow as a background job, and build the real
Entry view with confidence-highlighted transcription + correction UI.
