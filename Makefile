.PHONY: venv install lint test up down api tui models ingest eval

PYENV_VERSION := $(shell cat .python-version)
PYTHON := $(HOME)/.pyenv/versions/$(PYENV_VERSION)/bin/python

# Chat-model override, for comparing models against the eval harness without
# editing .env:  make eval OLLAMA_MODEL=granite4.1:8b
#
# The ifdef guard is load-bearing. A bare `export OLLAMA_MODEL` exports an
# EMPTY string when the variable is unset, and pydantic-settings ranks real
# environment variables above .env - so an unguarded export would silently
# blank out whatever .env configures. Only export it when it was actually
# passed; otherwise leave it unset and let .env win.
ifdef OLLAMA_MODEL
export OLLAMA_MODEL
endif

# `models` needs a concrete name to pull, so it falls back to ADR-0004's
# default rather than inheriting the "unset" case above.
PULL_MODEL := $(if $(OLLAMA_MODEL),$(OLLAMA_MODEL),llama3.2:1b)
PULL_EMBED_MODEL := nomic-embed-text

venv:
	pyenv install -s $(PYENV_VERSION)
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip

install:
	.venv/bin/pip install -e ".[dev]"

lint:
	.venv/bin/ruff check src tests

test:
	.venv/bin/pytest

up:
	docker compose up -d

down:
	docker compose down

api:
	.venv/bin/ekassistant-api

tui:
	.venv/bin/ekassistant-tui

models:
	ollama pull $(PULL_MODEL)
	ollama pull $(PULL_EMBED_MODEL)

ingest:
	.venv/bin/ekassistant-ingest

eval:
	.venv/bin/ekassistant-eval
