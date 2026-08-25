"""Propose the .ctxkeep / .ctxignore pair for a tree, and verify the proposal.

Run as `python -m ctxgraph.bootstrap` against the same read-only mount the
indexer walks, by `make install`. Nothing here reaches the database, so it
runs before the stack is needed, and everything it claims about supported
file types is read from the parser tables rather than restated here.

The proposal is printed rather than written: the mount is read-only by
contract, so the caller on the host is what puts the files in place, and only
when they are absent.
"""

from __future__ import annotations

import os
import posixpath
import sys
from collections import Counter
from dataclasses import dataclass, field

import pathspec

from ctxgraph.config import (
    DEFAULT_IGNORED_DIRS,
    EXTRA_SOURCE_EXTENSIONS,
    GRAPHIFYY_EXTENSIONS,
    IGNORE_FILE,
    IGNORED_FILE_NAMES,
    KEEP_FILE,
    MAX_FILE_BYTES,
    PROJECT_NAME,
    PROJECT_PATH,
    PROJECT_ROOT,
)
from ctxgraph.discovery import load_spec, walk_selected
from ctxgraph.identifiers import project_name
from ctxgraph.parsers.registry import (
    EXTENSION_PARSERS,
    FILENAME_PARSERS,
    parser_class,
)

# Key material is pruned whatever else the pair says: the text of every
# selected file lands in graph_nodes.content, and a graph is not the place
# for a private key.
SECRET_PATTERNS = (
    "*.pem",
    "*.key",
    "*.crt",
    "*.p12",
    "*.pfx",
    "*.jwt",
    ".htpasswd",
    "authorized_keys",
    "credentials",
    "id_rsa",
    "id_ed25519",
)
# Shebang lines worth admitting. An interpreter no parser reads is left out
# rather than turned into a file node nothing can answer from.
INTERPRETER_EXTENSIONS = {
    "bash": ".sh",
    "dash": ".sh",
    "ksh": ".sh",
    "node": ".js",
    "nodejs": ".js",
    "python": ".py",
    "python2": ".py",
    "python3": ".py",
    "ruby": ".rb",
    "sh": ".sh",
    "zsh": ".sh",
}
# Parser classes are named for their grammar, which spells a few of them the
# way Python wants rather than the way the format is written.
LABEL_OVERRIDES = {"Html": "HTML", "Json": "JSON", "Phtml": "PHP template"}
# A directory is only a bulk candidate when it is both large in absolute
# terms and a real share of the tree, so a small repository never reports one.
BULK_MIN_FILES = 200
BULK_MIN_SHARE = 0.2
# Detail lines are a report, not a listing: past this many the rest is a count.
DETAIL_LIMIT = 20
SECTIONS = ("name", "ctxkeep", "ctxignore", "report")


@dataclass
class Inventory:
    """What one walk of the tree found, before any selection is applied."""

    files: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    extensions: Counter[str] = field(default_factory=Counter)
    filenames: dict[str, str] = field(default_factory=dict)
    dockerfile_suffixed: bool = False
    shebangs: list[tuple[str, str]] = field(default_factory=list)
    directories: Counter[str] = field(default_factory=Counter)
    generated: set[str] = field(default_factory=set)


def interpreter(full_path: str, size: int) -> str:
    """Return the interpreter named by a shebang line, lowercased."""
    if not 0 < size <= MAX_FILE_BYTES:
        return ""
    try:
        with open(full_path, "rb") as handle:
            first = handle.readline(200)
    except OSError:
        return ""
    if not first.startswith(b"#!"):
        return ""
    tokens = [
        token
        for token in first[2:].decode("utf-8", "ignore").split()
        if not token.startswith("-")
    ]
    if not tokens:
        return ""
    name = posixpath.basename(tokens[0])
    if name == "env" and len(tokens) > 1:
        name = posixpath.basename(tokens[1])
    return name.lower()


def collect(root_path: str) -> Inventory:
    """Walk the tree once, pruning only the built-in skip list."""
    inventory = Inventory()
    for current_dir, dir_names, file_names in os.walk(root_path):
        rel_dir = os.path.relpath(current_dir, root_path)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        dir_names[:] = [
            name for name in sorted(dir_names) if name not in DEFAULT_IGNORED_DIRS
        ]
        top = rel_dir.split("/")[0] if rel_dir else ""
        for file_name in sorted(file_names):
            rel_path = posixpath.join(rel_dir, file_name)
            full_path = os.path.join(current_dir, file_name)
            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue
            inventory.files.append(rel_path)
            inventory.sizes[rel_path] = size
            if top:
                inventory.directories[top] += 1
            lowered = file_name.lower()
            extension = posixpath.splitext(lowered)[1]
            if lowered in IGNORED_FILE_NAMES:
                inventory.generated.add(file_name)
            elif extension:
                inventory.extensions[extension] += 1
            if lowered in FILENAME_PARSERS:
                inventory.filenames[lowered] = file_name
            elif lowered.startswith("dockerfile."):
                inventory.dockerfile_suffixed = True
            elif not extension:
                found = interpreter(full_path, size)
                if found in INTERPRETER_EXTENSIONS:
                    inventory.shebangs.append((found, rel_path))
    return inventory


