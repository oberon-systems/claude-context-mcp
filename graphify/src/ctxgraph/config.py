"""Settings the indexer reads once at start up.

Everything here is a knob or a limit. Nothing in this module imports the rest
of the package, so it stays importable from anywhere.
"""

import os

PROJECT_PATH = os.getenv("TARGET_PROJECT_PATH", "/project")
# The host path of the tree being indexed. Inside the container the mount is
# always at PROJECT_PATH, which says nothing about where it came from, so the
# real location is passed separately and recorded in the projects table. That
# is what lets a project name be traced back to a checkout, and what stops two
# checkouts sharing a basename from merging into one graph.
PROJECT_ROOT = os.getenv("PROJECT_ROOT", "")
# What the project is addressed by everywhere else: the `project` argument of
# the MCP tools, and the /mcp/<name> endpoint a client connects to. Derived
# from the last segment of PROJECT_ROOT when not set.
PROJECT_NAME = os.getenv("PROJECT_NAME", "")
# What kind of thing the project is, stored on the projects row and used by
# the MCP tools to narrow a cross-project search. Empty means "leave it as it
# is", so a re-index without TYPE= does not demote a project to the default.
PROJECT_TYPE = os.getenv("PROJECT_TYPE", "").strip() or None
# Documentation, not validation: the column is deliberately unconstrained, so
# an unknown value is warned about and then stored.
KNOWN_PROJECT_TYPES = frozenset({"codebase", "docs", "config", "memory"})
DEFAULT_PROJECT_TYPE = "codebase"
# Reserved for built-in projects the indexer never writes: `_memory` today,
# `_common` when shared records land.
BUILTIN_NAME_PREFIX = "_"
IGNORE_FILE = ".ctxignore"
KEEP_FILE = ".ctxkeep"
# Re-extract every file instead of trusting either cache: the extractor's own
# per-file cache and our file_hashes table. For when a cache is suspected
# rather than known to be wrong. `make index FRESH=1` sets it.
FORCE_REEXTRACT = os.getenv("FORCE_REEXTRACT", "").strip().lower() not in {
    "",
    "0",
    "false",
    "no",
}

DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".cache",
        ".git",
        ".gradle",
        ".idea",
        ".mypy_cache",
        ".next",
        ".pre-commit",
        ".pytest_cache",
        ".ruff_cache",
        ".terraform",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "pgdata",
        "target",
        "vendor",
        "venv",
    }
)

# Generated files that parse as a supported format but say nothing their
# source file does not. Skipped when the project has no .ctxkeep; an
# explicit .ctxkeep still wins, since that is the project asking for them
# by name.
IGNORED_FILE_NAMES = frozenset(
    {"composer.lock", "npm-shrinkwrap.json", "package-lock.json", "yarn.lock"}
)

# graph_nodes.id and graph_nodes.name are VARCHAR(255). Longer values are
# truncated here so one deep path cannot abort the transaction.
MAX_NODE_ID_LENGTH = 255
MAX_NAME_LENGTH = 255
# graph_nodes.type is VARCHAR(50), and every parser is free to name an entity
# kind of its own.
MAX_TYPE_LENGTH = 50
# projects.name is VARCHAR(64). It also travels in a URL path, so the
# characters it may contain are narrower than the column would allow.
MAX_PROJECT_NAME_LENGTH = 64
# Generated files (minified bundles, vendored blobs) cost minutes of parsing
# and contribute nothing but noise.
MAX_FILE_BYTES = 1_000_000
# Separates the owning file from the entity name in a node id, so two files
# may each define `main` without collapsing into one node.
ENTITY_SEPARATOR = "::"
# Extensions worth a file node even though no parser looks inside them.
EXTRA_SOURCE_EXTENSIONS = (".sql",)
# Extensions handed to the graphifyy extractor instead of a parser of our own.
# It covers more languages than we do and tags every edge with a confidence,
# so code goes to it and the infrastructure formats it cannot read stay here.
GRAPHIFYY_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".kt",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".ts",
        ".tsx",
    }
)
# Where the graph is materialized for the tools that read a file rather than
# the database. graphifyy's own security check refuses anything outside a
# `graphify-out` directory next to the working directory, so the name is not
# ours to choose.
GRAPHIFY_OUT_DIR = os.getenv("GRAPHIFY_OUT_DIR", "graphify-out")
# Marks which producer wrote a node or an edge, so re-running one of them
# never clears what the other found.
SOURCE_NATIVE = "native"
SOURCE_GRAPHIFYY = "graphifyy"
# Suffixes tried when resolving a path-like import to an indexed file.
MODULE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".d.ts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".go",
    ".rs",
)
# Suffixes a TypeScript import may carry while naming a source file that has
# a different one ("./util.js" resolving to "util.ts").
REWRITABLE_IMPORT_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
# Comment markers stripped when a summary is taken from the head of a file.
COMMENT_MARKERS = ('"""', "'''", "###", "#", "//", "/*", "*/", "*", "--", "<!--")
SUMMARY_SCAN_LINES = 40
MAX_SUMMARY_LENGTH = 300
# How many declared names a fallback summary lists before it says "+N more".
SUMMARY_ENTITY_LIMIT = 8

