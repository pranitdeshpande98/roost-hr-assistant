"""The search_handbook tool: the agent's only source of policy facts (ADR-0009 retrieval)."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from roost.agent.tools import ToolContext
from roost.llm.types import ToolSpec
from roost.rag.embeddings import Embedder
from roost.rag.retrieval import SectionText, get_sections, vector_search

HANDBOOK_EMBEDDING_MODEL = "bge-m3"  # ADR-0009: best retrieval in all three languages
TOP_K = 3  # ADR-0009: vector search, top 3

Search = Callable[[str], list[SectionText]]

# Same name, description and schema as the Phase 4 eval measured; change them together.
SEARCH_HANDBOOK_SPEC = ToolSpec(
    name="search_handbook",
    description=(
        "Search the employee handbook. Returns the most relevant sections with their "
        "section numbers. Write the query in English for best results."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    },
)


class SearchArguments(BaseModel):
    """What the model must send. Bounds keep a runaway query from reaching the database."""

    query: str = Field(min_length=1, max_length=300)


def format_sections(sections: Sequence[SectionText]) -> str:
    """Tool result shown to the model: section number, heading path, then the text."""
    if not sections:
        return "No matching handbook sections."
    return "\n\n".join(f"[§{s.section_number}] {s.heading_path}\n{s.content}" for s in sections)


class HandbookSearchTool:
    """Validates the model's arguments, runs the search and formats the sections."""

    def __init__(self, search: Search) -> None:
        self._search = search

    @property
    def spec(self) -> ToolSpec:
        return SEARCH_HANDBOOK_SPEC

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:  # noqa: ARG002 - same handbook for everyone
        try:
            args = SearchArguments.model_validate(arguments)
        except ValidationError:
            # Tell the model exactly how to fix the call, so it can retry correctly.
            return "Error: call search_handbook with a 'query' string of 1-300 characters."
        query = args.query.strip()
        if not query:
            return "Error: call search_handbook with a non-blank 'query'."
        return format_sections(self._search(query))


def database_search(
    session_factory: Callable[[], AbstractContextManager[Session]],
    embedder: Embedder,
    *,
    top_k: int = TOP_K,
) -> Search:
    """A Search backed by pgvector. Opens a short session per search, so no connection
    is held while the model is thinking."""

    def search(query: str) -> list[SectionText]:
        vector = embedder.embed_queries([query])[0]
        with session_factory() as session:
            numbers = vector_search(session, embedder.model_name, vector, limit=top_k)
            return get_sections(session, numbers)

    return search
