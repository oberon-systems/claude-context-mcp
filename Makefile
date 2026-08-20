# Developer entry points for claude-context-mcp. Run `make` for the target list.

NAME := claude-context-mcp
VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

VERSION := $(shell sed -n 's/^  version: \(.*\)/\1/p' .cz.yaml)

COMPOSE ?= docker compose
DOCKER ?= docker

# Inherited by the service Makefiles the same way TAG is: each one repeats the
# default so it still builds standalone, and a value set here wins. The images
# are named for the registry they are published to, so a local build replaces
# the reference docker-compose.yaml pins instead of producing one nothing runs.
REGISTRY ?= ghcr.io
NAMESPACE ?= oberon-systems/claude-context-mcp
TAG ?= latest
export TAG

GRAPHIFY_IMAGE := $(REGISTRY)/$(NAMESPACE)/graphify
MCP_IMAGE := $(REGISTRY)/$(NAMESPACE)/mcp-server
WEB_IMAGE := $(REGISTRY)/$(NAMESPACE)/web

GRAPHIFY_DIR := graphify
MCP_DIR := mcp-server
WEB_DIR := web
MIGRATIONS_DIR := migrations

# Process substitution in the shell target needs bash, not sh.
SHELL := /bin/bash

.DEFAULT_GOAL := help

# `make mcp build` reads as a subcommand, but make sees two goals. Absorb
# everything after the subdivision name into do-nothing rules so only the
# delegation runs. Root target names are left alone, otherwise make warns about
# the override.
SUBS := graphify mcp db web
ROOT_GOALS := help init install shell lint check build pull up down restart \
	logs ps status index reindex summarize unindex backup restore psql clean \
	skill-install skill-uninstall skill-status llm-model-install \
	api-up api-down api-logs jobs job $(SUBS)
ifneq (,$(filter $(firstword $(MAKECMDGOALS)),$(SUBS)))
SUBARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
$(eval $(filter-out $(ROOT_GOALS),$(SUBARGS)):;@:)
endif

.PHONY: help init install shell lint check build pull up down restart logs ps \
	status index reindex summarize unindex backup restore psql clean graphify \
	mcp db web skill-install skill-uninstall skill-status llm-model-install \
	api-up api-down api-logs jobs job \
	require-venv require-env require-not-root require-model require-api

help:  ## Show the current version and the available targets
	@echo "$(NAME) $(VERSION)"
	@echo
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST)
	@echo
	@echo "  graphify <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) \
		IMAGE='$(GRAPHIFY_IMAGE)' help | sed 's/^\(.\)/      \1/'
	@echo
	@echo "  mcp <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(MCP_DIR) \
		IMAGE='$(MCP_IMAGE)' help | sed 's/^\(.\)/      \1/'
	@echo
	@echo "  web <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(WEB_DIR) \
		IMAGE='$(WEB_IMAGE)' help | sed 's/^\(.\)/      \1/'
	@echo
	@echo "  db <target>"
	@echo
	@$(MAKE) --no-print-directory -C $(MIGRATIONS_DIR) \
		help | sed 's/^\(.\)/      \1/'
	@echo

init:  ## Create the virtualenv and install the pre-commit hooks
	python3 -m venv --prompt $(NAME) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install pre-commit commitizen ruff
	# The commit adapter is optional. Where it cannot be installed, commitizen
	# falls back to its own rules and .cz.yaml has to stop naming this one.
	-$(PIP) install 'wyld-cz>=0.2.1'
	$(VENV)/bin/pre-commit install --install-hooks
	@test -f .env || cp .env.example .env
	# The eslint/tsc hooks run from each tree's own node_modules, so
	# `make lint` needs both present.
	@command -v npm > /dev/null \
		&& $(MAKE) --no-print-directory -C $(MCP_DIR) deps \
		&& $(MAKE) --no-print-directory -C $(WEB_DIR) deps \
		|| echo "npm not found, run 'make mcp deps' and 'make web deps' first"
	@echo "Initialization complete. Edit .env, then run 'make build && make up'."

# Everything a codebase needs to be usable from an agent, in one pass: the
# `context` server registered for both agents, the skill rendered, an
# instruction file, the .ctxkeep/.ctxignore pair generated from what the tree
# actually holds, the shell aliases, and finally the graph itself.
#
# It reads AGENT_ROOT exactly as the skill targets below do, and their
# comment is where that variable is explained, so onboarding a neighbour is
# the same one variable. Nothing it writes replaces a file that exists, which
# is what makes a second run safe: it fills in whatever is missing and reports
# the rest as kept.
#
# INDEX=0 stops before building the graph, for a large tree that is better
# indexed later. ALIASES=0 leaves the shell rc file alone; SHELL_RC= names a
# different one.
INDEX ?= 1
ALIASES ?=
SHELL_RC ?=

