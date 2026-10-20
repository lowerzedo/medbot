"""Real local OCR/model benchmark against frozen synthetic oracles and the Compose API.

Reports contain counts/timings only, never document text, model output, or credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from medbot.field_grounding import normalize_field_value
from medbot.schemas import ProcessingConfigurationResponse, ReferralDetail

ROOT = Path(__file__).resolve().parents[1]


class Location(BaseModel):
    page: int
    top: float


class DocumentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    file: str
    fields: dict[str, str]
    locations: dict[str, Location]
    alternate_locations: dict[str, Location] = Field(default_factory=dict)
    text: str
    ocr: bool
    error: str | None
    conflict: bool


def local_url(name: str, default: str) -> str:
    value = os.getenv(name, default)
    if urlsplit(value).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"Local evaluation requires a loopback URL for {name}")
    return value.rstrip("/")


def token_for(username: str) -> str:
    realm = json.loads((ROOT / "infra/local/keycloak/medbot-realm.json").read_text())
    user = next(user for user in realm["users"] if user["username"] == username)
    password = next(item["value"] for item in user["credentials"] if item["type"] == "password")
    response = httpx.post(
        f"{local_url('KEYCLOAK_BASE_URL', 'http://localhost:8081')}/realms/medbot/protocol/openid-connect/token",
        data={
            "client_id": "medbot-ui",
            "grant_type": "password",
            "username": username,
            "password": password,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError("Synthetic local OIDC login failed")
    token: object = response.json()["access_token"]
    if not isinstance(token, str):
        raise RuntimeError("Invalid local OIDC response")
    return token


def word_errors(expected: str, actual: str) -> tuple[int, int]:
    wanted, received = expected.casefold().split(), actual.casefold().split()
    previous = list(range(len(received) + 1))
    for i, word in enumerate(wanted, 1):
        current = [i]
        for j, found in enumerate(received, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != found))
            )
        previous = current
    return previous[-1], len(wanted)


OCR_PROBE = """import json,sys
from medbot.document_processing import PDFDocumentExtractor
from medbot.errors import ApplicationError
try:
 blocks=PDFDocumentExtractor().extract('synthetic-evaluation',sys.stdin.buffer.read())
 text='\\n'.join(b.text for b in blocks)
 print(json.dumps({'text':text,'ocr_blocks':sum(b.extraction_method=='ocr' for b in blocks)}))
except ApplicationError as error:
 print(json.dumps({'error':error.code}))
"""

RUNTIME_PROBE = """import hashlib,json,platform,subprocess
from pathlib import Path
from importlib.metadata import version
import httpx,medbot
from medbot.config import Settings
settings=Settings.from_env()
source=Path(medbot.__file__).parent
source_bytes=b''.join(p.name.encode()+p.read_bytes() for p in sorted(source.glob('*.py')))
digest=hashlib.sha256(source_bytes).hexdigest()
model_digest=None
if settings.ai_provider=='ollama':
 data=httpx.get(settings.ollama_base_url.removesuffix('/v1')+'/api/tags',timeout=10).json()
 model_digest=next((m['digest'] for m in data['models'] if m['name']==settings.ai_model),None)
