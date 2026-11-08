"""Shared by every test: OpenTelemetry spans go to memory, so tests can read them."""

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


class Spans:
    """The spans finished during one test."""

    def all(self) -> list[ReadableSpan]:
        return list(_EXPORTER.get_finished_spans())

    def named(self, name: str) -> list[ReadableSpan]:
        return [s for s in self.all() if s.name == name]

    def one(self, name: str) -> ReadableSpan:
        found = self.named(name)
        assert len(found) == 1, [s.name for s in self.all()]
        return found[0]


@pytest.fixture(autouse=True)
def _fresh_spans() -> Iterator[None]:
    _EXPORTER.clear()
    yield
    _EXPORTER.clear()


@pytest.fixture
def spans() -> Spans:
    return Spans()