install: require-env require-not-root  ## Onboard AGENT_ROOT and index it
	@AGENT_ROOT='$(abspath $(AGENT_ROOT))' PROJECT_NAME='$(PROJECT_NAME)' \
		MAKE_PREFIX='$(MAKE_PREFIX)' MAKE_BIN='$(MAKE)' \
		COMPOSE='$(COMPOSE)' ALIASES='$(ALIASES)' SHELL_RC='$(SHELL_RC)' \
		scripts/install.sh
	@test '$(INDEX)' = '0' \
		|| $(MAKE) --no-print-directory index TYPE='$(TYPE)' \
			PROJECT='$(abspath $(AGENT_ROOT))'

shell: require-venv  ## Open an interactive subshell with the virtualenv activated
	@$(SHELL) --rcfile <(cat ~/.bashrc 2> /dev/null; \
		echo 'source $(CURDIR)/$(VENV)/bin/activate') -i

lint: require-venv  ## Run the pre-commit hooks over every file
	$(VENV)/bin/pre-commit run --all-files

check: lint  ## Alias for lint

# Both services build under the reference compose runs, so a build replaces
# what the stack starts. That is worth saying out loud: the summary is how you
# see the image id actually moved, and a running stack still holds the previous
# one until it is recreated.
build:  ## Build every service image
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) \
		IMAGE='$(GRAPHIFY_IMAGE)' TAG='$(TAG)' build
	@$(MAKE) --no-print-directory -C $(MCP_DIR) \
		IMAGE='$(MCP_IMAGE)' TAG='$(TAG)' build
	@$(MAKE) --no-print-directory -C $(WEB_DIR) \
		IMAGE='$(WEB_IMAGE)' TAG='$(TAG)' build
	@for ref in $(GRAPHIFY_IMAGE):$(TAG) $(MCP_IMAGE):$(TAG) \
			$(WEB_IMAGE):$(TAG); do \
		$(DOCKER) image ls --format \
			'{{.Repository}}:{{.Tag}}  {{.ID}}  {{.Size}}' "$$ref" \
			| sed 's/^/  /'; \
	done
	@test -z "$$($(COMPOSE) ps -q 2> /dev/null)" \
		|| echo "  stack is running, 'make up' recreates it with these"

# The other end of `build`, and the reason it needs one: a local build takes
# over the same :latest reference the registry publishes, and `up` never
# re-pulls an image that is already present. This is what puts the published
# one back - and what a first run uses instead of building at all.
pull:  ## Pull the published images, discarding a local build
	$(COMPOSE) --profile index pull

# The viewer is named here rather than left to a bare `up` so that /graph,
# which the MCP server redirects to, answers on a stack this target started.
up: require-env  ## Start postgres, the MCP server and the viewer in the background
	$(COMPOSE) up -d postgres mcp-server viewer web

# The index job sits behind a profile, so a plain `down` does not see it: a
# graphify container left over from `make index` keeps the network alive and
# the teardown ends in "Resource is still in use". Name the profile so the
# whole project goes.
down:  ## Stop the stack, keeping the database volume
	$(COMPOSE) --profile index down --remove-orphans

restart: down up  ## Recreate the running services

# The token is read from .env and handed to curl on stdin rather than as an
# argument: an argument is visible in `ps` to every user of this machine.
WORKER_API_PORT ?= $(shell sed -n 's/^WORKER_API_PORT=//p' .env 2>/dev/null)
WORKER_API_PORT := $(or $(WORKER_API_PORT),3003)
API_URL := http://127.0.0.1:$(WORKER_API_PORT)
CURL_AUTH = printf 'header = "Authorization: Bearer %s"\n' \
	"$$(sed -n 's/^WORKER_API_TOKEN=//p' .env)" | curl -sS --config -

api-up: require-env require-api  ## Start the remote summarization API
	$(COMPOSE) --profile remote up -d worker-api

api-down:  ## Stop the remote summarization API
	$(COMPOSE) --profile remote stop worker-api

api-logs:  ## Follow the remote summarization API log
	$(COMPOSE) --profile remote logs -f worker-api

jobs: require-api  ## Queue a summarization job: make jobs PROJECT_NAME=kurum
	@test -n '$(PROJECT_NAME)' || { \
		echo "PROJECT_NAME= is required" >&2; exit 1; \
	}
	@$(CURL_AUTH) -X POST '$(API_URL)/jobs' \
		-H 'Content-Type: application/json' \
		-d '{"project":"$(PROJECT_NAME)","refresh":$(if $(FRESH),true,false)}'
	@echo

