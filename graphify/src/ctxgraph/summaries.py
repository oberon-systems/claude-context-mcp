"""Describe a file in one line, without reading it for the caller."""

from __future__ import annotations

import posixpath

from ctxgraph.config import (
    COMMENT_MARKERS,
    MAX_SUMMARY_LENGTH,
    SUMMARY_ENTITY_LIMIT,
    SUMMARY_SCAN_LINES,
)
from ctxgraph.identifiers import truncate
from ctxgraph.parsers import MarkdownParser, parser_class


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
    if parser_class(rel_path) is MarkdownParser:
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
