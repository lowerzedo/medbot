from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx

from medbot.models import Document, ExtractedField, Referral
from medbot.policy import parse_referral_date
from medbot.referral_validation import TRUSTED_FIELD_STATUSES
from medbot.schemas import (
    FHIRAttachment,
    FHIRBundle,
    FHIRBundleEntry,
    FHIRDocumentContent,
    FHIRDocumentReference,
    FHIRHumanName,
    FHIRIdentifier,
    FHIROrganization,
    FHIRPatient,
    FHIRPractitioner,
    FHIRProvenance,
    FHIRProvenanceAgent,
    FHIRReference,
    FHIRRequest,
    FHIRResource,
    FHIRServiceRequest,
    FHIRTask,
)

IDENTIFIER_SYSTEM = "https://medbot.example.test/identifier"


@dataclass(frozen=True, slots=True)
class FHIRWriteResult:
    resource_ids: tuple[str, ...]
    idempotent: bool


class FHIRProvider(Protocol):
    def write(self, bundle: FHIRBundle) -> FHIRWriteResult: ...

    def ready(self) -> bool: ...


class InMemoryFHIRProvider:
    def __init__(self) -> None:
        self.resources: dict[tuple[str, str], FHIRResource] = {}

    def write(self, bundle: FHIRBundle) -> FHIRWriteResult:
        already_present = all(
            (entry.resource.resourceType, entry.resource.id) in self.resources
            for entry in bundle.entry
        )
        for entry in bundle.entry:
            self.resources[(entry.resource.resourceType, entry.resource.id)] = entry.resource
        return FHIRWriteResult(
            resource_ids=tuple(entry.resource.id for entry in bundle.entry),
            idempotent=already_present,
        )

    def ready(self) -> bool:
        return True


class HAPIFHIRProvider:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        backoff: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(10, connect=2))
        self._max_attempts = max_attempts
        self._backoff = backoff

    def write(self, bundle: FHIRBundle) -> FHIRWriteResult:
        ids: list[str] = []
        all_preexisting = True
        for entry in bundle.entry:
            url = f"{self._base_url}/{entry.request.url}"
            response = self._put_with_retry(
                url,
                entry.resource.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            all_preexisting = all_preexisting and response.status_code == 200
            ids.append(entry.resource.id)
        return FHIRWriteResult(tuple(ids), all_preexisting)

    def ready(self) -> bool:
        try:
            response = self._client.get(f"{self._base_url}/metadata")
        except httpx.HTTPError:
            return False
        return response.is_success

    def _put_with_retry(self, url: str, payload: object) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            try:
                response = self._client.put(url, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 == self._max_attempts:
                    raise
                self._backoff(0.1 * (2**attempt))
                continue
            last_response = response
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt + 1 < self._max_attempts:
                self._backoff(0.1 * (2**attempt))
        if last_response is None:
            raise RuntimeError("FHIR persistence failed without a response")
        last_response.raise_for_status()
        return last_response


def build_fhir_bundle(referral: Referral) -> FHIRBundle:
    values = _field_values(referral.extracted_fields)
    patient_id = _stable_id(referral, "patient")
    practitioner_id = _stable_id(referral, "practitioner")
    organization_id = _stable_id(referral, "organization")
    service_request_id = _stable_id(referral, "service-request")
    task_id = _stable_id(referral, "task")

    patient_name = values.get("patient.full_name")
    birth_date = parse_referral_date(values.get("patient.date_of_birth"))
    practitioner_name = values.get("referring_provider.name")
    requested_service = values.get("requested_service")

    resources: list[FHIRResource] = [
        FHIRPatient(
            id=patient_id,
            identifier=[_identifier(referral.id, "referral-patient")],
            name=[FHIRHumanName(text=patient_name)] if patient_name else None,
            birthDate=birth_date,
        ),
        FHIRPractitioner(
            id=practitioner_id,
            identifier=[_identifier(referral.id, "referring-practitioner")],
            name=[FHIRHumanName(text=practitioner_name)] if practitioner_name else None,
        ),
        FHIROrganization(
            id=organization_id,
            identifier=[_identifier(referral.organization_id, "organization")],
        ),
        FHIRServiceRequest(
            id=service_request_id,
            identifier=[_identifier(referral.id, "service-request")],
            subject=FHIRReference(reference=f"Patient/{patient_id}"),
            requester=FHIRReference(reference=f"Practitioner/{practitioner_id}"),
            authoredOn=referral.created_at,
            description=requested_service,
        ),
    ]
    for document in referral.documents:
        resources.append(_document_reference(referral, document, patient_id))
    resources.append(
        FHIRTask(
            id=task_id,
            identifier=[_identifier(referral.id, "workflow-task")],
            focus=FHIRReference(reference=f"ServiceRequest/{service_request_id}"),
            **{
                "for": FHIRReference(reference=f"Patient/{patient_id}"),
                "owner": FHIRReference(reference=f"Organization/{organization_id}"),
            },
        )
    )
    resources.append(
        FHIRProvenance(
            id=_stable_id(referral, "provenance"),
            target=[
                FHIRReference(reference=f"{resource.resourceType}/{resource.id}")
                for resource in resources
            ],
            recorded=referral.updated_at,
            agent=[
                FHIRProvenanceAgent(who=FHIRReference(reference=f"Organization/{organization_id}"))
            ],
        )
    )
    entries = [
        FHIRBundleEntry(
            fullUrl=f"urn:uuid:{resource.id}",
            resource=resource,
            request=FHIRRequest(method="PUT", url=f"{resource.resourceType}/{resource.id}"),
        )
        for resource in resources
    ]
    return FHIRBundle(id=_stable_id(referral, "bundle"), entry=entries)


def _document_reference(
    referral: Referral, document: Document, patient_id: str
) -> FHIRDocumentReference:
    return FHIRDocumentReference(
        id=_stable_id(referral, f"document-reference:{document.id}"),
        identifier=[_identifier(document.id, "document")],
        subject=FHIRReference(reference=f"Patient/{patient_id}"),
        date=document.uploaded_at,
        content=[
            FHIRDocumentContent(
                attachment=FHIRAttachment(
                    url=f"urn:medbot:document:{document.id}",
                    title=document.original_filename,
                    size=document.size_bytes,
                    hash=(
                        base64.b64encode(bytes.fromhex(document.fhir_sha1)).decode("ascii")
                        if document.fhir_sha1 is not None
                        else None
                    ),
                )
            )
        ],
    )


def _field_values(fields: Iterable[ExtractedField]) -> dict[str, str]:
    return {
        field.path: value
        for field in fields
        if field.evidence_status in TRUSTED_FIELD_STATUSES
        and (value := field.human_value if field.human_value is not None else field.ai_value)
    }


def _identifier(value: str, kind: str) -> FHIRIdentifier:
    return FHIRIdentifier(system=f"{IDENTIFIER_SYSTEM}/{kind}", value=value)


def _stable_id(referral: Referral, resource_key: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"medbot:{referral.organization_id}:{referral.id}:{resource_key}",
        )
    )
