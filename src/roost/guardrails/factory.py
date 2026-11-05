"""Build the input guardrail from configuration (the composition root for guardrails)."""

from pathlib import Path

from roost.guardrails.classifier import (
    DEFAULT_CONFIG,
    InputGuardrail,
    KeywordRules,
    LLMClassifier,
    SimilarityClassifier,
    load_guardrail_config,
)
from roost.llm.types import ChatModel
from roost.rag.embeddings import Embedder


def build_input_guardrail(
    embedder: Embedder, model: ChatModel | None, config_path: Path = DEFAULT_CONFIG
) -> InputGuardrail:
    """`model` is the classifier chain (GUARDRAIL_MODELS); None runs keywords and the
    similarity fallback only."""
    config = load_guardrail_config(config_path)
    return InputGuardrail(
        KeywordRules(config),
        LLMClassifier(model) if model is not None else None,
        SimilarityClassifier(config, embedder),
    )
