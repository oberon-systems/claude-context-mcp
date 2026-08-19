"""Decide which files of the mounted project are worth indexing."""

from __future__ import annotations

import logging
import os
import posixpath
from collections.abc import Iterator

import pathspec

from ctxgraph.config import (
    DEFAULT_IGNORED_DIRS,
    IGNORE_FILE,
    KEEP_FILE,
    MAX_FILE_BYTES,
)
from ctxgraph.parsers import is_default_source

LOG = logging.getLogger(__name__)


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
    yield from walk_selected(
        root_path,
        load_spec(root_path, IGNORE_FILE),
        load_spec(root_path, KEEP_FILE),
    )


def walk_selected(
    root_path: str,
    ignore_spec: pathspec.PathSpec | None,
    keep_spec: pathspec.PathSpec | None,
) -> Iterator[tuple[str, str]]:
    """Yield the files two specs select, without reading either from disk.

    Split out of iter_source_files so a proposed selection can be simulated
    against the tree it is proposed for. The mount is read-only, so the pair
    cannot be written and read back the ordinary way.
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
