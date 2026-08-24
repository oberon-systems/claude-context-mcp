"""Read Ansible YAML by its values rather than by its syntax.

Ansible keeps its structure in the values a document holds, so the tree-sitter
queries in languages.py can only see a bag of keys. The documents are loaded
instead, and anything that does not look like Ansible is handed to the
compose parser or, failing that, to the plain YAML parser this one extends.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from typing import Any

from ctxgraph.parsers.base import unique_pairs
from ctxgraph.parsers.compose import compose_parser, looks_like_compose
from ctxgraph.parsers.languages import YAMLParser
from ctxgraph.parsers.yamldocs import load_yaml_documents

# Ansible: the directories a role is made of, used to find the role a file
# belongs to and to resolve what its tasks refer to.
ANSIBLE_ROLE_DIRS = frozenset(
    {
        "defaults",
        "files",
        "handlers",
        "library",
        "meta",
        "molecule",
        "tasks",
        "templates",
        "tests",
        "vars",
    }
)


# Modules whose argument names a file, mapped to the edge they produce and the
# argument keys that carry the path.
ANSIBLE_FILE_MODULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "include": ("includes", ("file", "_raw_params")),
    "include_tasks": ("includes", ("file", "_raw_params")),
    "import_tasks": ("includes", ("file", "_raw_params")),
    "include_vars": ("reads_vars", ("file", "_raw_params")),
    "template": ("uses_template", ("src",)),
    "copy": ("uses_file", ("src",)),
}
ANSIBLE_ROLE_MODULES = frozenset({"import_role", "include_role"})


def role_root(rel_path: str) -> str:
    """Return the role directory owning a file, e.g. roles/sshd."""
    parts = rel_path.split("/")
    for index in range(len(parts) - 2, 0, -1):
        if parts[index] in ANSIBLE_ROLE_DIRS:
            return "/".join(parts[:index])
    return ""


def expand_path_variables(target: str, rel_path: str) -> str:
    """Replace the Ansible path variables with the directory they stand for.

    `include_tasks: "{{ role_path }}/tasks/rocky/9.yml"` is the idiomatic way
    to reach a sibling task file, and it is the only templating this indexer
    can resolve without running Ansible.
    """
    known = {
        "role_path": role_root(rel_path),
        "playbook_dir": posixpath.dirname(rel_path),
    }

    def replace(match: re.Match[str]) -> str:
        value = known.get(match.group(1))
        return match.group(0) if value is None else value

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, target).lstrip("/")


def is_templated(value: str) -> bool:
    """Report whether a value still holds Jinja that only Ansible can expand."""
    return "{{" in value or "{%" in value


def ansible_argument(value: Any, keys: tuple[str, ...]) -> str:  # noqa: ANN401
    """Read a file argument from a module call, in any of the forms it takes."""
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate.strip()
        return ""
    if isinstance(value, str):
        if "=" in value:
            # The old `src=x dest=y` free form.
            for key in keys:
                match = re.search(rf"(?:^|\s){key}=(\S+)", value)
                if match:
                    return match.group(1)
            return ""
        return value.strip()
    return ""


def iter_ansible_plays(document: Any) -> Iterator[dict[str, Any]]:  # noqa: ANN401
    """Yield the plays of a playbook document."""
    if isinstance(document, list):
        for item in document:
            if isinstance(item, dict) and "hosts" in item:
                yield item


def iter_ansible_tasks(
    node: Any,  # noqa: ANN401
    entity_type: str = "task",
) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield every task in a document, descending into plays and blocks."""
    if isinstance(node, list):
        for item in node:
            yield from iter_ansible_tasks(item, entity_type)
        return
    if not isinstance(node, dict):
        return
    if "hosts" in node:
        for key in ("pre_tasks", "tasks", "post_tasks"):
            yield from iter_ansible_tasks(node.get(key), "task")
        yield from iter_ansible_tasks(node.get("handlers"), "handler")
        return
    yield node, entity_type
    for key in ("block", "rescue", "always"):
        yield from iter_ansible_tasks(node.get(key), entity_type)


