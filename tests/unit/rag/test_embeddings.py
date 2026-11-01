"""Unit tests for roost.rag.embeddings (no model is downloaded)."""

from collections.abc import Sequence
from typing import Any

from roost.rag.embeddings import (
    EMBEDDING_MODELS,
    SentenceTransformerEmbedder,
    chunk_text_for_embedding,
)


class RecordingModel:
    """Stands in for a SentenceTransformer and records how it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def _encode(self, kind: str, texts: Sequence[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append((kind, list(texts), kwargs))
        return [[0.6, 0.8] for _ in texts]

    def encode_query(self, texts: Sequence[str], **kwargs: Any) -> list[list[float]]:
        return self._encode("query", texts, **kwargs)

    def encode_document(self, texts: Sequence[str], **kwargs: Any) -> list[list[float]]:
        return self._encode("document", texts, **kwargs)


def test_four_candidate_models_are_registered() -> None:
    assert set(EMBEDDING_MODELS) == {
        "embeddinggemma-300m",
        "qwen3-embedding-0.6b",
        "multilingual-e5-large-instruct",
        "bge-m3",
    }


def test_only_embeddinggemma_is_gated() -> None:
    assert [key for key, spec in EMBEDDING_MODELS.items() if spec.gated] == ["embeddinggemma-300m"]


def test_instruction_models_use_the_hr_instruction_for_queries() -> None:
    for key in ("qwen3-embedding-0.6b", "multilingual-e5-large-instruct"):
        prompt = EMBEDDING_MODELS[key].query_prompt
        assert prompt is not None
        assert prompt.startswith("Instruct: Given an employee's question")
        assert prompt.endswith("\nQuery: ")


def test_queries_and_documents_use_their_own_prompts() -> None:
    model = RecordingModel()
    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS["qwen3-embedding-0.6b"], model=model)

    embedder.embed_queries(["How many sick days?"])
    embedder.embed_documents(["8.1 Amount"])

    query_call, document_call = model.calls
    assert query_call[0] == "query"
    assert query_call[2]["prompt"] == EMBEDDING_MODELS["qwen3-embedding-0.6b"].query_prompt
    assert document_call[0] == "document"
    assert document_call[2]["prompt"] is None


def test_vectors_are_normalized_and_returned_as_floats() -> None:
    model = RecordingModel()
    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS["bge-m3"], model=model)

    vectors = embedder.embed_documents(["a", "b"])

    assert vectors == [[0.6, 0.8], [0.6, 0.8]]
    assert all(call[2]["normalize_embeddings"] is True for call in model.calls)


def test_model_name_is_the_registry_key() -> None:
    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS["bge-m3"], model=RecordingModel())

    assert embedder.model_name == "bge-m3"


def test_chunk_text_includes_heading_path() -> None:
    text = chunk_text_for_embedding("6. Vacation (PTO) > 6.9 Carryover", "Up to 5 days carry over.")

    assert text == "6. Vacation (PTO) > 6.9 Carryover\n\nUp to 5 days carry over."
