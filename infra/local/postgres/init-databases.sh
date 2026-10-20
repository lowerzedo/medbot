#!/bin/sh
set -eu

# Keep application, identity, and interoperability data under separate login,
# database, and schema boundaries. The bootstrap superuser is never given to an
# application service. These credentials are local defaults supplied by Compose.
psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=database_owner="$POSTGRES_USER" \
  --set=medbot_database="$MEDBOT_POSTGRES_DB" \
  --set=medbot_user="$MEDBOT_POSTGRES_USER" \
  --set=medbot_password="$MEDBOT_POSTGRES_PASSWORD" \
  --set=hapi_password="$HAPI_POSTGRES_PASSWORD" \
  --set=keycloak_password="$KEYCLOAK_POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'medbot_user', :'medbot_password')
WHERE NOT EXISTS (
  SELECT FROM pg_catalog.pg_roles WHERE rolname = :'medbot_user'
) \gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'medbot_database', :'database_owner')
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = :'medbot_database'
) \gexec

SELECT format('CREATE ROLE hapi_local LOGIN PASSWORD %L', :'hapi_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'hapi_local') \gexec

SELECT format('CREATE DATABASE hapi OWNER %I', :'database_owner')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hapi') \gexec

SELECT format('CREATE ROLE keycloak_local LOGIN PASSWORD %L', :'keycloak_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'keycloak_local') \gexec

SELECT format('CREATE DATABASE keycloak OWNER %I', :'database_owner')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak') \gexec
SQL

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$MEDBOT_POSTGRES_DB" \
  --set=medbot_database="$MEDBOT_POSTGRES_DB" \
  --set=medbot_user="$MEDBOT_POSTGRES_USER" <<'SQL'
REVOKE CONNECT ON DATABASE :"medbot_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"medbot_database" TO :"medbot_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS medbot AUTHORIZATION :"medbot_user";
ALTER ROLE :"medbot_user" IN DATABASE :"medbot_database" SET search_path TO medbot;
SQL

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hapi <<'SQL'
REVOKE CONNECT ON DATABASE hapi FROM PUBLIC;
GRANT CONNECT ON DATABASE hapi TO hapi_local;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS hapi AUTHORIZATION hapi_local;
ALTER ROLE hapi_local IN DATABASE hapi SET search_path TO hapi;
SQL

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname keycloak <<'SQL'
REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
GRANT CONNECT ON DATABASE keycloak TO keycloak_local;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS keycloak AUTHORIZATION keycloak_local;
ALTER ROLE keycloak_local IN DATABASE keycloak SET search_path TO keycloak;
SQL