job: require-api  ## Show a summarization job: make job ID=7
	@test -n '$(ID)' || { echo "ID= is required" >&2; exit 1; }
	@$(CURL_AUTH) '$(API_URL)/jobs/$(ID)'
	@echo

require-api:
	@grep -q '^WORKER_API_TOKEN=..' .env 2>/dev/null || { \
		echo "WORKER_API_TOKEN is not set in .env." >&2; \
		echo "Generate one with: openssl rand -hex 24" >&2; \
		exit 1; \
	}

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
	web=$$(sed -n 's/^WEB_PORT=//p' .env | tail -1); \
	web=$${web:-3002}; \
	dash=$$(curl -sS localhost:$$web/api/health 2> /dev/null); \
	echo "dashboard:  $${dash:-unreachable on localhost:$$web}"; \
	graph=$$($(COMPOSE) exec -T postgres psql -U "$${POSTGRES_USER:-user}" \
		-d "$${POSTGRES_DB:-context}" -tAc "select string_agg( \
			p.name || ' (' || c.nodes || ')', ', ' order by p.name) \
			from projects p, lateral ( \
				select count(*) as nodes from graph_nodes g \
				 where g.project = p.name) c" \
		2> /dev/null | tr -d '\r'); \
	echo "graph:      $${graph:-unavailable}"; \
	schema=$$($(COMPOSE) exec -T postgres psql -U "$${POSTGRES_USER:-user}" \
		-d "$${POSTGRES_DB:-context}" -tAc "select to_char( \
			max(version_id), 'FM0000') from schema_migrations \
			 where is_applied" 2> /dev/null | tr -d '\r'); \
	echo "schema:     $${schema:-unmigrated}"

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
# What the project is, for the cross-project MCP search: codebase (the
# default), docs, config. Unset keeps whatever is already stored, so it is
# given once and not on every re-index.
TYPE ?=
FRESH ?=
# SUMMARIZE=1 has the index run write model summaries as it goes. Off by
# default because it costs seconds per file: `make summarize` is the same work
# without holding up the graph.
SUMMARIZE ?=
BG ?=
# Stop after this many files. A first pass over a large tree is measured in
# hours, and this is how it is timed before one is spent.
LIMIT ?=
FILE ?=
# Backups rotate by default: a target that only ever grows is a target nobody
# prunes. KEEP= empty turns it off for one run and keeps everything.
KEEP ?= 7
BACKUP_DIR ?=
INDEXED := $(if $(PROJECT),$(abspath $(PROJECT)),)

index: require-env  ## Index PROJECT (TYPE= categorises it)
	$(if $(INDEXED),PROJECT_PATH='$(INDEXED)') \
		$(if $(PROJECT_NAME),PROJECT_NAME='$(PROJECT_NAME)') \
		$(if $(TYPE),PROJECT_TYPE='$(TYPE)') \
		$(if $(FRESH),FORCE_REEXTRACT=1) \
		$(if $(SUMMARIZE),SUMMARIZE=1 LLM_MODEL_PATH='$(MODEL_PATH)') \
		$(COMPOSE) --profile index run --rm graphify

# The slow half of indexing, on its own: the model describes the files whose
# summary still comes from the head of the file, and marks each one as its
# own. It commits per file, so an interrupted run keeps what it wrote and the
# next one starts from what is left. FRESH=1 re-describes everything instead,
# cache included; BG=1 detaches, for the hours a large tree takes.
summarize: require-env require-model  ## Summarize PROJECT with the model (BG=1 detaches)
	$(if $(INDEXED),PROJECT_PATH='$(INDEXED)') \
		$(if $(PROJECT_NAME),PROJECT_NAME='$(PROJECT_NAME)') \
		$(if $(FRESH),FORCE_REEXTRACT=1) \
		$(if $(LIMIT),SUMMARY_LIMIT='$(LIMIT)') \
		LLM_MODEL_PATH='$(MODEL_PATH)' \
		$(COMPOSE) --profile index run --rm $(if $(BG),--detach) \
		graphify python -m ctxgraph.summarize

# The named form of `index FRESH=1`: distrust both caches - the extractor's
# per-file one and the file_hashes table - and parse every selected file again.
# Nothing is deleted first, so the graph is never absent while the run proceeds:
# the run rewrites what it finds and the usual prune pass drops what the tree no
# longer has.
reindex: require-env  ## Re-index PROJECT, trusting neither cache
	@$(MAKE) --no-print-directory index FRESH=1 \
		PROJECT='$(PROJECT)' PROJECT_NAME='$(PROJECT_NAME)' \
		TYPE='$(TYPE)'

