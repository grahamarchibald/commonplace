"""Per-entry mood extraction (NLP_PIPELINE.md): a mood label + continuous score.

Two lightweight local tools, each doing what it's best at:
- VADER (rule-based lexicon, instant) → `mood_score`: true valence intensity in
  [-1, 1], good for trend lines.
- A small DistilRoBERTa emotion classifier (~330MB, CPU, one-time download) →
  `mood`: its emotion label mapped onto the app's mood vocabulary. The schema
  treats `mood` as a loose enum, so unmapped labels pass through raw and the UI
  color-falls-back.

Failures are caught and logged — sentiment must never flip an entry to
status='error'.
"""

import threading
import traceback

EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"

# Emotion-model labels → the app's mood vocabulary (content/calm/anxious/tired).
_MOOD_MAP = {
    "joy": "content",
    "neutral": "calm",
    "surprise": "calm",
    "fear": "anxious",
    "anger": "anxious",
    "disgust": "anxious",
    "sadness": "tired",
}

_lock = threading.Lock()
_vader = None
_emotion = None


def _load():
    global _vader, _emotion
    if _vader is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        _vader = SentimentIntensityAnalyzer()
    if _emotion is None:
        from transformers import pipeline

        _emotion = pipeline(
            "text-classification", model=EMOTION_MODEL, top_k=1, truncation=True
        )


def analyze(text: str | None) -> tuple[str | None, float | None]:
    """Return (mood, mood_score) for a transcript, or (None, None) on any
    failure or empty input. The lock covers inference as well as loading:
    concurrent background jobs sharing one pipeline object (on MPS) can race
    and error — classification is fast, so serializing is cheap."""
    if not text or not text.strip():
        return None, None
    try:
        with _lock:
            _load()
            mood_score = _vader.polarity_scores(text)["compound"]
            label = _emotion(text[:2000])[0][0]["label"]
        return _MOOD_MAP.get(label, label), mood_score
    except Exception:
        traceback.print_exc()
        return None, None
