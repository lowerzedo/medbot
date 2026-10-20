#!/bin/sh
set -eu

api_base_url=${MEDBOT_API_BASE_URL:-http://localhost:8000}
keycloak_base_url=${KEYCLOAK_BASE_URL:-http://localhost:8081}
fixture_path=${MEDBOT_INTEGRATION_FIXTURE:-synthetic/fixtures/complete-referral.pdf}
username=${MEDBOT_INTEGRATION_USERNAME:-synthetic-admin-alpha}
password=${MEDBOT_INTEGRATION_PASSWORD:-local-only-admin-password}

json_value() {
  JSON_DOCUMENT=$1 python3 -c \
    'import json, os, sys; print(json.loads(os.environ["JSON_DOCUMENT"])[sys.argv[1]])' "$2"
}

if [ ! -f "$fixture_path" ]; then
  printf 'Integration fixture is missing: %s\n' "$fixture_path" >&2
  exit 1
fi

token_response=$(curl --fail --silent --show-error \
  --request POST "$keycloak_base_url/realms/medbot/protocol/openid-connect/token" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode client_id=medbot-ui \
  --data-urlencode grant_type=password \
  --data-urlencode "username=$username" \
  --data-urlencode "password=$password")
access_token=$(json_value "$token_response" access_token)

created=$(curl --fail --silent --show-error \
  --request POST "$api_base_url/api/v1/referrals" \
  --header "Authorization: Bearer $access_token" \
  --header 'Content-Type: application/json' \
  --data '{"specialty":"cardiology"}')
referral_id=$(json_value "$created" id)

curl --fail --silent --show-error --output /dev/null \
  --request POST "$api_base_url/api/v1/referrals/$referral_id/documents" \
  --header "Authorization: Bearer $access_token" \
  --form classification=referral_order \
  --form "file=@$fixture_path;type=application/pdf"

processed=$(curl --fail --silent --show-error \
  --request POST "$api_base_url/api/v1/referrals/$referral_id/process" \
  --header "Authorization: Bearer $access_token")
processed_status=$(json_value "$processed" status)
if [ "$processed_status" = NEEDS_REVIEW ]; then
  field_ids=$(JSON_DOCUMENT=$processed python3 -c 'import json,os; print(" ".join(field["id"] for field in json.loads(os.environ["JSON_DOCUMENT"])["fields"] if field["evidence_status"] in {"LOW_CONFIDENCE", "REVIEW_REQUIRED"}))')
  for field_id in $field_ids; do
    processed=$(curl --fail --silent --show-error \
      --request POST "$api_base_url/api/v1/referrals/$referral_id/fields/$field_id/approve" \
      --header "Authorization: Bearer $access_token" \
      --header 'Content-Type: application/json' \
      --data '{"reason":"Compared the synthetic source document"}')
  done
  processed_status=$(json_value "$processed" status)
fi
if [ "$processed_status" != READY_TO_ROUTE ]; then
  printf 'Expected READY_TO_ROUTE after processing, got %s\n' "$processed_status" >&2
  exit 1
fi

routed=$(curl --fail --silent --show-error \
  --request POST "$api_base_url/api/v1/referrals/$referral_id/route" \
  --header "Authorization: Bearer $access_token")
routed_status=$(json_value "$routed" status)
if [ "$routed_status" != ROUTED ]; then
  printf 'Expected ROUTED after routing, got %s\n' "$routed_status" >&2
  exit 1
fi

audit=$(curl --fail --silent --show-error \
  "$api_base_url/api/v1/referrals/$referral_id/audit" \
  --header "Authorization: Bearer $access_token")
JSON_DOCUMENT=$audit python3 -c '
import json
import os

actions = {event["action"] for event in json.loads(os.environ["JSON_DOCUMENT"])}
required = {"REFERRAL_CREATED", "DOCUMENT_UPLOADED", "FHIR_WRITTEN", "REFERRAL_ROUTED"}
missing = required - actions
if missing:
    raise SystemExit(f"Integration audit is missing expected actions: {sorted(missing)}")
'

printf 'Integration smoke passed for referral %s: READY_TO_ROUTE -> ROUTED.\n' "$referral_id"
