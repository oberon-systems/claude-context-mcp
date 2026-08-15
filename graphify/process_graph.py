"""Build a coarse code graph from a mounted codebase and store it in PostgreSQL."""

from __future__ import annotations

import logging
import os
import posixpath
from collections.abc import Iterator
from functools import cache
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
from tree_sitter import Language, Node, Parser, Query, QueryCursor

LOG = logging.getLogger("graphify")

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


def node_text(node: Node) -> str:
    """Return the decoded source text of a node."""
    return (node.text or b"").decode("utf-8", "replace")


def strip_literal(value: str) -> str:
    """Strip the quoting a grammar keeps around string literals."""
    return value.strip().strip("'\"`")


class CodeParser:
    """Base class for language specific AST parsers.

    Subclasses supply two tree-sitter queries and the capture names carry the
    meaning. In ENTITY_QUERY a capture name is the node type to store
    ("function", "class", "method"); in RELATION_QUERY it is the edge type
    ("calls", "inherits", "imports"). Both queries are optional, so a grammar
    that only yields entities needs nothing else.

    FAMILY groups the languages whose files may refer to each other's symbols,
    which keeps a call in a `.ts` file from resolving to a Rust function that
    happens to share a name.
    """

    ENTITY_QUERY: str = ""
    RELATION_QUERY: str = ""
    FAMILY: str = ""

    def __init__(self, language: Any) -> None:  # noqa: ANN401
        """Compile the queries against the given tree-sitter language."""
        self.language = Language(language)
        self.parser = Parser(self.language)
        self.entity_query = self._compile(self.ENTITY_QUERY)
        self.relation_query = self._compile(self.RELATION_QUERY)

    def _compile(self, source: str) -> Query | None:
        """Compile a query, or return None when the subclass declared none."""
        return Query(self.language, source) if source.strip() else None

    def parse(self, content: str) -> Node:
        """Return the root node of the parsed content."""
        return self.parser.parse(content.encode("utf-8")).root_node

    def capture_nodes(self, query: Query, root: Node) -> dict[str, list[Node]]:
        """Run a query and return its captures keyed by capture name."""
        return QueryCursor(query).captures(root)

    def capture_texts(self, query: Query, root: Node) -> Iterator[tuple[str, str]]:
        """Yield (capture name, captured text) pairs for a query."""
        for capture_name, nodes in self.capture_nodes(query, root).items():
            for node in nodes:
                yield capture_name, node_text(node)

    def get_entities(self, content: str) -> list[dict[str, str]]:
        """Return the deduplicated entities declared in content."""
        if self.entity_query is None:
            return []
        return _unique_pairs(
            (name.strip(), kind)
            for kind, name in self.capture_texts(self.entity_query, self.parse(content))
        )

    def get_relations(self, content: str) -> list[dict[str, str]]:
        """Return the deduplicated relations found in content."""
        if self.relation_query is None:
            return []
        pairs: list[tuple[str, str]] = []
        for kind, text in self.capture_texts(self.relation_query, self.parse(content)):
            target = strip_literal(text) if kind == "imports" else text.strip()
            pairs.append((target, kind))
        return [
            {"target": entry["name"], "type": entry["type"]}
            for entry in _unique_pairs(iter(pairs))
        ]


def _unique_pairs(pairs: Iterator[tuple[str, str]]) -> list[dict[str, str]]:
    """Turn (name, kind) pairs into name/type dicts, dropping blanks and dupes."""
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for name, kind in pairs:
        if not name or (name, kind) in seen:
            continue
        seen.add((name, kind))
        result.append({"name": name, "type": kind})
    return result


