---
layout: default
title: Supported Formats
nav_order: 3
---

## Supported Formats

The system uses both an upstream parser (`graphifyy`) and custom Tree-sitter
parsers to extract structural information.

- **Languages** (via `graphifyy`): Python, TypeScript, JavaScript, Go, Rust,
  Ruby, PHP, Java, C, C++, C#, Kotlin, Scala.
- **Infrastructure and markup** (via this repo's own parsers): Ansible, Docker
  Compose, Puppet, Terraform/HCL, Dockerfiles, Makefiles, shell scripts, YAML,
  JSON, HTML, PHP templates.

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
worth seeing. YAML that is neither Ansible nor compose (CI configs, plain
data) falls back to plain top-level keys.

## Docker Compose

`compose.yaml`, `docker-compose.yaml` and the suffixed variants
(`docker-compose.prod.yml`, `compose.override.yaml`) are read as an
architecture. Any other YAML holding a top-level `services:` mapping of
service definitions is recognised by its shape, so a `stack.yml` is read the
same way.

Each declaration becomes a node named by its kind, the way an HCL block is -
a service and a volume may share a name, and the kind is what keeps them
apart:

| Compose                    | Node                       |
| -------------------------- | -------------------------- |
| `name: claude-context-mcp` | `stack.claude-context-mcp` |
| `services.postgres`        | `service.postgres`         |
| `volumes.graph-out`        | `volume.graph-out`         |
| `networks.base`            | `network.base`             |
| `configs.site`             | `config.site`              |
| `secrets.token`            | `secret.token`             |

`x-` extension fields declare nothing and get no node.

Every edge below leaves the **service** rather than the file, so
`get_code_graph_neighbors` on one service answers what it runs, waits for and
reads:

| Edge           | Source                                                 |
| -------------- | ------------------------------------------------------ |
| `depends_on`   | `depends_on:`, in list and `condition:` form; `links:` |
| `uses_image`   | `image:`, as `image:<ref>`                             |
| `builds`       | `build:`, resolved to the Dockerfile it names          |
| `uses_volume`  | a named volume in `volumes:`                           |
| `mounts`       | a bind mount in `volumes:`                             |
| `uses_network` | `networks:`                                            |
| `uses_config`  | `configs:`                                             |
| `uses_secret`  | `secrets:`                                             |
| `reads_vars`   | `env_file:`                                            |
| `extends`      | `extends: service:` within the same file               |

Top-level `include:`, `extends: file:` and the `file:` of a config or a secret
leave the compose file itself, as `includes` and `uses_file`.

Three things are deliberately dropped rather than turned into a node pointing
at nothing: any value still holding `${...}` (only compose can expand it), a
bind mount whose host side is absolute or names a directory rather than a
file, and a `build:` context that is a git URL. An image, by contrast, always
gets its external node - `image:` prefixed so `postgres` can never bind to an
unrelated symbol of that name.

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

## HTML

`.html` and `.htm` are read as a page: what it declares, and what it pulls in.

| Markup                          | Node                  |
| ------------------------------- | --------------------- |
| `<title>Dashboard</title>`      | `title.Dashboard`     |
| `<div id="root">`               | `anchor.root`         |
| `<my-widget>`                   | `element.my-widget`   |
| `.card { }` in `<style>`        | `style..card`         |
| `function boot()` in `<script>` | `boot`, as a function |

A standard tag gets no node - only a hyphenated one, which is what tells a
custom element from the structure around it. The `<script>` and `<style>`
bodies are joined in source order and handed to the JavaScript and CSS
grammars, so an inline script contributes the same kind of nodes a `.js` file
would.

| Reference                                   | Edge          |
| ------------------------------------------- | ------------- |
| `<script src>`                              | `uses_script` |
| `<link href>`                               | `uses_style`  |
| `<img src>`, `<source src>`, `<iframe src>` | `uses_file`   |
| `<form action>`                             | `uses_file`   |

A reference is tried next to the page first and then from the project root, so
both `js/app.js` and `/js/app.js` reach the same file, and a `?v=2` or `#top`
tail is trimmed before the lookup. Anything naming something other than a file
of this project is dropped rather than turned into an external node: another
origin (`https://cdn...`, `//cdn...`), a scheme of its own (`data:`,
`mailto:`), a bare `#fragment`, and a value still holding `{{ }}` or `${ }`.

A call an inline script makes becomes an edge only when the same page declares
what it calls. A call into a `.js` file cannot: those files belong to the
upstream extractor, whose symbols this pass never sees, so the edge would be a
placeholder rather than a link.

## PHP

`.php` goes to the upstream extractor, which reads classes, functions,
methods, `use` declarations and the call graph between them.

`.phtml` templates cannot: the extractor routes on the `.php` suffix alone. So
they get a parser here instead, reading the `<?php ?>` islands for classes,
interfaces, traits, functions and methods, with the same rule as an inline
script - a call or an `extends` becomes an edge only when the template declares
the target itself. What does reach another file is `include` and `require`,
which become `includes` edges resolved the way a page's assets are. The markup
around the islands is opaque to this parser.

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
