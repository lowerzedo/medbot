"""Loopback-only, ephemeral synthetic API for testing the UI/BFF against the real backend.

This harness uses an isolated in-memory database and fixture adapters. It is never a
production entry point or a substitute for Compose integration tests.
"""

import argparse
import json

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from medbot.config import Settings
from medbot.database import Base
from medbot.fhir import InMemoryFHIRProvider
from medbot.main import create_app
from medbot.storage import InMemoryObjectStorage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url="sqlite+pysqlite://",
        object_storage="memory",
        auth_mode="dev",
        dev_tokens_json=json.dumps(
            {
                "dev-token-org-a": {
                    "user_id": "synthetic-e2e-reviewer",
                    "organization_id": "synthetic-e2e-org",
                    "roles": ["administrator"],
                }
            }
        ),
    )
    app = create_app(
        settings,
        engine=engine,
        storage=InMemoryObjectStorage(),
        fhir_provider=InMemoryFHIRProvider(),
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