class PythonParser(CodeParser):
    """Python specific AST parser."""

    FAMILY = "python"
    ENTITY_QUERY = """
        (module (function_definition name: (identifier) @function))
        (module (decorated_definition
            definition: (function_definition name: (identifier) @function)))
        (module (class_definition name: (identifier) @class))
        (module (decorated_definition
            definition: (class_definition name: (identifier) @class)))
        (class_definition body:
            (block (function_definition name: (identifier) @method)))
        (class_definition body: (block (decorated_definition
            definition: (function_definition name: (identifier) @method))))
    """
    RELATION_QUERY = """
        (call function: (identifier) @calls)
        (call function: (attribute attribute: (identifier) @calls))
        (class_definition superclasses: (argument_list (identifier) @inherits))
        (class_definition superclasses:
            (argument_list (attribute attribute: (identifier) @inherits)))
        (import_statement name: (dotted_name) @imports)
        (import_statement name: (aliased_import name: (dotted_name) @imports))
        (import_from_statement module_name: (dotted_name) @imports)
        (import_from_statement module_name: (relative_import) @imports)
    """

    def __init__(self) -> None:
        """Initialize Python parser."""
        super().__init__(tree_sitter_python.language())


class TypeScriptParser(CodeParser):
    """TypeScript specific AST parser."""

    FAMILY = "ecmascript"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (type_identifier) @class)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type)
        (method_definition name: (property_identifier) @method)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function:
            (member_expression property: (property_identifier) @calls))
        (class_declaration (class_heritage (extends_clause value: (_) @inherits)))
        (class_declaration (class_heritage
            (implements_clause (type_identifier) @inherits)))
        (interface_declaration (extends_type_clause (type_identifier) @inherits))
        (import_statement source: (string) @imports)
        (call_expression
            function: (import)
            arguments: (arguments (string) @imports))
    """

    def __init__(self) -> None:
        """Initialize TypeScript parser."""
        super().__init__(tree_sitter_typescript.language_typescript())


class TSXParser(TypeScriptParser):
    """TSX specific AST parser.

    Same queries as TypeScript, but the TSX grammar is a separate language:
    parsing `.tsx` with the plain TypeScript grammar produces error nodes on
    every JSX element.
    """

    def __init__(self) -> None:
        """Initialize TSX parser."""
        CodeParser.__init__(self, tree_sitter_typescript.language_tsx())


class JavaScriptParser(CodeParser):
    """JavaScript specific AST parser."""

    FAMILY = "ecmascript"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (identifier) @class)
        (method_definition name: (property_identifier) @method)
        (lexical_declaration (variable_declarator
            name: (identifier) @function value: (arrow_function)))
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function:
            (member_expression property: (property_identifier) @calls))
        (class_declaration (class_heritage (identifier) @inherits))
        (import_statement source: (string) @imports)
    """

    def __init__(self) -> None:
        """Initialize JavaScript parser."""
        super().__init__(tree_sitter_javascript.language())


class GoParser(CodeParser):
    """Go specific AST parser."""

    FAMILY = "go"
    ENTITY_QUERY = """
        (function_declaration name: (identifier) @function)
        (method_declaration name: (field_identifier) @method)
        (type_spec name: (type_identifier) @type)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function: (selector_expression
            field: (field_identifier) @calls))
        (import_spec path: (interpreted_string_literal) @imports)
    """

    def __init__(self) -> None:
        """Initialize Go parser."""
        super().__init__(tree_sitter_go.language())


class RustParser(CodeParser):
    """Rust specific AST parser."""

    FAMILY = "rust"
    ENTITY_QUERY = """
        (function_item name: (identifier) @function)
        (struct_item name: (type_identifier) @struct)
        (enum_item name: (type_identifier) @enum)
        (trait_item name: (type_identifier) @trait)
    """
    RELATION_QUERY = """
        (call_expression function: (identifier) @calls)
        (call_expression function: (field_expression field: (field_identifier) @calls))
        (use_declaration argument: (_) @imports)
    """

    def __init__(self) -> None:
        """Initialize Rust parser."""
        super().__init__(tree_sitter_rust.language())


