"""Gemini-backed generation: theme -> unique prompt variations -> line-art images."""

import io
import os
import re
import time

from google import genai
from google.genai import types
from PIL import Image

STYLE_TEMPLATE = (
    "Black and white line art coloring book page, {subject}. "
    "Bold clean black outlines only, no shading, no gray fill, no color, "
    "pure white background, high contrast, print-ready, centered composition, "
    "suitable for an adult coloring book."
)


class GenerationError(Exception):
    pass


class ColoringPageGenerator:
    def __init__(self, api_key=None, text_model=None, image_model=None):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise GenerationError("GEMINI_API_KEY is not set")
        self.client = genai.Client(api_key=api_key)
        self.text_model = text_model or os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
        self.image_model = image_model or os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

    def generate_variations(self, theme, n):
        """Return a list of n distinct short subject phrases for the given theme."""
        prompt = (
            f"Generate exactly {n} distinct, short (3-8 word) subject descriptions for "
            f"coloring-book page designs on the theme '{theme}'. Each must describe a "
            "visually different composition (different layout, pattern, or focal subject) "
            "so no two are alike. Family-friendly, no text or words in the design. "
            f"Reply with exactly {n} lines, one description per line, numbered 1. 2. 3. etc. "
            "No extra commentary."
        )
        phrases = []
        try:
            response = self.client.models.generate_content(
                model=self.text_model,
                contents=prompt,
            )
            text = response.text or ""
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                cleaned = re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip("-• ").strip()
                if cleaned:
                    phrases.append(cleaned)
        except Exception:
            phrases = []

        # De-dupe while preserving order.
        seen = set()
        unique = []
        for p in phrases:
            key = p.lower()
            if key not in seen:
                seen.add(key)
                unique.append(p)
        phrases = unique

        # Pad if the model returned fewer than requested.
        i = len(phrases)
        while len(phrases) < n:
            i += 1
            phrases.append(f"{theme} design variation {i}")

        return phrases[:n]

    def generate_single_variation(self, theme, existing_phrases):
        """Ask for one more fresh phrase, avoiding repeats of existing_phrases."""
        avoid = "; ".join(existing_phrases[-20:])
        prompt = (
            f"Give one short (3-8 word) subject description for a coloring-book page on "
            f"the theme '{theme}', different from these already used: {avoid}. "
            "Reply with only the description, no numbering, no extra text."
        )
        try:
            response = self.client.models.generate_content(model=self.text_model, contents=prompt)
            phrase = (response.text or "").strip().splitlines()[0].strip()
            return phrase or f"{theme} design variation {len(existing_phrases) + 1}"
        except Exception:
            return f"{theme} design variation {len(existing_phrases) + 1}"

    def generate_image(self, subject, aspect_ratio="3:4", retries=3):
        """Generate one coloring-page image for the given subject phrase. Returns a PIL.Image."""
        prompt = STYLE_TEMPLATE.format(subject=subject)
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.image_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                    ),
                )
                image = self._extract_image(response)
                if image is not None:
                    return image
                last_err = GenerationError("No image returned in response")
            except Exception as e:
                last_err = e
            time.sleep(min(2 ** attempt, 10))
        raise GenerationError(f"Image generation failed for '{subject}': {last_err}")

    @staticmethod
    def _extract_image(response):
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content is None:
                continue
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    return Image.open(io.BytesIO(inline.data))
        return None
