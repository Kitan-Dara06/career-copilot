"""OpenTelemetry setup — tracer, meter, and log handler.

Wires traces + logs + metrics to Azure Application Insights via OTLP.
Also exposes a `BoundLogger` proxy that injects the current trace_id +
span_id into every structlog event, so logs in App Insights are correlatable
with the trace timeline.

When `applicationinsights_connection_string` is unset (dev mode), the
exporter is a no-op and traces/logs only land in stdout.

Auto-instruments: httpx, asyncpg, sqlalchemy, logging. No code changes
needed in our agents — the ModelClient, Tool base class, and dispatcher
pick up tracing automatically.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import structlog
from opentelemetry import trace, _logs as otel_logs
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.semconv.resource import ResourceAttributes

try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    OTLP_AVAILABLE = True
except ImportError:
    OTLP_AVAILABLE = False

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    AUTO_INSTRUMENT_AVAILABLE = True
except ImportError:
    AUTO_INSTRUMENT_AVAILABLE = False


_initialized = False
_tracer = None
_meter = None


def _parse_azure_connection_string(cs: str) -> tuple[str, str | None]:
    """Parse Application Insights connection string.

    Format: InstrumentationKey=<guid>;IngestionEndpoint=https://...
    Returns (endpoint, instrumentation_key).
    """
    if not cs:
        return ("", None)
    parts = dict(p.split("=", 1) for p in cs.split(";") if "=" in p)
    key = parts.get("InstrumentationKey")
    endpoint = parts.get("IngestionEndpoint")
    if endpoint and not endpoint.endswith("/v1/trace"):
        endpoint = endpoint.rstrip("/") + "/v1/trace"
    return (endpoint or "", key)


def setup_telemetry(
    service_name: str = "career-copilot",
    service_version: str = "0.2.0",
    connection_string: str | None = None,
    enable_console_export: bool = False,
) -> Any:
    """Initialize the OpenTelemetry tracer, meter, and log providers.

    Idempotent — calling twice is a no-op.

    Args:
        service_name: Logical service name, surfaces in App Insights.
        service_version: Surfaces in App Insights for deployment tracking.
        connection_string: Azure Application Insights connection string.
            If None, reads from APPINSIGHTS_CONNECTION_STRING env var.
        enable_console_export: Also emit spans/logs to stdout (dev mode).
    """
    global _initialized, _tracer, _meter
    if _initialized:
        return _tracer

    cs = connection_string or os.environ.get("APPINSIGHTS_CONNECTION_STRING", "")
    endpoint, _ = _parse_azure_connection_string(cs)

    # Resource = static labels on every span/log emitted by this process.
    resource = Resource.create({
        ResourceAttributes.SERVICE_NAME: service_name,
        ResourceAttributes.SERVICE_VERSION: service_version,
        ResourceAttributes.DEPLOYMENT_ENVIRONMENT: os.environ.get("ENV", "dev"),
    })

    # ── Tracer ──
    tracer_provider = TracerProvider(resource=resource)
    if endpoint and OTLP_AVAILABLE:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
    if enable_console_export:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer(service_name)

    # ── Logs ──
    logger_provider = LoggerProvider(resource=resource)
    if endpoint and OTLP_AVAILABLE:
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
        )
    if enable_console_export:
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))
    otel_logs.set_logger_provider(logger_provider)

    # ── Metrics ──
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=(
            [PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True))]
            if endpoint and OTLP_AVAILABLE
            else ([PeriodicExportingMetricReader(ConsoleMetricExporter())] if enable_console_export else [])
        ),
    )
    from opentelemetry import metrics as otel_metrics
    otel_metrics.set_meter_provider(meter_provider)
    _meter = otel_metrics.get_meter(service_name)

    # ── Auto-instrumentation ──
    if AUTO_INSTRUMENT_AVAILABLE:
        HTTPXClientInstrumentor().instrument()      # Gemini, DeepSeek, GitHub, Tavily, Firecrawl
        AsyncPGInstrumentor().instrument()            # Postgres queries
        SQLAlchemyInstrumentor().instrument()        # ORM-level spans
        LoggingInstrumentor().instrument()           # Bridge Python logging → OTel logs

    # ── structlog ↔ OTel bridge ──
    # Inject trace_id and span_id into every structlog event so App Insights
    # can correlate log events with trace timelines.
    def add_trace_context(_logger: Any, _method: str, event_dict: dict) -> dict:
        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx and ctx.trace_id:
                event_dict["trace_id"] = format(ctx.trace_id, "032x")
                event_dict["span_id"] = format(ctx.span_id, "016x")
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            add_trace_context,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    _initialized = True
    return _tracer


def get_tracer(name: str = "career-copilot") -> Any:
    """Return the configured tracer, or a no-op tracer if setup_telemetry was not called."""
    if not _initialized:
        setup_telemetry()
    return trace.get_tracer(name)


def get_meter(name: str = "career-copilot") -> Any:
    """Return the configured meter."""
    if not _initialized:
        setup_telemetry()
    from opentelemetry import metrics as otel_metrics
    return otel_metrics.get_meter(name)


# ── Semconv attribute keys for LLM calls ──

LLM_SYSTEM = "gen_ai.system"             # e.g. "gemini", "deepseek"
LLM_REQUEST_MODEL = "gen_ai.request.model"
LLM_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
LLM_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
LLM_USAGE_INPUT = "gen_ai.usage.input_tokens"
LLM_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
LLM_RESPONSE_FINISH = "gen_ai.response.finish_reasons"
LLM_COST_USD = "gen_ai.usage.cost_usd"
LLM_PROMPT_NAME = "gen_ai.prompt.name"
LLM_PROMPT_VERSION = "gen_ai.prompt.version"
LLM_AGENT = "gen_ai.agent.name"

TOOL_NAME = "tool.name"
TOOL_OWNER = "tool.owner"
TOOL_COST = "tool.cost_hint"
TOOL_LATENCY = "tool.latency_hint"

DISPATCHER_COMMAND = "dispatcher.command"
DISPATCHER_USER = "dispatcher.user_id"
DISPATCHER_AGENT = "dispatcher.agent"