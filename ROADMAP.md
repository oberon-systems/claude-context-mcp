# Roadmap

Current state: the stack builds, starts, indexes a mounted codebase into a
file/import graph, and serves that graph to Claude CLI over SSE. What follows is
deliberately deferred work, in the order it should be picked up.

## 1. Tree-sitter AST parsing [COMPLETED]

`graphify/process_graph.py` now uses tree-sitter for TS, PY, GO, RS, SH, MD, MAKE, DOCKERFILE, HCL, YAML, TOML.

- emit `function`, `class`, `method` nodes
- emit `calls`, `inherits` edges
- resolve relative import targets to file nodes

## 2. Embedding generation

`code_embeddings` exists with a `vector(1536)` column and an HNSW cosine index,
but nothing writes to it.

Open decision: a local model (`fastembed` or `sentence-transformers`, no key
needed, large image) versus an API provider (matches 1536 dimensions directly,
needs a key and network egress from the container). Whichever is chosen, chunk
on the AST node boundaries from step 1 rather than on fixed line windows.

## 3. Semantic search MCP tool

Once embeddings exist, add `search_code_semantic` to
`mcp-server/src/index.ts`, querying `embedding <=> $1` against the HNSW index
and returning the owning nodes. The tool is not worth adding before then, since
it would return nothing.

## 4. Incremental indexing

`make index` rewrites every node on every run. Track file content hashes in
`graph_nodes.metadata` and skip unchanged files, and delete nodes for files that
disappeared from the tree.

## 5. Use public registry for built images

User have to build images each time. Better way to pull images from some
public storage instead of.

## 6. Multi-projects support

In some cases we have to use multiple projects in context, e.g. code base, CI part and
infrastructure deps, but now we could index only one project. Better way use
coma separated project path and index all of them.

## 7. Git hook for re-indexing

Implement git-hook for run re-indexing for each commit.

## 8. Use some CPU-Driven local model for generate summary

We use dump analyst, better to use some local model
just for this, for reduce token usage.

Model should be part of compose deployment.
