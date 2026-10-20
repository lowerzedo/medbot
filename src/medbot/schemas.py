from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from medbot.document_processing import SourceRegion
from medbot.domain import (
    DocumentProcessingStatus,
    EvidenceStatus,
    FieldReviewStatus,
    IssueSeverity,
    ReferralStatus,
    Role,
)

SafeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
FieldPath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
EvidenceQuote = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ActorContext(StrictSchema):
    user_id: str
    organization_id: str
    roles: frozenset[Role]
    auth_method: Literal["dev", "oidc"]


class ReferralCreate(StrictSchema):
    specialty: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, to_lower=True)
    ] = "cardiology"


class EvidenceReferenceResponse(StrictSchema):
    document_id: str
    page: int
    block_id: str
    quote: str
    verified: bool
    extraction_method: Literal["fixture", "pdf_text", "ocr"] = "fixture"
    ocr_confidence: float | None = None
    region: SourceRegion | None = None


class ExtractedFieldResponse(StrictSchema):
    id: str
    field_name: str
    ai_value: str | None
    reviewed_value: str | None
    confidence: float
    evidence_status: EvidenceStatus
    review_status: FieldReviewStatus
    reviewer_id: str | None
    reviewed_at: datetime | None
    review_reason: str | None
    evidence: list[EvidenceReferenceResponse]


class ValidationIssueResponse(StrictSchema):
    id: str
    code: str
    severity: IssueSeverity
    message: str
    field_path: str | None
    resolved: bool


class DocumentResponse(StrictSchema):
    id: str
    original_filename: str
    media_type: str
    classification: str
    processing_state: DocumentProcessingStatus
    page_count: int
    created_at: datetime


class DocumentUploadResponse(StrictSchema):
    document: DocumentResponse
    duplicate: bool


class ReferralSummary(StrictSchema):
    id: str
    status: ReferralStatus
    requested_service: str | None
    routing_queue: str | None
    complete: bool | None
    created_at: datetime
    updated_at: datetime


class ReferralListResponse(StrictSchema):
    items: list[ReferralSummary]
    total: int


class RoutingDecisionResponse(StrictSchema):
    queue: str
    rule_version: str
    created_at: datetime


class ReferralValidationResponse(StrictSchema):
    complete: bool | None
    missing_fields: list[str]
    pending_review_fields: list[str] = Field(default_factory=list)
    missing_documents: list[str]
    warnings: list[str]


class ReferralDetail(ReferralSummary):
    documents: list[DocumentResponse]
    fields: list[ExtractedFieldResponse]
    validation: ReferralValidationResponse
    validation_issues: list[ValidationIssueResponse]
    routing_decision: RoutingDecisionResponse | None


class FieldApproveRequest(StrictSchema):
    reason: SafeText | None = None


class FieldCorrectRequest(StrictSchema):
    value: SafeText
    reason: SafeText | None = None


class FieldSupplyRequest(StrictSchema):
    field_path: FieldPath
    value: SafeText
    reason: SafeText


class RouteResponse(StrictSchema):
    referral_id: str
    status: ReferralStatus
    queue: str
    rule_version: str
    fhir_resource_count: int


class FHIRWriteResponse(StrictSchema):
    referral_id: str
    resource_ids: list[str]
    idempotent: bool


class AuditEventResponse(StrictSchema):
    id: str
    sequence: int
    timestamp: datetime
    organization_id: str
    referral_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    trace_id: str
    metadata: dict[str, str]
    previous_event_hash: str | None
    event_hash: str


class ErrorBody(StrictSchema):
    code: str
    message: str
    trace_id: str
    fields: list[str] = Field(default_factory=list)


class ErrorResponse(StrictSchema):
    error: ErrorBody


class HealthResponse(StrictSchema):
    status: Literal["ok", "ready", "not_ready"]


class AIExtractionEvidence(StrictSchema):
    document_id: str
    page_number: int = Field(ge=1)
    block_id: str
    text_sha256: Sha256Hex
    quote: EvidenceQuote


