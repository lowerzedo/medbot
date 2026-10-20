SHELL := /bin/sh

COMPOSE ?= docker compose
UV ?= uv
UV_RUN := $(UV) run --extra dev
LOCAL_AI_MODEL ?= qwen3:4b

.DEFAULT_GOAL := help

.PHONY: help dev dev-real down logs health compose-config format lint typecheck test integration eval benchmark benchmark-real generate-fixtures generate-document-fixtures ci

help: ## Show available development commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev: ## Build and start the complete local backend platform, then wait for health.
	$(COMPOSE) up --build --detach
	./scripts/wait-for-local-stack.sh

dev-real: ## Start local OCR and a real local AI model (first run downloads model weights).
	$(COMPOSE) --profile ai up --detach ollama
	$(COMPOSE) exec -T ollama ollama pull $(LOCAL_AI_MODEL)
	MEDBOT_AI_PROVIDER=ollama MEDBOT_AI_MODEL=$(LOCAL_AI_MODEL) $(COMPOSE) up --build --detach api
	./scripts/wait-for-local-stack.sh

down: ## Stop the local stack without deleting persistent volumes.
	$(COMPOSE) down --remove-orphans

logs: ## Follow logs for the API and platform services.
	$(COMPOSE) logs --follow --tail=100

health: ## Print a concise health summary for every local HTTP service.
	./scripts/local-health.sh

compose-config: ## Validate and render the Docker Compose model.
	$(COMPOSE) config --quiet

format: ## Format Python files and apply safe lint fixes.
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

lint: ## Check formatting and lint rules without modifying files.
	$(UV_RUN) ruff format --check .
	$(UV_RUN) ruff check .

typecheck: ## Run the backend static type checker.
	$(UV_RUN) mypy src

test: ## Run fast backend tests (the local-stack smoke test is separate).
	$(UV_RUN) pytest -m "not integration"

integration: ## Run OIDC/API smoke and recovery tests against all local providers.
	./scripts/wait-for-local-stack.sh
	./scripts/integration-smoke.sh
	MEDBOT_COMPOSE_INTEGRATION=1 $(UV_RUN) pytest -m integration

provision-settings-test-user: ## Add the new synthetic settings identity to an existing local realm.
	$(UV_RUN) python -m scripts.provision_settings_test_user

eval: ## Measure Phase 1 fixture integrity; does not claim model quality.
	$(UV_RUN) python scripts/evaluate_fixtures.py

benchmark: ## Execute synthetic extraction, recovery, grounding, routing, and audit checks.
	$(UV_RUN) python scripts/evaluate_workflow.py

benchmark-real: ## Measure real OCR and the configured model through the local Compose API.
	$(UV_RUN) python scripts/evaluate_documents.py

generate-fixtures: ## Rebuild synthetic PDFs and independent ground truth.
	$(UV_RUN) python synthetic/generator/generate_referrals.py

generate-document-fixtures: ## Rebuild the real OCR/model synthetic PDFs and frozen oracle.
	$(UV_RUN) python synthetic/generator/generate_document_cases.py

ci: lint typecheck test compose-config ## Run the fast local equivalents of CI checks.
