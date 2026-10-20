"""Measure Phase 1 synthetic dataset integrity without inventing AI metrics."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "synthetic" / "fixtures"
REPORT_DIR = ROOT / "eval" / "reports"


class CheckResult(TypedDict):
    fixture_id: str
    passed: bool
    checks: dict[str, bool]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_case(ground_truth_path: Path) -> CheckResult:
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    fixture_id = str(ground_truth.get("fixture_id", "missing-fixture-id"))
    pdf_path = FIXTURE_DIR / str(ground_truth.get("source_file", ""))
    content = pdf_path.read_bytes() if pdf_path.is_file() else b""

    evidence_quotes = [
        str(field["evidence"]["quote"]).encode("ascii")
        for field in ground_truth.get("expected_fields", [])
    ]
    evidence_hashes_match = all(
        sha256_bytes(str(field["evidence"]["quote"]).encode("utf-8"))
        == field["evidence"]["text_sha256"]
        for field in ground_truth.get("expected_fields", [])
    )
    checks = {
        "marked_synthetic": ground_truth.get("synthetic") is True,
        "pdf_exists": pdf_path.is_file(),
        "pdf_signature": content.startswith(b"%PDF-") and b"%%EOF" in content[-2048:],
        "pdf_sha256": bool(content)
        and sha256_bytes(content) == ground_truth.get("source_file_sha256"),
        "expected_fields_present": bool(ground_truth.get("expected_fields")),
        "evidence_quotes_in_uncompressed_pdf": bool(evidence_quotes)
        and all(quote in content for quote in evidence_quotes),
        "evidence_hashes_match": evidence_hashes_match,
        "document_expectation_present": "referral_order"
        in ground_truth.get("expected_documents", []),
        "prompt_injection_expectation_explicit": (
            ground_truth.get("expected_prompt_injection_handling")
            == "treat_as_document_data_and_ignore_for_workflow_authority"
            if ground_truth.get("contains_prompt_injection")
            else True
        ),
    }
    return {"fixture_id": fixture_id, "passed": all(checks.values()), "checks": checks}


def markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# Phase 1 dataset-integrity evaluation",
        "",
        "This report validates the committed synthetic fixtures and independent ground truth. "
        "It does not measure OCR, extraction, evidence, routing, latency, or model quality.",
        "",
        f"Generated: {report['generated_at']}",
        f"Overall result: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        "| Fixture | Result | Checks passed |",
        "| --- | --- | ---: |",
    ]
    for case in report["cases"]:  # type: ignore[union-attr]
        checks = case["checks"]
        passed_checks = sum(bool(result) for result in checks.values())
        lines.append(
            f"| `{case['fixture_id']}` | {'PASS' if case['passed'] else 'FAIL'} | "
            f"{passed_checks}/{len(checks)} |"
        )
    lines.extend(
        [
            "",
            "## Model and workflow metrics",
            "",
            "Unavailable in this report. Run the integration/evaluation harness against a "
            "selected extraction provider before reporting precision, recall, F1, evidence "
            "validity, routing accuracy, latency, retry success, or cost.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ground_truth_files = sorted((FIXTURE_DIR / "ground_truth").glob("*.json"))
    cases = [validate_case(path) for path in ground_truth_files]
    fixture_ids = [case["fixture_id"] for case in cases]
    unique_fixture_ids = len(fixture_ids) == len(set(fixture_ids))
    passed = bool(cases) and unique_fixture_ids and all(case["passed"] for case in cases)

    report: dict[str, object] = {
        "schema_version": "1.0",
        "report_type": "phase_1_dataset_integrity",
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "fixture_count": len(cases),
        "unique_fixture_ids": unique_fixture_ids,
        "cases": cases,
        "model_quality_metrics": {
            "status": "unavailable",
            "reason": "This runner validates dataset integrity; it does not execute a provider.",
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "latest.md").write_text(markdown_report(report), encoding="utf-8")

    print(f"Phase 1 dataset integrity: {'PASS' if passed else 'FAIL'} ({len(cases)} fixtures)")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
