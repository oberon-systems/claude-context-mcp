"""Decide which files of the mounted project are worth indexing."""

from __future__ import annotations

import logging
import os
import posixpath
from collections.abc import Iterator

import pathspec

from ctxgraph.config import DEFAULT_IGNORED_DIRS, MAX_FILE_BYTES
from ctxgraph.identifiers import source_node_id
from ctxgraph.parsers import is_default_source

LOG = logging.getLogger(__name__)

# One source's two specs, keep first, as `ctxgraph.selection` resolves them.
SpecPair = tuple[pathspec.PathSpec | None, pathspec.PathSpec | None]


def to_spec(lines: list[str]) -> pathspec.PathSpec | None:
    """Build a PathSpec from the lines of a selection document.

    A document holding only comments and blank lines is None rather than an
    empty spec, which is what makes it identical to no document at all.
    """
    patterns = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns) if patterns else None


def load_spec(root_path: str, file_name: str) -> pathspec.PathSpec | None:
    """Return the PathSpec held in file_name."""
    path = os.path.join(root_path, file_name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return to_spec(handle.readlines())
    except OSError:
        LOG.exception("Failed to read %s", file_name)
        return None


def iter_project_files(
    mount: str, selections: list[tuple[str, SpecPair]]
) -> Iterator[tuple[str, str]]:
    """Yield (absolute path, project relative path) for every source of a project.

    Each source is walked from its own root under its own selection, so a slice
    of a monorepo says which of its files are worth indexing without the
    repository it was cut from having to agree. Where a selection came from -
    a file in the tree or a row of `project_settings` - is settled by
    `ctxgraph.selection` before the walk, so nothing here reads a database.
    The empty alias is the project mounted whole.
    """
    for alias, (keep_spec, ignore_spec) in selections:
        base = os.path.join(mount, alias) if alias else mount
        for full_path, rel_path in walk_selected(base, ignore_spec, keep_spec):
            yield full_path, source_node_id(alias, rel_path)


def walk_selected(
    root_path: str,
    ignore_spec: pathspec.PathSpec | None,
    keep_spec: pathspec.PathSpec | None,
) -> Iterator[tuple[str, str]]:
    """Yield the files two specs select, without reading either from disk.

    Neither spec is loaded here, so a selection that exists only as a proposal
    or as a row of `project_settings` is walked exactly as one read off disk
    would be. The mount is read-only, so a stored pair could not be written
    into the tree and read back the ordinary way.
    """
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


def read_source(full_path: str, rel_path: str) -> tuple[str | None, str]:
    """Return the text of a file, and why it was skipped when it has no text.

    The reason travels with the content because a skipped file is a file the
    graph will not describe, and the run reports that at the end rather than
    leaving a node missing with no account of it.
    """
    try:
        size = os.path.getsize(full_path)
    except OSError:
        LOG.exception("Failed to stat %s", rel_path)
        return None, "unreadable"
    if size > MAX_FILE_BYTES:
        LOG.info("Skipping %s: %d bytes exceeds the limit", rel_path, size)
        return None, "over the size limit"
    try:
        with open(full_path, encoding="utf-8", errors="ignore") as handle:
            return handle.read(), ""
    except OSError:
        LOG.exception("Failed to read %s", rel_path)
        return None, "unreadable"
