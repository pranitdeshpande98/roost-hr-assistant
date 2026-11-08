"""What a trace may say (NFR-05, FR-49): ids, numbers, names of things and categories.

Every attribute Roost sets on a span goes through `safe_attributes`. Keys must be on the
list below, and text values must be a short token without spaces (a model name, a
category, a section number), so a sentence someone typed can never end up in a trace,
even by mistake. Anything else is dropped.
"""

import re
from collections.abc import Mapping, Sequence

from opentelemetry.util.types import AttributeValue

ALLOWED_KEYS = frozenset(
    {
        # the turn
        "roost.channel",
        "roost.language",
        "roost.resumed",
        "roost.reply.kind",
        "roost.reply.unanswered",
        "roost.turn.tool_rounds",
        "roost.turn.cost_usd",
        "roost.turn.input_tokens",
        "roost.turn.output_tokens",
        # agent steps
        "roost.agent.node",
        "roost.route",
        "roost.guardrail.category",
        "roost.guardrail.source",
        "roost.tool.name",
        "roost.tool.outcome",
        "roost.rag.top_k",
        "roost.rag.hits",
        "roost.rag.sections",
        "roost.referral.answer",
        # model calls (OpenTelemetry GenAI conventions where they exist)
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.response.finish_reasons",
        "roost.llm.purpose",
        "roost.llm.fallback_position",
        "roost.llm.cost_usd",
        "roost.llm.outcome",
        "roost.error.type",
    }
)

# A token: letters, digits and a few separators, no spaces, at most 120 characters.
_TOKEN = re.compile(r"[A-Za-z0-9_.:/§,+\-]{0,120}")


def _safe_value(value: object) -> AttributeValue | None:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if _TOKEN.fullmatch(value) else None
    if isinstance(value, Sequence) and all(isinstance(v, str) for v in value):
        items = [v for v in value if isinstance(v, str) and _TOKEN.fullmatch(v)]
        return tuple(items) if len(items) == len(value) else None
    return None


def safe_attributes(attributes: Mapping[str, object]) -> dict[str, AttributeValue]:
    """The attributes that may be recorded; everything else is left out."""
    safe: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if key not in ALLOWED_KEYS or value is None:
            continue
        checked = _safe_value(value)
        if checked is not None:
            safe[key] = checked
    return safe
