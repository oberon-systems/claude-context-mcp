"""Read a file of an indexed tree from its mount rather than from the graph.

The one place that turns a project and a project-relative path into bytes, so
the containment check and the deny list exist once. Everything that serves file
text - the summarization queue, the content endpoint - comes through here.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath

from ctxgraph.config import CONTENT_DENIED_NAMES
from ctxgraph.discovery import read_source
from ctxgraph.identifiers import project_mount

DENIED = "denied by name"
ESCAPES = "outside the project"
UNMOUNTED = "the project is not mounted"


def is_denied(rel_path: str) -> bool:
    """Report whether a file's text must never leave this machine."""
    name = posixpath.basename(rel_path)
    return any(fnmatch.fnmatch(name, pattern) for pattern in CONTENT_DENIED_NAMES)


def resolve(project: str, rel_path: str) -> str | None:
    """Return the absolute path of a file inside a project's mount.

    None when the result escapes the mount. `os.walk` never descends a
    symlinked directory, so no such path is ever indexed - but a symlinked
    file is, and it can point anywhere, which is why this resolves links
    before comparing.
    """
    mount = os.path.realpath(project_mount(project))
    full = os.path.realpath(os.path.join(mount, rel_path))
    if full != mount and not full.startswith(mount + os.sep):
        return None
    return full


def read(project: str, rel_path: str, limit: int = 0) -> tuple[str | None, str]:
    """Return a file's text, or None and the reason there is none."""
    if is_denied(rel_path):
        return None, DENIED
    if not os.path.isdir(project_mount(project)):
        return None, UNMOUNTED
    full = resolve(project, rel_path)
    if full is None:
        return None, ESCAPES
    content, reason = read_source(full, rel_path)
    if content is None:
        return None, reason
    return (content[:limit] if limit > 0 else content), ""