engine=subprocess.run(['tesseract','--version'],capture_output=True,check=True).stdout.decode().splitlines()[0]
print(json.dumps({'python':platform.python_version(),'source_sha256':digest,'pdfium':version('pypdfium2'),'provider_library':version('pydantic-ai-slim'),'tesseract':engine,'model_digest':model_digest}))
"""


def detail(response: httpx.Response) -> ReferralDetail:
    if not response.is_success:
        raise RuntimeError(f"Synthetic workflow operation failed with HTTP {response.status_code}")
    return ReferralDetail.model_validate(response.json())


def exercise_case(client: httpx.Client, case: DocumentCase) -> dict[str, object]:
    path = (ROOT / case.file).resolve()
    if not path.is_relative_to(ROOT / "synthetic/documents"):
        raise ValueError("Only committed synthetic documents may be evaluated")
    content = path.read_bytes()
    started = perf_counter()
    # Attribute OCR errors independently of what the model chose to extract.
    probe = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", OCR_PROBE],
        input=content,
        capture_output=True,
        timeout=70,
        check=True,
    )
    raw = json.loads(probe.stdout)
    errors, words = word_errors(case.text, raw.get("text", "")) if case.error is None else (0, 0)
    created = detail(client.post("referrals", json={"specialty": "cardiology"}))
    uploaded = client.post(
        f"referrals/{created.id}/documents",
        data={"classification": "referral_order"},
        files={"file": (f"synthetic-{case.id}.pdf", content, "application/pdf")},
    )
    if uploaded.status_code != 201:
        raise RuntimeError("Synthetic document upload failed")
    response = client.post(f"referrals/{created.id}/process")
    if case.error:
        matched = (
            response.status_code == 422
            and response.json().get("error", {}).get("code") == case.error
            and raw.get("error") == case.error
        )
        return {
            "id": case.id,
            "passed": matched,
            "expected_error_matched": matched,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "false_ready": 0,
            "seconds": round(perf_counter() - started, 3),
        }
    processed = detail(response)
    false_ready = int(processed.status == "READY_TO_ROUTE")
    correct = 0
    located = 0
    proposals = {field.field_name: field for field in processed.fields}
    for field in processed.fields:
        expected = case.fields.get(field.field_name)
        if expected is None:
            continue
        values = {expected}
        if case.conflict and field.field_name == "patient.date_of_birth":
            values.add("1981-03-04")
        if normalize_field_value(field.field_name, field.ai_value or "") in {
            normalize_field_value(field.field_name, value) for value in values
        }:
            correct += 1
        location = case.locations[field.field_name]
        locations = [location]
        if field.field_name in case.alternate_locations:
            locations.append(case.alternate_locations[field.field_name])
        if any(
            ref.region
            and any(
                ref.page == item.page and abs(ref.region.y - item.top) < 0.035 for item in locations
            )
            for ref in field.evidence
        ):
            located += 1
    blocked = client.post(f"referrals/{created.id}/route").status_code == 409
    conflict_checked = not case.conflict or any(
        field.evidence_status == "CONFLICTING" for field in processed.fields
    )
    # Recovery is an explicit synthetic reviewer action, never an inferred AI action.
    recovered = processed
    for field in processed.fields:
        if field.evidence_status == "CONFLICTING":
            recovered = detail(
                client.post(
                    f"referrals/{created.id}/fields/{field.id}/correct",
                    json={
                        "value": case.fields[field.field_name],
                        "reason": "Compared synthetic source pages with independent test oracle",
                    },
                )
            )
        elif field.evidence_status in {"LOW_CONFIDENCE", "REVIEW_REQUIRED"}:
            recovered = detail(
                client.post(
                    f"referrals/{created.id}/fields/{field.id}/approve",
                    json={"reason": "Compared the synthetic source region"},
                )
            )
    if "patient.date_of_birth" not in proposals:
        recovered = detail(
            client.post(
                f"referrals/{created.id}/fields",
                json={
                    "field_path": "patient.date_of_birth",
                    "value": "1970-01-02",
                    "reason": "Confirmed with synthetic referrer",
                },
            )
        )
    routed = client.post(f"referrals/{created.id}/route")
    workflow_ok = recovered.status == "READY_TO_ROUTE" and routed.status_code == 200
    evidence_ok = all(
        field.evidence and all(ref.verified and ref.region for ref in field.evidence)
        for field in processed.fields
    )
    method_ok = (raw.get("ocr_blocks", 0) > 0) == case.ocr
    return {
        "id": case.id,
        "passed": bool(
            blocked
            and conflict_checked
            and evidence_ok
            and method_ok
            and workflow_ok
            and not false_ready
            and correct == len(case.fields)
            and len(proposals) == len(case.fields)
            and located == len(case.fields)
        ),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "field_correct": correct,
        "field_predicted": len(proposals),
        "field_expected": len(case.fields),
        "located_fields": located,
        "ocr_word_errors": errors,
        "ocr_expected_words": words,
        "ocr_blocks": raw.get("ocr_blocks", 0),
        "extraction_method_matched": method_ok,
        "false_ready": false_ready,
        "routing_blocked_before_review": blocked,
        "conflict_checked": conflict_checked,
        "recovery_and_routing": workflow_ok,
        "seconds": round(perf_counter() - started, 3),
    }


def main() -> None:
    oracle_path = ROOT / "eval/document_cases.json"
    oracle = json.loads(oracle_path.read_text())
    if oracle.get("synthetic_only") is not True:
        raise ValueError("This benchmark permits synthetic cases only")
    cases = [DocumentCase.model_validate(case) for case in oracle["cases"]]
    url = local_url("MEDBOT_API_BASE_URL", "http://localhost:8000")
    results = []
    with httpx.Client(base_url=f"{url}/api/v1/", timeout=140) as client:
        client.headers["Authorization"] = f"Bearer {token_for('synthetic-admin-alpha')}"
        config = ProcessingConfigurationResponse.model_validate(
            client.get("processing-configuration").json()
        )
        if config.document_extractor != "pdfium-tesseract" or config.ai_provider == "fixture":
            raise ValueError(
                "Start the real OCR/model stack with make dev-real before this benchmark"
            )
        if config.hosted and os.getenv("MEDBOT_ALLOW_HOSTED_EVAL") != "1":
            raise ValueError("Hosted evaluation requires explicit MEDBOT_ALLOW_HOSTED_EVAL=1")
        for case in cases:
            client.headers["Authorization"] = f"Bearer {token_for('synthetic-admin-alpha')}"
            try:
                result = exercise_case(client, case)
            except (RuntimeError, httpx.HTTPError, subprocess.SubprocessError):
                result = {"id": case.id, "passed": False, "operation_failed": True}
            results.append(result)
            print(f"{case.id}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    expected_count = sum(len(case.fields) for case in cases if case.error is None)
    correct_count = sum(int(result.get("field_correct", 0)) for result in results)
    predicted_count = sum(int(result.get("field_predicted", 0)) for result in results)
    expected_words = sum(int(result.get("ocr_expected_words", 0)) for result in results)
    metrics = {
        "field_precision": correct_count / predicted_count if predicted_count else 0,
        "field_recall": correct_count / expected_count if expected_count else 0,
        "source_region_recall": sum(int(result.get("located_fields", 0)) for result in results)
        / expected_count,
        "word_error_rate": sum(int(result.get("ocr_word_errors", 0)) for result in results)
        / expected_words
        if expected_words
        else 0,
        "false_ready_count": sum(int(result.get("false_ready", 0)) for result in results),
    }
    runtime = json.loads(
        subprocess.run(
            ["docker", "compose", "exec", "-T", "api", "python", "-c", RUNTIME_PROBE],
            capture_output=True,
            timeout=20,
            check=True,
        ).stdout
    )
    report = {
        "schema_version": "1.0",
        "synthetic_only": True,
        "created_at": datetime.now(UTC).isoformat(),
        "provider": config.model_dump(),
        "oracle_sha256": hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
        "passed": all(result["passed"] for result in results),
        "cases": results,
        "metrics": metrics,
        "runtime": runtime,
    }
    directory = ROOT / "eval/reports"
    directory.mkdir(exist_ok=True)
    (directory / "documents-latest.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