class BashParser(CodeParser):
    """Bash specific AST parser."""

    FAMILY = "bash"
    ENTITY_QUERY = "(function_definition name: (word) @function)"

    def __init__(self) -> None:
        """Initialize Bash parser."""
        super().__init__(tree_sitter_bash.language())


class DockerfileParser(CodeParser):
    """Dockerfile specific AST parser."""

    FAMILY = "dockerfile"
    ENTITY_QUERY = """
        (from_instruction (image_spec (image_name) @image))
        (from_instruction (image_alias) @stage)
    """

    def __init__(self) -> None:
        """Initialize Dockerfile parser."""
        super().__init__(tree_sitter_dockerfile.language())


class HCLParser(CodeParser):
    """HCL and Terraform specific AST parser."""

    FAMILY = "hcl"
    ENTITY_QUERY = "(block) @block"

    def __init__(self) -> None:
        """Initialize HCL parser."""
        super().__init__(tree_sitter_hcl.language())

    def get_entities(self, content: str) -> list[dict[str, str]]:
        """Name a block by its type and labels, e.g. resource.aws_s3_bucket.main."""
        if self.entity_query is None:
            return []
        captures = self.capture_nodes(self.entity_query, self.parse(content))
        pairs: list[tuple[str, str]] = []
        for block in captures.get("block", []):
            kind = ""
            labels: list[str] = []
            for child in block.named_children:
                if child.type == "identifier" and not kind:
                    kind = node_text(child)
                elif child.type == "string_lit":
                    labels.extend(
                        node_text(part)
                        for part in child.named_children
                        if part.type == "template_literal"
                    )
            if kind:
                pairs.append((".".join([kind, *labels]), "block"))
        return _unique_pairs(iter(pairs))


class MakeParser(CodeParser):
    """Makefile specific AST parser."""

    FAMILY = "make"
    ENTITY_QUERY = "(rule (targets (word) @target))"

    def __init__(self) -> None:
        """Initialize Makefile parser."""
        super().__init__(tree_sitter_make.language())


class MarkdownParser(CodeParser):
    """Markdown specific AST parser."""

    FAMILY = "markdown"
    ENTITY_QUERY = """
        (atx_heading (atx_h1_marker) (inline) @heading)
        (atx_heading (atx_h2_marker) (inline) @heading)
    """

    def __init__(self) -> None:
        """Initialize Markdown parser."""
        super().__init__(tree_sitter_markdown.language())


class TOMLParser(CodeParser):
    """TOML specific AST parser."""

    FAMILY = "toml"
    ENTITY_QUERY = """
        (table (bare_key) @table)
        (table (dotted_key) @table)
    """

    def __init__(self) -> None:
        """Initialize TOML parser."""
        super().__init__(tree_sitter_toml.language())


class YAMLParser(CodeParser):
    """YAML specific AST parser."""

    FAMILY = "yaml"
    # Top level keys only. Capturing every nested key turns a compose file into
    # hundreds of nodes named `image` or `ports`.
    ENTITY_QUERY = """
        (document (block_node (block_mapping
            (block_mapping_pair key: (flow_node) @key))))
    """

    def __init__(self) -> None:
        """Initialize YAML parser."""
        super().__init__(tree_sitter_yaml.language())


EXTENSION_PARSERS: dict[str, type[CodeParser]] = {
    ".bash": BashParser,
    ".cjs": JavaScriptParser,
    ".dockerfile": DockerfileParser,
    ".go": GoParser,
    ".hcl": HCLParser,
    ".js": JavaScriptParser,
    ".jsx": JavaScriptParser,
    ".markdown": MarkdownParser,
    ".md": MarkdownParser,
    ".mjs": JavaScriptParser,
    ".mk": MakeParser,
    ".py": PythonParser,
    ".rs": RustParser,
    ".sh": BashParser,
    ".tf": HCLParser,
    ".tfvars": HCLParser,
    ".toml": TOMLParser,
    ".ts": TypeScriptParser,
    ".tsx": TSXParser,
    ".yaml": YAMLParser,
    ".yml": YAMLParser,
}

