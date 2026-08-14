# Developer entry points for claude-context-mcp. Run `make` for the target list.

PROJECT := claude-context-mcp
VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

VERSION := $(shell sed -n 's/^  version: \(.*\)/\1/p' .cz.yaml)

COMPOSE ?= docker compose
TAG ?= dev
export TAG

GRAPHIFY_DIR := graphify
MCP_DIR := mcp-server

# Process substitution in the shell target needs bash, not sh.
SHELL := /bin/bash

.DEFAULT_GOAL := help

# `make mcp build` reads as a subcommand, but make sees two goals. Absorb
# everything after the subdivision name into do-nothing rules so only the
# delegation runs. Root target names are left alone, otherwise make warns about
# the override.
SUBS := graphify mcp
ROOT_GOALS := help init shell lint check build up down restart logs ps index \
	psql clean $(SUBS)
ifneq (,$(filter $(firstword $(MAKECMDGOALS)),$(SUBS)))
SUBARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(eval $(filter-out $(ROOT_GOALS),$(SUBARGS)):;@:)
endif

.PHONY: help init shell lint check build up down restart logs ps index psql \
	clean graphify mcp require-venv require-env

help:  ## Show the current version and the available targets
	@echo "$(PROJECT) $(VERSION)"
	@echo
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST)
	@echo
	@echo "  graphify <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) help | sed 's/^\(.\)/      \1/'
	@echo
	@echo "  mcp <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(MCP_DIR) help | sed 's/^\(.\)/      \1/'
	@echo

init:  ## Create the virtualenv and install the pre-commit hooks
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install pre-commit commitizen 'wyld-cz>=0.2.1' ruff
	$(VENV)/bin/pre-commit install --install-hooks
	@test -f .env || cp .env.example .env
	# The eslint/tsc hook runs from mcp-server/node_modules, so `make lint`
	# needs them present.
	@command -v npm > /dev/null \
		&& $(MAKE) --no-print-directory -C $(MCP_DIR) install \
		|| echo "npm not found, run 'make mcp install' before 'make lint'"
	@echo "Initialization complete. Edit .env, then run 'make build && make up'."

shell: require-venv  ## Open an interactive subshell with the virtualenv activated
	@$(SHELL) --rcfile <(cat ~/.bashrc 2> /dev/null; \
		echo 'source $(CURDIR)/$(VENV)/bin/activate') -i

lint: require-venv  ## Run the pre-commit hooks over every file
	$(VENV)/bin/pre-commit run --all-files

check: lint  ## Alias for lint

build:  ## Build every service image
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) TAG='$(TAG)' build
	@$(MAKE) --no-print-directory -C $(MCP_DIR) TAG='$(TAG)' build

up: require-env  ## Start postgres and the MCP server in the background
	$(COMPOSE) up -d postgres mcp-server

down:  ## Stop the stack, keeping the database volume
	$(COMPOSE) down

restart: down up  ## Recreate the running services

logs:  ## Follow the logs of the running services
	$(COMPOSE) logs -f

ps:  ## Show the state of every service
	$(COMPOSE) ps

# graphify runs to completion and exits, so `run --rm` rather than `up`.
index: require-env  ## Index PROJECT_PATH into the graph (one-shot job)
	$(COMPOSE) --profile index run --rm graphify

psql: require-env  ## Open a psql session against the context database
	$(COMPOSE) exec postgres psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-context}"

# `down -v` also drops the pgdata volume, so the schema in init-db/ is replayed
# on the next `up`.
clean:  ## Remove the containers, the database volume and the built images
	$(COMPOSE) --profile index down -v --remove-orphans
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) TAG='$(TAG)' clean
	@$(MAKE) --no-print-directory -C $(MCP_DIR) TAG='$(TAG)' clean

# The sub-Makefiles own their own target lists, so everything after the
# subdivision name is passed straight through: `make mcp build`, `make mcp`.
graphify:
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) TAG='$(TAG)' $(SUBARGS)

mcp:
	@$(MAKE) --no-print-directory -C $(MCP_DIR) TAG='$(TAG)' $(SUBARGS)

require-venv:
	@test -x $(PYTHON) || { \
		echo "$(VENV) is missing or broken, run 'make init' first" >&2; \
		exit 1; \
	}

require-env:
	@test -f .env || { \
		echo ".env is missing, copy .env.example and fill it in" >&2; \
		exit 1; \
	}