# Whether an index run also writes model summaries. Off by default: a first
# index of a large tree would spend hours in the model, and the summaries the
# parsers write are there immediately. `make index SUMMARIZE=1` turns it on
# for one run, and `make summarize` does the same work afterwards instead.
SUMMARIZE = os.getenv("SUMMARIZE", "").strip().lower() not in {
    "",
    "0",
    "false",
    "no",
}
# Which GGUF weights to load. `make llm-model-install` puts them under
# ~/.local/share/context-mcp/models on the host, mounted read-only at
# LLM_MODEL_DIR, and every `make` target that runs the model names the file it
# downloaded. Left empty, the directory is searched instead, which is what
# keeps a hand-rolled `docker run` working.
LLM_MODEL_DIR = os.getenv("LLM_MODEL_DIR", "/models")
LLM_MODEL_PATH = os.getenv("LLM_MODEL_PATH", "").strip()
# Kept at or below the cpu quota the container runs under: llama.cpp threads
# oversubscribed against a quota buy context switches, not throughput.
LLM_THREADS = int(os.getenv("LLM_THREADS", "2"))
LLM_CTX = int(os.getenv("LLM_CTX", "2048"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "64"))
# The prompt is clamped to this many characters, so a file can never overflow
# the context window. It is also the setting that decides how long the pass
# takes: reading the prompt is most of the work on a CPU, and the answer is
# 64 tokens whatever the input was. About 25 lines of code, against the 40
# `extract_summary` reads for the same purpose.
LLM_INPUT_CHARS = int(os.getenv("LLM_INPUT_CHARS", "2000"))
# Stop the summarizing pass after this many files. Zero is all of them. It is
# how a first pass over a large tree is timed before one is spent on it.
SUMMARY_LIMIT = int(os.getenv("SUMMARY_LIMIT", "") or 0)

# How much of each file's text is kept in graph_nodes.content, which is where
# a summarizing worker on another machine reads it from: it has no checkout of
# the tree and no mount. Zero stores nothing and turns remote summarizing off.
# Deliberately larger than the head the fast summary is written from - a GPU
# can afford a context a CPU could not - and the dial to turn down on a very
# large tree, since an index run holds one of these per code file at once.
CONTENT_STORE_CHARS = int(os.getenv("CONTENT_STORE_CHARS", "16000") or 0)
# Files that get a node and a head-of-file summary like any other, but whose
# text is never stored. Storing it would put a secret in a table the worker
# API serves over the network, and a tree without a .ctxignore is the case
# this exists for.
CONTENT_DENIED_NAMES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jwt",
    "*.tfvars",
    ".htpasswd",
    "authorized_keys",
    "credentials",
    "id_rsa*",
    "id_ed25519*",
)

# The worker API. It is the one service here that is meant to be reached from
# another machine, so the token is not optional: it refuses to start without
# one, and a short one is a configuration mistake rather than a choice.
WORKER_API_PORT = int(os.getenv("WORKER_API_PORT", "3003"))
WORKER_API_TOKEN = os.getenv("WORKER_API_TOKEN", "")
WORKER_API_TOKEN_MIN = 16
# FastAPI cannot put its own docs page behind the token, and an open route on
# a published port is the thing being avoided.
WORKER_API_DOCS = bool(os.getenv("WORKER_API_DOCS", ""))
# How long a claimed batch is held before it returns to the queue, and how
# many files one claim may take.
WORKER_LEASE_SECONDS = int(os.getenv("WORKER_LEASE_SECONDS", "300"))
WORKER_MAX_BATCH = int(os.getenv("WORKER_MAX_BATCH", "8"))
# A file that reliably kills the worker is dropped rather than retried
# forever.
WORKER_MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
# A reply longer than this is refused before it is shaped: the worker is a
# network peer, and the summary it is answering with is one sentence.
WORKER_MAX_REPLY_CHARS = int(os.getenv("WORKER_MAX_REPLY_CHARS", "4000"))
