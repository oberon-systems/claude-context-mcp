# Schema migrations, applied with goose.
#
# Normally reached from the repo root as `make db <target>`, which passes
# COMPOSE down through $(MAKE) -C. Standalone use works too and falls back to
# the default below.

REPO_ROOT := $(abspath $(CURDIR)/..)

# The goose service is declared in the root compose file, and `make -C` leaves
# the working directory here, where there is none. So name the file.
COMPOSE ?= docker compose -f $(REPO_ROOT)/docker-compose.yaml
SERVICE := migrate

NAME ?=
FORCE ?=

# `read -p` and the 10# arithmetic prefix in `new` are both bashisms.
SHELL := /bin/bash

.DEFAULT_GOAL := help

.PHONY: help migrate version validate rollback new

help:
	@echo "Migrations: $(CURDIR)"
	@echo
	@echo "Targets:"
	@echo "  migrate   Apply every pending migration"
	@echo "  version   Show which migrations are applied and which are pending"
	@echo "  validate  Check the migration files without touching the database"
	@echo "  rollback  Undo the last applied migration (FORCE=1 skips the prompt)"
	@echo "  new       Write the next migration file, NAME=<slug>"

# The service runs `up` by default, so this needs no override.
migrate:
	$(COMPOSE) run --rm $(SERVICE)

version:
	$(COMPOSE) run --rm -e GOOSE_COMMAND=status $(SERVICE)

validate:
	$(COMPOSE) run --rm -e GOOSE_COMMAND=validate $(SERVICE)
	@echo "migrations are valid"

# A down migration is a development tool, not an operating procedure: `make
# clean` at the repo root is the documented way to start the database over.
rollback:
	@test -n "$(FORCE)" || { \
		echo "This undoes the last applied migration."; \
		read -r -p "Continue? [y/N] " reply; \
		case "$$reply" in [yY]*) ;; *) echo "aborted"; exit 1 ;; esac; \
	}
	$(COMPOSE) run --rm -e GOOSE_COMMAND=down $(SERVICE)

# Written here rather than by `goose create`: the directory is mounted
# read-only by contract, and a container-side write would land root-owned.
new:
	@test -n "$(NAME)" || { echo "NAME= is required" >&2; exit 1; }
	@last=$$(ls $(CURDIR)/[0-9]*.sql 2> /dev/null | sed 's|.*/||' \
		| cut -d_ -f1 | sort -n | tail -1); \
	next=$$(printf '%04d' $$((10#$${last:-0} + 1))); \
	file="$(CURDIR)/$${next}_$(NAME).sql"; \
	test ! -e "$$file" || { echo "$$file exists" >&2; exit 1; }; \
	printf -- '-- +goose Up\n\n-- +goose Down\n' > "$$file"; \
	echo "$$file"