FILENAME_PARSERS: dict[str, type[CodeParser]] = {
    "containerfile": DockerfileParser,
    "dockerfile": DockerfileParser,
    "gnumakefile": MakeParser,
    "makefile": MakeParser,
}

DEFAULT_SOURCE_EXTENSIONS = tuple(sorted(EXTENSION_PARSERS)) + EXTRA_SOURCE_EXTENSIONS


@cache
def _parser_instance(parser_class: type[CodeParser]) -> CodeParser:
    """Return the shared instance of a parser class.

    Compiling the queries costs more than parsing a small file, so the
    instances are reused across the whole run.
    """
    return parser_class()


def _parser_class(file_path: str) -> type[CodeParser] | None:
    """Return the parser class matching a path, by file name then extension."""
    file_name = posixpath.basename(file_path).lower()
    if file_name in FILENAME_PARSERS:
        return FILENAME_PARSERS[file_name]
    # Dockerfile.dev, Dockerfile.ci and friends.
    if file_name.startswith("dockerfile."):
        return DockerfileParser
    _, extension = posixpath.splitext(file_name)
    return EXTENSION_PARSERS.get(extension)


def get_parser(file_path: str) -> CodeParser | None:
    """Return a parser for the file, or None when the type has no grammar."""
    parser_class = _parser_class(file_path)
    return _parser_instance(parser_class) if parser_class else None


def is_default_source(file_name: str) -> bool:
    """Report whether a file is indexed when the project has no .ctxkeep."""
    return (
        file_name.endswith(DEFAULT_SOURCE_EXTENSIONS)
        or _parser_class(file_name) is not None
    )


def truncate(value: str, limit: int) -> str:
    """Clip a value to what the database column accepts."""
    return value if len(value) <= limit else value[:limit]


def entity_node_id(rel_path: str, name: str) -> str:
    """Build the file scoped id of an entity node."""
    return truncate(f"{rel_path}{ENTITY_SEPARATOR}{name}", MAX_NODE_ID_LENGTH)


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
    """Yield (absolute path, project relative path) for every file to index."""
    ignore_spec = load_spec(root_path, IGNORE_FILE)
    keep_spec = load_spec(root_path, KEEP_FILE)
    for current_dir, dir_names, file_names in os.walk(root_path):
        rel_dir = os.path.relpath(current_dir, root_path)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        # The default skip list always applies. Dropping it whenever the
        # project ships a .ctxignore made the indexer walk .git and
        # node_modules for exactly those projects that configured it.
        dir_names[:] = [
            name
            for name in sorted(dir_names)
            if name not in DEFAULT_IGNORED_DIRS
            and not (
                ignore_spec is not None
                and ignore_spec.match_file(f"{posixpath.join(rel_dir, name)}/")
            )
        ]
        for file_name in sorted(file_names):
            rel_path = posixpath.join(rel_dir, file_name)
            if keep_spec is None:
                if not is_default_source(file_name):
                    continue
            elif not keep_spec.match_file(rel_path):
                continue
            if ignore_spec is not None and ignore_spec.match_file(rel_path):
                continue
            yield os.path.join(current_dir, file_name), rel_path


