.PHONY: venv install lint test up down api tui models

PYENV_VERSION := $(shell cat .python-version)
PYTHON := $(HOME)/.pyenv/versions/$(PYENV_VERSION)/bin/python

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
	ollama pull llama3.2:1b
	ollama pull nomic-embed-text
