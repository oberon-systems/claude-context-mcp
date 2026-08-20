---
layout: default
title: Summarization
nav_order: 5
---

## Automatic summarization

Indexing writes a summary for every file node from its leading docstring,
comment block or first heading - that's the fast producer, and it's what a
plain `make index` does.

A local GGUF model writes better ones, but it's slow, so it runs as its own
pass after the graph exists:

```bash
make llm-model-install               # once: ~1 GB of weights
make summarize PROJECT=$(pwd)        # describe what has no model summary yet
make summarize PROJECT=$(pwd) BG=1   # the same, detached, for a large tree
make summarize PROJECT=$(pwd) LIMIT=20  # stop after 20, to time the rest
```

`MODEL=` picks the weights, for both download and run:

| `MODEL=`              | size    | ~s per file at 8 threads            |
| --------------------- | ------- | ----------------------------------- |
| `qwen-1.5b` (default) | 1.12 GB | ~8                                  |
| `qwen-3b`             | 2.10 GB | ~20, wants `GRAPHIFY_MEM=6g`        |
| `qwen-0.5b`           | 0.49 GB | ~4                                  |
| `smollm2`             | 1.06 GB | ~13, and it declines far more files |

The pass only visits files whose summary still comes from the head of the
file, so it can be stopped and restarted without repeating itself
(`FRESH=1` re-describes everything). Every answer is cached in
`summary_cache`, keyed by a hash of the text shown to the model, so a
re-index only pays for the files that changed.

Expect an occasional decline: a small model reading the head of a changelog
or lock file can answer with just the file name, and an answer that says no
more than the node id is rejected rather than stored - those keep the
head-of-file summary.

`metadata.summary_source` records which of the three wrote it: `auto` (head
of file), `llm` (the model), `manual` (via `save_node_summary`). A manual
summary is never overwritten.

## Summarizing on another machine

The pass above runs on whatever CPU the stack was given - a machine with a
GPU does the same work about 30x faster, so the model half can move there
without giving that machine a checkout of the code, a database port, or a
share. It reads each file's text from the graph, over HTTP.

That means the text has to be in the graph, which it now is: indexing keeps
the first `CONTENT_STORE_CHARS` characters (16000 by default) of every file
in `graph_nodes.content`. Files that look like secrets (`.env`, `*.pem`,
`*.tfvars`, `id_rsa*`, ...) still get a node and a head-of-file summary, but
their text is never stored. `CONTENT_STORE_CHARS=0` turns storage off
entirely.

### On the stack (server side)

```bash
openssl rand -hex 24                 # put it in .env as WORKER_API_TOKEN
make reindex PROJECT=/path/to/repo   # once, so the text is in the graph
make api-up                          # publishes the queue on port 3003
make jobs PROJECT_NAME=kurum         # queue every file with no model summary
make job ID=7                        # check how far along it is
```

The API is plain HTTP bound to every interface, so it belongs on a trusted
LAN or a VPN (behind a reverse proxy if it needs to go further). It refuses
to start without `WORKER_API_TOKEN`, and refuses a token shorter than 16
characters.

### On the machine with the GPU (worker)

The worker is a standalone Python package (`worker/`) - no Docker, no
checkout of the code it describes, no database access. Copy just that
directory over, or clone the whole repository and `cd worker`.

There are two ways to run the model, and the worker treats them the same.
Either it loads the weights itself through `llama-cpp-python`, or it talks
over HTTP to a `llama-server` that holds them.

**Through a llama.cpp server** - the route that works on any CPU, and the
only one if the machine running the loop is not the machine with the GPU:

```bat
cd worker
get-llama-server.bat
llama-server\llama-server.exe -m %LOCALAPPDATA%\context-mcp\models\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf -c 8192 -ngl 99 --host 127.0.0.1 --port 8080 --parallel 1
py -m ctxworker --api http://192.168.1.10:3003 --token <token> --project kurum --llama-server http://127.0.0.1:8080
```

`get-llama-server.bat` takes the llama.cpp release matching the driver and
unpacks it into `worker\llama-server\`. In the server's log,
`loaded CPU backend from ...ggml-cpu-<variant>.dll` is the line that matters:
the release binaries carry one such library per instruction set and choose at
load time, where a wheel is compiled for exactly one and dies with
`0xc000001d` on a CPU that lacks it. With this backend the worker needs no
weights and no `llama-cpp-python` at all, so it can run anywhere - see "Three
machines, or one" in the worker README for driving a server across the LAN,
where `--api-key` stops being optional.

**In the worker's own process, Windows:**

```bat
cd worker
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
py -c "import glob,os,shutil,sysconfig;p=sysconfig.get_paths()['purelib'];[shutil.copy(f,os.path.join(p,'llama_cpp','lib')) for f in glob.glob(os.path.join(p,'nvidia','*','bin','*.dll'))]"
py -c "import llama_cpp; print('llama_cpp ok')"
py -m ctxworker.download
py -m ctxworker --api http://192.168.1.10:3003 --token <token> --project kurum
```

That is the whole setup: `llama_cpp ok` and then a worker reporting files.
The `nvidia-` install and the copy after it are not optional - the wheel carries
`llama.dll` and `ggml-cuda.dll` but not the CUDA runtime they link against,
and `llama_cpp\lib\` is where its loader looks. Installing a CUDA 12.x
Toolkit instead supplies the same DLLs.

Use the `cu124` index. It's the only one of `abetlen`'s CUDA indexes that
currently publishes Windows wheels for a recent `llama-cpp-python` release -
`cu121`/`cu122`/`cu123` only have Windows wheels for much older releases.
CUDA is driver-backward-compatible, so `cu124` works regardless of which
exact CUDA version `nvidia-smi` reports, as long as the driver is
reasonably current.

**In the worker's own process, Linux:**

```bash
cd worker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
python -c "import llama_cpp; print('llama_cpp ok')"
python -m ctxworker.download
python -m ctxworker --api http://192.168.1.10:3003 --token <token> --project kurum
```

Without a GPU, drop `--extra-index-url` and pass `--gpu-layers 0` when
running the worker - but PyPI's own `llama-cpp-python` ships no wheel, so a
C/C++ toolchain (`apt install build-essential`, or equivalent) has to be
present to build it.

**If `pip install` downloads a `.tar.gz` instead of a `.whl`**, no prebuilt
wheel matched your platform for the pinned version, and pip is compiling
from source. On Windows that needs a full MSVC toolchain (Visual Studio
Build Tools, "Desktop development with C++") and a matching CUDA Toolkit -
avoidable by keeping the version pin on a release that actually publishes a
wheel for `cu124`/win_amd64 (see `worker/requirements.txt`).

`WORKER_API_TOKEN` in the stack's `.env` is the token those last lines
want, and the project is one of the names `make status` lists.

Full flag reference, model catalogue and troubleshooting live in
[`worker/README.md`](https://github.com/oberon-systems/claude-context-mcp/blob/main/worker/README.md).

## How it works

The queue is leased, not handed out: a worker claims a batch for five
minutes, and a batch whose worker dies returns to the queue for whoever asks
next. Several workers can share one job.

Answers aren't trusted blindly - each one goes through the same "says
nothing the file name doesn't" gate as the local pass, is length-capped, and
can never overwrite a `manual` summary. Already-summarized files are served
from `summary_cache` and never leased again.

The local pass and a remote job show the model different amounts of text
(2000 characters vs 16000), so they fill different cache rows - that's
expected, not a bug.
