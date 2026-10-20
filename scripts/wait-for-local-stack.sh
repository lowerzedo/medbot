#!/bin/sh
set -u

timeout_seconds=${STACK_TIMEOUT_SECONDS:-300}
deadline=$(( $(date +%s) + timeout_seconds ))

printf 'Waiting up to %s seconds for the MedBot local stack...\n' "$timeout_seconds"
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ./scripts/local-health.sh >/dev/null 2>&1; then
    ./scripts/local-health.sh
    exit 0
  fi
  sleep 5
done

./scripts/local-health.sh || true
printf '\nLocal stack did not become healthy within %s seconds.\n' "$timeout_seconds" >&2
docker compose ps >&2
exit 1
