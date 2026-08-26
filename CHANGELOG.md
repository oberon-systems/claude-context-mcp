## v0.14.2 (2026-08-26)

### Refactor

- **make, scripts, graphify**: register the project row when its mount is written

## v0.14.1 (2026-08-26)

### Bug Fixes

- **graphify**: report compose files and drop volatile counts
- **install, templates**: keep a codebase's own GEMINI.md

### Build

- **ctxkeep**: regenerate the selection, keeping *.conf by hand

### Documentation

- **roadmap**: settings and formats move to the database

## v0.14.0 (2026-08-26)

### Features

- **make, scripts**: install FORCE=1 offers to refresh what a project already has
- **web**: index a project from the dashboard
- **graphify, migrations**: indexing runs in the API instead of a container
- **graphify, web, migrations**: file text leaves the database for the mount
- **graphify, compose, make**: mount every indexed tree at /code/<project>
- **graphify**: html support added
- **migrations, mcp-server, web**: plans move to the _plans built-in project
- **graphify**: update docker compose parsing
- **worker**: auto mode added
- **ai**: update cloud's local settings
- **agents**: cloud local template updated

### Bug Fixes

- **mcp-server, web**: GATEWAY_HOSTS=* reaches the MCP server too
- **scripts**: the nginx config check pins the image compose runs
- **graphify**: set fingerprints for files hashes
- **web/server**: guard: fix error message
- **compose**: up nginx version to 1.30.4

### Refactor

- **make, scripts, docs**: one install target and one alias

### Documentation

- **docs**: roadmap gains per-project file tracking
- **docs, instructions**: updated

## v0.13.0 (2026-08-21)

### Features

- **web**: single entry point
- **make**: add skill-reinstall, and the two skill aliases it needs
- **skills**: install the context and write-docs skills, drop graphify
- **worker**: start the model server from a batch file, not a typed command line
- **worker**: run the model on a llama.cpp server, not only in this process
- **worker**: fetch llama-server for whatever driver the machine turns out to have

### Documentation

- **summarization.md**: say what it does, then the three ways to run it, then which to pick
- **worker**: a runbook for the model server, and what 0xc000001d means
- **worker**: make the GPU setup one copy-paste block that ends in a running worker

## v0.12.0 (2026-08-20)

### Features

- **make**: install every skill under skills/, not only graphify
- **core**: implement _suggestions support
- **core**: implement repository categorization and agents memory support
- **workerapi**: summarize a project from a machine with a GPU
- **graphify**: pick the summarizer model with MODEL=, defaulting to Qwen2.5-Coder

### Bug Fixes

- **worker**: load the model before the job and say what llama.dll is missing
- **pages**: build the site from docs/Gemfile instead of the github-pages gems
- **worker**: pin llama-cpp-python to a version that actually has wheels

### Build

- **github**: added action for deploy github pages

### Documentation

- **ROADMAP.md**: record a reinstall target for already onboarded tools
- **CLAUDE.md**: shrink the always-on agent instruction files
- **docs**: restructure Pages docs and trim README to point at them
- **pages**: fix site config
- **pages**: added docks for github pages

## v0.11.0 (2026-08-20)

### Features

- **graphify**: summarize file nodes with a local model in its own pass
- **make**: implement context reindex command
- **web**: show the projects, the graph and the plans
- **web**: read and write the plans of every project
- **web**: browse the nodes, their neighbours and the files
- **web**: serve the projects and the viewer's graph
- **web**: add the dashboard service, empty but wired
- **index.ts**: let the plan tools read and write the type
- **migrations**: add a type column to plans, apart from status
- **plans**: implement cross-project plans
- **make**: unified onboarding added
- **database**: implement schema management
- **make**: implement support for local builds
- **Makefile**: keep the seven newest backups by default
- **scripts**: back up and restore the graph database
- **index**: re-index must invalidate the extractor cach
- **tools**: drop a project from the graph
- **compose**: use latest tag for applicaiton images
- **database**: move the database out of the working tree
- **PuppetParser**: resolve qualified names into reads_var and references edges
- **JsonParser**: index .json and read what its manifests declare

### Bug Fixes

- **config.py**: index .tsx, which arrived with the dashboard
- **Makefile**: build and run one image reference, tagged latest
- **database**: DATA_DIR removed completly
- **MakeParser**: read the whole Makefile and keep only its real targets
- **indexer**: link every entity to the file that declares it

### Build

- **github**: update build triggers and cache using
- **Makefile**: install the commit adapter best-effort
- **github**: actions: added tag latest
- **github**: action for build images added
- **gemini**: update instructions
- **CLAUDE.local.md**: write down the commitizen question flow and how to drive it
- **CLAUDE.local.md**: record template plans, the suggestions recap line and the gemini trust flag
- **.ctxkeep**: index this repository through an explicit keep list
- **CLAUDE.local.md**: require a recap of how the answer was produced

### Documentation

- **README.md**: document the dashboard and the plan type
- **README.md**: drop every trace of DATA_DIR and COMPOSE_PROJECT_NAME
- **CLAUDE.md**: drop the puppet residue and stop assuming one cz adapter
- **roadmap**: refatored and updated
- **ROADMAP.md**: rank the deferred work by cost and record the gaps found since
- **01-init.sql**: describe both metadata keys the indexer writes
- **ROADMAP.md**: drop the finished Make parser item and renumber

## v0.10.1 (2026-08-17)

### Bug Fixes

- **compose**: pin the stack to a single compose project again

### Build

- **CLAUDE.local.md**: forbid reading anything the loaded plan does not name
- **CLAUDE.local.md**: add the local agent instructions

### Documentation

- **ROADMAP.md**: drop the completed items and renumber what is left

## v0.10.0 (2026-08-17)

### Features

- **graphify**: index Puppet manifests and their templates
- **graphify**: index C and C++ sources

### Build

- **CLAUDE.md**: added instractions for delegating tasks to gemini light

### Documentation

- **ROADMAP.md**: added task for add cross-projects relations and queries

## v0.9.1 (2026-08-17)

### Bug Fixes

- **mcp**: bind both clients to the per-project endpoint

### Documentation

- **roadmap**: record the work the neighbouring projects exposed

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
