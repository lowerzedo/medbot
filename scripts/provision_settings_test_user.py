"""Add the dedicated synthetic settings user to an already initialized local realm."""

import json
import os
from pathlib import Path

import httpx

from scripts.evaluate_documents import local_url


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    realm = json.loads((root / "infra/local/keycloak/medbot-realm.json").read_text())
    user = next(item for item in realm["users"] if item["username"] == "synthetic-settings-alpha")
    base = local_url("KEYCLOAK_BASE_URL", "http://localhost:8081")
    with httpx.Client(base_url=base, timeout=20) as client:
        response = client.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": os.getenv("KEYCLOAK_ADMIN_USERNAME", "local-admin"),
                "password": os.getenv(
                    "KEYCLOAK_ADMIN_PASSWORD", "local-only-keycloak-admin-password"
                ),
            },
        )
        if response.status_code != 200:
            raise SystemExit(
                "Local Keycloak login failed; provide explicit local bootstrap credentials"
            )
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        profile_response = client.get("/admin/realms/medbot/users/profile")
        if profile_response.status_code != 200:
            raise SystemExit("Cannot inspect local user profile configuration")
        profile = profile_response.json()
        # Never enable arbitrary unmanaged attributes or end-user organization editing.
        profile["attributes"] = [
            attribute
            for attribute in profile["attributes"]
            if attribute["name"] != "organization_id"
        ] + [
            {
                "name": "organization_id",
                "displayName": "Organization",
                "permissions": {"view": ["admin"], "edit": ["admin"]},
                "validations": {"length": {"max": 100}},
                "multivalued": False,
            }
        ]
        if client.put("/admin/realms/medbot/users/profile", json=profile).status_code not in {
            200,
            204,
        }:
            raise SystemExit("Cannot configure the trusted organization attribute")
        existing = client.get(
            "/admin/realms/medbot/users", params={"username": user["username"], "exact": "true"}
        )
        if existing.status_code != 200:
            raise SystemExit("Cannot inspect local test identity")
        if existing.json():
            identity = existing.json()[0]
            user_id = identity["id"]
            identity["attributes"] = identity.get("attributes", {}) | user["attributes"]
            if (
                client.put(f"/admin/realms/medbot/users/{user_id}", json=identity).status_code
                != 204
            ):
                raise SystemExit("Cannot repair the synthetic test organization attribute")
        else:
            response = client.post(
                "/admin/realms/medbot/users",
                json={key: value for key, value in user.items() if key != "realmRoles"},
            )
            if response.status_code != 201:
                raise SystemExit("Cannot create dedicated synthetic settings user")
            user_id = response.headers["location"].rsplit("/", 1)[-1]
        roles = []
        for name in user["realmRoles"]:
            role = client.get(f"/admin/realms/medbot/roles/{name}")
            if role.status_code != 200:
                raise SystemExit("Cannot resolve local test role")
            roles.append(role.json())
        assigned = client.post(
            f"/admin/realms/medbot/users/{user_id}/role-mappings/realm", json=roles
        )
        if assigned.status_code != 204:
            raise SystemExit("Cannot assign local test role")
    print("Dedicated synthetic settings user and trusted organization attribute are configured")


if __name__ == "__main__":
    main()
