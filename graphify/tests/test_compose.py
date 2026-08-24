"""What a Docker Compose file contributes to the graph."""

from __future__ import annotations

import pytest

from ctxgraph.parsers.ansible import AnsibleParser
from ctxgraph.parsers.compose import (
    ComposeParser,
    build_dockerfile,
    is_compose_name,
    looks_like_compose,
    mounted_file,
    volume_entry,
)
from ctxgraph.parsers.registry import parser_class
from ctxgraph.parsers.yamldocs import load_yaml_documents

COMPOSE = """
name: demo

x-shared: &shared
  restart: unless-stopped

services:
  db:
    <<: *shared
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ${HOME}/backups:/backups
    networks: [base]

  api:
    build:
      context: ./api
      dockerfile: Dockerfile.dev
    depends_on:
      db:
        condition: service_healthy
    env_file: ./api/.env.example
    networks:
      base:
        aliases: [api]
    volumes:
      - ./api/settings.toml:/app/settings.toml:ro
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock

  proxy:
    image: ${REGISTRY}/proxy:latest
    build: ./proxy
    depends_on: [api]
    links:
      - db:database
    configs:
      - source: site
        target: /etc/nginx/site.conf
    secrets: [token]

volumes:
  pgdata:

networks:
  base:

configs:
  site:
    file: ./proxy/site.conf

secrets:
  token:
    file: ./secrets/token.txt
"""

PLAYBOOK = """
- hosts: web
  roles:
    - nginx
  tasks:
    - name: Ship the config
      template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
"""

WORKFLOW = """
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
    steps:
      - uses: actions/checkout@v4
"""

GITLAB_CI = """
test:
  image: python:3.11
  services:
    - postgres:16
  script:
    - pytest
"""

PATH = "docker-compose.yaml"


@pytest.fixture(scope="module")
def parser() -> ComposeParser:
    """Build the parser under test, once for the whole module."""
    return ComposeParser()


def named(entities: list[dict[str, str]]) -> set[tuple[str, str]]:
    """Reduce entities to the (name, type) pairs worth asserting on."""
    return {(entity["name"], entity["type"]) for entity in entities}


def edges(relations: list[dict[str, str]]) -> set[tuple[str, str, str]]:
    """Reduce relations to (source, type, target), the file being ""."""
    return {
        (relation.get("source", ""), relation["type"], relation["target"])
        for relation in relations
    }


def test_entities_are_qualified_by_kind(parser: ComposeParser) -> None:
    """Every declaration becomes a node named by its kind and its name."""
    assert named(parser.get_entities(COMPOSE, PATH)) == {
        ("stack.demo", "stack"),
        ("service.db", "service"),
        ("service.api", "service"),
        ("service.proxy", "service"),
        ("volume.pgdata", "volume"),
        ("network.base", "network"),
        ("config.site", "config"),
        ("secret.token", "secret"),
    }


def test_extension_fields_declare_nothing(parser: ComposeParser) -> None:
    """`x-shared` is an extension field, not a service."""
    names = {entity["name"] for entity in parser.get_entities(COMPOSE, PATH)}
    assert not any(name.endswith("x-shared") for name in names)


X_SERVICE = """
services:
  x-defaults:
    image: alpine:3
    restart: always
  web:
    image: nginx:1.27
    depends_on: [x-defaults]
"""


def test_extension_key_under_services_leaves_no_edge(parser: ComposeParser) -> None:
    """An `x-` block under `services:` is neither a node nor an edge source.

    Both halves have to agree: an edge leaving a node `get_entities` refused
    to declare has nothing to hang on, and the database refuses it.
    """
    declared = {entity["name"] for entity in parser.get_entities(X_SERVICE, PATH)}
    assert declared == {"service.web"}
    sources = {
        relation["source"]
        for relation in parser.get_relations(X_SERVICE, PATH)
        if relation.get("source")
    }
    assert sources <= declared


def test_dependencies_leave_the_service(parser: ComposeParser) -> None:
    """depends_on and links both point one service at another."""
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("service.api", "depends_on", "service.db") in found
    assert ("service.proxy", "depends_on", "service.api") in found
    # `links: [db:database]` names the service, not the alias.
    assert ("service.proxy", "depends_on", "service.db") in found


def test_images_are_prefixed_and_interpolation_dropped(
    parser: ComposeParser,
) -> None:
    """A static image becomes one external node; a templated one becomes none."""
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("service.db", "uses_image", "image:postgres:16") in found
    assert not [edge for edge in found if edge[1] == "uses_image" and "$" in edge[2]]


def test_build_resolves_to_a_dockerfile(parser: ComposeParser) -> None:
    """Both forms of `build:` name the file the image is built from."""
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("service.api", "builds", "api/Dockerfile.dev") in found
    assert ("service.proxy", "builds", "proxy/Dockerfile") in found


