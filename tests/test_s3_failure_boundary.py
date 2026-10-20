from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from medbot.storage import ObjectStorageUnavailableError, S3ObjectStorage


@pytest.mark.parametrize("operation", ["put", "get", "ready"])
def test_provider_client_errors_are_normalized_without_sensitive_details(operation: str) -> None:
    client = Mock()
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Synthetic-private-provider-value"}},
        "SyntheticOperation",
    )
    client.head_bucket.return_value = {}
    if operation == "put":
        client.put_object.side_effect = error
    elif operation == "get":
        client.get_object.side_effect = error
    else:
        client.head_bucket.side_effect = error
    with patch("medbot.storage.boto3.client", return_value=client) as factory:
        storage = S3ObjectStorage(
            endpoint_url="http://minio:9000",
            access_key_id="synthetic-access",
            secret_access_key="local-only-synthetic-secret",
            bucket="synthetic",
            region="us-east-1",
        )
    # The SDK always receives explicit local credentials, never the default credential chain.
    assert factory.call_args.kwargs["aws_access_key_id"] == "synthetic-access"
    assert factory.call_args.kwargs["aws_secret_access_key"] == "local-only-synthetic-secret"
    if operation == "ready":
        assert storage.ready() is False
        return
    with pytest.raises(ObjectStorageUnavailableError) as caught:
        if operation == "put":
            storage.put("synthetic-key", b"synthetic-content", "application/pdf")
        else:
            storage.get("synthetic-key")
    assert "Synthetic-private-provider-value" not in str(caught.value)