def is_supported(rel_path: str) -> bool:
    """Say whether some producer would read this file if it were selected."""
    file_name = posixpath.basename(rel_path).lower()
    if file_name in IGNORED_FILE_NAMES:
        return False
    extension = posixpath.splitext(file_name)[1]
    return (
        parser_class(rel_path) is not None
        or extension in GRAPHIFYY_EXTENSIONS
        or extension in EXTRA_SOURCE_EXTENSIONS
    )


def extension_groups(inventory: Inventory) -> list[tuple[str, int, list[str]]]:
    """Group the supported extensions present into labelled blocks.

    Ordered the way the indexer routes them: the extractor first, since it
    wins over a parser of ours for the same extension, then the parsers, then
    the extensions that only ever become a file node.
    """
    blocks: dict[tuple[int, str], list[tuple[str, int]]] = {}
    for extension, count in inventory.extensions.items():
        if extension in GRAPHIFYY_EXTENSIONS:
            key = (0, "Code, read by the graphifyy extractor")
        elif extension in EXTENSION_PARSERS:
            name = EXTENSION_PARSERS[extension].__name__.removesuffix("Parser")
            key = (1, LABEL_OVERRIDES.get(name, name))
        elif extension in EXTRA_SOURCE_EXTENSIONS:
            key = (2, "File node only, no parser reads inside these")
        else:
            continue
        blocks.setdefault(key, []).append((extension, count))
    return [
        (
            label,
            sum(count for _, count in found),
            [f"*{extension}" for extension, _ in sorted(found)],
        )
        for (_, label), found in sorted(blocks.items())
    ]


def wrapped_comment(prefix: str, items: list[str]) -> list[str]:
    """Return comment lines listing items, wrapped at 79 characters."""
    lines: list[str] = []
    current = f"# {prefix}"
    for item in items:
        separator = " " if current.endswith(":") else ", "
        candidate = f"{current}{separator}{item}"
        if len(candidate) > 79:
            lines.append(f"{current},")
            current = f"#   {item}"
        else:
            current = candidate
    lines.append(current)
    return lines


def keep_document(inventory: Inventory) -> list[str]:
    """Render the proposed .ctxkeep."""
    lines = [
        "# What of this tree becomes a node in the code graph.",
        "#",
        "# Generated by `make install` from the file types actually present",
        "# here. This file REPLACES the default selection rather than adding",
        "# to it, so deleting a line removes that file type from the graph.",
    ]
    for label, count, patterns in extension_groups(inventory):
        plural = "file" if count == 1 else "files"
        lines.extend(["", f"# {label} ({count} {plural})"])
        lines.extend(patterns)
    names = sorted(inventory.filenames.values())
    if inventory.dockerfile_suffixed:
        names.append("Dockerfile.*")
    if names:
        lines.extend(["", "# Matched by name rather than by extension"])
        lines.extend(sorted(set(names)))
    if inventory.shebangs:
        lines.extend(
            [
                "",
                "# Scripts with no extension, found by their shebang. There is",
                "# no suffix to match on, so a new one has to be added here by",
                "# hand - and it becomes a file node with no entities, because",
                "# a parser is chosen by extension.",
            ]
        )
        lines.extend(sorted(path for _, path in inventory.shebangs))
    absent = sorted(
        extension
        for extension in set(EXTENSION_PARSERS)
        | GRAPHIFYY_EXTENSIONS
        | set(EXTRA_SOURCE_EXTENSIONS)
        if not inventory.extensions[extension]
    )
    if absent:
        lines.append("")
        lines.extend(wrapped_comment("Supported but absent from this tree:", absent))
    return lines


def bulk_candidates(inventory: Inventory) -> list[tuple[str, int]]:
    """Return the top-level directories large enough to be worth pruning."""
    total = len(inventory.files) or 1
    return [
        (name, count)
        for name, count in inventory.directories.most_common()
        if count >= BULK_MIN_FILES and count / total >= BULK_MIN_SHARE
    ]


def ignore_document(inventory: Inventory) -> list[str]:
    """Render the proposed .ctxignore."""
    lines = [
        "# What is pruned from the walk, applied after .ctxkeep.",
        "#",
        "# Generated by `make install`. Additive on top of the built-in skip",
        "# list (.git, .venv, node_modules, dist, target and friends), so it",
        "# only names what those do not already cover.",
        "",
        "# Key material. The text of every selected file is stored in the",
        "# graph, which is not a place for a private key.",
    ]
    lines.extend(SECRET_PATTERNS)
    lines.extend(
        [
            "",
            "# Agent state rather than code of this tree. The skill installed",
            "# here is a rendered copy of one that lives in claude-context-mcp,",
            "# and indexing it describes that repository, not this one.",
            ".claude/",
            ".gemini/",
        ]
    )
    if inventory.generated:
        lines.extend(
            [
                "",
                "# Generated, and saying nothing their source file does not.",
                "# The default selection skips these; an explicit .ctxkeep",
                "# pattern would otherwise pull them back in.",
            ]
        )
        lines.extend(sorted(inventory.generated))
    candidates = bulk_candidates(inventory)
    if candidates:
        lines.extend(
            [
                "",
                "# Bulk directories found in this tree, left commented out on",
                "# purpose: pruning one is a judgement call, and a tree this",
                "# code calls into loses real edges when it goes.",
            ]
        )
        lines.extend(f"# {name}/  ({count} files)" for name, count in candidates)
    return lines


