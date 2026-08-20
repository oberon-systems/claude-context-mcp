---
layout: default
title: Deployment
nav_order: 4
---

## Deployment and Capabilities

...

## Deployment

All services are defined in `docker-compose.yaml`.

- **Singleton Database:** One database container serves all indexed codebases.
- **State:** Data is persisted in `~/.local/share/context-mcp/db` (not configurable, to ensure data integrity across index jobs).

## GitHub Pages Setup

You can host this documentation on GitHub Pages.

### Setup Instructions

- Enable Pages: Go to your repository **Settings** -> **Pages**.
- Source: Set **Build and deployment** -> **Source** to "GitHub Actions".
- Automatic Build: This repository includes a GitHub Action (`.github/workflows/pages.yml`) that will build and deploy the files in `/docs` automatically upon every push to the `main` branch.
- Wait: After the first push, GitHub will take a few minutes to build and publish your documentation at `https://your-username.github.io/your-repository/`.