def test_volumes_split_into_mounts_and_named_volumes(parser: ComposeParser) -> None:
    """A named volume is a node of the file; a bind mount is a file of the tree.

    The mounted path is normalized, so it names the same node an indexed file
    would.
    """
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("service.db", "uses_volume", "volume.pgdata") in found
    assert ("service.api", "mounts", "api/settings.toml") in found
    # A directory has no file node, an absolute path is outside the tree, and
    # an interpolated one points at nothing static.
    mounted = {edge[2] for edge in found if edge[1] == "mounts"}
    assert mounted == {"api/settings.toml"}


def test_networks_configs_secrets_and_env_files(parser: ComposeParser) -> None:
    """The remaining service references, in both the list and the map form."""
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("service.db", "uses_network", "network.base") in found
    assert ("service.api", "uses_network", "network.base") in found
    assert ("service.proxy", "uses_config", "config.site") in found
    assert ("service.proxy", "uses_secret", "secret.token") in found
    assert ("service.api", "reads_vars", "api/.env.example") in found


def test_top_level_configs_and_secrets_name_files(parser: ComposeParser) -> None:
    """A `file:` under configs or secrets is a file of the tree, not a service."""
    found = edges(parser.get_relations(COMPOSE, PATH))
    assert ("", "uses_file", "proxy/site.conf") in found
    assert ("", "uses_file", "secrets/token.txt") in found


def test_non_compose_yaml_falls_back_to_top_level_keys(
    parser: ComposeParser,
) -> None:
    """A file named like compose but shaped otherwise stays plain YAML."""
    entities = parser.get_entities(WORKFLOW, "compose.yaml")
    assert {entity["name"] for entity in entities} == {"name", "on", "jobs"}
    assert parser.get_relations(WORKFLOW, "compose.yaml") == []


@pytest.mark.parametrize("content", [PLAYBOOK, WORKFLOW, GITLAB_CI])
def test_other_yaml_does_not_sniff_as_compose(content: str) -> None:
    """`services:` alone is not compose: a workflow and a CI config have one."""
    documents = load_yaml_documents(content)
    assert documents is not None
    assert not looks_like_compose(documents)


def test_compose_sniffs_by_shape() -> None:
    """A compose file under any other name is still recognised."""
    documents = load_yaml_documents(COMPOSE)
    assert documents is not None
    assert looks_like_compose(documents)


def test_ansible_parser_hands_over_a_compose_file() -> None:
    """`stack.yml` reaches AnsibleParser and comes back read as compose."""
    entities = AnsibleParser().get_entities(COMPOSE, "deploy/stack.yml")
    assert ("service.db", "service") in named(entities)


def test_ansible_parsing_is_unchanged() -> None:
    """The handover leaves a playbook alone."""
    entities = AnsibleParser().get_entities(PLAYBOOK, "site.yml")
    assert ("Ship the config", "task") in named(entities)


@pytest.mark.parametrize(
    "file_name",
    [
        "compose.yml",
        "compose.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.prod.yml",
        "compose.override.yaml",
        "deploy/docker-compose.ci.yaml",
    ],
)
def test_registry_routes_compose_names(file_name: str) -> None:
    """Every name compose answers to reaches the compose parser."""
    assert is_compose_name(file_name)
    assert parser_class(file_name) is ComposeParser


@pytest.mark.parametrize("file_name", ["stack.yml", "site.yaml", "composer.json"])
def test_registry_leaves_other_names_alone(file_name: str) -> None:
    """A name that is not compose keeps the parser it had."""
    assert not is_compose_name(file_name)
    assert parser_class(file_name) is not ComposeParser


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("pgdata:/data", ("pgdata", "volume")),
        ("./conf/nginx.conf:/etc/nginx.conf:ro", ("./conf/nginx.conf", "bind")),
        ("/var/run/docker.sock:/var/run/docker.sock", ("/var/run/docker.sock", "bind")),
        ({"type": "bind", "source": "./data", "target": "/data"}, ("./data", "bind")),
        (
            {"type": "volume", "source": "pgdata", "target": "/data"},
            ("pgdata", "volume"),
        ),
        ({"type": "tmpfs", "target": "/tmp"}, ("", "")),
    ],
)
def test_volume_entry_forms(entry: object, expected: tuple[str, str]) -> None:
    """Short and long form volume entries both yield a source and a kind."""
    assert volume_entry(entry) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("./nginx/nginx.conf", "./nginx/nginx.conf"),
        ("../shared/app.ini", "../shared/app.ini"),
        ("./migrations", ""),
        ("/etc/localtime", ""),
        ("${HOME}/db", ""),
    ],
)
def test_mounted_file_keeps_only_files_of_the_tree(source: str, expected: str) -> None:
    """Only a relative path naming a file can resolve to a file node."""
    assert mounted_file(source) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("./app", "./app/Dockerfile"),
        ({"context": "./app"}, "./app/Dockerfile"),
        ({"context": "./app", "dockerfile": "Dockerfile.dev"}, "./app/Dockerfile.dev"),
        ({"dockerfile": "Dockerfile.dev"}, "./Dockerfile.dev"),
        ({"context": "https://github.com/example/repo.git"}, ""),
        (None, ""),
    ],
)
def test_build_dockerfile_forms(value: object, expected: str) -> None:
    """A build section names a Dockerfile, or nothing this tree holds."""
    assert build_dockerfile(value) == expected
