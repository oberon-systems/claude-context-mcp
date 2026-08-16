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
ROOT_GOALS := help init shell lint check build up down restart logs ps status \
	index psql clean skill-install skill-uninstall skill-status $(SUBS)
ifneq (,$(filter $(firstword $(MAKECMDGOALS)),$(SUBS)))
SUBARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(eval $(filter-out $(ROOT_GOALS),$(SUBARGS)):;@:)
endif

.PHONY: help init shell lint check build up down restart logs ps status index \
	psql clean graphify mcp skill-install skill-uninstall skill-status \
	require-venv require-env require-not-root

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

# Answers two questions `ps` cannot: is the server reachable, and is anything
# actually using it. `sessions` in the health payload counts connected MCP
# clients, so zero there means no client attached however healthy the containers
# look. Every step is best-effort: a stopped stack reports what is missing
# rather than failing the target.
#
# MCP_PORT is read out of .env rather than from `$(COMPOSE) port`, which only
# answers while the container runs and would leave the address unprintable in
# exactly the case worth reporting.
#
# The service list goes through xargs rather than `tr`: compose prints a bare
# newline when nothing runs, and a translated newline is a space, which is not
# empty enough for the shell default to fire.
status: require-env  ## Show whether the stack runs and whether anything uses it
	@running=$$($(COMPOSE) ps --services --filter status=running 2> /dev/null \
		| xargs); \
	echo "containers: $${running:-none running}"; \
	port=$$(sed -n 's/^MCP_PORT=//p' .env | tail -1); \
	port=$${port:-3000}; \
	health=$$(curl -sS localhost:$$port/health 2> /dev/null); \
	echo "health:     $${health:-unreachable on localhost:$$port}"; \
	nodes=$$($(COMPOSE) exec -T postgres psql -U "$${POSTGRES_USER:-user}" \
		-d "$${POSTGRES_DB:-context}" -tAc 'select count(*) from graph_nodes' \
		2> /dev/null | tr -d '\r'); \
	echo "graph:      $${nodes:-unavailable} nodes"

# graphify runs to completion and exits, so `run --rm` rather than `up`.
index: require-env  ## Index PROJECT_PATH into the graph (one-shot job)
	$(COMPOSE) --profile index run --rm graphify

psql: require-env  ## Open a psql session against the context database
	$(COMPOSE) exec postgres psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-context}"

# The database lives in a bind mount, not a volume, so `down -v` never reached
# it: that flag only drops named and anonymous volumes. Emptying the directory
# here is what actually lets init-db/ be replayed on the next `up`, and what
# stops a regenerated POSTGRES_PASSWORD from meeting a database that still
# holds the old one and refuses every connection.
#
# The files belong to the postgres uid, so the host user cannot remove them.
# Compose runs the deletion as root inside the postgres service itself, which
# also means DATA_DIR stays resolved by compose rather than parsed again here.
clean: require-env  ## Remove the containers, the database and the built images
	@test -n "$(FORCE)" || { \
		echo "This empties the database, and the index goes with it."; \
		read -r -p "Continue? [y/N] " reply; \
		case "$$reply" in [yY]*) ;; *) echo "aborted"; exit 1 ;; esac; \
	}
	$(COMPOSE) --profile index down --remove-orphans
	$(COMPOSE) run --rm --no-deps --user root --entrypoint sh postgres -c \
		'rm -rf /var/lib/postgresql/data/..?* \
			/var/lib/postgresql/data/.[!.]* \
			/var/lib/postgresql/data/*'
	$(COMPOSE) --profile index down -v --remove-orphans
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) TAG='$(TAG)' clean
	@$(MAKE) --no-print-directory -C $(MCP_DIR) TAG='$(TAG)' clean

# The skill is one file, and both agents read the same format. It is not
# copied into a home directory: Claude finds it in the project, and Gemini is
# pointed at the working copy, so an edit here takes effect without a reinstall
# and without anything of ours living in $HOME.
SKILL_DIR := skills/graphify
CLAUDE_SKILL_DIR := .claude/skills/graphify

skill-install: require-not-root  ## Register the graphify skill for Claude and Gemini
	@mkdir -p $(dir $(CLAUDE_SKILL_DIR))
	@ln -sfn ../../$(SKILL_DIR) $(CLAUDE_SKILL_DIR)
	@echo "claude: $(CLAUDE_SKILL_DIR) -> $(SKILL_DIR)"
	@command -v gemini > /dev/null \
		&& gemini skills link --consent --scope workspace \
			$(CURDIR)/$(SKILL_DIR) > /dev/null \
		&& echo "gemini: linked $(SKILL_DIR)" \
		|| echo "gemini: not installed, skipped"

skill-uninstall: require-not-root  ## Remove the graphify skill from both agents
	@rm -rf $(CLAUDE_SKILL_DIR)
	@echo "claude: removed"
	@command -v gemini > /dev/null \
		&& { gemini skills uninstall graphify > /dev/null 2>&1 \
			&& echo "gemini: removed" \
			|| echo "gemini: was not installed"; } \
		|| echo "gemini: not installed, skipped"

skill-status:  ## Show where the skill is registered
	@test -f $(CLAUDE_SKILL_DIR)/SKILL.md \
		&& echo "claude: $(CLAUDE_SKILL_DIR)/SKILL.md" \
		|| echo "claude: not installed"
	@command -v gemini > /dev/null \
		&& gemini skills list 2> /dev/null | grep -i graphify \
			|| echo "gemini: not listed"

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

# Under sudo the agents' own state lives in /root, so an install would land
# where the user running them never looks.
require-not-root:
	@test -z "$$SUDO_USER" || { \
		echo "Run this without sudo: it registers the skill for $$SUDO_USER" >&2; \
		exit 1; \
	}
