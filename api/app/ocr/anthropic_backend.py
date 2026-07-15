"""Hosted Claude vision backend. Most accurate on messy handwriting; returns
true per-word confidence + alternate readings. Uses structured JSON output
(`output_config.format` + json_schema) to force the shape — not assistant
prefill. Requires ANTHROPIC_API_KEY in api/.env."""

import base64
import json

from . import PAGE_SCHEMA
from ..config import ANTHROPIC_MODEL

SYSTEM_PROMPT = (
    "You transcribe a single photographed page from someone's handwritten journal. "
    "Return the transcript as an ordered list of words, preserving the original "
    "wording, spelling, and word order exactly as written. For each word, assign "
    "a confidence tier: 'high' if you are confident in the reading, 'med' or 'low' "
    "if the handwriting is ambiguous. Flag honestly rather than guessing silently. "
    "For any word not rated 'high', include 2-3 plausible alternate readings in "
    "'alternates'; for 'high' confidence words, 'alternates' must be an empty list.\n\n"
    "Also detect the entry's written date. It is usually handwritten at the top of "
    "the page, most often in day/month/year order (e.g. '7/3/26' means 7 March 2026). "
    "Return 'detected_date' as an ISO 'YYYY-MM-DD' string, or null if no date is "
    "visible. Assume years are 20xx. Set 'date_confidence' to 'high' only when you "
    "clearly read an unambiguous date; use 'med'/'low' when the digits are unclear or "
    "the day/month order is genuinely ambiguous, and 'none' when no date is present."
)

USER_PROMPT = "Transcribe this journal page and detect its date."


def transcribe(image_bytes: bytes, media_type: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": PAGE_SCHEMA}},
    )
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)
