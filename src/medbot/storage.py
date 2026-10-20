from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageUnavailableError(ObjectStorageError):
    pass


class ObjectIntegrityError(ObjectStorageError):
    pass


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def ready(self) -> bool: ...


def get_verified_object(
    storage: ObjectStorage,
    key: str,
    *,
    expected_sha256: str,
    expected_size: int,
) -> bytes:
    try:
        content = storage.get(key)
    except KeyError:
        raise ObjectStorageUnavailableError("Stored object is unavailable") from None
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != expected_size or not hmac.compare_digest(digest, expected_sha256):
        raise ObjectIntegrityError("Stored object failed its integrity check")
    return content


class MalwareScanner(Protocol):
    def is_safe(self, content: bytes) -> bool: ...


class FixtureMalwareScanner:
    """Insertion point for a production malware scanner; fixtures are treated as clean."""

    def is_safe(self, content: bytes) -> bool:
        return bool(content)


@dataclass(frozen=True, slots=True)
class StoredObject:
    content: bytes
    content_type: str


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}
        self._lock = RLock()

    def put(self, key: str, content: bytes, content_type: str) -> None:
        with self._lock:
            self._objects.setdefault(key, StoredObject(bytes(content), content_type))

    def get(self, key: str) -> bytes:
        with self._lock:
            return self._objects[key].content

    def ready(self) -> bool:
        return True


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=5),
        )

    def put(self, key: str, content: bytes, content_type: str) -> None:
        try:
            self._ensure_bucket()
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except (BotoCoreError, ClientError):
            raise ObjectStorageUnavailableError("Object storage write failed") from None

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"].read()
        except (BotoCoreError, ClientError, KeyError):
            raise ObjectStorageUnavailableError("Object storage read failed") from None
        if not isinstance(body, bytes):
            raise ObjectStorageUnavailableError("Object storage returned an invalid response")
        return body

    def ready(self) -> bool:
        try:
            self._ensure_bucket()
        except (BotoCoreError, ClientError):
            return False
        return True

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self._client.create_bucket(Bucket=self._bucket)
