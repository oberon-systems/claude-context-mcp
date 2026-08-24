"""Read a Docker Compose file as an architecture rather than as a bag of keys.

Compose keeps its structure in the values a document holds, so the file is
loaded rather than queried. Services, volumes, networks, configs and secrets
become nodes, and what a service names - the image it runs, the Dockerfile it
builds, the volumes and networks it joins, the files it mounts - becomes an
edge leaving that service. Anything that does not look like compose falls back
to the plain YAML parser this one extends.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from functools import cache
from typing import Any

from ctxgraph.parsers.languages import YAMLParser
from ctxgraph.parsers.yamldocs import load_yaml_documents

# The top level sections holding named declarations, and the entity type each
# key becomes. The type is part of the node name because an entity node id is
# `<file>::<name>`, and a service and a volume may share one.
COMPOSE_SECTIONS: dict[str, str] = {
    "services": "service",
    "volumes": "volume",
    "networks": "network",
    "configs": "config",
    "secrets": "secret",
}
# Where a service names something one of those sections declares: the key it
# is written under, the edge it produces and the type of what it points at.
COMPOSE_SERVICE_REFERENCES: dict[str, tuple[str, str]] = {
    "networks": ("uses_network", "network"),
    "configs": ("uses_config", "config"),
    "secrets": ("uses_secret", "secret"),
}
# Keys that only a compose service carries. Checked before a file is read as
# compose, so an unrelated document with a `services:` mapping stays YAML.
COMPOSE_SERVICE_KEYS = frozenset(
    {
        "build",
        "command",
        "container_name",
        "depends_on",
        "entrypoint",
        "env_file",
        "environment",
        "extends",
        "healthcheck",
        "image",
        "networks",
        "ports",
        "restart",
        "volumes",
    }
)
COMPOSE_FILE_NAMES = frozenset(
    {"compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}
)
COMPOSE_NAME_PREFIXES = ("compose.", "docker-compose.")
COMPOSE_NAME_SUFFIXES = (".yaml", ".yml")
# What a build context is read from when the section names nothing else.
DEFAULT_DOCKERFILE = "Dockerfile"
# `x-anything` is an extension field, free for the author to use, and it
# declares nothing worth a node.
EXTENSION_PREFIX = "x-"
# The prefix an image target carries. Without it `resolve_symbol` would bind
# `postgres` to a declared symbol of that name somewhere else; prefixed, it
# matches nothing and becomes one external node per image - the same shape the
# Ansible `role:` and the npm `npm:` placeholders have.
IMAGE_PREFIX = "image:"
# A build context that is not a directory of this tree.
REMOTE_CONTEXT_MARKERS = ("://", "git@")


def is_compose_name(file_name: str) -> bool:
    """Report whether a file name is one compose answers to."""
    name = posixpath.basename(file_name).lower()
    if name in COMPOSE_FILE_NAMES:
        return True
    # docker-compose.prod.yml, compose.override.yaml and friends.
    return name.startswith(COMPOSE_NAME_PREFIXES) and name.endswith(
        COMPOSE_NAME_SUFFIXES
    )


def is_interpolated(value: str) -> bool:
    """Report whether a value holds a variable only compose can expand."""
    return "${" in value or "$(" in value


def compose_mapping(documents: list[Any]) -> dict[str, Any] | None:
    """Return the one document of a file when it is shaped like compose."""
    if len(documents) != 1 or not isinstance(documents[0], dict):
        return None
    services = documents[0].get("services")
    if not isinstance(services, dict) or not services:
        return None
    bodies = [value for value in services.values() if isinstance(value, dict)]
    if len(bodies) != len(services):
        return None
    if not any(set(body) & COMPOSE_SERVICE_KEYS for body in bodies):
        return None
    return documents[0]


def looks_like_compose(documents: list[Any]) -> bool:
    """Report whether loaded YAML documents are a compose file."""
    return compose_mapping(documents) is not None


def compose_document(content: str) -> dict[str, Any] | None:
    """Load a file and return it when it is compose, or None when it is not."""
    documents = load_yaml_documents(content)
    return None if documents is None else compose_mapping(documents)


def qualified(entity_type: str, name: str) -> str:
    """Name an entity by its kind and its declared name."""
    return f"{entity_type}.{name}"


def as_list(value: Any) -> list[Any]:  # noqa: ANN401
    """Return a value as a list, whichever of the two forms it takes."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def declared_items(section: Any) -> Iterator[tuple[Any, str]]:  # noqa: ANN401
    """Yield the (key, name) pairs a top level section declares."""
    if not isinstance(section, dict):
        return
    for key in section:
        name = str(key).strip()
        if name and not name.startswith(EXTENSION_PREFIX):
            yield key, name


