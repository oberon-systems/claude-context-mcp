# Image build and local checks for the dashboard.
#
# Normally reached from the repo root, which passes TAG down through
# $(MAKE) -C. Standalone use works too and falls back to the defaults below.

# The reference compose runs, so `make build` replaces the image the stack
# starts rather than producing one nothing addresses.
IMAGE ?= ghcr.io/oberon-systems/claude-context-mcp/web
TAG ?= latest
REF := $(IMAGE):$(TAG)

DOCKER ?= docker
NPM ?= npm

PORT ?= 3002

.DEFAULT_GOAL := help

.PHONY: help build rebuild deps run dev shell lint typecheck format clean

help:
	@echo "Image: $(REF)"
	@echo
	@echo "Targets:"
	@echo "  build      Build the image"
	@echo "  rebuild    Build the image without the layer cache"
	@echo "  deps       Install the node dependencies from the lockfile"
	@echo "  run        Run the image on port $(PORT) (needs DATABASE_URL)"
	@echo "  dev        Serve the client from vite against a running server"
	@echo "  shell      Open a shell inside the image"
	@echo "  lint       Run eslint over the sources"
	@echo "  typecheck  Run tsc over both projects"
	@echo "  format     Rewrite the sources with prettier"
	@echo "  clean      Remove node_modules, dist and the built image"

build:
	$(DOCKER) build -t $(REF) .

rebuild:
	$(DOCKER) build --no-cache --pull -t $(REF) .

# Named `deps` rather than `install`: the root Makefile has an `install`
# of its own, and a shared name makes `make web install` run both.
deps: node_modules

node_modules: package-lock.json package.json
	$(NPM) ci
	@touch node_modules

# Joins the compose network so `postgres` and `viewer` resolve. Prefer
# `make up` from the repo root, which does this through compose.
run:
	$(DOCKER) run --rm -it \
		--network claude-context-mcp_base \
		-p $(PORT):3002 \
		-e DATABASE_URL \
		-e VIEWER_URL=http://viewer:3001 \
		$(REF)

# The client only. The API and the graph proxy come from the container the
# stack already runs, which vite forwards to.
dev: node_modules
	$(NPM) run dev

shell:
	$(DOCKER) run --rm -it --entrypoint /bin/sh $(REF)

lint: node_modules
	$(NPM) run lint

typecheck: node_modules
	$(NPM) run typecheck

format: node_modules
	$(NPM) run format

clean:
	rm -rf node_modules dist
	-$(DOCKER) image rm $(REF)
