"""Readable text for brief-judge evidence.

A captured documentation page is mostly markup: one GitHub docs page was
250,000 characters of HTML. Sent raw, a few cited pages exceeded the 300,000
token judge budget, so the claude-code judge was killed and every such cell went
ungraded. Judges need complete readable prose, without the page chrome.
"""

from __future__ import annotations

from html.parser import HTMLParser
import re

# Content that is never prose a claim can rely on.
_SKIPPED = frozenset(
    {"script", "style", "noscript", "svg", "template", "head", "iframe", "canvas", "nav"}
)
_BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)


class _ReadableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping: list[str] = []
        self.content: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"article", "main"}:
            self.content.append(tag)
        # Only explicit site chrome is safe to discard. Ambiguous footers,
        # including footnotes outside article/main, remain source evidence.
        site_footer = (
            tag == "footer"
            and not self.content
            and "contentinfo" in (dict(attrs).get("role") or "").lower().split()
        )
        if tag == "body":
            # A malformed page can leave <head> open; the body is still prose.
            self.skipping.clear()
        elif tag in _SKIPPED or site_footer or (tag == "footer" and self.skipping):
            # Nested footers must close their own skip entry, not the outer one.
            self.skipping.append(tag)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        if tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.content:
            while self.content and self.content.pop() != tag:
                pass
        if tag in _SKIPPED or (tag == "footer" and tag in self.skipping):
            if tag in self.skipping:
                while self.skipping and self.skipping.pop() != tag:
                    pass
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def looks_like_html(text: str, content_type: str | None = None) -> bool:
    """HTML by the server's declared type; sniff the body only when none was sent."""

    if content_type:
        return "html" in content_type.lower()
    head = text.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def readable_text(html: str) -> str:
    """Visible prose of an HTML page, one block per line."""

    parser = _ReadableText()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)
