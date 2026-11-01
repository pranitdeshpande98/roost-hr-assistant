"""Split the handbook Markdown into numbered sections, the unit we retrieve and cite."""

import re
from dataclasses import dataclass

# "## 6. Vacation (PTO)" and "### 6.4 Notice required"
_HEADING = re.compile(r"^(?P<hashes>#{2,3})\s+(?P<number>\d+(?:\.\d+)?)\.?\s+(?P<title>.+?)\s*$")


class HandbookParseError(ValueError):
    """The handbook does not follow the expected numbered-heading structure."""


@dataclass(frozen=True, slots=True)
class HandbookSection:
    """One section ("6") or subsection ("6.4") of the handbook."""

    number: str
    title: str
    heading_path: str  # "6. Vacation (PTO) > 6.4 Notice required", gives each chunk context
    content: str


@dataclass(slots=True)
class _Open:
    number: str
    title: str
    heading_path: str
    lines: list[str]


def parse_handbook(markdown: str) -> list[HandbookSection]:
    """Return every numbered section with its text, in document order.

    Text before the first "## 1." heading (title, disclaimer) is ignored.
    A section's own introduction becomes a chunk; each "###" subsection is a
    separate chunk. Raises HandbookParseError if numbering is inconsistent.
    """
    sections: list[HandbookSection] = []
    current: _Open | None = None
    parent_number = ""
    parent_label = ""

    def close() -> None:
        if current is not None:
            content = "\n".join(current.lines).strip()
            if content:
                sections.append(
                    HandbookSection(current.number, current.title, current.heading_path, content)
                )

    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match is None:
            if current is not None:
                current.lines.append(line)
            continue

        close()
        number, title = match["number"], match["title"]
        if match["hashes"] == "##":
            if "." in number:
                raise HandbookParseError(f"Top-level heading has a subsection number: {line!r}")
            parent_number, parent_label = number, f"{number}. {title}"
            current = _Open(number, title, parent_label, [])
        else:
            if not number.startswith(f"{parent_number}.") or not parent_number:
                raise HandbookParseError(
                    f"Subsection {number} is not under section {parent_number}"
                )
            current = _Open(number, title, f"{parent_label} > {number} {title}", [])
    close()

    if not sections:
        raise HandbookParseError("No numbered sections found.")
    numbers = [s.number for s in sections]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise HandbookParseError(f"Duplicate section numbers: {', '.join(duplicates)}")
    return sections