def ansible_role_name(entry: Any) -> str:  # noqa: ANN401
    """Return the role name of a `roles:` or `dependencies:` entry."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("role", "name"):
            value = entry.get(key)
            if isinstance(value, str):
                return value.strip()
    return ""


def ansible_kind(rel_path: str, documents: list[Any]) -> str:
    """Classify an Ansible YAML file by its path and its shape."""
    if any(any(True for _ in iter_ansible_plays(document)) for document in documents):
        return "playbook"
    segments = rel_path.split("/")
    if "handlers" in segments:
        return "handlers"
    if "tasks" in segments:
        return "tasks"
    if {"defaults", "vars"} & set(segments) or segments[0] in (
        "group_vars",
        "host_vars",
    ):
        return "vars"
    if "meta" in segments:
        return "meta"
    return "other"


class AnsibleParser(YAMLParser):
    """Ansible aware YAML parser.

    Ansible expresses its structure in ordinary YAML, so the tree-sitter
    queries above cannot tell a task from a variable. The documents are loaded
    instead, and files that do not look like Ansible fall back to the plain
    YAML behaviour of the base class.
    """

    FAMILY = "ansible"

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Extract plays, tasks, handlers and role variables."""
        documents = load_yaml_documents(content)
        if documents is None:
            return super().get_entities(content, rel_path)
        kind = ansible_kind(rel_path, documents)
        if kind == "other" and looks_like_compose(documents):
            return compose_parser().get_entities(content, rel_path)
        if kind in ("other", "meta"):
            return super().get_entities(content, rel_path)

        pairs: list[tuple[str, str]] = []
        for document in documents:
            if kind == "vars":
                if isinstance(document, dict):
                    pairs.extend((str(key), "variable") for key in document)
                continue
            for play in iter_ansible_plays(document):
                pairs.append((str(play.get("hosts")), "play"))
            default_type = "handler" if kind == "handlers" else "task"
            for task, entity_type in iter_ansible_tasks(document, default_type):
                name = task.get("name")
                if isinstance(name, str) and name.strip():
                    pairs.append((name.strip(), entity_type))
        return unique_pairs(iter(pairs))

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Extract includes, role uses, template and handler references."""
        documents = load_yaml_documents(content)
        if documents is None:
            return []
        kind = ansible_kind(rel_path, documents)
        if kind == "other" and looks_like_compose(documents):
            return compose_parser().get_relations(content, rel_path)
        if kind in ("other", "vars"):
            return []

        found: list[tuple[str, str]] = []
        for document in documents:
            if kind == "meta":
                if isinstance(document, dict):
                    found.extend(
                        (ansible_role_name(entry), "depends_on")
                        for entry in document.get("dependencies") or []
                    )
                continue
            for play in iter_ansible_plays(document):
                found.extend(
                    (ansible_role_name(entry), "uses_role")
                    for entry in play.get("roles") or []
                )
                found.extend(
                    (str(entry), "reads_vars")
                    for entry in play.get("vars_files") or []
                    if isinstance(entry, str)
                )
            for task, _ in iter_ansible_tasks(document):
                found.extend(self._task_relations(task))

        relations = []
        for entry in unique_pairs(iter(found)):
            target = entry["name"]
            relation_type = entry["type"]
            if relation_type != "notifies":
                target = expand_path_variables(target, rel_path)
                if is_templated(target):
                    # Nothing static is left to point at.
                    continue
            relations.append(
                {
                    "target": target,
                    "type": relation_type,
                    "scope": "symbol" if relation_type == "notifies" else "file",
                }
            )
        return relations

    @staticmethod
    def _task_relations(task: dict[str, Any]) -> Iterator[tuple[str, str]]:
        """Yield the (target, relation type) pairs a single task declares."""
        for key, value in task.items():
            if key == "notify":
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    if isinstance(entry, str):
                        yield entry.strip(), "notifies"
                continue
            # Modules may be written plain or fully qualified.
            module = key.rsplit(".", 1)[-1]
            if module in ANSIBLE_FILE_MODULES:
                relation_type, argument_keys = ANSIBLE_FILE_MODULES[module]
                target = ansible_argument(value, argument_keys)
                if target:
                    yield target, relation_type
            elif module in ANSIBLE_ROLE_MODULES:
                name = ansible_role_name(value)
                if name:
                    yield name, "uses_role"
