from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from medbot.models import AuditRecord, Referral


def append_audit_record(
    session: Session,
    *,
    referral: Referral,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    trace_id: str,
    metadata: dict[str, str] | None = None,
) -> AuditRecord:
    # Serialize writers on the stable parent row. Locking only the latest audit row
    # leaves the first event and concurrent inserts vulnerable to sequence races.
    session.scalar(
        select(Referral.id)
        .where(
            Referral.id == referral.id,
            Referral.organization_id == referral.organization_id,
        )
        .with_for_update()
    )
    previous = session.scalar(
        select(AuditRecord)
        .where(AuditRecord.referral_id == referral.id)
        .order_by(AuditRecord.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    timestamp = datetime.now(UTC)
    metadata_json = _canonical_metadata(metadata or {})
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = None if previous is None else previous.event_hash
    event = AuditRecord(
        id=str(uuid4()),
        sequence=sequence,
        timestamp=timestamp,
        organization_id=referral.organization_id,
        referral_id=referral.id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        trace_id=trace_id,
        metadata_json=metadata_json,
        previous_event_hash=previous_hash,
        event_hash=_event_hash(
            sequence=sequence,
            timestamp=timestamp,
            organization_id=referral.organization_id,
            referral_id=referral.id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
            metadata_json=metadata_json,
            previous_event_hash=previous_hash,
        ),
    )
    session.add(event)
    session.flush()
    return event


def verify_audit_chain(records: list[AuditRecord]) -> bool:
    expected_previous: str | None = None
    for record in sorted(records, key=lambda item: item.sequence):
        if record.previous_event_hash != expected_previous:
            return False
        expected_hash = _event_hash(
            sequence=record.sequence,
            timestamp=record.timestamp,
            organization_id=record.organization_id,
            referral_id=record.referral_id,
            actor_id=record.actor_id,
            action=record.action,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            trace_id=record.trace_id,
            metadata_json=record.metadata_json,
            previous_event_hash=record.previous_event_hash,
        )
        if record.event_hash != expected_hash:
            return False
        expected_previous = record.event_hash
    return True


def decode_metadata(metadata_json: str) -> dict[str, str]:
    decoded: object = json.loads(metadata_json)
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()
    ):
        raise ValueError("Audit metadata is invalid")
    return {str(key): str(value) for key, value in decoded.items()}


def _canonical_metadata(metadata: dict[str, str]) -> str:
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _event_hash(
    *,
    sequence: int,
    timestamp: datetime,
    organization_id: str,
    referral_id: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    trace_id: str,
    metadata_json: str,
    previous_event_hash: str | None,
) -> str:
    normalized_timestamp = timestamp
    if normalized_timestamp.tzinfo is None:
        normalized_timestamp = normalized_timestamp.replace(tzinfo=UTC)
    payload = {
        "action": action,
        "actor_id": actor_id,
        "metadata": json.loads(metadata_json),
        "organization_id": organization_id,
        "previous_event_hash": previous_event_hash,
        "referral_id": referral_id,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "sequence": sequence,
        "timestamp": normalized_timestamp.astimezone(UTC).isoformat(timespec="microseconds"),
        "trace_id": trace_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
