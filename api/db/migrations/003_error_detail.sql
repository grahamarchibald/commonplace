-- Store why an entry's OCR failed, so the UI can show the actual error
-- instead of a bare status='error'.
ALTER TABLE entries ADD COLUMN IF NOT EXISTS error_detail TEXT;
