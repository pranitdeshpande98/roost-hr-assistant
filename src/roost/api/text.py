"""Plain text for the kiosk. Models write markdown; the kiosk shows text as it is
(escaped, never as HTML), so stray **, # and [x](y) would be noise."""

import re

_BOLD_OR_ITALIC = re.compile(r"(\*\*|__)(.+?)\1|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]§][^\]]*)\]\(([^)]+)\)")  # not a [§6.4] citation
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)


def kiosk_text(text: str) -> str:
    """Markdown emphasis, code, links and headings removed; bullets shown as •."""
    text = _BOLD_OR_ITALIC.sub(lambda m: m.group(2) or m.group(3), text)
    text = _CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    return _BULLET.sub(r"\1• ", text)
