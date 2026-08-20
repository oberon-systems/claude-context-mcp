# Driven from the repository root as `make worker <target>`, and directly on
# the machine with the GPU, which has no compose and no Docker.
SHELL := /bin/bash
PYTHON ?= python3
MODEL ?= qwen-1.5b
API ?= http://127.0.0.1:3003

.DEFAULT_GOAL := help
.PHONY: help deps model server run lint clean

help:  ## Show this help
	@echo "Remote summarization worker"
	@echo
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

deps:  ## Install the model runtime (add the CUDA index on a GPU host)
	$(PYTHON) -m pip install -r requirements.txt

model:  ## Download the weights (MODEL=qwen-3b, FORCE=1)
	$(PYTHON) -m ctxworker.download --model '$(MODEL)' $(if $(FORCE),--force)

server:  ## Download llama-server into llama-server/ (VARIANT=cpu)
	$(PYTHON) -m ctxworker.getserver $(if $(VARIANT),--variant '$(VARIANT)')

run:  ## Summarize a project: make run PROJECT=kurum API=http://host:3003
	@test -n '$(PROJECT)' || { echo "PROJECT= is required" >&2; exit 1; }
	$(PYTHON) -m ctxworker --api '$(API)' --project '$(PROJECT)' \
		--model '$(MODEL)' $(if $(BATCH),--batch '$(BATCH)') \
		$(if $(LLAMA_SERVER),--llama-server '$(LLAMA_SERVER)')

lint:  ## Run the repository hooks over this directory
	cd .. && .venv/bin/pre-commit run --files worker/ctxworker/*.py

clean:  ## Remove the caches
	rm -rf ctxworker/__pycache__ .pytest_cache
