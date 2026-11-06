"""The output guardrail: remove handbook citations that no tool returned (ADR-0012).

The model is told to cite only sections in its tool results, and mostly does; Phase 6
still saw it cite sections it was never given (ADR-0011). A citation is a promise that
the employee can look the rule up there, so an unsupported one is removed in code.
"""

import re
from collections.abc import Iterable

# [§6.4], [§ 6], [§6.4.1]: the format the handbook tool and the prompt use.
_CITATION = re.compile(r"\[\s*§\s*(\d+(?:\.\d+)*)\s*\]")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?)])")
_REPEATED_SPACES = re.compile(r"[ \t]{2,}")


def cited_sections(text: str) -> list[str]:
    """Every section cited in `text`, in order, without duplicates."""
    return list(dict.fromkeys(_CITATION.findall(text)))


def _supported(section: str, returned: set[str]) -> bool:
    """Returned itself, or the parent of a returned section ([§6] when §6.4 came back)."""
    return section in returned or any(r.startswith(f"{section}.") for r in returned)


def remove_unsupported_citations(answer: str, sources: Iterable[str]) -> tuple[str, list[str]]:
    """The answer without citations that none of `sources` (tool results) contains, and
    the sections removed. Only the bracketed citation goes; the sentence stays."""
    returned = {s for source in sources for s in cited_sections(source)}
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        if _supported(match.group(1), returned):
            return match.group(0)
        removed.append(match.group(1))
        return ""

    cleaned = _CITATION.sub(replace, answer)
    if not removed:
        return answer, []
    lines = [
        _REPEATED_SPACES.sub(" ", _SPACE_BEFORE_PUNCTUATION.sub(r"\1", line)).rstrip()
        for line in cleaned.split("\n")
    ]
    return "\n".join(lines).strip(), list(dict.fromkeys(removed))
