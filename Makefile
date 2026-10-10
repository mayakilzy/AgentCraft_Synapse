# AgentCraft Synapse — developer Makefile
# All targets are idempotent and safe to re-run.

PYTHON ?= python3
PIP ?= pip
COVERAGE_THRESHOLD ?= 70

.PHONY: help install lint format test test-fast test-deterministic test-slow \
	migrate-up migrate-down openapi-check smoke clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Editable install with dev + postgres extras
	$(PIP) install -e ".[dev,postgres]"

lint:  ## Ruff lint
	ruff check src tests

format:  ## Ruff format (in place)
	ruff format src tests

test:  ## Full pytest with coverage
	$(PYTHON) -m pytest

test-fast:  ## Pytest without coverage (skips live tests)
	$(PYTHON) -m pytest --no-cov -q -m "not live"

test-deterministic:  ## Complete deterministic suite — no coverage, no live tests, single invocation
	$(PYTHON) -m pytest --no-cov -q -p no:cacheprovider -m "not live"

test-slow:  ## Run only the known-slow integration files (for diagnosis)
	$(PYTHON) -m pytest --no-cov -q -p no:cacheprovider \
	  tests/integration/test_g04_t02_capability_registry.py \
	  tests/integration/test_g04_t03_reasoning.py \
	  tests/integration/test_g04_t05_external_client.py \
	  tests/integration/test_g05_t01_knowledge_combination.py

migrate-up:  ## Apply all migrations
	$(PYTHON) -m alembic upgrade head

migrate-down:  ## Roll back all migrations (only in dev/test!)
	$(PYTHON) -m alembic downgrade base

migrate-new:  ## Create empty migration: make migrate-new MSG="description"
	test -n "$(MSG)" || (echo "Usage: make migrate-new MSG='description'" && exit 1)
	$(PYTHON) -m alembic revision -m "$(MSG)" --autogenerate

openapi-check:  ## Validate OpenAPI schema is well-formed
	$(PYTHON) -c "import json, sys; \
	  from synapse.main import create_app; \
	  schema = create_app().openapi(); \
	  print(json.dumps(schema, indent=2)[:200]); \
	  assert schema['openapi'].startswith('3.'), 'OpenAPI version mismatch'; \
	  print('OpenAPI OK')"

# ── G02 Audit Preparation ────────────────────────────────────────────────────
# These targets are run AFTER the user provides TOOLKIT_INDEX(1).json.
# Until then they exit with a friendly message.

audit-validate:  ## Validate TOOLKIT_INDEX(1).json against schema
	@if [ ! -f "docs/toolkit_audit/TOOLKIT_INDEX(1).json" ]; then \
	  echo "TOOLKIT_INDEX(1).json not yet provided — awaiting user (per ADR-0007 D-01)."; \
	else \
	  $(PYTHON) scripts/validate_toolkit_index.py "docs/toolkit_audit/TOOLKIT_INDEX(1).json"; \
	fi

audit-verify-imports:  ## Verify which indexed tools are actually importable (stage 1 + stage 2)
	@if [ ! -f "docs/toolkit_audit/TOOLKIT_INDEX(1).json" ]; then \
	  echo "TOOLKIT_INDEX(1).json not yet provided — awaiting user (per ADR-0007 D-01)."; \
	else \
	  $(PYTHON) scripts/verify_tool_imports.py "docs/toolkit_audit/TOOLKIT_INDEX(1).json"; \
	fi

audit-smoke:  ## Run functional smoke tests for shortlisted providers (stage 3, per ADR-0008)
	@if [ ! -f "docs/toolkit_audit/TOOLKIT_INDEX(1).json" ]; then \
	  echo "TOOLKIT_INDEX(1).json not yet provided — awaiting user (per ADR-0007 D-01)."; \
	elif [ ! -f "docs/toolkit_audit/verification_results.json" ]; then \
	  echo "Run 'make audit-verify-imports' first."; exit 1; \
	else \
	  $(PYTHON) scripts/functional_smoke_tests.py "docs/toolkit_audit/verification_results.json"; \
	fi

audit-generate-matrix:  ## Generate Layer-1 Capability Matrix (schema v2.0) from index + verification + smoke
	@if [ ! -f "docs/toolkit_audit/TOOLKIT_INDEX(1).json" ]; then \
	  echo "TOOLKIT_INDEX(1).json not yet provided — awaiting user (per ADR-0007 D-01)."; \
	elif [ ! -f "docs/toolkit_audit/verification_results.json" ]; then \
	  echo "Run 'make audit-verify-imports' first."; exit 1; \
	else \
	  SMOKE_ARG=""; \
	  if [ -f "docs/toolkit_audit/smoke_results.json" ]; then \
	    SMOKE_ARG="docs/toolkit_audit/smoke_results.json"; \
	  fi; \
	  $(PYTHON) scripts/generate_capability_matrix.py \
	    "docs/toolkit_audit/TOOLKIT_INDEX(1).json" \
	    "docs/toolkit_audit/verification_results.json" \
	    $$SMOKE_ARG \
	    --out-json "docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json" \
	    --out-md   "docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md"; \
	fi

audit-all: audit-validate audit-verify-imports audit-smoke audit-generate-matrix  ## Full audit pipeline (per ADR-0008)

smoke:  ## Run smoke tests against a running service (uvicorn must be up)
	$(PYTHON) -c "import httpx; r=httpx.get('http://127.0.0.1:8000/health/live'); \
	  print(r.status_code, r.json()); assert r.status_code == 200"

clean:  ## Remove build artifacts and caches
	rm -rf build dist *.egg-info .pytest_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
