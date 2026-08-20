# Remote summarization worker

Describes the files of an indexed project with a local model, for a
`claude-context-mcp` stack running on another machine. The worker needs no
checkout of the code it describes, no database access and no Docker: it claims
batches over HTTP, is handed the text and the prompt to run, and sends one
sentence per file back.

It exists because the stack summarizes on the CPU it was given - about 30
seconds a file at two threads - while a desktop GPU does the same work in
about one.

## What it needs

- Python 3.10 or newer.
- `llama-cpp-python`, built for your GPU (below). It is the only dependency;
  everything else is the standard library.
- The API's token, and a route to its port. Plain HTTP, so this belongs on a
  trusted LAN or a VPN.

## Install

### Windows, NVIDIA

From the repository root:

```powershell
cd worker
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

The CUDA index matters. Installing without it gets the CPU build, the card
sits idle, and nothing says so - check the llama.cpp banner on startup and
confirm the layer count next to `assigned to device CUDA0` is not zero.

Always use `cu124` on Windows, regardless of the exact CUDA version
`nvidia-smi` reports: it is the only one of `abetlen`'s CUDA wheel indexes
that currently publishes Windows wheels for a recent `llama-cpp-python`
release - `cu121`, `cu122` and `cu123` only carry Windows wheels for much
older releases, and installing against one of them falls back to compiling
from source (see Troubleshooting below). CUDA is driver-backward-compatible,
so a `cu124` wheel runs fine on a driver that only advertises an older CUDA
version.

The wheel is not the whole GPU stack. It carries `llama.dll` and
`ggml-cuda.dll`, never the CUDA runtime those link against
(`cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`), and the loader
looks for that only under `%CUDA_PATH%`. So a CUDA 12.x Toolkit has to be
installed as well, or `llama.dll` refuses to load with a message that names
the DLL rather than what is missing - see Troubleshooting.

### Linux

```bash
cd worker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

Without a GPU, drop the `--extra-index-url` and pass `--gpu-layers 0`. PyPI's
own `llama-cpp-python` ships no wheel, so a C/C++ toolchain has to be present
to build it - `sudo apt install build-essential` on Debian/Ubuntu, or the
equivalent for your distro.

## Get the weights

```text
py -m ctxworker.download                 # the default, qwen-1.5b
py -m ctxworker.download --model qwen-3b
py -m ctxworker.download --check         # where they would be, and whether they are
```

They land in `%LOCALAPPDATA%\context-mcp\models` on Windows and
`~/.local/share/context-mcp/models` elsewhere. The file is only moved into
place once it starts with the GGUF magic number, so an HTTP error page cannot
end up installed as a model.

`ctxworker/catalogue.py` is the list of models, and the stack's own
`make llm-model-install` reads the same file - one table, not two.

A 6 GB card runs `qwen-3b` comfortably at this context size; `qwen-7b` wants
more. `qwen-1.5b` is the default because it is what the stack downloads.

## Run it

```text
set WORKER_API_TOKEN=<the token from the stack's .env>
py -m ctxworker --api http://192.168.1.10:3003 --project kurum
```

That opens a job over every file of `kurum` that still has no model summary,
then claims, describes and reports until the job is drained.

Useful flags, all of which also read an environment variable:

| Flag           | Variable            | Default                 | What it is                           |
| -------------- | ------------------- | ----------------------- | ------------------------------------ |
| `--api`        | `WORKER_API_URL`    | `http://127.0.0.1:3003` | where the stack is                   |
| `--token`      | `WORKER_API_TOKEN`  | -                       | required                             |
| `--project`    | `WORKER_PROJECT`    | -                       | what to describe                     |
| `--job-id`     | -                   | -                       | join a job that is already open      |
| `--model`      | `WORKER_MODEL`      | `qwen-1.5b`             | which weights, by catalogue name     |
| `--model-path` | `WORKER_MODEL_PATH` | -                       | a GGUF file directly                 |
| `--batch`      | `WORKER_BATCH`      | 4                       | files per claim                      |
| `--gpu-layers` | `WORKER_GPU_LAYERS` | -1 (all)                | 0 to stay on the CPU                 |
| `--ctx`        | `WORKER_CTX`        | 8192                    | context window                       |
| `--worker-id`  | `WORKER_ID`         | host-pid                | what shows up in the job's file list |
| `--once`       | -                   | -                       | one batch, then stop                 |

Several workers may run against one job; each claim is exclusive.

## Stopping, and crashing

Ctrl-C finishes the file in hand, hands the rest of the batch straight back,
and exits. A second Ctrl-C drops it immediately.

If the machine dies instead, the batch it held simply expires - the stack
gives those files to whoever asks next, and no work is lost beyond the file
that was in flight. This is why a job is a lease queue and not a list.

## Troubleshooting

- **`pip install` downloads a `.tar.gz` instead of a `.whl`, then fails with
  a CMake/`nmake`/compiler error** - no prebuilt wheel matched your platform
  for the pinned version, so pip fell back to compiling the source
  distribution. On Windows that needs a full MSVC toolchain (Visual Studio
  Build Tools, "Desktop development with C++" workload) and a matching CUDA
  Toolkit, neither of which this setup requires otherwise. The fix is to
  keep the pin in `requirements.txt` on a release that actually publishes a
  `cu124`/`win_amd64` wheel - it does by default; if you changed the pin,
  change it back or pick another version confirmed present at
  <https://abetlen.github.io/llama-cpp-python/whl/cu124/llama-cpp-python/>.
- **`Failed to load shared library ... llama.dll`** - the message ends
  with "or one of its dependencies", and that is almost always what it is,
  rather than `llama.dll` itself: the CUDA runtime is missing. Run
  `dir .venv\Lib\site-packages\llama_cpp\lib` to tell the two apart. If
  `llama.dll` is there, install a CUDA 12.x Toolkit (that is what sets
  `CUDA_PATH`), or `pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12`
  and copy `cudart64_12.dll`, `cublas64_12.dll` and `cublasLt64_12.dll` into
  that same `llama_cpp\lib\` directory, which is already on the DLL search
  path. If `llama.dll` is absent and the directory holds `libllama.so`, the
  installed wheel is for another platform - reinstall it from the `cu124`
  index above. The worker prints this diagnosis itself on startup.
- **`Could not open requirements file`** - the install commands above must
  be run from the `worker/` directory, not the repository root; `cd worker`
  first.
- **`--ctx N cannot hold M characters`** - the job was created to show the
  model more text than the context window fits. Raise `--ctx`, or create the
  job with a smaller `input_chars`.
- **401** - the token does not match the stack's `WORKER_API_TOKEN`.
- **A job that finishes instantly with everything skipped** - the project was
  indexed before file text was stored. Re-index it on the stack
  (`make reindex PROJECT=...`) and create the job again.
- **The GPU is idle** - the CPU wheel got installed. Reinstall with the CUDA
  index above.
