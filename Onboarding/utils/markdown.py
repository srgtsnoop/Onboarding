"""Markdown rendering with HTML sanitization."""
from __future__ import annotations

import bleach
from markdown_it import MarkdownIt

md = MarkdownIt("commonmark", {"breaks": True, "html": True, "linkify": True})

ALLOWED_TAGS = ["a", "p", "br", "strong", "em", "ul", "ol", "li", "code", "pre", "u"]
ALLOWED_ATTRS = {"a": ["href", "title", "target"]}


def render_markdown(text: str | None) -> str:
    if not text:
        return ""
    html = md.render(text.strip())
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
