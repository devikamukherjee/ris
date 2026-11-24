SHELL := /bin/bash

# .env values (no quotes) are exported to every recipe.
-include .env
export

# Interpreter used to create the venv. Override with: make setup PYTHON=/path/to/python3.12
PYTHON ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.11 || command -v python3)
VENV := $(CURDIR)/.venv
PY := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help doctor setup seed dev test study-export verify-audit precompute clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

doctor: ## Check Python, Node, Ollama and the configured model
	@cd backend && $(PYTHON) -m app.doctor

setup: ## Create .venv, install backend + analysis deps, install frontend deps, create .env
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))' || \
		{ echo "$(PYTHON) is older than 3.11. Run: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	@if command -v uv >/dev/null; then \
		uv venv --python $(PYTHON) $(VENV) && uv pip install --python $(PY) -e 'backend[dev,analysis]'; \
	else \
		$(PYTHON) -m venv $(VENV) && $(PY) -m pip install -q --upgrade pip && \
		$(PY) -m pip install -q -e 'backend[dev,analysis]'; \
	fi
	@if [ -f frontend/package.json ]; then npm --prefix frontend install; fi
	@[ -f .env ] || { cp .env.example .env && echo "Created .env from .env.example"; }
	@echo "Setup done. Run: make doctor"

dev: ## Run the API on 127.0.0.1:8000 (and the Vite dev server once frontend/ exists)
	@if [ -f frontend/package.json ]; then \
		trap 'kill 0' EXIT; \
		(cd backend && $(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & \
		npm --prefix frontend run dev; \
	else \
		cd backend && $(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload; \
	fi

test: ## Run the backend test suite
	@cd backend && $(PY) -m pytest -q

# Targets below are filled in by later milestones; until then they fail loudly.
seed: ## Generate the seeded shift into SQLite + JSON fixtures (M1)
	@echo "make seed is not implemented yet (milestone M1)" >&2; exit 1

verify-audit: ## Verify the audit hash chain (M3)
	@echo "make verify-audit is not implemented yet (milestone M3)" >&2; exit 1

precompute: ## Generate and cache all LLM explanations (M4)
	@echo "make precompute is not implemented yet (milestone M4)" >&2; exit 1

study-export: ## Export study data to study/exports/*.csv (M6)
	@echo "make study-export is not implemented yet (milestone M6)" >&2; exit 1

clean: ## Remove the venv and caches (keeps .env and data)
	rm -rf $(VENV) backend/*.egg-info .pytest_cache backend/.pytest_cache
