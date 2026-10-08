# AgentCraft Synapse — developer Makefile
# All targets are idempotent and safe to re-run.

PYTHON ?= python3
PIP ?= pip
COVERAGE_THRESHOLD ?= 70

.PHONY: help install lint format test test-fast migrate-up migrate-down \
        openapi-check smoke clean

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

test-fast:  ## Pytest without coverage
	$(PYTHON) -m pytest --no-cov -q

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

smoke:  ## Run smoke tests against a running service (uvicorn must be up)
	$(PYTHON) -c "import httpx; r=httpx.get('http://127.0.0.1:8000/health/live'); \
	  print(r.status_code, r.json()); assert r.status_code == 200"

clean:  ## Remove build artifacts and caches
	rm -rf build dist *.egg-info .pytest_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
