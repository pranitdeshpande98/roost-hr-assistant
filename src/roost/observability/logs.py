"""Structured logs: one JSON object per line, with the trace id (NFR-11).

Log calls in Roost never include what someone typed (FR-49, NFR-05); a test sends a
sensitive message through a whole turn and checks no word of it reaches the logs.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

from opentelemetry import trace

# Libraries that log every HTTP request or download step at INFO.
_NOISY = (
    "httpx",
    "httpx2",
    "httpcore",
    "openai",
    "huggingface_hub",
    "sentence_transformers",
    "transformers",
    "urllib3",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            entry["trace_id"] = format(context.trace_id, "032x")
            entry["span_id"] = format(context.span_id, "016x")
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: TextIO = sys.stderr) -> None:
    """JSON lines for Roost and uvicorn; quieter third-party libraries."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers[:] = []
        uvicorn_logger.propagate = True
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
