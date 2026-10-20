"""Execute the local referral workflow against frozen synthetic expectations.

No TestClient, live services, paid providers, or predictions-as-ground-truth are used.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import statistics
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from medbot.ai import FixtureAIProvider
from medbot.audit import verify_audit_chain
from medbot.database import Base
from medbot.document_processing import ExtractedTextBlock, FixtureDocumentExtractor
from medbot.domain import EvidenceStatus, ReferralStatus, Role
from medbot.errors import ApplicationError
from medbot.fhir import InMemoryFHIRProvider
from medbot.field_grounding import normalize_field_value
from medbot.models import AuditRecord
from medbot.observability import Observability
from medbot.policy import POLICY_VERSION
from medbot.schemas import (
    ActorContext,
    AIExtractedField,
    AIExtractionOutput,
    FieldCorrectRequest,
    FieldSupplyRequest,
    ReferralCreate,
)
from medbot.service import ReferralService
from medbot.storage import FixtureMalwareScanner, InMemoryObjectStorage

ROOT = Path(__file__).resolve().parents[1]


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    specialty: str = "cardiology"
    source_file: str | None = None
    documents: list[list[str]] = Field(default_factory=list)
    expected_fields: dict[str, str]
    expected_missing_fields: list[str]
    expected_missing_documents: list[str] = Field(default_factory=list)
    expected_status: ReferralStatus
    expected_issues: list[str] = Field(default_factory=list)
    provider_fault: Literal["none", "wrong_field", "truncated_quote", "fabricated_document"] = (
        "none"
    )
    recovery: Literal["manual", "supplemental", "conflict"] | None = None
    recovery_value: str | None = None
    expected_final_status: ReferralStatus | None = None
    expected_queue: str = "cardiology_intake"


class BenchmarkProvider(FixtureAIProvider):
    def __init__(self, fault: str) -> None:
        self.fault = fault

    def extract(self, blocks: tuple[ExtractedTextBlock, ...]) -> object:
        output = AIExtractionOutput.model_validate(super().extract(blocks))
        fields = list(output.fields)
        if self.fault == "wrong_field":
            source = next(field for field in fields if field.path == "requested_service")
            fields.append(
                AIExtractedField(
                    path="patient.date_of_birth",
                    value=source.value,
                    confidence=0.99,
                    evidence=source.evidence,
                )
            )
        elif self.fault in {"truncated_quote", "fabricated_document"}:
            for index, field in enumerate(fields):
                if field.path != "patient.date_of_birth":
                    continue
                evidence = field.evidence[0].model_copy(
                    update=(
                        {"quote": field.value}
                        if self.fault == "truncated_quote"
                        else {"document_id": "synthetic-nonexistent-document"}
                    )
                )
                fields[index] = field.model_copy(update={"evidence": (evidence,)})
        return output.model_copy(update={"fields": tuple(fields)})


def synthetic_document(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, invariant=1, pageCompression=0)
    canvas.drawString(40, 800, "SYNTHETIC TEST DATA - NOT A REAL PATIENT RECORD")
    for index, line in enumerate(lines):
        canvas.drawString(40, 760 - index * 22, line)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def load_cases() -> list[BenchmarkCase]:
    cases = []
    for path in sorted((ROOT / "synthetic/fixtures/ground_truth").glob("*.json")):
        gold = json.loads(path.read_text())
        cases.append(
            BenchmarkCase(
                case_id=gold["fixture_id"],
                source_file=gold["source_file"],
                specialty=next(
                    field["value"]
                    for field in gold["expected_fields"]
                    if field["path"] == "specialty"
                ),
                expected_fields={
                    field["path"]: field["value"] for field in gold["expected_fields"]
                },
                expected_missing_fields=gold["expected_missing_fields"],
                expected_missing_documents=gold["expected_missing_documents"],
                expected_status=gold["expected_state_after_processing"],
                expected_queue=gold["expected_queue"],
            )
        )
    cases.extend(
        BenchmarkCase.model_validate(case)
        for case in json.loads((ROOT / "eval/benchmark_cases.json").read_text())
    )
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Benchmark cases must be nonempty and uniquely identified")
    return cases


def evaluate_case(case: BenchmarkCase) -> dict[str, object]:
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    provider = BenchmarkProvider(case.provider_fault)
    service = ReferralService(
        storage=InMemoryObjectStorage(),
        scanner=FixtureMalwareScanner(),
        extractor=FixtureDocumentExtractor(),
        ai_provider=provider,
        fhir_provider=InMemoryFHIRProvider(),
        observability=Observability(),
        max_upload_bytes=10 * 1024 * 1024,
    )
    actor = ActorContext(
        user_id="synthetic-benchmark-reviewer",
        organization_id="synthetic-benchmark-org",
        roles=frozenset({Role.ADMINISTRATOR}),
        auth_method="dev",
    )
    trace = "synthetic-benchmark-trace"
    try:
        with Session(engine) as session:
            detail = service.create_referral(
                session, actor, ReferralCreate(specialty=case.specialty), trace
            )
            referral_id = detail.id
            contents = (
                [(ROOT / "synthetic/fixtures" / case.source_file).read_bytes()]
                if case.source_file
                else [synthetic_document(lines) for lines in case.documents]
            )
            for content in contents:
                service.upload_document(
                    session,
                    actor,
                    referral_id,
                    filename="synthetic-benchmark.pdf",
                    content_type="application/pdf",
                    content=content,
                    classification="referral_order",
                    trace_id=trace,
                )
            start = perf_counter()
            detail = service.process_referral(session, actor, referral_id, trace)
            elapsed_ms = (perf_counter() - start) * 1000
            accepted = {
                field.field_name: normalize_field_value(
                    field.field_name, field.reviewed_value or field.ai_value or ""
                )
                for field in detail.fields
                if field.evidence_status
                in {EvidenceStatus.VERIFIED, EvidenceStatus.HUMAN_CORRECTED}
            }
            expected = {
                path: normalize_field_value(path, value)
                for path, value in case.expected_fields.items()
            }
            correct = sum(accepted.get(path) == value for path, value in expected.items())
            checks = {
                "accepted_fields_match": accepted == expected,
                "missing_fields_match": detail.validation.missing_fields
                == sorted(case.expected_missing_fields),
                "missing_documents_match": detail.validation.missing_documents
                == sorted(case.expected_missing_documents),
                "processing_state_matches": detail.status == case.expected_status,
                "expected_issues_present": set(case.expected_issues)
                <= {issue.code for issue in detail.validation_issues if not issue.resolved},
            }
            initial_status = detail.status
            # Ineligible referrals must remain unroutable, including with a high model confidence.
            if case.expected_status != ReferralStatus.READY_TO_ROUTE:
                try:
                    service.route_referral(session, actor, referral_id, trace)
                except ApplicationError as error:
                    session.rollback()
                    checks["routing_blocked"] = error.status_code == 409
                else:
                    checks["routing_blocked"] = False
            if case.recovery:
                if case.recovery != "conflict":
                    service.request_information(session, actor, referral_id, trace)
                    service.request_information(session, actor, referral_id, trace)
                if case.recovery == "manual":
                    request = FieldSupplyRequest(
                        field_path="patient.date_of_birth",
                        value=case.recovery_value,
                        reason="Confirmed with synthetic referrer",
                    )
                    service.supply_missing_field(session, actor, referral_id, request, trace)
                    detail = service.supply_missing_field(
                        session, actor, referral_id, request, trace
                    )
                elif case.recovery == "supplemental":
                    service.upload_document(
                        session,
                        actor,
                        referral_id,
                        filename="synthetic-response.pdf",
                        content_type="application/pdf",
                        content=synthetic_document([f"DOB: {case.recovery_value}"]),
                        classification="supplemental",
                        trace_id=trace,
                    )
                    detail = service.process_referral(session, actor, referral_id, trace)
                else:
                    field = next(
                        field
                        for field in detail.fields
                        if field.field_name == "patient.date_of_birth"
                    )
                    detail = service.correct_field(
                        session,
                        actor,
                        referral_id,
                        field.id,
                        FieldCorrectRequest(
                            value=case.recovery_value,
                            reason="Confirmed synthetic conflicting sources",
                        ),
                        trace,
                    )
                checks["recovery_ready"] = detail.status == ReferralStatus.READY_TO_ROUTE
            if case.expected_status == ReferralStatus.READY_TO_ROUTE or case.recovery:
                try:
                    routed = service.route_referral(session, actor, referral_id, trace)
                    retried = service.route_referral(session, actor, referral_id, trace)
                except ApplicationError:
                    session.rollback()
                    checks["eligible_referral_routable"] = False
                else:
                    checks["routing_queue_matches"] = routed.queue == case.expected_queue
                    checks["routing_retry_idempotent"] = routed == retried
                    if case.expected_final_status:
                        checks["final_state_matches"] = routed.status == case.expected_final_status
            events = list(
                session.scalars(
                    select(AuditRecord)
                    .where(AuditRecord.referral_id == referral_id)
                    .order_by(AuditRecord.sequence)
                )
            )
            checks["audit_chain_valid"] = verify_audit_chain(events)
            actions = [event.action for event in events]
            if case.recovery == "manual":
                checks["supply_retry_idempotent"] = actions.count("FIELD_SUPPLIED") == 1
            if case.recovery in {"manual", "supplemental"}:
                checks["request_retry_idempotent"] = actions.count("INFORMATION_REQUESTED") == 1
            return {
                "case_id": case.case_id,
                "passed": all(checks.values()),
                "checks": checks,
                "correct_accepted_fields": correct,
                "accepted_fields": len(accepted),
                "expected_fields": len(expected),
                "false_ready": initial_status == ReferralStatus.READY_TO_ROUTE
                and case.expected_status != ReferralStatus.READY_TO_ROUTE,
                "processing_ms": round(elapsed_ms, 3),
            }
    finally:
        engine.dispose()


def rate(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def run_benchmark() -> dict[str, object]:
    cases = load_cases()
    results = [evaluate_case(case) for case in cases]
    correct = sum(int(result["correct_accepted_fields"]) for result in results)
    predicted = sum(int(result["accepted_fields"]) for result in results)
    expected = sum(int(result["expected_fields"]) for result in results)
    latencies = sorted(float(result["processing_ms"]) for result in results)
    fingerprint = hashlib.sha256()
    for path in sorted(
        [
            *(ROOT / "src/medbot").glob("*.py"),
            Path(__file__),
            ROOT / "eval/benchmark_cases.json",
            *(ROOT / "synthetic/fixtures").rglob("*.json"),
            *(ROOT / "synthetic/fixtures").glob("*.pdf"),
        ]
    ):
        fingerprint.update(str(path.relative_to(ROOT)).encode())
        fingerprint.update(path.read_bytes())
    return {
        "schema_version": "1.0",
        "report_type": "synthetic_workflow_benchmark",
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": all(result["passed"] for result in results),
        "case_count": len(results),
        "provider": "FixtureAIProvider with explicit adversarial cases",
        "policy_version": POLICY_VERSION,
        "extraction_schema_version": "1.0",
        "source_dataset_sha256": fingerprint.hexdigest(),
        "python_version": platform.python_version(),
        "dependencies": {name: version(name) for name in ("medbot", "pydantic", "sqlalchemy")},
        "metrics": {
            "accepted_field_precision": rate(correct, predicted),
            "accepted_field_recall": rate(correct, expected),
            "accepted_field_f1": 2 * correct / (predicted + expected)
            if predicted + expected
            else None,
            "false_ready_rate": rate(
                sum(bool(result["false_ready"]) for result in results),
                sum(case.expected_status != ReferralStatus.READY_TO_ROUTE for case in cases),
            ),
            "case_pass_rate": rate(sum(bool(result["passed"]) for result in results), len(results)),
            "processing_latency_ms": {
                "sample_count": len(latencies),
                "median": statistics.median(latencies),
                "p95_nearest_rank": latencies[max(0, (95 * len(latencies) + 99) // 100 - 1)],
            },
        },
        "limitations": [
            "Fixture-only digital PDFs; no real model or OCR evaluated.",
            "Accepted-field metrics measure validated output, not raw model precision.",
            "Small local SQLite sample; timing does not establish production performance.",
            "Confidence calibration, real-provider cost, and review time are unmeasured.",
        ],
        "cases": results,
    }


def main() -> None:
    report = run_benchmark()
    directory = ROOT / "eval/reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workflow-latest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# Synthetic workflow benchmark",
        "",
        f"Result: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        "| Case | Result |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {result['case_id']} | {'PASS' if result['passed'] else 'FAIL'} |"
        for result in report["cases"]
    )
    metrics = report["metrics"]
    lines.extend(
        [
            "",
            f"Provider: {report['provider']}",
            f"Policy: {report['policy_version']}",
            f"Source/dataset fingerprint: `{report['source_dataset_sha256']}`",
            "",
            "Measured accepted-field metrics (validated output, not raw proposals):",
            f"- Precision: {metrics['accepted_field_precision']}",
            f"- Recall: {metrics['accepted_field_recall']}",
            f"- F1: {metrics['accepted_field_f1']}",
            f"- False-ready rate: {metrics['false_ready_rate']}",
            f"- Processing latency (ms): {metrics['processing_latency_ms']}",
            "",
            "Full metrics and reproducibility metadata are in workflow-latest.json.",
            "",
            *[f"- {limitation}" for limitation in report["limitations"]],
            "",
        ]
    )
    (directory / "workflow-latest.md").write_text("\n".join(lines))
    print(
        f"Synthetic workflow benchmark: {'PASS' if report['passed'] else 'FAIL'} "
        f"({report['case_count']} cases)"
    )
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
