#!/bin/sh
set -u

failures=0

check_url() {
  name=$1
  url=$2
  if curl --fail --silent --show-error --output /dev/null --max-time 5 "$url"; then
    printf '%-18s %s\n' "$name" healthy
  else
    printf '%-18s %s (%s)\n' "$name" unavailable "$url"
    failures=$((failures + 1))
  fi
}

check_url "MedBot API" "${MEDBOT_HEALTH_URL:-http://localhost:8000/health/ready}"
check_url "Keycloak" "${KEYCLOAK_HEALTH_URL:-http://localhost:8081/realms/medbot/.well-known/openid-configuration}"
check_url "MinIO" "${MINIO_HEALTH_URL:-http://localhost:9000/minio/health/live}"
check_url "HAPI FHIR" "${HAPI_HEALTH_URL:-http://localhost:8082/actuator/health/readiness}"
check_url "OTel Collector" "${OTEL_HEALTH_URL:-http://localhost:13133/}"
check_url "Prometheus" "${PROMETHEUS_HEALTH_URL:-http://localhost:9090/-/ready}"
check_url "Grafana" "${GRAFANA_HEALTH_URL:-http://localhost:3001/api/health}"
check_url "Tempo" "${TEMPO_HEALTH_URL:-http://localhost:3200/ready}"

if [ "$failures" -ne 0 ]; then
  printf '\n%d service(s) are not healthy. Run `docker compose ps` and `make logs`.\n' "$failures"
  exit 1
fi

printf '\nAll local HTTP services are healthy.\n'
