"""Bounded HTML card rendering using AstrBot's existing render service."""

import asyncio
import html
import json
import logging
from collections import OrderedDict
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

RENDER_TIMEOUT = 12
ROOT = Path(__file__).parent
logger = logging.getLogger(__name__)


def escape_data(value):
    """Escape before remote Jinja substitution, even if autoescape is disabled."""
    if isinstance(value, str):
        return html.escape(value)
    if isinstance(value, dict):
        return {key: escape_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [escape_data(item) for item in value]
    return value


class CardRenderer:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cache = OrderedDict()
        self.retry_at = 0.0

    async def render(self, card: dict, render_html) -> str | None:
        """Share identical cards; any renderer failure leaves text delivery usable."""
        key = json.dumps(card, sort_keys=True, ensure_ascii=False)
        async with self.lock:
            now = monotonic()
            for old_key, (until, _) in list(self.cache.items()):
                if until <= now:
                    del self.cache[old_key]
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key][1]
            if now < self.retry_at:
                return None
            try:
                template = (ROOT / "templates/reset_card.html").read_text(
                    encoding="utf-8"
                )
                # Only bundled, trusted SVG is inserted as markup.
                logo = (ROOT / "assets/openai.svg").read_text(encoding="utf-8")
                template = template.replace("<!-- OPENAI_LOGO -->", logo)
                image = await asyncio.wait_for(
                    render_html(
                        template,
                        {"card": escape_data(card)},
                        return_url=True,
                        options={
                            "type": "jpeg",
                            "quality": 95,
                            "full_page": True,
                            "viewport_width": 760,
                            "viewport_height": 1,
                            "animations": "disabled",
                            "timeout": 10000,
                        },
                    ),
                    timeout=RENDER_TIMEOUT,
                )
                if not isinstance(image, str) or not image.strip():
                    raise ValueError("Empty render result")
                url = urlsplit(image)
                if url.scheme not in {"http", "https"} or not url.netloc:
                    raise ValueError("Expected render image URL")
            except Exception as exc:
                self.retry_at = monotonic() + 60
                logger.warning(
                    "Codex card renderer unavailable (%s); using text for 60s.",
                    type(exc).__name__,
                )
                return None
            self.cache[key] = (monotonic() + 60, image)
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
            return image
