# Developer entry points for claude-context-mcp. Run `make` for the target list.

NAME := claude-context-mcp
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
	index unindex backup restore psql clean skill-install skill-uninstall \
	skill-status $(SUBS)
ifneq (,$(filter $(firstword $(MAKECMDGOALS)),$(SUBS)))
SUBARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(eval $(filter-out $(ROOT_GOALS),$(SUBARGS)):;@:)
endif

.PHONY: help init shell lint check build up down restart logs ps status index \
	unindex backup restore psql clean graphify mcp skill-install \
	skill-uninstall skill-status require-venv require-env require-not-root

help:  ## Show the current version and the available targets
	@echo "$(NAME) $(VERSION)"
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
	python3 -m venv --prompt $(NAME) $(VENV)
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

# The viewer is named here rather than left to a bare `up` so that /graph,
# which the MCP server redirects to, answers on a stack this target started.
up: require-env  ## Start postgres, the MCP server and the viewer in the background
	$(COMPOSE) up -d postgres mcp-server viewer

# The index job sits behind a profile, so a plain `down` does not see it: a
# graphify container left over from `make index` keeps the network alive and
# the teardown ends in "Resource is still in use". Name the profile so the
# whole project goes.
down:  ## Stop the stack, keeping the database volume
	$(COMPOSE) --profile index down --remove-orphans

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
	graph=$$($(COMPOSE) exec -T postgres psql -U "$${POSTGRES_USER:-user}" \
		-d "$${POSTGRES_DB:-context}" -tAc "select string_agg( \
			p.name || ' (' || c.nodes || ')', ', ' order by p.name) \
			from projects p, lateral ( \
				select count(*) as nodes from graph_nodes g \
				 where g.project = p.name) c" \
		2> /dev/null | tr -d '\r'); \
	echo "graph:      $${graph:-unavailable}"

# graphify runs to completion and exits, so `run --rm` rather than `up`.
#
# One database holds every indexed codebase, so the tree to walk is an argument
# rather than a setting: `make index PROJECT=/path/to/anything` mounts that
# path and stores its graph under its own name. The target needs nothing of the
# indexed repository - no checkout of this one inside it, no .env, no Makefile -
# which is what makes indexing a neighbour a one-liner. PROJECT_PATH from .env
# is the default, so a plain `make index` keeps meaning what it did.
#
# PROJECT_ROOT travels alongside the mount because the container only ever sees
# /project, and the projects table records where that came from.
#
# FRESH=1 re-extracts every file instead of trusting a cache - the extractor's
# own per-file cache and the file_hashes table both. Spelled apart from FORCE,
# which means "skip the confirmation" for `unindex` and `clean`.
PROJECT ?=
PROJECT_NAME ?=
FRESH ?=
FILE ?=
KEEP ?=
BACKUP_DIR ?=
INDEXED := $(if $(PROJECT),$(abspath $(PROJECT)),)

index: require-env  ## Index PROJECT (default: PROJECT_PATH from .env)
	$(if $(INDEXED),PROJECT_PATH='$(INDEXED)') \
		$(if $(PROJECT_NAME),PROJECT_NAME='$(PROJECT_NAME)') \
		$(if $(FRESH),FORCE_REEXTRACT=1) \
		$(COMPOSE) --profile index run --rm graphify

# The other end of `index`: one project leaves the database, the rest stay.
# Naming it is deliberate work - PROJECT= resolves through the projects table by
# root path, PROJECT_NAME= names the row directly, and neither defaulting to
# PROJECT_PATH is the point. A bare `make index` is convenient; a bare
# `make unindex` would be a way to delete the wrong graph.
unindex: require-env  ## Drop PROJECT= or PROJECT_NAME= from the database
	@COMPOSE='$(COMPOSE)' UNINDEX_PATH='$(INDEXED)' \
		UNINDEX_NAME='$(PROJECT_NAME)' FORCE='$(FORCE)' scripts/unindex.sh

