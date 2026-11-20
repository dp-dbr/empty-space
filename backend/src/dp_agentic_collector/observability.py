"""Tracing (OpenTelemetry) and structured JSON logging with secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("dpac_trace_id", default=None)
_log_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("dpac_log_ctx", default=None)
_configured = False

tracer = trace.get_tracer("dp_agentic_collector")

SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(\"?(?:access_token|refresh_token|api_key|password|client_secret|authorization)\"?\s*[:=]\s*\"?)[^\s\",}]+"),
    re.compile(r"(sk-[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+"),
]


def redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + "***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "trace_id": current_trace_id(),
        }
        data.update(_log_ctx.get() or {})
        if record.exc_info:
            data["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        return True


def configure(otlp_endpoint: str | None = None, json_logs: bool = True, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": "dp-agentic-collector"}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return _trace_id.get()


@contextmanager
def span(name: str, trace_id: str | None = None, **attributes: Any) -> Iterator[Any]:
    """Start a span; if ``trace_id`` is given (e.g. restored from an outbox event) the span
    joins that trace so API → worker → agent → tool calls share one trace id."""
    parent_ctx = None
    if trace_id:
        try:
            sc = SpanContext(trace_id=int(trace_id, 16), span_id=int(secrets.token_hex(8), 16),
                             is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED))
            parent_ctx = trace.set_span_in_context(NonRecordingSpan(sc))
        except ValueError:
            parent_ctx = None
    token = _trace_id.set(trace_id or _trace_id.get() or new_trace_id())
    try:
        with tracer.start_as_current_span(name, context=parent_ctx) as sp:
            for k, v in attributes.items():
                if v is not None:
                    sp.set_attribute(f"dpac.{k}", str(v))
            yield sp
    finally:
        _trace_id.reset(token)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    current = dict(_log_ctx.get() or {})
    current.update({k: v for k, v in fields.items() if v is not None})
    token = _log_ctx.set(current)
    try:
        yield
    finally:
        _log_ctx.reset(token)
