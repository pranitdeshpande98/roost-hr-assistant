"""Embedding models behind one small interface (ADR-0005).

Each candidate model needs its own prompt format for queries and documents.
Getting this wrong silently lowers retrieval quality, so the formats live here,
in one place, next to the model they belong to.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

# One instruction for every instruction-tuned model, phrased for our use case.
HR_RETRIEVAL_INSTRUCTION = (
    "Given an employee's question, retrieve the handbook section that answers it"
)


class Embedder(Protocol):
    """Turns text into vectors. Queries and documents may be encoded differently."""

    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class EmbeddingModelSpec:
    """How to load a candidate model and format its inputs."""

    key: str  # short name used on the command line and in chunk_embedding.model_name
    hf_id: str  # Hugging Face model id
    query_prompt: str | None  # None = use the model's own built-in query prompt, if any
    document_prompt: str | None
    gated: bool = False  # True = needs a Hugging Face token and accepted license


EMBEDDING_MODELS: dict[str, EmbeddingModelSpec] = {
    spec.key: spec
    for spec in (
        EmbeddingModelSpec(
            key="embeddinggemma-300m",
            hf_id="google/embeddinggemma-300m",
            query_prompt=None,  # built-in "query" / "document" prompts
            document_prompt=None,
            gated=True,
        ),
        EmbeddingModelSpec(
            key="qwen3-embedding-0.6b",
            hf_id="Qwen/Qwen3-Embedding-0.6B",
            query_prompt=f"Instruct: {HR_RETRIEVAL_INSTRUCTION}\nQuery: ",
            document_prompt=None,
        ),
        EmbeddingModelSpec(
            key="multilingual-e5-large-instruct",
            hf_id="intfloat/multilingual-e5-large-instruct",
            query_prompt=f"Instruct: {HR_RETRIEVAL_INSTRUCTION}\nQuery: ",
            document_prompt=None,
        ),
        EmbeddingModelSpec(
            key="bge-m3",
            hf_id="BAAI/bge-m3",
            query_prompt=None,  # bge-m3 dense retrieval uses no prefixes
            document_prompt=None,
        ),
    )
}


def chunk_text_for_embedding(heading_path: str, content: str) -> str:
    """The text we embed for a chunk: its heading path gives the body context."""
    return f"{heading_path}\n\n{content}"


class SentenceTransformerEmbedder:
    """Runs a model locally with sentence-transformers. Vectors are L2-normalized."""

    def __init__(self, spec: EmbeddingModelSpec, *, model: Any | None = None) -> None:
        self.spec = spec
        if model is None:  # pragma: no cover - loads a real model; run by scripts/embed_handbook.py
            # Imported here so the rest of the app never loads PyTorch unless it embeds.
            import torch
            from sentence_transformers import SentenceTransformer

            # float32: most laptop CPUs have no bfloat16 hardware, and emulating it is very slow.
            model = SentenceTransformer(
                spec.hf_id, device="cpu", model_kwargs={"dtype": torch.float32}
            )
        self._model = model

    @property
    def model_name(self) -> str:
        return self.spec.key

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode_document(
            list(texts),
            prompt=self.spec.document_prompt,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode_query(
            list(texts),
            prompt=self.spec.query_prompt,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors]