def declared_names(section: Any) -> Iterator[str]:  # noqa: ANN401
    """Yield the names a top level section declares."""
    for _, name in declared_items(section):
        yield name


def referenced_names(value: Any) -> Iterator[str]:  # noqa: ANN401
    """Yield the names a service reference section points at.

    `networks: [base]`, `networks: {base: {aliases: ...}}` and
    `configs: [{source: nginx, target: ...}]` are all in use.
    """
    if isinstance(value, dict):
        yield from declared_names(value)
        return
    for entry in as_list(value):
        if isinstance(entry, str) and entry.strip():
            yield entry.strip()
        elif isinstance(entry, dict):
            source = entry.get("source")
            if isinstance(source, str) and source.strip():
                yield source.strip()


def depends_on_names(value: Any) -> Iterator[str]:  # noqa: ANN401
    """Yield the services a `depends_on:` or a `links:` names."""
    if isinstance(value, dict):
        yield from declared_names(value)
        return
    for entry in as_list(value):
        if isinstance(entry, str) and entry.strip():
            # `links:` allows `service:alias`; the alias is not a service.
            yield entry.split(":", 1)[0].strip()


def volume_entry(entry: Any) -> tuple[str, str]:  # noqa: ANN401
    """Return the (source, kind) of one service volume entry.

    The kind is what compose itself decides it by: a source starting with a
    dot, a slash or a tilde is a host path, anything else is a named volume.
    """
    if isinstance(entry, dict):
        source = entry.get("source")
        source = source.strip() if isinstance(source, str) else ""
        declared = entry.get("type")
    elif isinstance(entry, str):
        source = entry.split(":", 1)[0].strip()
        declared = None
    else:
        return "", ""
    if not source:
        return "", ""
    if isinstance(declared, str):
        return source, declared.strip()
    return source, "bind" if source.startswith((".", "/", "~")) else "volume"


def mounted_file(source: str) -> str:
    """Return the host path of a bind mount when it can name a file node.

    Only a path relative to the compose file can be one: an absolute mount
    (`/var/run/docker.sock`) is outside the tree by definition. A source
    without an extension is a directory, and only files get nodes - emitting
    the edge anyway would leave a placeholder pointing at nothing.
    """
    if not source.startswith(("./", "../")) or is_interpolated(source):
        return ""
    return source if posixpath.splitext(source)[1] else ""


def build_dockerfile(value: Any) -> str:  # noqa: ANN401
    """Return the Dockerfile a `build:` section names, relative to the file."""
    if isinstance(value, str):
        context, dockerfile = value.strip(), DEFAULT_DOCKERFILE
    elif isinstance(value, dict):
        context = value.get("context")
        context = context.strip() if isinstance(context, str) else "."
        named = value.get("dockerfile")
        dockerfile = named.strip() if isinstance(named, str) else DEFAULT_DOCKERFILE
    else:
        return ""
    if not context or not dockerfile:
        return ""
    # A git URL builds somewhere that is not this tree.
    if any(marker in context for marker in REMOTE_CONTEXT_MARKERS):
        return ""
    return posixpath.join(context, dockerfile)


def env_file_paths(value: Any) -> Iterator[str]:  # noqa: ANN401
    """Yield the paths an `env_file:` names, in any of its forms."""
    for entry in as_list(value):
        if isinstance(entry, str):
            yield entry.strip()
        elif isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str):
                yield path.strip()


def extends_relations(value: Any) -> Iterator[tuple[str, str, str]]:  # noqa: ANN401
    """Yield the (target, type, scope) triples an `extends:` declares."""
    if isinstance(value, str):
        service, file_path = value.strip(), ""
    elif isinstance(value, dict):
        named = value.get("service")
        service = named.strip() if isinstance(named, str) else ""
        named = value.get("file")
        file_path = named.strip() if isinstance(named, str) else ""
    else:
        return
    if file_path:
        yield file_path, "includes", "file"
    elif service:
        # Only a same-file `extends` resolves to a node; across files the
        # `includes` edge above already says where to look.
        yield qualified("service", service), "extends", "symbol"


def file_targets(section: Any) -> Iterator[str]:  # noqa: ANN401
    """Yield the `file:` of every entry of a top level configs or secrets."""
    if not isinstance(section, dict):
        return
    for body in section.values():
        if isinstance(body, dict):
            path = body.get("file")
            if isinstance(path, str) and path.strip():
                yield path.strip()


