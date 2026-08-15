"""Settings the indexer reads once at start up.

Everything here is a knob or a limit. Nothing in this module imports the rest
of the package, so it stays importable from anywhere.
"""

import os

PROJECT_PATH = os.getenv("TARGET_PROJECT_PATH", "/project")
IGNORE_FILE = ".ctxignore"
KEEP_FILE = ".ctxkeep"

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

# graph_nodes.id and graph_nodes.name are VARCHAR(255). Longer values are
# truncated here so one deep path cannot abort the transaction.
MAX_NODE_ID_LENGTH = 255
MAX_NAME_LENGTH = 255
# graph_nodes.type is VARCHAR(50), and every parser is free to name an entity
# kind of its own.
MAX_TYPE_LENGTH = 50
# Generated files (minified bundles, vendored blobs) cost minutes of parsing
# and contribute nothing but noise.
MAX_FILE_BYTES = 1_000_000
# Separates the owning file from the entity name in a node id, so two files
# may each define `main` without collapsing into one node.
ENTITY_SEPARATOR = "::"
# Extensions worth a file node even though no parser looks inside them.
EXTRA_SOURCE_EXTENSIONS = (".sql",)
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
