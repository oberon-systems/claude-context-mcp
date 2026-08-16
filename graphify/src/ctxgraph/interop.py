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

import logging
import posixpath
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
from graphify import cache as extractor_cache
from graphify.cluster import cluster
from graphify.export import to_html, to_json

LOG = logging.getLogger(__name__)

# graphifyy names a node after its label alone, so `index` stands for 475
# different files in a tree with a node_modules in it. Only the file and the
# line together tell two of them apart.
_KEY_TEMPLATE = "{path}::{label}@{location}"


def redirect_extractor_cache() -> None:
    """Keep the extractor's own cache out of the mounted project.

    It caches a parsed file under a `graphify-out/cache` directory next to the
    common parent of the paths it was handed, which for us is inside the
    project mount - and that mount is read only by contract, so the write
    fails and takes the extraction with it. Pointing the directory at our own
    writable one keeps the cache working, which is what makes a re-index skip
    files that did not change.

    This reaches into the extractor rather than going through an option
    because it has none. The version is pinned, and the failure mode if the
    internals move is loud: the write goes back to the read-only mount.
    """
    target = Path(GRAPHIFY_OUT_DIR) / "cache"
    target.mkdir(parents=True, exist_ok=True)
    extractor_cache.cache_dir = lambda root=None: target


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
    extraction: dict[str, list[dict[str, str]]],
    contents: dict[str, str],
) -> tuple[int, int]:
    """Store one graphifyy extraction. Returns the nodes and edges written.

    `contents` maps a project relative path to the text of that file, which is
    what `extract_summary` needs: graphifyy writes no summary of its own, and
    a graph of bare labels sends the agent back to opening files one by one.
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
            upsert_file_node(cursor, source_file, summary, source=SOURCE_GRAPHIFYY)
        else:
            upsert_extracted_node(
                cursor,
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
            ensure_external_node(cursor, target_id, "external_import")
        if target_id == source_id:
            continue
        insert_edge(
            cursor,
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

    return written, linked


def db_to_graph(cursor: Cursor) -> nx.Graph:
    """Rebuild the whole graph in memory, in the shape graphifyy expects.

    Node attributes follow its own export: `label`, `source_file`,
    `community`. `summary` is ours and has no counterpart there, which is the
    point - it rides along into its HTML view and its MCP tools.
    """
    graph = nx.Graph()
    for node_key, name, kind, file_path, summary, community in iter_nodes(cursor):
        attrs: dict[str, object] = {
            "label": name,
            "kind": kind,
            "source_file": file_path or "",
            "summary": summary or "",
        }
        if community:
            attrs["community"] = int(community)
        graph.add_node(node_key, **attrs)

    for source_id, target_id, relation, confidence in iter_edges(cursor):
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


def recluster(cursor: Cursor) -> int:
    """Cluster the merged graph and store the result on the nodes."""
    graph = db_to_graph(cursor)
    if graph.number_of_nodes() == 0:
        return 0
    grouped = cluster(graph)
    sizes = Counter(len(members) for members in grouped.values())
    LOG.info("Clustered into %d communities (sizes %s)", len(grouped), dict(sizes))
    return store_communities(cursor, grouped)


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


def materialize_graph_json(cursor: Cursor, output_path: str) -> None:
    """Write the database out as the graph.json graphifyy tools read."""
    graph = annotate_for_graphifyy(db_to_graph(cursor))
    to_json(graph, communities_of(graph), output_path)


def render_html(cursor: Cursor, output_path: str) -> None:
    """Render the database as the interactive graph page."""
    graph = annotate_for_graphifyy(db_to_graph(cursor))
    grouped = communities_of(graph)
    to_html(
        graph,
        grouped,
        output_path,
        community_labels=community_labels(graph, grouped),
    )