class ComposeParser(YAMLParser):
    """Docker Compose aware YAML parser."""

    FAMILY = "compose"

    def get_entities(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Extract the stack, its services, volumes, networks and secrets."""
        document = compose_document(content)
        if document is None:
            return super().get_entities(content, rel_path)

        entities: list[dict[str, str]] = []
        seen: set[str] = set()
        name = document.get("name")
        if isinstance(name, str) and name.strip() and not is_interpolated(name):
            entities.append({"name": qualified("stack", name.strip()), "type": "stack"})
            seen.add(entities[0]["name"])
        for section, entity_type in COMPOSE_SECTIONS.items():
            for declared in declared_names(document.get(section)):
                node_name = qualified(entity_type, declared)
                if node_name in seen:
                    continue
                seen.add(node_name)
                entities.append({"name": node_name, "type": entity_type})
        return entities

    def get_relations(self, content: str, rel_path: str) -> list[dict[str, str]]:
        """Extract what the stack includes and what each service refers to."""
        document = compose_document(content)
        if document is None:
            return super().get_relations(content, rel_path)

        relations: list[dict[str, str]] = []
        for target in as_list(document.get("include")):
            path = self._include_path(target)
            if path:
                relations.append(self._relation("", path, "includes", "file"))
        for section in ("configs", "secrets"):
            relations.extend(
                self._relation("", path, "uses_file", "file")
                for path in file_targets(document.get(section))
            )
        services = document["services"]
        # The same filter `get_entities` declares nodes through: a relation
        # leaving an `x-` extension key would have no node to leave.
        for key, name in declared_items(services):
            source = qualified("service", name)
            relations.extend(self._service_relations(source, services[key]))
        return self._deduplicate(relations)

    @staticmethod
    def _include_path(entry: Any) -> str:  # noqa: ANN401
        """Return the path of one top level `include:` entry."""
        if isinstance(entry, str):
            return entry.strip()
        if isinstance(entry, dict):
            for path in as_list(entry.get("path")):
                if isinstance(path, str) and path.strip():
                    return path.strip()
        return ""

    @staticmethod
    def _relation(source: str, target: str, kind: str, scope: str) -> dict[str, str]:
        """Build one relation, with the service it leaves when there is one.

        A path is normalized here rather than left to resolution: when nothing
        in the tree answers it, `./nginx.conf` and `nginx.conf` would become
        two external nodes standing for one file.
        """
        if scope == "file":
            target = posixpath.normpath(target)
        relation = {"target": target, "type": kind, "scope": scope}
        if source:
            relation["source"] = source
        return relation

    def _service_relations(
        self,
        source: str,
        service: Any,  # noqa: ANN401
    ) -> Iterator[dict[str, str]]:
        """Yield the relations one service declares."""
        if not isinstance(service, dict):
            return
        image = service.get("image")
        if isinstance(image, str) and image.strip() and not is_interpolated(image):
            yield self._relation(
                source, f"{IMAGE_PREFIX}{image.strip()}", "uses_image", "symbol"
            )

        dockerfile = build_dockerfile(service.get("build"))
        if dockerfile and not is_interpolated(dockerfile):
            yield self._relation(source, dockerfile, "builds", "file")

        for key in ("depends_on", "links"):
            for name in depends_on_names(service.get(key)):
                if name and not is_interpolated(name):
                    yield self._relation(
                        source, qualified("service", name), "depends_on", "symbol"
                    )

        for key, (kind, entity_type) in COMPOSE_SERVICE_REFERENCES.items():
            for name in referenced_names(service.get(key)):
                if not is_interpolated(name):
                    yield self._relation(
                        source, qualified(entity_type, name), kind, "symbol"
                    )

        for entry in as_list(service.get("volumes")):
            volume_source, kind = volume_entry(entry)
            if kind == "volume" and not is_interpolated(volume_source):
                yield self._relation(
                    source, qualified("volume", volume_source), "uses_volume", "symbol"
                )
            elif kind == "bind":
                path = mounted_file(volume_source)
                if path:
                    yield self._relation(source, path, "mounts", "file")

        for path in env_file_paths(service.get("env_file")):
            if path and not is_interpolated(path):
                yield self._relation(source, path, "reads_vars", "file")

        for target, kind, scope in extends_relations(service.get("extends")):
            if not is_interpolated(target):
                yield self._relation(source, target, kind, scope)

    @staticmethod
    def _deduplicate(relations: list[dict[str, str]]) -> list[dict[str, str]]:
        """Drop the repeats, keeping the service each relation leaves."""
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, str]] = []
        for relation in relations:
            key = (relation.get("source", ""), relation["target"], relation["type"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(relation)
        return unique


@cache
def compose_parser() -> ComposeParser:
    """Return the shared parser, for a file this module did not get by name.

    A compose file may be called anything, so `AnsibleParser` hands one over
    when the document it loaded turns out to be compose after all.
    """
    return ComposeParser()
