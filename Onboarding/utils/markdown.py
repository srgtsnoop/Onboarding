import bleach
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"breaks": True, "html": True, "linkify": True})

_ALLOWED_TAGS = ["a", "p", "br", "strong", "em", "ul", "ol", "li", "code", "pre", "u"]
_ALLOWED_ATTRS = {"a": ["href", "title", "target"]}


def render_markdown(text: str | None) -> str:
    if not text:
        return ""
    html = _md.render(text.strip())
    return bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
