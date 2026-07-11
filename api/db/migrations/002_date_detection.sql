-- Auto-detect the written date from the page during OCR; only ask the user
-- when the model is unsure. needs_date_review flags entries that fell back to
-- the capture-date placeholder; detected_date holds the model's best guess
-- (may be NULL) so the confirmation UI can prefill it.
ALTER TABLE entries ADD COLUMN IF NOT EXISTS needs_date_review BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE entries ADD COLUMN IF NOT EXISTS detected_date DATE;
