"""The parser contract every language implementation follows."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tree_sitter import Language, Node, Parser, Query, QueryCursor


def node_text(node: Node) -> str:
    """Return the decoded source text of a node."""
    return (node.text or b"").decode("utf-8", "replace")


def qualified(entity_type: str, name: str) -> str:
    """Name an entity by its kind and its declared name."""
    return f"{entity_type}.{name}"


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

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the deduplicated entities declared in content.

        rel_path is unused by most grammars; the Ansible parser needs it to
        tell a task file from a handler file.
        """
        if self.entity_query is None:
            return []
        return unique_pairs(
            (name.strip(), kind)
            for kind, name in self.capture_texts(self.entity_query, self.parse(content))
        )

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Return the deduplicated relations found in content.

        Each relation carries the scope its target is resolved in: "file" for
        something the tree holds, "symbol" for a name another file declares.
        """
        if self.relation_query is None:
            return []
        pairs: list[tuple[str, str]] = []
        for kind, text in self.capture_texts(self.relation_query, self.parse(content)):
            target = strip_literal(text) if kind == "imports" else text.strip()
            pairs.append((target, kind))
        return [
            {
                "target": entry["name"],
                "type": entry["type"],
                "scope": "file" if entry["type"] == "imports" else "symbol",
            }
            for entry in unique_pairs(iter(pairs))
        ]


def unique_pairs(pairs: Iterator[tuple[str, str]]) -> list[dict[str, str]]:
    """Turn (name, kind) pairs into name/type dicts, dropping blanks and dupes."""
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for name, kind in pairs:
        if not name or (name, kind) in seen:
            continue
        seen.add((name, kind))
        result.append({"name": name, "type": kind})
    return result