# The other maintenance pair: `backup` writes, `restore` puts back. Both take
# the same selectors as `unindex` - nothing named means the whole database -
# and both leave the destination directory to the script, which defaults it
# next to the database rather than reading a setting for it.
#
# FILE= names one file instead of the generated name, and then rotation leaves
# it alone: KEEP=N only ever prunes files this naming scheme produced.
backup: require-env  ## Back up the database, or PROJECT= / PROJECT_NAME= alone
	@COMPOSE='$(COMPOSE)' BACKUP_PATH='$(INDEXED)' \
		BACKUP_NAME='$(PROJECT_NAME)' BACKUP_FILE='$(FILE)' \
		BACKUP_DIR='$(BACKUP_DIR)' KEEP='$(KEEP)' scripts/backup.sh

# No default here for the same reason `unindex` has none: a bare invocation
# that replaces a database is the accident worth designing out.
restore: require-env  ## Restore FILE= over the database or over one project
	@COMPOSE='$(COMPOSE)' BACKUP_FILE='$(FILE)' BACKUP_DIR='$(BACKUP_DIR)' \
		FORCE='$(FORCE)' scripts/restore.sh

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

# The skill is one file, and both agents read the same format. What it cannot
# know from here is where it is being installed, which decides three things:
#
#   AGENT_ROOT   where the agents look. Claude Code reads .claude/skills/ of
#                the project root, and this repository is not that root when
#                the skill is installed into another codebase.
#   MAKE_PREFIX  how that codebase reaches these targets. From inside this
#                repository a plain `make`; from anywhere else `make -C` here,
#                since the stack lives here and nothing of it is in the other
#                repository. A codebase reaching this through a proxy target
#                of its own passes that instead.
#   SKILL_ROOT   which tree the skill's own project is. Indexing is an
#                argument now, so the rebuild command has to name the tree it
#                belongs to, or it would re-index whatever .env points at.
#
# Which is why the skill is rendered rather than linked: the placeholders in
# the source carry all three, and the installed copy names commands that
# actually run where it is installed. Editing the source needs a reinstall.
AGENT_ROOT ?= $(CURDIR)
MAKE_PREFIX ?= $(if $(filter $(abspath $(AGENT_ROOT)),$(CURDIR)),make,make -C $(CURDIR))
SKILL_ROOT ?= $(abspath $(AGENT_ROOT))
SKILL_SRC := skills/graphify/SKILL.md
SKILL_DIR := $(AGENT_ROOT)/.claude/skills/graphify

skill-install: require-not-root  ## Register the graphify skill for Claude and Gemini
	@mkdir -p $(SKILL_DIR)
	@sed -e 's|@MAKE@|$(MAKE_PREFIX)|g' -e 's|@ROOT@|$(SKILL_ROOT)|g' \
		$(SKILL_SRC) > $(SKILL_DIR)/SKILL.md
	@echo "claude: $(SKILL_DIR)/SKILL.md"
	@echo "        rebuilds with: $(MAKE_PREFIX) index PROJECT=$(SKILL_ROOT)"
	@command -v gemini > /dev/null \
		&& (cd $(AGENT_ROOT) && gemini skills link --consent --scope workspace \
			$(SKILL_DIR) > /dev/null) \
		&& echo "gemini: linked $(SKILL_DIR)" \
		|| echo "gemini: not installed, skipped"

skill-uninstall: require-not-root  ## Remove the graphify skill from both agents
	@rm -rf $(SKILL_DIR)
	@echo "claude: removed"
	@command -v gemini > /dev/null \
		&& { (cd $(AGENT_ROOT) && gemini skills uninstall graphify) \
			> /dev/null 2>&1 \
			&& echo "gemini: removed" \
			|| echo "gemini: was not installed"; } \
		|| echo "gemini: not installed, skipped"

skill-status:  ## Show where the skill is registered
	@test -f $(SKILL_DIR)/SKILL.md \
		&& echo "claude: $(SKILL_DIR)/SKILL.md" \
		|| echo "claude: not installed"
	@command -v gemini > /dev/null \
		&& (cd $(AGENT_ROOT) && gemini skills list 2> /dev/null) \
			| grep -i graphify \
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
