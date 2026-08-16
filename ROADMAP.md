# Roadmap

Current state: the stack builds, starts, indexes a mounted codebase into a
file/import graph, and serves that graph to Claude CLI over SSE. What follows is
deliberately deferred work, in the order it should be picked up.

## 1. Tree-sitter AST parsing [COMPLETED]

The `ctxgraph` package (`graphify/src/ctxgraph/`) uses tree-sitter for SH, MD,
MAKE, DOCKERFILE, HCL/TF, YAML, TOML. Programming languages moved to the
upstream graphifyy extractor, which covers more of them and tags every edge
with a confidence; the parsers for TS, TSX, JS, PY, GO, RS are kept as a
fallback but are no longer reached. The split is `GRAPHIFYY_EXTENSIONS` in
`config.py`.

- emits `function`, `method`, `class` and per-language entity nodes, scoped to
  their file as `path::name`
- emits `calls`, `inherits`, `imports` edges, resolved against the entities of
  the same language family in a second pass
- resolves relative import targets to file nodes, and everything else to
  `external_import` / `external_symbol` placeholders
- reads Ansible YAML by its values rather than its syntax: plays, tasks,
  handlers and role variables, plus `includes`, `uses_role`, `depends_on`,
  `reads_vars`, `uses_template`, `uses_file` and `notifies` edges

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

## 4. Incremental indexing [COMPLETED]

`make index` rewrites every node on every run. A re-index now clears what it
previously derived from each file it visits, but a file deleted from the tree is
never visited again and its nodes stay. Track file content hashes in
`graph_nodes.metadata` to skip unchanged files, and prune nodes whose file is
gone.

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

## 9. Implement MCP support for Gemini [COMPLETED]

`.gemini/settings.json` registers both servers, and the skill in `skills/` is
registered for Claude and Gemini by `make skill-install`. The transport moved
from SSE to Streamable HTTP (`/mcp`) along the way; `/sse` is still served for
older clients.

## 10. Incremental extraction for the code half

The Tree-sitter parsers skip a file whose hash is unchanged. The graphifyy pass
does not: it re-extracts the whole code corpus on every run, because an
extraction over a subset loses the edges between the files left out. Its own
per-file cache absorbs most of the cost, but the merge into the database is
still full. Worth revisiting with `graphify.detect.detect_incremental` once the
graph is large enough for it to matter.
