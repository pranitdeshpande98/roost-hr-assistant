"""Estimated cost at list price (NFR-08)."""

from decimal import Decimal

from roost.observability.prices import estimate_cost, load_prices


def test_known_models_are_costed_per_million_tokens() -> None:
    # gpt-oss-120b on Groq: $0.15 in, $0.60 out per million tokens.
    assert estimate_cost("groq/openai/gpt-oss-120b", 1_000_000, 0) == Decimal("0.15")
    assert estimate_cost("groq/openai/gpt-oss-120b", 2000, 500) == Decimal("0.0006")


def test_free_and_unknown_models_cost_nothing() -> None:
    assert estimate_cost("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", 5000, 500) == 0
    assert estimate_cost("somewhere/new-model", 5000, 500) == 0


def test_every_configured_agent_and_guardrail_model_has_a_price() -> None:
    from roost.llm.providers import LLMSettings

    settings = LLMSettings(_env_file=None)  # the defaults, not a developer's .env
    refs = settings.agent_model_refs + settings.guardrail_model_refs
    names = {ref.replace(":", "/", 1) for ref in refs}
    assert names <= set(load_prices())