def to_spec(lines: list[str]) -> pathspec.PathSpec | None:
    """Build a PathSpec from document lines, the way load_spec does."""
    patterns = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns) if patterns else None


def detail(label: str, items: list[str]) -> list[str]:
    """Return a capped detail block for the report."""
    if not items:
        return []
    lines = [f"{label} ({len(items)}):"]
    lines.extend(f"  {item}" for item in sorted(items)[:DETAIL_LIMIT])
    if len(items) > DETAIL_LIMIT:
        lines.append(f"  ... and {len(items) - DETAIL_LIMIT} more")
    return lines


def verify(
    root_path: str,
    inventory: Inventory,
    keep_spec: pathspec.PathSpec | None,
    ignore_spec: pathspec.PathSpec | None,
) -> list[str]:
    """Simulate the selection against the tree and report what it does."""
    kept = [rel for _, rel in walk_selected(root_path, ignore_spec, keep_spec)]
    kept_set = set(kept)
    # The two exclusions are told apart because only one of them is ever a
    # mistake. A supported file .ctxkeep never names is a gap in the
    # selection; one .ctxignore drops was named on purpose - key material,
    # agent state, generated files - and is reported so a surprise in that
    # list is visible, an ignore glob eating a keep pattern above all.
    selected = {rel for _, rel in walk_selected(root_path, None, keep_spec)}
    missed = [
        rel for rel in inventory.files if is_supported(rel) and rel not in selected
    ]
    pruned = [
        rel for rel in sorted(selected) if is_supported(rel) and rel not in kept_set
    ]
    oversized = [rel for rel in kept if inventory.sizes.get(rel, 0) > MAX_FILE_BYTES]
    unparsed = [rel for rel in kept if not is_supported(rel)]
    lines = [
        f"walked: {len(inventory.files)}  kept: {len(kept)}  "
        f"missed: {len(missed)}  pruned: {len(pruned)}  "
        f"over-limit: {len(oversized)}  no-parser: {len(unparsed)}",
    ]
    lines.extend(detail("supported, and .ctxkeep does not name it - a gap", missed))
    lines.extend(detail("supported, and .ctxignore drops it - on purpose?", pruned))
    lines.extend(detail("over the 1 MB limit, read and then dropped", oversized))
    lines.extend(detail("file node with a summary and nothing else", unparsed))
    lines.extend(
        detail(
            "extension-less scripts admitted by their shebang",
            [f"{path} ({found})" for found, path in inventory.shebangs],
        )
    )
    return lines


def emit(section: str, lines: list[str]) -> None:
    """Print one section of the output contract."""
    print(f"#--- {section} ---")
    for line in lines:
        print(line)


def main() -> None:
    """Scan the mounted tree and print the proposal, the name and the report."""
    root_path = PROJECT_PATH
    if not os.path.isdir(root_path):
        print(f"{root_path} is not a directory", file=sys.stderr)
        raise SystemExit(1)
    inventory = collect(root_path)
    keep_lines = keep_document(inventory)
    ignore_lines = ignore_document(inventory)
    # An existing pair is what the tree is actually indexed by, so that is
    # what the report has to describe. The proposal is still printed; the
    # caller decides, and never overwrites.
    existing_keep = load_spec(root_path, KEEP_FILE)
    existing_ignore = load_spec(root_path, IGNORE_FILE)
    sources = []
    if os.path.isfile(os.path.join(root_path, KEEP_FILE)):
        sources.append(f"{KEEP_FILE} already in the tree")
    else:
        existing_keep = to_spec(keep_lines)
        sources.append(f"proposed {KEEP_FILE}")
    if os.path.isfile(os.path.join(root_path, IGNORE_FILE)):
        sources.append(f"{IGNORE_FILE} already in the tree")
    else:
        existing_ignore = to_spec(ignore_lines)
        sources.append(f"proposed {IGNORE_FILE}")
    report = [f"verified against: {', '.join(sources)}"]
    report.extend(verify(root_path, inventory, existing_keep, existing_ignore))
    emit("name", [project_name(PROJECT_NAME, PROJECT_ROOT or root_path)])
    emit("ctxkeep", keep_lines)
    emit("ctxignore", ignore_lines)
    emit("report", report)


if __name__ == "__main__":
    main()
