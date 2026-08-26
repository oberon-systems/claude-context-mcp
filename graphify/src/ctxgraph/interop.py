"""Bridge between the graphifyy extractor and the graph stored in PostgreSQL.

graphifyy is used as a library, not as a command. Its CLI only installs a
skill; the pipeline the skill drives is a handful of functions over a networkx
graph, which is what makes it usable from here at all.

Two directions cross this module:

    extraction dict  ->  graph_nodes / graph_edges      (import_extraction)
    graph_nodes / graph_edges  ->  networkx.Graph       (db_to_graph)

The second one is what lets everything graphifyy builds on top of a graph -
its HTML view, its clustering, its own stdio MCP server - read our database
instead of the file it normally expects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import shutil
from collections import Counter
from pathlib import Path

import networkx as nx
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    GRAPHIFY_OUT_DIR,
    MAX_NODE_ID_LENGTH,
    SOURCE_GRAPHIFYY,
)
from ctxgraph.identifiers import truncate
from ctxgraph.storage import (
    ensure_external_node,
    insert_edge,
    iter_edges,
    iter_nodes,
    store_communities,
    upsert_extracted_node,
    upsert_file_node,
)
from ctxgraph.summaries import extract_summary
from ctxgraph.summarizer import Summarizer
from graphify import cache as extractor_cache
from graphify import extract as extractor_extract
from graphify.cluster import cluster
from graphify.export import to_html, to_json

LOG = logging.getLogger(__name__)

# graphifyy names a node after its label alone, so `index` stands for 475
# different files in a tree with a node_modules in it. Only the file and the
# line together tell two of them apart.
_KEY_TEMPLATE = "{path}::{label}@{location}"


def cache_root(project: str) -> Path:
    """Return the extractor cache directory of one project."""
    return Path(GRAPHIFY_OUT_DIR) / "cache" / project


def install_extractor_cache(project: str, fresh: bool = False) -> int:
    """Scope the extractor's own cache to one project. Returns entries cleared.

    Two things are wrong with the cache as shipped, and both have to be fixed
    from here because it has no options.

    It is written next to the common parent of the paths it was handed, which
    for us is inside the project mount - read only by contract, so the write
    fails and takes the extraction with it. That is what `cache_dir` is
    redirected for, and it is what makes a re-index skip unchanged files.

    Worse, an entry is keyed by file content alone, and a hit is returned
    verbatim - carrying the `source_file` of whichever file was extracted
    first. Every tree is mounted at the same path, so two codebases cannot even
    be told apart: one empty `__init__.py` answers for every empty
    `__init__.py` ever indexed, under a path that is not in the tree being
    indexed, and the file it stood in for gets no node at all. The key here is
    the path and the content together, under a directory of the project's own,
    so neither collision can happen.

    This reaches into the extractor rather than going through an option because
    it has none. The version is pinned, and the failure modes if the internals
    move are loud: the write goes back to the read-only mount, and an
    unpatched `extract` module reports a gap for every file at the end of the
    run. The lookups are replaced on `graphify.extract` rather than on
    `graphify.cache`, because that module binds them by value at import time.
    """
    root = cache_root(project)
    root.mkdir(parents=True, exist_ok=True)
    extractor_cache.cache_dir = lambda _root=None: root

    def entry_for(path: Path) -> Path:
        digest = hashlib.sha256(f"{path}\0".encode())
        digest.update(path.read_bytes())
        return root / f"{digest.hexdigest()}.json"

    def load_cached(path: Path, _root: Path | None = None) -> dict | None:
        try:
            entry = entry_for(Path(path))
            return json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def save_cached(path: Path, result: dict, _root: Path | None = None) -> None:
        try:
            entry_for(Path(path)).write_text(json.dumps(result))
        except OSError:
            LOG.warning("Failed to cache the extraction of %s", path)

    extractor_extract.load_cached = load_cached
    extractor_extract.save_cached = save_cached

    if not fresh:
        return 0
    cleared = len(list(root.glob("*.json")))
    extractor_cache.clear_cache()
    return cleared


def prune_extractor_caches(known: set[str]) -> tuple[int, int]:
    """Drop cached extractions no project owns. Returns projects, entries.

    A project can leave the database through the dashboard or through the
    `drop_project` tool, and neither can reach this volume: the tool runs in
    another container, and the script talks to postgres only. So the cache is
    collected here instead, against the projects that still exist.

    The loose entries are the ones written before the cache was scoped, when
    one directory held every project at once. Nothing can read them now, and
    they are the entries that answered for the wrong tree.
    """
    root = Path(GRAPHIFY_OUT_DIR) / "cache"
    if not root.is_dir():
        return 0, 0

    entries = 0
    for stale in root.glob("*.json"):
        stale.unlink(missing_ok=True)
        entries += 1

    projects = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in known:
            continue
        entries += len(list(child.glob("*.json")))
        shutil.rmtree(child, ignore_errors=True)
        projects += 1
    return projects, entries


def node_type(label: str, source_file: str | None) -> str:
    """Classify a graphifyy node, which carries no kind of its own.

    The extraction only reports a label and where it was found, so the kind
    has to be read off the label. `foo()` is a function, `.foo()` is a method
    reached through an instance, a capitalised bare name is a class, and a
    label equal to the file name is the file itself.
    """
    if source_file and label == posixpath.basename(source_file):
        return "file"
    if label.startswith(".") and label.endswith("()"):
        return "method"
    if label.endswith("()"):
        return "function"
    if label[:1].isupper():
        return "class"
    return "entity"


def node_id(node: dict[str, str]) -> str:
    """Build an id unique across the tree for one graphifyy node."""
    source_file = node.get("source_file") or ""
    label = node.get("label") or node.get("id", "")
    if not source_file:
        return truncate(label, MAX_NODE_ID_LENGTH)
    if label == posixpath.basename(source_file):
        # File nodes share our own convention so an edge from an Ansible
        # playbook and an edge from a Python import land on the same node.
        return truncate(source_file, MAX_NODE_ID_LENGTH)
    return truncate(
        _KEY_TEMPLATE.format(
            path=source_file,
            label=label,
            location=node.get("source_location", ""),
        ),
        MAX_NODE_ID_LENGTH,
    )


def import_extraction(
    cursor: Cursor,
    project: str,
    extraction: dict[str, list[dict[str, str]]],
    contents: dict[str, str],
    summarizer: Summarizer | None = None,
) -> tuple[int, int, set[str]]:
    """Store one graphifyy extraction.

    Returns the nodes written, the edges written, and the paths a file node was
    written for - which is what tells a run whose extraction came back short
    from a clean one.

    `contents` maps a project relative path to the head of that file, which is
    what the summary is written from: graphifyy writes no summary of its own,
    and a graph of bare labels sends the agent back to opening files one by
    one. The head, not the whole file - it is what bounds both the memory of
    this pass and the prompt the model is given.
    """
    nodes = extraction.get("nodes", [])
    edges = extraction.get("edges", [])

    entities_by_file: dict[str, list[dict[str, str]]] = {}
    for node in nodes:
        source_file = node.get("source_file")
        label = node.get("label", "")
        if not source_file or label == posixpath.basename(source_file):
            continue
        entities_by_file.setdefault(source_file, []).append(
            {"name": label, "type": node_type(label, source_file)}
        )

    # First pass: every node, and the map from their ids to ours. Their ids
    # repeat, so the first one wins and the rest are counted rather than
    # allowed to silently retarget an edge.
    id_map: dict[str, str] = {}
    collisions = 0
    written = 0
    files: set[str] = set()
    for node in nodes:
        their_id = node.get("id", "")
        our_id = node_id(node)
        if their_id in id_map:
            collisions += 1
        else:
            id_map[their_id] = our_id

        label = node.get("label", their_id)
        source_file = node.get("source_file")
        kind = node_type(label, source_file)

        if kind == "file" and source_file:
            summary = extract_summary(
                source_file,
                contents.get(source_file, ""),
                entities_by_file.get(source_file, []),
            )
            upsert_file_node(
                cursor,
                project,
                source_file,
                summary,
                source=SOURCE_GRAPHIFYY,
            )
            if summarizer is not None:
                summarizer.refine(
                    cursor, project, source_file, contents.get(source_file, "")
                )
            files.add(source_file)
        else:
            upsert_extracted_node(
                cursor,
                project,
                our_id,
                label,
                kind,
                source_file,
                "",
                {
                    "source": SOURCE_GRAPHIFYY,
                    "graphifyy_id": their_id,
                    "source_location": node.get("source_location", ""),
                },
            )
        written += 1

    if collisions:
        LOG.info("graphifyy reused %d node ids across files", collisions)

    # Second pass: edges. A target with no node of its own is something
    # outside the tree, which is the same placeholder our own resolver makes.
    linked = 0
    for edge in edges:
        source_id = id_map.get(edge.get("source", ""))
        if source_id is None:
            continue
        their_target = edge.get("target", "")
        target_id = id_map.get(their_target)
        if target_id is None:
            target_id = truncate(their_target, MAX_NODE_ID_LENGTH)
            ensure_external_node(cursor, project, target_id, "external_import")
        if target_id == source_id:
            continue
        insert_edge(
            cursor,
            project,
            source_id,
            target_id,
            edge.get("relation", "uses"),
            {
                "source": SOURCE_GRAPHIFYY,
                "confidence": edge.get("confidence", "EXTRACTED"),
                "weight": edge.get("weight", 1.0),
            },
        )
        linked += 1

    return written, linked, files


def db_to_graph(cursor: Cursor, project: str) -> nx.Graph:
    """Rebuild the whole graph in memory, in the shape graphifyy expects.

    Node attributes follow its own export: `label`, `source_file`,
    `community`. `summary` is ours and has no counterpart there, which is the
    point - it rides along into its HTML view and its MCP tools.
    """
    graph = nx.Graph()
    for node_key, name, kind, file_path, summary, community in iter_nodes(
        cursor, project
    ):
        attrs: dict[str, object] = {
            "label": name,
            "kind": kind,
            "source_file": file_path or "",
            "summary": summary or "",
        }
        if community:
            attrs["community"] = int(community)
        graph.add_node(node_key, **attrs)

    for source_id, target_id, relation, confidence in iter_edges(cursor, project):
        if source_id is None or target_id is None:
            continue
        graph.add_edge(
            source_id,
            target_id,
            relation=relation,
            confidence=confidence,
        )
    return graph


def communities_of(graph: nx.Graph) -> dict[int, list[str]]:
    """Group the nodes of a graph by the community stored on them."""
    grouped: dict[int, list[str]] = {}
    for node_key, data in graph.nodes(data=True):
        community_id = data.get("community")
        if community_id is not None:
            grouped.setdefault(int(community_id), []).append(node_key)
    return grouped


def recluster(cursor: Cursor, project: str) -> int:
    """Cluster the merged graph and store the result on the nodes."""
    graph = db_to_graph(cursor, project)
    if graph.number_of_nodes() == 0:
        return 0
    grouped = cluster(graph)
    sizes = Counter(len(members) for members in grouped.values())
    LOG.info("Clustered into %d communities (sizes %s)", len(grouped), dict(sizes))
    return store_communities(cursor, project, grouped)


def annotate_for_graphifyy(graph: nx.Graph) -> nx.Graph:
    """Fold our own attributes into the two fields graphifyy renders.

    Neither its page nor its `get_node` tool shows an attribute it was not
    written to expect: both build a fixed block of label, type, source and
    degree. A summary therefore has nowhere of its own to arrive in, so it is
    appended to the source line, and our node type is copied into the
    `file_type` field its `Type:` line reads. The searchable side effect is
    deliberate - its `query_graph` scores on the source string, so summary
    words become searchable there too.
    """
    annotated = graph.copy()
    for _, data in annotated.nodes(data=True):
        data["file_type"] = data.get("kind", "")
        summary = data.get("summary")
        if summary:
            source_file = data.get("source_file") or ""
            separator = " - " if source_file else ""
            data["source_file"] = f"{source_file}{separator}{summary}"
    return annotated


def community_labels(graph: nx.Graph, grouped: dict[int, list[str]]) -> dict[int, str]:
    """Name each community after its most connected member.

    The legend on the rendered page is built from the labels it is given and
    is empty without them. Upstream fills them in by asking a model; naming a
    community after the node everything in it hangs off is free and says
    roughly the same thing. Communities of one are left out: there are more of
    them than of everything else, and a legend listing each would bury the
    groups worth seeing.
    """
    labels: dict[int, str] = {}
    for community_id, members in grouped.items():
        if len(members) < 2:
            continue
        hub = max(members, key=lambda node_key: graph.degree(node_key))
        labels[community_id] = str(graph.nodes[hub].get("label", hub))
    return labels


def materialize_graph_json(cursor: Cursor, project: str, output_path: str) -> None:
    """Write the database out as the graph.json graphifyy tools read."""
    graph = annotate_for_graphifyy(db_to_graph(cursor, project))
    to_json(graph, communities_of(graph), output_path)


def render_html(cursor: Cursor, project: str, output_path: str) -> None:
    """Render the database as the interactive graph page."""
    graph = annotate_for_graphifyy(db_to_graph(cursor, project))
    grouped = communities_of(graph)
    to_html(
        graph,
        grouped,
        output_path,
        community_labels=community_labels(graph, grouped),
    )
