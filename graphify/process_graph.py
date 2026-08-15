"""Build a coarse code graph from a mounted codebase and store it in PostgreSQL."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

import pathspec
import psycopg2
import tree_sitter_bash
import tree_sitter_dockerfile
import tree_sitter_go
import tree_sitter_hcl
import tree_sitter_javascript
import tree_sitter_make
import tree_sitter_markdown
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_toml
import tree_sitter_typescript
import tree_sitter_yaml
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor
from tree_sitter import Language, Parser

LOG = logging.getLogger("graphify")

PROJECT_PATH = os.getenv("TARGET_PROJECT_PATH", "/project")
IGNORE_FILE = ".ctxignore"
KEEP_FILE = ".ctxkeep"

DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "pgdata",
        "target",
        "venv",
    }
)

DEFAULT_SOURCE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".rs",
    ".sql",
)
IMPORT_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs")
MAX_NODE_ID_LENGTH = 200


class CodeParser(ABC):
    """Abstract base class for language-specific AST parsers."""

    def __init__(self, language: Any) -> None:  # noqa: ANN401
        """Initialize the parser with a specific language."""
        self.parser = Parser()
        self.parser.set_language(Language(language.language()))

    @abstractmethod
    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Return list of entities found in content."""
        pass

    @abstractmethod
    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Return list of relations found in content."""
        return []


class PythonParser(CodeParser):
    """Python specific AST parser."""

    def __init__(self) -> None:
        """Initialize Python parser."""
        super().__init__(tree_sitter_python)
        self.entity_query = self.parser.language.query("""
            (function_definition name: (identifier) @name) @function
            (class_definition name: (identifier) @name) @class
        """)
        self.call_query = self.parser.language.query(
            "(call function: (identifier) @name) @call"
        )
        self.inherit_query = self.parser.language.query(
            "(class_definition superclasses: (argument_list) @super) @inherit"
        )

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Extract function calls and inheritance."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        relations = []
        for node, _ in self.call_query.captures(tree.root_node):
            relations.append(
                {
                    "source": rel_path,
                    "target": node.text.decode("utf-8"),
                    "type": "calls",
                }
            )
        for node, _ in self.inherit_query.captures(tree.root_node):
            relations.append(
                {
                    "source": rel_path,
                    "target": node.text.decode("utf-8"),
                    "type": "inherits",
                }
            )
        return relations


class TypeScriptParser(CodeParser):
    """TypeScript specific AST parser."""

    def __init__(self) -> None:
        """Initialize TypeScript parser."""
        super().__init__(tree_sitter_typescript)
        self.entity_query = self.parser.language.query("""
            (function_declaration name: (identifier) @name) @function
            (class_declaration name: (identifier) @name) @class
            (interface_declaration name: (identifier) @name) @interface
        """)
        self.call_query = self.parser.language.query(
            "(call_expression expression: (identifier) @name) @call"
        )
        self.import_query = self.parser.language.query(
            "(import_statement source: (string) @source)"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract function, class, and interface entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        captures = self.entity_query.captures(tree.root_node)
        return [
            {"name": node.text.decode("utf-8"), "type": tag}
            for node, tag in captures
            if tag == "name"
        ]

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Extract relations (calls, inherits, imports)."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        relations = []
        for node, _ in self.call_query.captures(tree.root_node):
            relations.append(
                {
                    "source": rel_path,
                    "target": node.text.decode("utf-8"),
                    "type": "calls",
                }
            )
        inherit_query = self.parser.language.query("""
            (class_declaration heritage_clause:
                (heritage_clause (extends_clause value: (_) @super))) @inherit
            (interface_declaration heritage_clause:
                (heritage_clause (extends_clause value: (_) @super))) @inherit
        """)
        for node, _ in inherit_query.captures(tree.root_node):
            relations.append(
                {
                    "source": rel_path,
                    "target": node.text.decode("utf-8"),
                    "type": "inherits",
                }
            )
        for node, _ in self.import_query.captures(tree.root_node):
            target = node.text.decode("utf-8").strip("'\"")
            relations.append({"source": rel_path, "target": target, "type": "imports"})
        return relations


class GoParser(CodeParser):
    """Go specific AST parser."""

    def __init__(self) -> None:
        """Initialize Go parser."""
        super().__init__(tree_sitter_go)
        self.query = self.parser.language.query(
            "(function_declaration name: (identifier) @name) @function"
        )

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Extract function calls."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"source": rel_path, "target": node.text.decode("utf-8"), "type": "calls"}
            for node, _ in self.call_query.captures(tree.root_node)
        ]


class RustParser(CodeParser):
    """Rust specific AST parser."""

    def __init__(self) -> None:
        """Initialize Rust parser."""
        super().__init__(tree_sitter_rust)
        self.query = self.parser.language.query(
            "(function_item name: (identifier) @name) @function"
        )

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Extract function calls."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"source": rel_path, "target": node.text.decode("utf-8"), "type": "calls"}
            for node, _ in self.call_query.captures(tree.root_node)
        ]


class BashParser(CodeParser):
    """Bash specific AST parser."""

    def __init__(self) -> None:
        """Initialize Bash parser."""
        super().__init__(tree_sitter_bash)
        self.query = self.parser.language.query(
            "(function_definition name: (word) @name) @function"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract function entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "function"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class DockerfileParser(CodeParser):
    """Dockerfile specific AST parser."""

    def __init__(self) -> None:
        """Initialize Dockerfile parser."""
        super().__init__(tree_sitter_dockerfile)
        self.query = self.parser.language.query(
            "(instruction name: (instruction_name) @name)"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract instruction entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "instruction"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class HCLParser(CodeParser):
    """HCL specific AST parser."""

    def __init__(self) -> None:
        """Initialize HCL parser."""
        super().__init__(tree_sitter_hcl)
        self.query = self.parser.language.query(
            "(resource_spec type: (identifier) @name)"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract resource entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "resource"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class JavaScriptParser(CodeParser):
    """JavaScript specific AST parser."""

    def __init__(self) -> None:
        """Initialize JavaScript parser."""
        super().__init__(tree_sitter_javascript)
        self.query = self.parser.language.query("""
            (function_declaration name: (identifier) @name) @function
            (class_declaration name: (identifier) @name) @class
        """)

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Extract function calls."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"source": rel_path, "target": node.text.decode("utf-8"), "type": "calls"}
            for node, _ in self.call_query.captures(tree.root_node)
        ]


class MakeParser(CodeParser):
    """Makefile specific AST parser."""

    def __init__(self) -> None:
        """Initialize Makefile parser."""
        super().__init__(tree_sitter_make)
        self.query = self.parser.language.query("(target name: (target_name) @name)")

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract target entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "target"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class MarkdownParser(CodeParser):
    """Markdown specific AST parser."""

    def __init__(self) -> None:
        """Initialize Markdown parser."""
        super().__init__(tree_sitter_markdown)
        self.query = self.parser.language.query(
            "(atx_heading (atx_h1_marker) (inline) @name)"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract heading entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "heading"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class TOMLParser(CodeParser):
    """TOML specific AST parser."""

    def __init__(self) -> None:
        """Initialize TOML parser."""
        super().__init__(tree_sitter_toml)
        self.query = self.parser.language.query("(table (bare_key) @name)")

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract table entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "table"}
            for node, _ in self.query.captures(tree.root_node)
        ]


class YAMLParser(CodeParser):
    """YAML specific AST parser."""

    def __init__(self) -> None:
        """Initialize YAML parser."""
        super().__init__(tree_sitter_yaml)
        self.query = self.parser.language.query(
            "(block_mapping_pair key: (flow_node) @name)"
        )

    def get_entities(self, content: str) -> list[dict[str, Any]]:
        """Extract key entities."""
        tree = self.parser.parse(bytes(content, "utf-8"))
        return [
            {"name": node.text.decode("utf-8"), "type": "key"}
            for node, _ in self.query.captures(tree.root_node)
        ]

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, Any]]:
        """Return empty list of relations for YAML."""
        return []


def get_parser(file_path: str) -> CodeParser | None:
    """Return a parser for the file extension."""
    if file_path.endswith(".py"):
        return PythonParser()
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return TypeScriptParser()
    if file_path.endswith(".go"):
        return GoParser()
    if file_path.endswith(".rs"):
        return RustParser()
    if file_path.endswith(".sh"):
        return BashParser()
    if file_path.endswith("Dockerfile"):
        return DockerfileParser()
    if file_path.endswith(".hcl"):
        return HCLParser()
    if file_path.endswith(".js"):
        return JavaScriptParser()
    if file_path.endswith("Makefile"):
        return MakeParser()
    if file_path.endswith(".md"):
        return MarkdownParser()
    if file_path.endswith(".toml"):
        return TOMLParser()
    if file_path.endswith((".yaml", ".yml")):
        return YAMLParser()
    return None


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def load_spec(root_path: str, file_name: str) -> pathspec.PathSpec | None:
    """Return the PathSpec held in file_name."""
    path = os.path.join(root_path, file_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [
                line
                for line in (raw.strip() for raw in handle)
                if line and not line.startswith("#")
            ]
    except OSError:
        LOG.exception("Failed to read %s", file_name)
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", lines) if lines else None


def iter_source_files(root_path: str) -> Iterator[tuple[str, str]]:
    """Yield source files to index."""
    ignore_spec = load_spec(root_path, IGNORE_FILE)
    keep_spec = load_spec(root_path, KEEP_FILE)
    for current_dir, dir_names, file_names in os.walk(root_path):
        rel_dir = os.path.relpath(current_dir, root_path)
        rel_dir = "" if rel_dir == "." else rel_dir
        if ignore_spec is None:
            dir_names[:] = [
                name for name in dir_names if name not in DEFAULT_IGNORED_DIRS
            ]
        else:
            dir_names[:] = [
                name
                for name in dir_names
                if not ignore_spec.match_file(f"{os.path.join(rel_dir, name)}/")
            ]
        for file_name in sorted(file_names):
            rel_path = os.path.join(rel_dir, file_name)
            if keep_spec is None:
                if not file_name.endswith(DEFAULT_SOURCE_EXTENSIONS):
                    continue
            elif not keep_spec.match_file(rel_path):
                continue
            if ignore_spec is not None and ignore_spec.match_file(rel_path):
                continue
            yield os.path.join(current_dir, file_name), rel_path


def extract_summary(file_path: str, content: str) -> str:
    """Extract a simple summary."""
    return "Summary: " + file_path


def index_file(cursor: Cursor, full_path: str, rel_path: str) -> int:
    """Insert the file node, its entities, and relations."""
    content = ""
    try:
        with open(full_path, encoding="utf-8", errors="ignore") as handle:
            content = handle.read()
    except OSError:
        LOG.exception("Failed to read %s", rel_path)
        return 0

    summary = extract_summary(rel_path, content)
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path, summary)
        VALUES (%s, %s, 'file', %s, %s)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name,
        summary = EXCLUDED.summary;
    """,
        (rel_path, os.path.basename(rel_path), rel_path, summary),
    )

    parser = get_parser(rel_path)
    if not parser:
        return 0

    # Persist entities
    for entity in parser.get_entities(content):
        cursor.execute(
            """
            INSERT INTO graph_nodes (id, name, type)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING;
        """,
            (entity["name"], entity["name"], entity["type"]),
        )

    # Persist relations
    edges = 0
    for relation in parser.get_relations(content, rel_path):
        target = relation["target"]
        if relation["type"] == "imports":
            # Attempt to resolve the import target to an existing file node
            query = "SELECT id FROM graph_nodes WHERE id LIKE %s LIMIT 1"
            cursor.execute(query, (f"%{target}%",))
            result = cursor.fetchone()
            if result:
                target = result[0]
            else:
                # If not found, create a placeholder import node
                cursor.execute(
                    """
                    INSERT INTO graph_nodes (id, name, type)
                    VALUES (%s, %s, 'external_import')
                    ON CONFLICT (id) DO NOTHING;
                """,
                    (target, target),
                )

        cursor.execute(
            """
            INSERT INTO graph_edges (source_id, target_id, relation_type)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
        """,
            (relation["source"], target, relation["type"]),
        )
        edges += 1

    return edges


def scan_and_build_graph() -> None:
    """Walk project and build graph."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError("not a directory")
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for full_path, rel_path in iter_source_files(PROJECT_PATH):
                try:
                    index_file(cursor, full_path, rel_path)
                except (OSError, psycopg2.Error):
                    conn.rollback()
                    LOG.exception("Failed")
                    continue
                conn.commit()
    finally:
        conn.close()


def main() -> None:
    """Configure logging and run."""
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    scan_and_build_graph()


if __name__ == "__main__":
    main()
