"""Language parsers and the dispatch that picks one for a path.

The rest of the package talks to this facade rather than to the individual
grammar modules, so adding a language stays a change inside `parsers`.
"""

from graphify.parsers.base import CodeParser, node_text, strip_literal, unique_pairs
from graphify.parsers.languages import MarkdownParser
from graphify.parsers.registry import (
    DEFAULT_SOURCE_EXTENSIONS,
    get_parser,
    is_default_source,
    language_family,
    parser_class,
)

__all__ = [
    "DEFAULT_SOURCE_EXTENSIONS",
    "CodeParser",
    "MarkdownParser",
    "get_parser",
    "is_default_source",
    "language_family",
    "node_text",
    "parser_class",
    "strip_literal",
    "unique_pairs",
]
