# Supported Formats

The indexer understands various file types and structures.

## Languages and Infrastructure

The system uses both an upstream parser (`graphifyy`) and custom Tree-sitter parsers to extract structural information.

- **Supported Languages:** Python, TypeScript, JavaScript, Go, Rust, Ruby.
- **Infrastructure Formats:**
  - **Ansible:** Tasks, handlers, roles, and variables are mapped.
  - **Puppet:** Classes, resource declarations, and relationships are mapped.
  - **Terraform/HCL, Dockerfiles, Makefiles, Shell scripts:** Basic structural mapping.
  - **YAML/JSON:** Top-level keys are extracted as nodes.

## Controlling what gets indexed

You can customize indexing at the root of your codebase using two files:

1. .ctxignore: Use Gitignore-style syntax to prune paths you do not want to index (e.g., `.git`, `.venv`, `node_modules`).
2. .ctxkeep: Explicitly lists files to index. **Note:** Using `.ctxkeep` completely overrides the default selection mechanism.

## Nuances

- **File Size Limit:** Files larger than 1 MB are automatically skipped.
- **Dependency/Entity Extraction:** Only performed for languages where structural understanding is available. Documentation files (like Markdown) are treated as text nodes without structural mining.