# The other end of `index`: one project leaves the database, the rest stay.
# Naming it is deliberate work - PROJECT= resolves through the projects table by
# root path, PROJECT_NAME= names the row directly, and neither defaulting to
# PROJECT_PATH is the point. A bare `make index` is convenient; a bare
# `make unindex` would be a way to delete the wrong graph.
unindex: require-env  ## Drop PROJECT= or PROJECT_NAME= from the database
	@COMPOSE='$(COMPOSE)' UNINDEX_PATH='$(INDEXED)' \
		UNINDEX_NAME='$(PROJECT_NAME)' FORCE='$(FORCE)' scripts/unindex.sh

# The weights the summarizer runs. Mounted read-only at /models by compose, so
# MODEL_DIR is the host half of that mount and MODEL_NAME is the same file name
# on both sides - which is what stops the download and the container disagreeing
# about which model is in use.
#
# MODEL= picks one of the names below. The upstream file name is kept as it is,
# so several can sit in the directory at once and LLM_MODEL_PATH says which one
# a run uses.
MODEL ?= qwen-1.5b
# The table of models lives in worker/ctxworker/catalogue.py, which is also
# what the machine with the GPU reads: two copies would drift, and the Windows
# half cannot run this Makefile.
# Any interpreter answers: the catalogue is a table and imports nothing. The
# venv is preferred only so a machine without a system python3 still works.
CATALOGUE_PYTHON := $(if $(wildcard $(PYTHON)),$(PYTHON),python3)
CATALOGUE := PYTHONPATH='$(CURDIR)/worker' $(CATALOGUE_PYTHON) -m ctxworker.catalogue
MODELS := $(shell $(CATALOGUE) list 2>/dev/null)
MODEL_NAME := $(shell $(CATALOGUE) file '$(MODEL)' 2>/dev/null)
MODEL_DIR := $(HOME)/.local/share/context-mcp/models
MODEL_FILE := $(MODEL_DIR)/$(MODEL_NAME)
MODEL_PATH := /models/$(MODEL_NAME)

# Downloaded beside the target name and moved into place only once it is a
# GGUF file: without `-f` curl saves the error page under the model's name and
# exits 0, and llama.cpp is then the one to report it, a run later.
llm-model-install: require-model  ## Download the summarizer weights (MODEL=, FORCE=1)
	@PYTHONPATH='$(CURDIR)/worker' $(CATALOGUE_PYTHON) -m ctxworker.download \
		--model '$(MODEL)' --dir '$(MODEL_DIR)' $(if $(FORCE),--force)

require-model:
	@test -n '$(MODEL_NAME)' || { \
		echo "unknown MODEL=$(MODEL), pick one of: $(MODELS)" >&2; \
		exit 1; \
	}

# The other maintenance pair: `backup` writes, `restore` puts back. Both take
# the same selectors as `unindex` - nothing named means the whole database -
# and both leave the destination directory to the script, which defaults it
# next to the database rather than reading a setting for it.
#
# FILE= names one file instead of the generated name, and then rotation leaves
# it alone: KEEP=N only ever prunes files this naming scheme produced, and each
# kind on its own.
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
# here is what makes the next `up` a fresh database - the migrate service
# rebuilds the schema into it - and what stops a regenerated
# POSTGRES_PASSWORD from meeting a database that still
# holds the old one and refuses every connection.
#
# The files belong to the postgres uid, so the host user cannot remove them.
# Compose runs the deletion as root inside the postgres service itself, which
# also means the path stays resolved by compose rather than parsed again here.
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
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) \
		IMAGE='$(GRAPHIFY_IMAGE)' TAG='$(TAG)' clean
	@$(MAKE) --no-print-directory -C $(MCP_DIR) \
		IMAGE='$(MCP_IMAGE)' TAG='$(TAG)' clean
	@$(MAKE) --no-print-directory -C $(WEB_DIR) \
		IMAGE='$(WEB_IMAGE)' TAG='$(TAG)' clean

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
	@$(MAKE) --no-print-directory -C $(GRAPHIFY_DIR) \
		IMAGE='$(GRAPHIFY_IMAGE)' TAG='$(TAG)' $(SUBARGS)

mcp:
	@$(MAKE) --no-print-directory -C $(MCP_DIR) \
		IMAGE='$(MCP_IMAGE)' TAG='$(TAG)' $(SUBARGS)

web:
	@$(MAKE) --no-print-directory -C $(WEB_DIR) \
		IMAGE='$(WEB_IMAGE)' TAG='$(TAG)' $(SUBARGS)

db:
	@$(MAKE) --no-print-directory -C $(MIGRATIONS_DIR) \
		COMPOSE='$(COMPOSE) -f $(CURDIR)/docker-compose.yaml' $(SUBARGS)

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
