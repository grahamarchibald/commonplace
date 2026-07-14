import os
from pathlib import Path

from dotenv import load_dotenv

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parent
load_dotenv(API_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/commonplace")
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", REPO_ROOT / "storage" / "entries"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# OCR backend: "anthropic" (hosted Claude vision) or "ollama" (local model, no API key).
OCR_BACKEND = os.getenv("OCR_BACKEND", "anthropic").lower()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# Local Ollama vision model settings (only used when OCR_BACKEND=ollama).
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")
# Context window for the local model. Must fit the (downscaled) image's vision
# tokens + the transcription. Bigger costs memory — keep modest on an 8GB machine.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
# Max seconds to wait on a local OCR call. Local vision OCR is slow on modest
# hardware (minutes per page), so this is generous by design.
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "1800"))

# Longest edge (px) an image is downscaled to before OCR. 1568 is Anthropic's
# recommended max; also keeps a full-res phone photo from overflowing a local
# model's context window. The original full-res photo on disk is untouched.
OCR_MAX_DIM = int(os.getenv("OCR_MAX_DIM", "1568"))