def read_source(full_path: str, rel_path: str) -> str | None:
    """Return the text of a file, or None when it cannot or should not be read."""
    try:
        size = os.path.getsize(full_path)
    except OSError:
        LOG.exception("Failed to stat %s", rel_path)
        return None
    if size > MAX_FILE_BYTES:
        LOG.info("Skipping %s: %d bytes exceeds the limit", rel_path, size)
        return None
    try:
        with open(full_path, encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        LOG.exception("Failed to read %s", rel_path)
        return None


def markdown_title(content: str) -> str:
    """Return the title of a Markdown document, atx or setext style."""
    lines = content.splitlines()[:SUMMARY_SCAN_LINES]
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        # Skips blank lines, front matter fences and setext underlines.
        if not line or set(line) <= {"-", "="}:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if next_line and set(next_line) in ({"="}, {"-"}):
            return line
        # A document that opens with prose has no title to take.
        return ""
    return ""


def leading_comment(content: str) -> str:
    """Return the first line of the comment block at the top of a file.

    Only that block counts: a comment further down describes the code around
    it, not the file.
    """
    for raw_line in content.splitlines()[:SUMMARY_SCAN_LINES]:
        line = raw_line.strip()
        if not line or line.startswith("#!"):
            continue
        marker = next(
            (marker for marker in COMMENT_MARKERS if line.startswith(marker)), None
        )
        if marker is None:
            return ""
        text = line[len(marker) :].strip(" \t*/-<>!").strip("\"'").strip()
        if text:
            return text
    return ""


def extract_summary(rel_path: str, content: str, entities: list[dict[str, str]]) -> str:
    """Summarize a file by its title, its leading comment, or what it declares."""
    if _parser_class(rel_path) is MarkdownParser:
        title = markdown_title(content)
        if title:
            return truncate(title, MAX_SUMMARY_LENGTH)

    comment = leading_comment(content)
    if comment:
        return truncate(comment, MAX_SUMMARY_LENGTH)

    # No prose to quote: name what the file declares, which beats a line count
    # for deciding whether the file is worth opening.
    if entities:
        groups: dict[str, list[str]] = {}
        for entity in entities:
            groups.setdefault(entity["type"], []).append(entity["name"])
        parts = []
        for kind, names in groups.items():
            listed = ", ".join(names[:SUMMARY_ENTITY_LIMIT])
            if len(names) > SUMMARY_ENTITY_LIMIT:
                listed += f", +{len(names) - SUMMARY_ENTITY_LIMIT} more"
            parts.append(f"{kind}: {listed}")
        return truncate("; ".join(parts), MAX_SUMMARY_LENGTH)

    lines = len(content.splitlines())
    return f"{posixpath.basename(rel_path)} ({lines} line{'' if lines == 1 else 's'})"


def upsert_file_node(cursor: Cursor, rel_path: str, summary: str) -> None:
    """Insert or refresh the node standing for a file.

    A summary written through the MCP `save_node_summary` tool is marked
    manual in the metadata and survives re-indexing; the generated one does
    not, so it keeps up with the file.
    """
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path, summary, metadata)
        VALUES (%s, %s, 'file', %s, %s, '{"summary_source": "auto"}'::JSONB)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            type = 'file',
            file_path = EXCLUDED.file_path,
            summary = CASE
                WHEN COALESCE(
                    graph_nodes.metadata ->> 'summary_source', 'auto'
                ) = 'auto'
                THEN EXCLUDED.summary
                ELSE graph_nodes.summary
            END;
        """,
        (
            truncate(rel_path, MAX_NODE_ID_LENGTH),
            truncate(posixpath.basename(rel_path), MAX_NAME_LENGTH),
            rel_path,
            summary,
        ),
    )


def clear_file_artifacts(cursor: Cursor, rel_path: str) -> None:
    """Drop what a previous run derived from a file.

    Without this an entity or a call that was deleted from the source stays in
    the graph forever, because every write below is an upsert.
    """
    file_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    cursor.execute(
        "DELETE FROM graph_nodes WHERE file_path = %s AND type <> 'file';",
        (rel_path,),
    )
    cursor.execute("DELETE FROM graph_edges WHERE source_id = %s;", (file_id,))


def prune_orphans(cursor: Cursor) -> int:
    """Delete placeholder nodes nothing points at any more.

    An import or a call that was removed from the source leaves its external
    node behind, and releases before entity ids were file scoped wrote entity
    nodes without a file_path. Both are dead weight in every search result.
    """
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE (
            type IN ('external_import', 'external_symbol')
            AND NOT EXISTS (
                SELECT 1 FROM graph_edges WHERE target_id = graph_nodes.id
            )
        ) OR (
            file_path IS NULL
            AND type NOT IN ('file', 'external_import', 'external_symbol')
        );
        """
    )
    return cursor.rowcount


