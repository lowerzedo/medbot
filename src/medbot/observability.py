from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


class Observability:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.referrals_received = Counter(
            "referrals_received_total",
            "Referrals created",
            registry=self.registry,
        )
        self.referrals_processed = Counter(
            "referrals_processed_total",
            "Referral processing runs completed",
            registry=self.registry,
        )
        self.referrals_failed = Counter(
            "referrals_failed_total",
            "Referral processing failures",
            registry=self.registry,
        )
        self.referrals_review_required = Counter(
            "referrals_review_required_total",
            "Referrals requiring human review",
            registry=self.registry,
        )
        self.evidence_failures = Counter(
            "evidence_verification_failures_total",
            "Evidence references rejected",
            registry=self.registry,
        )
        self.workflow_retries = Counter(
            "workflow_retries_total",
            "External workflow retries",
            registry=self.registry,
        )
        self.document_processing_duration = Histogram(
            "document_processing_duration_seconds",
            "Document text extraction and OCR duration",
            registry=self.registry,
        )
        self.referral_processing_duration = Histogram(
            "referral_processing_duration_seconds",
            "End-to-end referral processing duration",
            registry=self.registry,
        )
        self.tracer = trace.get_tracer("medbot")

    def render_metrics(self) -> bytes:
        return generate_latest(self.registry)

    @contextmanager
    def duration(self, histogram: Histogram) -> Iterator[None]:
        started = monotonic()
        try:
            yield
        finally:
            histogram.observe(monotonic() - started)


def configure_tracing(service_name: str, endpoint: str | None) -> None:
    if endpoint is None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
