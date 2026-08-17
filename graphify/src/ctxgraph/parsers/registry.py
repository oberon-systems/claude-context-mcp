"""Pick the parser for a path, and say what a path belongs to."""

from __future__ import annotations

import posixpath
from functools import cache

from ctxgraph.config import EXTRA_SOURCE_EXTENSIONS, GRAPHIFYY_EXTENSIONS
from ctxgraph.parsers.ansible import AnsibleParser
from ctxgraph.parsers.base import CodeParser
from ctxgraph.parsers.languages import (
    BashParser,
    DockerfileParser,
    EppParser,
    ErbParser,
    GoParser,
    HCLParser,
    JavaScriptParser,
    MakeParser,
    MarkdownParser,
    PuppetParser,
    PythonParser,
    RustParser,
    TOMLParser,
    TSXParser,
    TypeScriptParser,
)

EXTENSION_PARSERS: dict[str, type[CodeParser]] = {
    ".bash": BashParser,
    ".cjs": JavaScriptParser,
    ".dockerfile": DockerfileParser,
    ".epp": EppParser,
    ".erb": ErbParser,
    ".go": GoParser,
    ".hcl": HCLParser,
    ".js": JavaScriptParser,
    ".jsx": JavaScriptParser,
    ".markdown": MarkdownParser,
    ".md": MarkdownParser,
    ".mjs": JavaScriptParser,
    ".mk": MakeParser,
    ".pp": PuppetParser,
    ".py": PythonParser,
    ".rs": RustParser,
    ".sh": BashParser,
    ".tf": HCLParser,
    ".tfvars": HCLParser,
    ".toml": TOMLParser,
    ".ts": TypeScriptParser,
    ".tsx": TSXParser,
    ".yaml": AnsibleParser,
    ".yml": AnsibleParser,
}
FILENAME_PARSERS: dict[str, type[CodeParser]] = {
    "containerfile": DockerfileParser,
    "dockerfile": DockerfileParser,
    "gnumakefile": MakeParser,
    "makefile": MakeParser,
}
DEFAULT_SOURCE_EXTENSIONS = tuple(sorted(EXTENSION_PARSERS)) + EXTRA_SOURCE_EXTENSIONS


@cache
def _parser_instance(matched: type[CodeParser]) -> CodeParser:
    """Return the shared instance of a parser class.

    Compiling the queries costs more than parsing a small file, so the
    instances are reused across the whole run.
    """
    return matched()


def parser_class(file_path: str) -> type[CodeParser] | None:
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
    matched = parser_class(file_path)
    return _parser_instance(matched) if matched else None


def is_default_source(file_name: str) -> bool:
    """Report whether a file is indexed when the project has no .ctxkeep."""
    _, extension = posixpath.splitext(file_name.lower())
    return (
        file_name.endswith(DEFAULT_SOURCE_EXTENSIONS)
        or extension in GRAPHIFYY_EXTENSIONS
        or parser_class(file_name) is not None
    )


def language_family(rel_path: str) -> str:
    """Return the symbol namespace a file belongs to."""
    matched = parser_class(rel_path)
    return matched.FAMILY if matched else ""