def index_file(cursor: Cursor, rel_path: str, content: str) -> list[dict[str, str]]:
    """Store the file node and its entities. Returns the entities written."""
    parser = get_parser(rel_path)
    entities = parser.get_entities(content) if parser else []

    upsert_file_node(cursor, rel_path, extract_summary(rel_path, content, entities))
    clear_file_artifacts(cursor, rel_path)

    for entity in entities:
        cursor.execute(
            """
            INSERT INTO graph_nodes (id, name, type, file_path)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                file_path = EXCLUDED.file_path;
            """,
            (
                entity_node_id(rel_path, entity["name"]),
                truncate(entity["name"], MAX_NAME_LENGTH),
                truncate(entity["type"], 50),
                rel_path,
            ),
        )
    return entities


def python_import_candidates(target: str, base_dir: str) -> list[str]:
    """Return the file paths a Python import may refer to."""
    stripped = target.lstrip(".")
    dots = len(target) - len(stripped)
    if dots:
        parts = [part for part in base_dir.split("/") if part]
        # One dot is the current package, each further dot climbs one level.
        climb = dots - 1
        if climb > len(parts):
            return []
        parts = parts[: len(parts) - climb] if climb else parts
        tail = stripped.split(".") if stripped else []
        prefix = "/".join([*parts, *tail])
    else:
        prefix = target.replace(".", "/")
    if not prefix:
        return []
    return [f"{prefix}.py", f"{prefix}/__init__.py"]


def path_import_candidates(target: str, base_dir: str) -> list[str]:
    """Return the file paths a relative path-like import may refer to."""
    if not target.startswith("."):
        # Bare specifiers name a package, not a file in this project.
        return []
    joined = posixpath.normpath(posixpath.join(base_dir, target))
    if joined.startswith(".."):
        return []
    joined = joined.lstrip("./")
    if not joined:
        return []
    bases = [joined]
    for extension in REWRITABLE_IMPORT_EXTENSIONS:
        if joined.endswith(extension):
            bases.append(joined[: -len(extension)])
    candidates = list(bases)
    for base in bases:
        candidates.extend(f"{base}{extension}" for extension in MODULE_EXTENSIONS)
        candidates.extend(f"{base}/index{extension}" for extension in MODULE_EXTENSIONS)
    return candidates


def resolve_import(target: str, rel_path: str, known_files: set[str]) -> str | None:
    """Resolve an import target to the id of an indexed file node."""
    target = strip_literal(target)
    if not target:
        return None
    base_dir = posixpath.dirname(rel_path)
    if rel_path.endswith(".py"):
        candidates = python_import_candidates(target, base_dir)
    else:
        candidates = path_import_candidates(target, base_dir)
    for candidate in candidates:
        if candidate in known_files:
            return truncate(candidate, MAX_NODE_ID_LENGTH)
    return None


def language_family(rel_path: str) -> str:
    """Return the symbol namespace a file belongs to."""
    parser_class = _parser_class(rel_path)
    return parser_class.FAMILY if parser_class else ""


def owner_path(node_id: str) -> str:
    """Return the file part of an entity node id."""
    return node_id.split(ENTITY_SEPARATOR, 1)[0]


