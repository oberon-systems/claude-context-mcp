## v0.9.0 (2026-08-16)

### Features

- **multi-project**: hold every indexed codebase in one database

### Bug Fixes

- **compose**: put the network comment above the block it explains
- **skills**: install the skill into the root the agent actually reads
- **compose**: name the compose project after the indexed codebase

### Documentation

- **roadmap**: record moving the database out of the working tree

## v0.8.0 (2026-08-16)

### Features

- **skills**: register the graphify skill for both agents
- **graphify**: extract code with graphifyy, keep the database
- **mcp-server**: serve the graph over streamable http
- **mcp-server**: added tools for interact with file hashes

### Documentation

- **project**: describe the two producer pipeline

## v0.7.0 (2026-08-15)

### Features

- **graphify**: implement incremental indexing

## v0.6.0 (2026-08-15)

### Refactor

- **graphify**: split the indexer into a package under src

### Documentation

- **graphify**: point the docs at the package layout

## v0.5.3 (2026-08-15)

### Features

- **graphify**: read Ansible YAML as Ansible

### Bug Fixes

- **mcp-server**: return the summary the graph already stores
- **graphify**: summarize a file by its title or what it declares

### Documentation

- **README**: document the Ansible edges

## v0.5.2 (2026-08-15)

### Bug Fixes

- **mcp-server**: keep a saved summary from being overwritten by the indexer
- **graphify**: port the parsers to the tree-sitter 0.26 query API

### Documentation

- **ROADMAP**: record what the tree-sitter step delivers and what incremental indexing still owes
- **README**: describe what the indexer actually selects and summarizes

## v0.5.1 (2026-08-15)

### Bug Fixes

- **graphify**: implement missing abstract method get_relations in YAMLParser

## v0.5.0 (2026-08-15)

### Features

- **graphify**: implement full Tree-sitter AST parsing and import resolution

### Documentation

- **README**: add documentation for summaries and planning
- **ROADMAP**: mark Tree-sitter implementation as completed

## v0.4.0 (2026-08-15)

### Features

- **graphify**: implement automatic summaries and persistent project planning

### Documentation

- **GEMINI**: add project instructions

## v0.3.1 (2026-08-15)

### Bug Fixes

- **make**: say none running when the stack is down

## v0.3.0 (2026-08-15)

### Features

- **make**: report stack and index status

### Documentation

- **ROADMAP.md**: extend reodmap for 7 planned points

## v0.2.1 (2026-08-15)

### Bug Fixes

- **make**: delete the database that clean promised to delete

### Documentation

- **readme**: correct what clean removes

## v0.2.0 (2026-08-15)

### Features

- **graphify**: let the project choose what gets indexed

### Build

- **gitignore**: ignore the jetbrains project directory

### Documentation

- **readme**: document the indexing configuration files

## v0.1.0 (2026-08-14)

### Features

- **stack**: add graphrag context service with tooling and docs
