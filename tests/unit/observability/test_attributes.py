"""Only ids, numbers and categories may go into a trace (NFR-05, FR-49)."""

from roost.observability.attributes import safe_attributes


def test_known_keys_with_token_values_are_kept() -> None:
    kept = safe_attributes(
        {
            "roost.guardrail.category": "harassment",
            "gen_ai.request.model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            "gen_ai.usage.input_tokens": 1200,
            "roost.turn.cost_usd": 0.00018,
            "roost.resumed": True,
            "roost.rag.sections": ["6.4", "12.5"],
        }
    )

    assert kept["roost.guardrail.category"] == "harassment"
    assert kept["gen_ai.usage.input_tokens"] == 1200
    assert kept["roost.rag.sections"] == ("6.4", "12.5")
    assert len(kept) == 6


def test_sentences_unknown_keys_and_odd_values_are_dropped() -> None:
    dropped = safe_attributes(
        {
            "roost.guardrail.category": "my lead grabbed me",  # a sentence
            "roost.tool.name": "x" * 121,  # too long
            "roost.rag.sections": ["6.4", "what about pay"],  # one item is text
            "message": "hello",  # not on the list
            "roost.route": None,
            "roost.channel": {"nested": "dict"},
        }
    )

    assert dropped == {}