def resolve_symbol(
    name: str,
    rel_path: str,
    symbols: dict[str, list[str]],
    imported: set[str],
) -> str | None:
    """Resolve a called or inherited name to an entity node id.

    Preference order: the same file, then a file this one imports, then any
    other file of the same language. Names are matched within a language
    family only, otherwise a `helper()` call in TypeScript happily binds to a
    Rust `fn helper` that shares nothing but the spelling.
    """
    node_ids = symbols.get(name)
    if not node_ids:
        return None
    local = entity_node_id(rel_path, name)
    if local in node_ids:
        return local
    family = language_family(rel_path)
    candidates = [
        node_id
        for node_id in node_ids
        if language_family(owner_path(node_id)) == family
    ]
    if not candidates:
        return None
    for node_id in candidates:
        if owner_path(node_id) in imported:
            return node_id
    return candidates[0]


def ensure_external_node(cursor: Cursor, node_id: str, node_type: str) -> None:
    """Create a placeholder node for a target defined outside the project."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING;
        """,
        (node_id, truncate(node_id, MAX_NAME_LENGTH), node_type),
    )


def link_file(
    cursor: Cursor,
    rel_path: str,
    content: str,
    known_files: set[str],
    symbols: dict[str, list[str]],
) -> int:
    """Store the edges leaving a file. Returns the edge count.

    Runs after every file has been indexed, so a call to something defined
    further down the tree still resolves.
    """
    parser = get_parser(rel_path)
    if parser is None:
        return 0

    source_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    relations = parser.get_relations(content)
    # Imports come first: knowing which files this one pulls in is what makes
    # a call resolve to the right definition below.
    relations.sort(key=lambda relation: relation["type"] != "imports")
    imported: set[str] = set()
    edges = 0
    for relation in relations:
        target = relation["target"]
        relation_type = relation["type"]
        if relation_type == "imports":
            target_id = resolve_import(target, rel_path, known_files)
            if target_id is None:
                target_id = truncate(target, MAX_NODE_ID_LENGTH)
                ensure_external_node(cursor, target_id, "external_import")
            else:
                imported.add(target_id)
        else:
            target_id = resolve_symbol(target, rel_path, symbols, imported)
            if target_id is None:
                target_id = truncate(target, MAX_NODE_ID_LENGTH)
                ensure_external_node(cursor, target_id, "external_symbol")
        if target_id == source_id:
            continue
        cursor.execute(
            """
            INSERT INTO graph_edges (source_id, target_id, relation_type)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (source_id, target_id, relation_type),
        )
        edges += 1
    return edges


def scan_and_build_graph() -> None:
    """Walk the project and build the graph in two passes."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError(f"{PROJECT_PATH} is not a directory")

    conn = get_db_connection()
    try:
        sources = list(iter_source_files(PROJECT_PATH))
        LOG.info("Indexing %d files from %s", len(sources), PROJECT_PATH)
        known_files: set[str] = set()
        symbols: dict[str, list[str]] = {}
        entity_total = 0
        edge_total = 0
        failures = 0

        with conn.cursor() as cursor:
            for full_path, rel_path in sources:
                content = read_source(full_path, rel_path)
                if content is None:
                    continue
                try:
                    entities = index_file(cursor, rel_path, content)
                except Exception:
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to index %s", rel_path)
                    continue
                conn.commit()
                known_files.add(rel_path)
                entity_total += len(entities)
                for entity in entities:
                    symbols.setdefault(entity["name"], []).append(
                        entity_node_id(rel_path, entity["name"])
                    )

            for full_path, rel_path in sources:
                if rel_path not in known_files:
                    continue
                content = read_source(full_path, rel_path)
                if content is None:
                    continue
                try:
                    edge_total += link_file(
                        cursor, rel_path, content, known_files, symbols
                    )
                except Exception:
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to link %s", rel_path)
                    continue
                conn.commit()

            try:
                pruned = prune_orphans(cursor)
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
                pruned = 0
                LOG.exception("Failed to prune orphan nodes")

        LOG.info(
            "Done: %d files, %d entities, %d edges, %d pruned, %d failures",
            len(known_files),
            entity_total,
            edge_total,
            pruned,
            failures,
        )
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
