import os
from collections.abc import Iterator

import httpx
import pytest

from scripts.evaluate_documents import local_url, token_for


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    if os.getenv("MEDBOT_COMPOSE_INTEGRATION") != "1":
        pytest.skip("Run make integration against the local Compose stack")
    url = local_url("MEDBOT_API_BASE_URL", "http://localhost:8000")
    with httpx.Client(
        base_url=f"{url}/api/v1/",
        timeout=190,
        headers={"Authorization": f"Bearer {token_for('synthetic-admin-alpha')}"},
    ) as session:
        yield session
