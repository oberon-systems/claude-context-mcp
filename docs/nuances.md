---
layout: default
title: System Nuances
nav_order: 6
---

## System Nuances

...

## Read-Only Access

The system mounts all indexed codebases as read-only (`:ro`). Containers **never** mutate your source files.

## Pure ASCII Policy

To ensure compatibility across all environments and tools, all documentation, comments, commit messages, and source code must use only ASCII characters. Unicode symbols and emojis are prohibited.

## Database Integrity

- **One Database:** A single database holds the graph of every codebase you index.
- **Corruption Risk:** Running multiple database containers over the same data directory will cause irreversible corruption. Do not attempt to run parallel stacks from this repository.
- **Persistence:** `docker compose down` does not remove the database volume. To completely reset the index, use `make clean`.

## MCP and Security

- **DNS Rebinding:** The server implements basic DNS rebinding protection via `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` environment variables.
- **Trust:** When registering the MCP server for Gemini, `trust: true` is recommended only for codebases you own.
