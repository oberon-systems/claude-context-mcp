---
layout: default
title: Supported Formats
nav_order: 3
---

## Supported Formats

The system uses both an upstream parser (`graphifyy`) and custom Tree-sitter
parsers to extract structural information.

- **Languages** (via `graphifyy`): Python, TypeScript, JavaScript, Go, Rust,
  Ruby.
- **Infrastructure** (via this repo's own parsers): Ansible, Puppet,
  Terraform/HCL, Dockerfiles, Makefiles, shell scripts, YAML, JSON.

## Controlling what gets indexed

Two optional files at the root of the indexed project, both gitignore
syntax, read fresh on every `make index`:

| File         | Purpose                                             |
| ------------ | --------------------------------------------------- |
| `.ctxignore` | Paths pruned from the walk                          |
| `.ctxkeep`   | Files that become nodes; everything else is skipped |

```text
# .ctxignore
.git/
.cache/
build/
*.qcow2

# .ctxkeep
*.py
*.ts
*.hcl
*.md
Makefile
```

`.ctxkeep` **replaces** the default selection instead of adding to it, which
is how a project indexes file types this repo has never heard of.
`.ctxignore` is additive on top of the built-in skip list (`.git`, `.venv`,
`node_modules`, `dist`, `target`, ...), so forgetting `.git/` there still
does not walk into git's internals.

Without either file, the built-in defaults apply: every extension a parser
understands, plus `.sql` as an unparsed file node. Files above 1 MB are
always skipped, whichever way they were selected.

`make install` generates both files from what the tree holds, and verifies
the result by simulation before writing it - so a generated pair is a
working starting point, not a guess to double check by hand.

## Ansible

YAML that looks like Ansible is read as Ansible: plays, tasks, handlers and
role variables become nodes, and the references between them become edges.

| Edge            | Source                                          |
| --------------- | ----------------------------------------------- |
| `includes`      | `include_tasks`, `import_tasks`                 |
| `uses_role`     | `roles:`, `include_role`, `import_role`         |
| `depends_on`    | `dependencies:` in `meta/main.yml`              |
| `reads_vars`    | `include_vars`, `vars_files:`                   |
| `uses_template` | `template: src:`                                |
| `uses_file`     | `copy: src:`                                    |
| `notifies`      | `notify:`, resolved to the handler that answers |

Targets resolve inside the owning role, so `template: src: sshd_config.j2`
finds `roles/sshd/templates/sshd_config.j2`. A `notify:` that no handler
answers stays in the graph as an unresolved external node - usually a typo
worth seeing. YAML that isn't Ansible (CI configs, compose files) falls back
to plain top-level keys.

## Puppet

Classes, defined types, node definitions and functions become nodes under
their full name (`class profile::web` is one node regardless of which file
declares it). Resource declarations become nodes too, named by type and
title:

| Manifest               | Node            |
| ---------------------- | --------------- |
| `package { 'nginx': }` | `package.nginx` |
| `service { 'nginx': }` | `service.nginx` |

| Edge            | Source                                         |
| --------------- | ---------------------------------------------- |
| `inherits`      | `inherits` on a class                          |
| `includes`      | `include`                                      |
| `requires`      | `require`, both the statement and `require =>` |
| `requires`      | the `->` and `~>` ordering chains              |
| `notifies`      | `notify =>`                                    |
| `uses_template` | `template(...)` and `epp(...)`                 |

Templates are read too: the code inside `<% %>` is reassembled in source
order and parsed as Ruby (`.erb`) or Puppet (`.epp`), so `<%= @port %>` puts
`@port` in the graph next to the manifest that renders the file. Ruby files
themselves go through the upstream extractor (classes, methods, call graph),
but report no `require` edges between files.

## JSON

Every top-level key becomes a node; nothing below it does, on purpose - a
second level taken indiscriminately turns one data file into hundreds of
nodes named `type` or `url`.

Manifests that carry known structure are read one level deeper by file name:
`scripts` in `package.json` becomes `script` nodes, `mcpServers` in
`.mcp.json` becomes `mcp_server` nodes, and dependency sections become
`depends_on` edges (`npm:express`, `composer:monolog/monolog` - namespaced by
ecosystem so they can't collide with an unrelated key of the same name).

Lock files (`package-lock.json`, `yarn.lock`, `composer.lock`,
`npm-shrinkwrap.json`) are skipped by default - they're generated and say
nothing the manifest next to them doesn't. Name one explicitly in
`.ctxkeep` to index it anyway.

## Nuances

- Files larger than 1 MB are always skipped.
- Entity and import edges are only extracted for languages a parser
  understands. Indexing a Markdown file never mines its prose for the word
  "import".
