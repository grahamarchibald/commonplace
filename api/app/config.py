import os
from pathlib import Path

from dotenv import load_dotenv

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parent
load_dotenv(API_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/commonplace")
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", REPO_ROOT / "storage" / "entries"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# OCR backend: "local" (PaddleOCR line detection + TrOCR handwriting recognition,
# fully on-device, no API key) or "anthropic" (hosted Claude vision — most accurate,
# per-word confidence + alternates, needs ANTHROPIC_API_KEY).
OCR_BACKEND = os.getenv("OCR_BACKEND", "local").lower()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# TrOCR recognition model for the local backend. "-small" is the speed fallback,
# "-large" the accuracy upgrade (slower).
TROCR_MODEL = os.getenv("TROCR_MODEL", "microsoft/trocr-base-handwritten")

# Longest edge (px) an image is downscaled to before OCR. 1568 is Anthropic's
# recommended max and gives the line detector enough resolution to work with.
# The original full-res photo on disk is untouched.
OCR_MAX_DIM = int(os.getenv("OCR_MAX_DIM", "1568"))
