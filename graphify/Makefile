# Image build for the graphify indexer.
#
# Normally reached from the repo root, which passes TAG down through
# $(MAKE) -C. Standalone use works too and falls back to the defaults below.

# The reference compose runs, so `make build` replaces the image the stack
# starts rather than producing one nothing addresses.
IMAGE ?= ghcr.io/oberon-systems/claude-context-mcp/graphify
TAG ?= latest
REF := $(IMAGE):$(TAG)

DOCKER ?= docker
PYTHON ?= python3

REPO_ROOT := $(abspath $(CURDIR)/..)

# Only used by `run` and `shell`, which start the image outside compose.
PROJECT_PATH ?= $(REPO_ROOT)

.DEFAULT_GOAL := help

.PHONY: help build rebuild run shell lint test clean

help:
	@echo "Image: $(REF)"
	@echo
	@echo "Targets:"
	@echo "  build     Build the image"
	@echo "  rebuild   Build the image without the layer cache"
	@echo "  run       Run the indexer against PROJECT_PATH (needs a reachable database)"
	@echo "  shell     Open a shell inside the image"
	@echo "  lint      Run ruff over the Python sources"
	@echo "  test      Run the unit tests (pip install -r requirements-dev.txt)"
	@echo "  clean     Remove the built image"

build:
	$(DOCKER) build -t $(REF) .

rebuild:
	$(DOCKER) build --no-cache --pull -t $(REF) .

# Joins the compose network so `postgres` resolves. Prefer `make index` from
# the repo root, which does this through compose.
run:
	$(DOCKER) run --rm \
		--network claude-context-mcp_base \
		-e DATABASE_URL \
		-v "$(PROJECT_PATH)":/project:ro \
		$(REF)

shell:
	$(DOCKER) run --rm -it \
		-v "$(PROJECT_PATH)":/project:ro \
		--entrypoint /bin/bash \
		$(REF)

# Uses the repo virtualenv when it exists so the ruff version matches the one
# pinned in .pre-commit-config.yaml.
lint:
	@if [ -x $(REPO_ROOT)/.venv/bin/ruff ]; then \
		$(REPO_ROOT)/.venv/bin/ruff check .; \
	else \
		$(PYTHON) -m ruff check .; \
	fi

# The suite mocks the model, so it needs neither the weights nor a llama.cpp
# build - see tests/conftest.py.
test:
	@if [ -x $(REPO_ROOT)/.venv/bin/pytest ]; then \
		$(REPO_ROOT)/.venv/bin/pytest -q; \
	else \
		$(PYTHON) -m pytest -q; \
	fi

clean:
	-$(DOCKER) image rm $(REF)
