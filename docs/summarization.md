---
layout: default
title: Summarization
nav_order: 5
---

## Summarizing on another machine

...
The pass described above runs on whatever CPU the stack was given. A machine with a GPU does the same work in about a second a file, so the model half can be moved to it - without giving that machine a checkout of your code, a database port or a share. It reads the text of each file from the graph, over HTTP.

That means the text has to be in the graph, which it now is: an index run keeps the first `CONTENT_STORE_CHARS` characters (16000 by default, about 4k tokens) of every file in `graph_nodes.content`. Files named like secrets - `.env`, `*.pem`, `*.tfvars`, `id_rsa*` and the rest - still get a node and a head-of-file summary, but their text is never stored. `CONTENT_STORE_CHARS=0` turns the whole thing off.

## Setting up the Remote Worker

### On the main stack (server)

1. Configure an API token in `.env`:

   ```bash
   openssl rand -hex 24                 # Add this to .env as WORKER_API_TOKEN
   ```

2. Re-index your project to store file content:

   ```bash
   make reindex PROJECT=/path/to/repo
   ```

3. Start the worker API on the stack:

   ```bash
   make api-up                          # Publishes the queue on port 3003
   ```

4. Queue files for processing:

   ```bash
   make jobs PROJECT_NAME=your_project  # Queue every file missing a model summary
   ```

### On the machine with the GPU (worker)

You need a copy of this repository on the GPU machine to act as the worker. See `worker/README.md` in that repository for setting up the CUDA environment and weights.

Run the worker:

```bash
py -m ctxworker.download                # Download model weights once
py -m ctxworker --api http://<stack-ip>:3003 --project your_project
```

## How it works

The queue is leased, not handed out: a worker claims a batch for five minutes. If a worker dies, its batch returns to the queue. Several workers can share the load for one job.

Answers are validated: each summary is checked for quality and length, and it can never overwrite a `manual` summary. Already-summarized files are served from cache and never re-processed.

The worker API is plain HTTP bound to all interfaces. It should only be used on trusted networks or behind a reverse proxy, as it exposes the database contents via the worker API.