class AIExtractedField(StrictSchema):
    path: FieldPath
    value: SafeText
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[AIExtractionEvidence, ...] = Field(min_length=1)


class AIExtractionOutput(StrictSchema):
    schema_version: Literal["1.0"]
    fields: tuple[AIExtractedField, ...]
    requires_review: bool = False


class ProcessingConfigurationResponse(StrictSchema):
    document_extractor: Literal["fixture", "pdfium-tesseract"]
    ai_provider: Literal["fixture", "ollama", "openai", "gemini"]
    ai_model: str | None
    hosted: bool


class CompletenessResult(StrictSchema):
    complete: bool
    missing_fields: list[str]
    missing_documents: list[str]
    warnings: list[str]


class FHIRIdentifier(StrictSchema):
    system: str
    value: str


class FHIRHumanName(StrictSchema):
    text: str


class FHIRReference(StrictSchema):
    reference: str


class FHIRPatient(StrictSchema):
    resourceType: Literal["Patient"] = "Patient"
    id: str
    identifier: list[FHIRIdentifier] | None = None
    name: list[FHIRHumanName] | None = None
    birthDate: date | None = None


class FHIRPractitioner(StrictSchema):
    resourceType: Literal["Practitioner"] = "Practitioner"
    id: str
    identifier: list[FHIRIdentifier]
    active: bool = True
    name: list[FHIRHumanName] | None = None


class FHIROrganization(StrictSchema):
    resourceType: Literal["Organization"] = "Organization"
    id: str
    identifier: list[FHIRIdentifier]
    active: bool = True


class FHIRServiceRequest(StrictSchema):
    resourceType: Literal["ServiceRequest"] = "ServiceRequest"
    id: str
    identifier: list[FHIRIdentifier]
    status: Literal["active"] = "active"
    intent: Literal["order"] = "order"
    subject: FHIRReference
    requester: FHIRReference | None = None
    authoredOn: datetime
    description: str | None = None


class FHIRAttachment(StrictSchema):
    contentType: Literal["application/pdf"] = "application/pdf"
    url: str
    title: str
    size: int
    hash: str | None = None


class FHIRDocumentContent(StrictSchema):
    attachment: FHIRAttachment


class FHIRDocumentReference(StrictSchema):
    resourceType: Literal["DocumentReference"] = "DocumentReference"
    id: str
    identifier: list[FHIRIdentifier]
    status: Literal["current"] = "current"
    subject: FHIRReference
    date: datetime
    content: list[FHIRDocumentContent]


class FHIRTask(StrictSchema):
    resourceType: Literal["Task"] = "Task"
    id: str
    identifier: list[FHIRIdentifier]
    status: Literal["requested"] = "requested"
    intent: Literal["order"] = "order"
    focus: FHIRReference
    for_: FHIRReference = Field(alias="for", serialization_alias="for")
    owner: FHIRReference


class FHIRProvenanceAgent(StrictSchema):
    who: FHIRReference


class FHIRProvenance(StrictSchema):
    resourceType: Literal["Provenance"] = "Provenance"
    id: str
    target: list[FHIRReference]
    recorded: datetime
    agent: list[FHIRProvenanceAgent]


FHIRResource = Annotated[
    FHIRPatient
    | FHIRPractitioner
    | FHIROrganization
    | FHIRServiceRequest
    | FHIRDocumentReference
    | FHIRTask
    | FHIRProvenance,
    Field(discriminator="resourceType"),
]


class FHIRRequest(StrictSchema):
    method: Literal["PUT"]
    url: str


class FHIRBundleEntry(StrictSchema):
    fullUrl: str
    resource: FHIRResource
    request: FHIRRequest


class FHIRBundle(StrictSchema):
    resourceType: Literal["Bundle"] = "Bundle"
    type: Literal["transaction"] = "transaction"
    id: str
    entry: list[FHIRBundleEntry]
