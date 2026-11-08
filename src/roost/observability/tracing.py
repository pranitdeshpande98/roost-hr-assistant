"""OpenTelemetry tracing (NFR-11, ADR-0014).

With no exporter configured (tests, the terminal chat) the API is a no-op and costs
nothing. With OTEL_EXPORTER_OTLP_ENDPOINT set, spans go over OTLP/HTTP, locally to
Jaeger (docker compose), later to any OTLP backend: a configuration change only.

Exceptions are recorded by type only, never by message: a message can quote input.
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from roost.observability.attributes import safe_attributes

logger = logging.getLogger(__name__)
TRACER_NAME = "roost"
SERVICE_NAME = "roost"


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def set_attributes(span: Span, attributes: Mapping[str, object]) -> None:
    """Set only the attributes safe_attributes lets through."""
    span.set_attributes(safe_attributes(attributes))


@contextmanager
def span(
    name: str,
    attributes: Mapping[str, object] | None = None,
    *,
    passthrough: tuple[type[BaseException], ...] = (),
) -> Iterator[Span]:
    """A span with safe attributes. On an exception: status ERROR and the exception's type,
    nothing else (no message, no stack trace in the trace). `passthrough` exceptions are
    control flow (LangGraph pausing for a confirmation), not errors."""
    with tracer().start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as current:
        if attributes:
            set_attributes(current, attributes)
        try:
            yield current
        except passthrough:
            raise
        except BaseException as error:
            current.set_status(Status(StatusCode.ERROR))
            current.set_attribute("roost.error.type", type(error).__name__)
            raise


def setup_tracing(endpoint: str | None, *, environment: str) -> TracerProvider | None:
    """Install the exporter when an endpoint is configured. Returns the provider (call
    shutdown() at exit to flush), or None when tracing stays off."""
    if not endpoint:
        return None
    # Imported here: the exporter pulls in protobuf, needed only when tracing is on.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create(
        {"service.name": SERVICE_NAME, "deployment.environment.name": environment}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    logger.info("Tracing to %s", endpoint)
    return provider
