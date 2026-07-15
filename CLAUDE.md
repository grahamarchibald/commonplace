# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Commonplace turns photographed handwritten journal pages into a searchable knowledge base (transcribed entries, recurring people/themes, mood/insight tracking). This repo is a from-scratch build following a **design + build-plan handoff bundle that lives in a sibling directory**: `../design_handoff_commonplace/`. That bundle is the source of truth for intent — read it before making architectural decisions:

- `DATA_MODEL.md` — the canonical Postgres schema (this repo's `api/db/schema.sql` is derived from it)
- `OCR_PIPELINE.md` — how transcription should work (few-shot VLM prompt, per-word confidence + alternates, correction feedback loop)
- `NLP_PIPELINE.md` — entity/theme/mood extraction + pattern detection (not yet built)
- `ROADMAP.md` — milestone build order
- `README.md` + `design/` — the 7-screen UI/UX spec with final-intent design tokens (oklch colors, Source Serif 4 / Work Sans, radii, spacing). Treat those values literally when building UI.

Current state: Milestone 2 (real transcription + correction loop) plus per-entry mood from `NLP_PIPELINE.md`. Milestones 3–6 (people/theme extraction, knowledge graph, search, insights) are not built yet.

## Commands

All backend work happens in `api/` with a venv. Postgres 16 runs as a local Homebrew service; its binaries are not on PATH by default.

```bash
# One-time setup — MUST be Python 3.12 (PaddlePaddle does not support 3.13 yet).
# An arm64 3.12 interpreter lives at /opt/anaconda3/envs/py312/bin/python.
cd api
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env          # defaults to the local OCR backend

# Postgres (Homebrew service already installed as postgresql@16)
brew services start postgresql@16
export PATH="/usr/local/opt/postgresql@16/bin:$PATH"   # psql/createdb not on PATH otherwise
createdb commonplace                                    # first time only
psql -d commonplace -f db/schema.sql                    # apply full schema to a fresh DB
psql -d commonplace -f db/migrations/00N_*.sql          # apply an incremental migration to an existing DB

# Run the API (serves the app UI at http://localhost:8000)
uvicorn app.main:app --reload --port 8000
```

There is no test suite or linter configured yet. `ANTHROPIC_MODEL` (default `claude-opus-4-8`) overrides the OCR model.

## Architecture

**Single FastAPI service** (`api/app/`) that is both the JSON API and the (temporary) HTML UI host. Plain `psycopg` 3 with hand-written SQL — no ORM. The design bundle recommends React Native + React web clients eventually; the current `app/static/*.html` pages are throwaway scaffolding to exercise the pipeline, not the real frontend.

**The OCR pipeline is the core flow** and spans several files:
1. `routes/entries.py` `upload_entry` — stores the photo (via `storage.py`), inserts an `entries` row with `status='processing'`, then schedules OCR as a FastAPI `BackgroundTasks` job so the request returns immediately (stand-in for a real job queue).
2. `run_ocr` (same file) calls `app/ocr` `transcribe_page`, which EXIF-rotates + downscales the photo, takes the process-wide `_OCR_LOCK` (one page at a time — concurrent local inference thrashes 8 GB RAM), and dispatches to the configured backend. Every backend returns the same ordered word list (`text` / `confidence` high|med|low / `alternates`) plus a detected date and date confidence.
3. `run_ocr` then runs `app/nlp/sentiment.py` `analyze` on the transcript (mood + mood_score; failures return `(None, None)` and never error the entry) and writes everything to `entries` in one UPDATE; `status` flips to `ready` (or `error` with the reason in `error_detail`).

**`transcript_json` is the contract** between OCR and the correction UI: its exact shape (`[{text, confidence, alternates}]`) is what the Entry view renders and what corrections mutate. Keep OCR output and any UI consuming it in sync.

**Date auto-detection** reduces upload friction: OCR reads the date off the page (usually handwritten D/M/Y at top). Only a `high`-confidence read is applied automatically; anything less sets `needs_date_review=true` with the model's guess in `detected_date`, and the UI prompts for a one-click confirm via `POST /entries/{id}/date`. A user-supplied date at upload time locks the date and skips detection.

**Corrections are the future fine-tuning dataset.** `POST /entries/{id}/corrections` updates the word in `transcript_json` (marking it high-confidence) *and* appends a row to the `corrections` table logging model_text → corrected_text. Per `OCR_PIPELINE.md`, that log is meant to feed few-shot examples / eventual model training — don't drop it when reworking the correction flow.

**`storage.py` is a deliberate seam.** `save_entry_image` writes to local disk and returns a `storage_key`; it's designed to be swapped for S3/R2 later without touching callers, so keep `storage_key` meaning "where to find the bytes" and route reads through the `/files` static mount.

## Schema conventions

`db/schema.sql` is canonical for provisioning a fresh database. Incremental changes go in numbered files under `db/migrations/` (idempotent `ADD COLUMN IF NOT EXISTS` etc.) **and** must be folded back into `schema.sql` so a from-scratch setup matches a migrated one. `mood` is intentionally a loose enum — store whatever the model returns and color-fallback unknown values in the UI rather than hard-validating.

## OCR backends

`OCR_BACKEND` (in `api/.env`) selects where transcription runs. Both backends live under `app/ocr/` and return the same `PAGE_SCHEMA`-shaped dict (defined in `app/ocr/__init__.py`), so nothing downstream changes when you switch.

- **`local`** (default) — `local_backend.py`: PaddleOCR's **detector** (`PP-OCRv5_mobile_det`) finds the handwritten text-line boxes; **TrOCR** (`microsoft/trocr-base-handwritten`, via transformers) transcribes each crop. This is the split `OCR_PIPELINE.md` prescribes: TrOCR is line-level, so a detector must find lines first; Paddle's own recognizer is printed-text-focused so we don't use it. TrOCR is a dedicated recognizer, not a generative VLM — a small VLM (Qwen 3B, since removed) was tested and *fabricated entire plausible paragraphs* on this handwriting; TrOCR's failure mode is a visible garble instead. Per-line confidence tiers come from beam scores (`CONF_HIGH`/`CONF_MED` constants); words inherit their line's tier with empty alternates. The date is regex-parsed from the transcript (`_guess_date_from_text`) and capped at `med` so local results always route to date review. Models are lazy-loaded singletons (~1.4 GB TrOCR + ~5 MB det, cached after first run). Tunables: `TROCR_MODEL` (`-small`/`-large`), `OCR_MAX_DIM`.
- **`anthropic`** — `anthropic_backend.py`: hosted Claude vision with **structured JSON output** (`output_config.format` + json_schema — not prefill). Most accurate on messy handwriting, seconds per page, true per-word confidence + alternates. Requires a real `ANTHROPIC_API_KEY` in `api/.env` (the app calls the Anthropic API directly; Claude Code's own auth does not carry into the running app).

Before either backend runs, `_prepare_image` (in `app/ocr/__init__.py`) applies the photo's EXIF rotation (phone photos are stored sideways — an unrotated page made models hallucinate), downscales to `OCR_MAX_DIM` (default 1568px longest edge), and re-encodes to JPEG. The original full-res image on disk is untouched.

## Sentiment

`app/nlp/sentiment.py` `analyze(text)` returns `(mood, mood_score)`: VADER's compound valence (−1..1) is the score; a small emotion classifier (`j-hartmann/emotion-english-distilroberta-base`, ~330 MB cached) supplies the label, mapped onto content/calm/anxious/tired (unmapped labels pass through — `mood` is a loose enum). It runs inside `run_ocr` after transcription and again on word corrections. It must never fail an entry: all exceptions are swallowed to `(None, None)`.

If the configured backend is unreachable/misconfigured, uploads still store the photo but the
background OCR job fails and the entry lands in `status='error'` with the reason in
`entries.error_detail` (shown in the UI; full traceback in the server log).
