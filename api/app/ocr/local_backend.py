"""Local, fully on-device OCR: PaddleOCR finds the text lines, TrOCR reads them.

Why this split (per OCR_PIPELINE.md): TrOCR is the strongest open handwriting
recognizer but works on *single cropped text lines*, so a detector must find the
lines first. PaddleOCR's detection stage is excellent at that (its own
recognizer is printed-text-focused, so we don't use it). Because TrOCR only
transcribes the crop in front of it, its failure mode is a garbled word — not
the fabricated-essay failure mode of a small generative VLM.

Confidence comes from TrOCR's beam scores (geometric-mean token probability per
line), mapped onto the high/med/low tiers of the shared PAGE_SCHEMA contract.
Models are lazy-loaded singletons; callers already hold _OCR_LOCK, so one page
runs at a time on this modest hardware.
"""

import io
import statistics

from PIL import Image

from ..config import TROCR_MODEL

# Line-confidence tiers from TrOCR's length-normalized sequence probability.
CONF_HIGH = 0.90
CONF_MED = 0.75

_BATCH_SIZE = 8  # line crops per TrOCR forward pass (CPU-friendly)
_CROP_PAD = 4  # px of context around each detected line box

_detector = None
_processor = None
_recognizer = None


def _load_models():
    """First call downloads the models to local caches (~5MB Paddle det,
    ~1.4GB TrOCR-base); afterwards everything runs offline."""
    global _detector, _processor, _recognizer
    if _detector is None:
        from paddleocr import TextDetection

        _detector = TextDetection(model_name="PP-OCRv5_mobile_det")
    if _recognizer is None:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        _processor = TrOCRProcessor.from_pretrained(TROCR_MODEL)
        _recognizer = VisionEncoderDecoderModel.from_pretrained(TROCR_MODEL)
        _recognizer.eval()


def _detect_line_boxes(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Run Paddle text detection; return axis-aligned (l, t, r, b) crops in
    reading order (top-to-bottom bands, then left-to-right within a band)."""
    import numpy as np

    result = _detector.predict(np.asarray(img))
    polys = []
    for res in result:
        polys.extend(res.get("dt_polys", []) if hasattr(res, "get") else res["dt_polys"])

    boxes = []
    for poly in polys:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        l, t = max(0, int(min(xs)) - _CROP_PAD), max(0, int(min(ys)) - _CROP_PAD)
        r, b = min(img.width, int(max(xs)) + _CROP_PAD), min(img.height, int(max(ys)) + _CROP_PAD)
        if r - l >= 8 and b - t >= 8:  # drop specks
            boxes.append((l, t, r, b))
    if not boxes:
        return []

    # Reading order: bucket boxes into horizontal bands one median-line tall,
    # then left-to-right within each band.
    band_h = max(1.0, statistics.median(b - t for l, t, r, b in boxes))
    return sorted(boxes, key=lambda box: (int((box[1] + box[3]) / 2 / band_h), box[0]))


def _recognize_lines(img: Image.Image, boxes: list) -> list[tuple[str, float]]:
    """TrOCR over each line crop, batched. Returns (text, confidence) per line,
    confidence = exp(length-normalized beam log-prob) ≈ geometric-mean token
    probability."""
    import torch

    crops = [img.crop(box) for box in boxes]
    lines: list[tuple[str, float]] = []
    for i in range(0, len(crops), _BATCH_SIZE):
        batch = crops[i : i + _BATCH_SIZE]
        pixel_values = _processor(images=batch, return_tensors="pt").pixel_values
        with torch.no_grad():
            out = _recognizer.generate(
                pixel_values,
                num_beams=3,
                max_new_tokens=64,
                output_scores=True,
                return_dict_in_generate=True,
            )
        texts = _processor.batch_decode(out.sequences, skip_special_tokens=True)
        confs = torch.exp(out.sequences_scores).tolist()
        lines.extend((t.strip(), c) for t, c in zip(texts, confs))
    return [(t, c) for t, c in lines if t]


def _tier(confidence: float) -> str:
    if confidence >= CONF_HIGH:
        return "high"
    if confidence >= CONF_MED:
        return "med"
    return "low"


def transcribe(image_bytes: bytes) -> dict:
    """PAGE_SCHEMA-shaped result. Words inherit their line's confidence tier;
    alternates are deferred (the correction UI accepts free-text retyping).
    The date is parsed from the transcript and capped at 'med' so it always
    routes to the one-click review step."""
    from . import _guess_date_from_text

    _load_models()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    boxes = _detect_line_boxes(img)
    lines = _recognize_lines(img, boxes) if boxes else []

    words = []
    for text, conf in lines:
        tier = _tier(conf)
        words.extend({"text": w, "confidence": tier, "alternates": []} for w in text.split())

    raw_text = "\n".join(t for t, _ in lines)
    detected_date, date_confidence = _guess_date_from_text(raw_text)
    return {"detected_date": detected_date, "date_confidence": date_confidence, "words": words}
