"""Build a coarse code graph from a mounted codebase and store it in PostgreSQL.

The package is laid out along the pipeline it runs: `discovery` decides what to
read, `parsers` turns a file into entities and references, `summaries` describes
it in one line, `resolution` points those references at real nodes, `storage`
writes them, and `indexer` drives the two passes.
"""
