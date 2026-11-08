"""Where the agent's answer streams to during a turn (kiosk streaming, NFR-01).

`ask(on_text=...)` sets the sink for one turn; the graph's model step passes it to the
model. Only the agent's own answer streams, never the guardrail's classifier. Each model
step first resets the sink, so text a model wrote before deciding to call a tool is
dropped from the screen. The final, checked reply replaces whatever was streamed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from roost.llm.types import TextSink
from roost.observability import turns

_sink: ContextVar[TextSink | None] = ContextVar("roost_text_sink", default=None)


class _TimedSink:
    """Passes text on, and notes when the first piece arrived (time to first token)."""

    def __init__(self, inner: TextSink) -> None:
        self._inner = inner

    def text(self, delta: str) -> None:
        stats = turns.current()
        if stats is not None:
            stats.mark_first_token()
        self._inner.text(delta)

    def reset(self) -> None:
        self._inner.reset()


@contextmanager
def streaming_to(sink: TextSink | None) -> Iterator[None]:
    token = _sink.set(_TimedSink(sink) if sink is not None else None)
    try:
        yield
    finally:
        _sink.reset(token)


def current_sink() -> TextSink | None:
    return _sink.get()
