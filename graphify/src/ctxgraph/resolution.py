"""Turn what a file refers to into the id of a node in the graph.

Nothing here touches the database: a reference resolves against the set of
indexed paths and the table of declared symbols, both handed in by the caller.
"""

from __future__ import annotations

import posixpath

from ctxgraph.config import (
    MAX_NODE_ID_LENGTH,
    MODULE_EXTENSIONS,
    REWRITABLE_IMPORT_EXTENSIONS,
)
from ctxgraph.identifiers import entity_node_id, owner_path, truncate
from ctxgraph.parsers import language_family, strip_literal
from ctxgraph.parsers.ansible import role_root

# Where each edge looks inside the owning role when the target is a bare name.
ANSIBLE_RELATION_DIRS = {
    "includes": ("tasks",),
    "reads_vars": ("vars", "defaults"),
    "uses_template": ("templates",),
    "uses_file": ("files",),
}
# Edges whose target is a role rather than a file below the role.
ANSIBLE_ROLE_RELATIONS = frozenset({"uses_role", "depends_on"})
# The files a role name resolves to, in the order they are tried.
ANSIBLE_ROLE_ENTRY_POINTS = (
    "tasks/main.yml",
    "tasks/main.yaml",
    "meta/main.yml",
    "meta/main.yaml",
)
# The two fixed directories of a Puppet module, which is what makes a template
# reference resolvable from the path of the manifest naming it.
PUPPET_MANIFEST_DIR = "/manifests/"
PUPPET_TEMPLATE_DIR = "templates"
# Puppet is the only other producer of `uses_template`, and Ansible already
# claims that name in ANSIBLE_RELATION_DIRS, so the two are told apart by the
# file the edge leaves rather than by the relation alone.
PUPPET_SOURCE_EXTENSIONS = (".pp",)


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


def ansible_candidates(relation_type: str, target: str, rel_path: str) -> list[str]:
    """Return the file paths an Ansible reference may point at."""
    root = role_root(rel_path)
    base_dir = posixpath.dirname(rel_path)
    if relation_type in ANSIBLE_ROLE_RELATIONS:
        # A role sits next to the role that names it, or under a top level
        # roles directory.
        prefixes = ["roles", posixpath.join(base_dir, "roles")]
        if root:
            prefixes.insert(0, posixpath.dirname(root))
        return [
            posixpath.normpath(posixpath.join(prefix, target, entry_point))
            for prefix in prefixes
            for entry_point in ANSIBLE_ROLE_ENTRY_POINTS
        ]

    directories = [base_dir]
    if root:
        directories.extend(
            posixpath.join(root, name)
            for name in ANSIBLE_RELATION_DIRS.get(relation_type, ())
        )
    # The target may already be written from the project root.
    directories.append("")
    return [
        posixpath.normpath(posixpath.join(directory, target))
        for directory in directories
    ]


def puppet_candidates(target: str, rel_path: str) -> list[str]:
    """Return the file paths a Puppet template reference may point at.

    A manifest lives at `<modules>/<module>/manifests/<name>.pp` and names a
    template by the module it belongs to rather than by its path, so
    `template('profile/nginx.conf.erb')` means
    `<modules>/profile/templates/nginx.conf.erb`. The module in the reference
    is not necessarily the one holding the manifest, which is why only the
    directory the modules sit in is taken from the manifest path.
    """
    candidates: list[str] = []
    root, separator, _ = rel_path.partition(PUPPET_MANIFEST_DIR)
    module, _, tail = target.partition("/")
    if separator and tail:
        modules_dir = posixpath.dirname(root)
        candidates.append(
            posixpath.normpath(
                posixpath.join(modules_dir, module, PUPPET_TEMPLATE_DIR, tail)
            )
        )
    # The target may already be written from the project root.
    candidates.append(posixpath.normpath(target))
    return candidates


def resolve_file_target(
    relation_type: str, target: str, rel_path: str, known_files: set[str]
) -> str | None:
    """Resolve a file scoped relation to the id of an indexed file node."""
    if relation_type == "imports":
        return resolve_import(target, rel_path, known_files)
    if rel_path.endswith(PUPPET_SOURCE_EXTENSIONS):
        candidates = puppet_candidates(target, rel_path)
    else:
        candidates = ansible_candidates(relation_type, target, rel_path)
    for candidate in candidates:
        if candidate in known_files:
            return truncate(candidate, MAX_NODE_ID_LENGTH)
    return None


def placeholder_id(relation_type: str, target: str) -> str:
    """Return the node id standing for a target outside the tree."""
    prefix = "role:" if relation_type in ANSIBLE_ROLE_RELATIONS else ""
    return truncate(f"{prefix}{target}", MAX_NODE_ID_LENGTH)


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
