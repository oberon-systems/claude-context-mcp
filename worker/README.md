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
- Somewhere to run the model: either `llama-cpp-python` built for your GPU,
  loaded into this process, or a `llama-server` this process talks to over
  HTTP. Pointed at a server, the worker needs nothing installed at all -
  every other line of this package is standard library.
- The API's token, and a route to its port. Plain HTTP, so this belongs on a
  trusted LAN or a VPN.

## Install

Two ways to run the model, and the flags are the only difference to the
worker. Start with the server if the wheel will not load on your CPU, or if
the machine running this loop is not the machine with the GPU.

### The model server, on any CPU and any machine

A prebuilt `llama-cpp-python` wheel is compiled for one instruction set, and
ggml decides at compile time which kernels exist. A wheel built with AVX-512
therefore claims AVX-512 everywhere, and a CPU without it - any Ryzen before
Zen 4, plenty of Intel laptop parts - dies on the first such kernel with

```text
OSError: [WinError -1073741795] Windows Error 0xc000001d
```

an illegal instruction, with nothing to switch off. The llama.cpp release
binaries ship one `ggml-cpu` library per instruction set and choose at load
time, so this route runs anywhere.

Three batch files do the whole of it, from `worker\` or by
double-clicking:

```bat
get-llama-server.bat    :: download the build for this driver
start-llama-server.bat  :: start it, downloading anything missing first
start-worker.bat --api http://192.168.1.10:3000/worker --token <token> --project kurum
```

On a clean machine only the middle one is needed - it installs the binaries
and the weights before starting. All three pass extra arguments through, so
`start-llama-server.bat --model qwen-3b --port 8090` does what it looks like.
`py -m ctxworker.getserver` and `py -m ctxworker.runserver --install` are the
same two without the double-click, and work on Linux for everything except
installing the server, which has no Linux CUDA archive to install.

What `get-llama-server.bat` does:

It reads the current release from GitHub, asks `nvidia-smi` which CUDA the
driver serves, takes the newest build that driver can run together with the
CUDA runtime it links against, and unpacks both into `worker\llama-server\`

- which `.gitignore` already excludes. `py -m ctxworker.getserver` is the same
  thing without the double-click, and both take the same flags:

| Flag            | What it does                                   |
| --------------- | ---------------------------------------------- |
| `--dry-run`     | only say which files it would take             |
| `--variant cpu` | override the detected build (`cuda-12.4`, ...) |
| `--tag b10519`  | pin a release instead of taking the latest     |
| `--dest <dir>`  | unpack somewhere else                          |

By hand it is two files from
<https://github.com/ggml-org/llama.cpp/releases/latest>:
`llama-<tag>-bin-win-cuda-13.3-x64.zip`, which holds `llama-server.exe` and
the `ggml-*.dll` set, and `cudart-llama-bin-win-cuda-13.3-x64.zip`, which
holds the CUDA runtime those link against. Both `cuda-13.3` and `cuda-12.4`
are published - take the one matching the CUDA version `nvidia-smi` prints in
its top right corner, and note that a lower one also runs on a newer driver,
because CUDA is driver-backward-compatible. There is a `win-cpu-x64` zip for a
machine with no NVIDIA card. Unpack BOTH into ONE directory, or the runtime
DLLs will not be beside the executable.

Then start it - which is what `start-llama-server.bat` runs for you:

```bat
cd worker\llama-server
llama-server.exe -m %LOCALAPPDATA%\context-mcp\models\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf ^
  -c 8192 -ngl 99 --host 127.0.0.1 --port 8080 --parallel 1
```

`py -m ctxworker.download` is how the `.gguf` gets onto that machine; it is
the only thing the server needs from this package.

What those four flags are for, and what not to touch:

- `-ngl 99` puts every layer on the GPU. `qwen-1.5b` in q4 is about 1 GB and
  `qwen-3b` about 2, so a 6 GB card has room either way.
- `-c 8192` is the context window, the same number `--ctx` means for the
  in-process backend. Jobs send up to `CONTENT_STORE_CHARS` characters plus
  the answer, so 16000 characters need roughly 5000 tokens of it.
- `--parallel 1` keeps that whole window on one slot. With `--parallel N` the
  context is divided between slots, and each request gets `-c` over N.
- `--host 127.0.0.1` refuses everything but this machine. See "Three
  machines, or one" below before changing it.
- Threads, `--flash-attn` and `--jinja` are already right by default.

Three lines in its log say the setup is correct:

```text
load_backend: loaded CPU backend from ...\ggml-cpu-haswell.dll
load_tensors: offloaded 29/29 layers to GPU
main: server is listening on http://127.0.0.1:8080
```

The first is the whole point of this route: the variant picked for your CPU,
where the wheel had none to pick from. The second is the card doing the work

- `0/29` means the `win-cpu` zip got unpacked, or `-ngl` was left out. Then
  open <http://127.0.0.1:8080> in a browser, where the server keeps a web UI,
  and ask it anything: an answer there means the model half is finished.

The worker joins it with one flag, and everything else stays as it was:

```bat
py -m ctxworker --api http://192.168.1.10:3000/worker --token <token> ^
  --project kurum --llama-server http://127.0.0.1:8080
```

At startup it reads the server's real context window and model from `/props`
and logs both, so `--ctx`, `--model`, `--model-path` and `--gpu-layers` are
the server's business in this mode and are ignored.

### Windows, NVIDIA

Everything from a fresh checkout to a running worker. Copy the whole block,
change the last line's address, token and project, and paste it into
a shell in the repository root - cmd or PowerShell, both work:

```bat
cd worker
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
py -c "import glob,os,shutil,sysconfig;p=sysconfig.get_paths()['purelib'];[shutil.copy(f,os.path.join(p,'llama_cpp','lib')) for f in glob.glob(os.path.join(p,'nvidia','*','bin','*.dll'))]"
py -c "import llama_cpp; print('llama_cpp ok')"
py -m ctxworker.download
py -m ctxworker --api http://192.168.1.10:3000/worker --token <token> --project kurum
```

If the seventh line printed `llama_cpp ok` and the last one started
reporting files, the setup is finished and the rest of this section is
background. If it stopped somewhere, Troubleshooting at the end names each
failure by its message.

What the two lines in the middle are for: the wheel carries `llama.dll` and
`ggml-cuda.dll` but not the CUDA runtime they link against
(`cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`), and
`llama_cpp\lib\` is a directory its loader already searches. Installing a
CUDA 12.x Toolkit from <https://developer.nvidia.com/cuda-downloads> is the
other way to supply the same three DLLs - about 3 GB, needs a fresh shell,
and survives a reinstall of `llama-cpp-python`, which the copy does not.

The CUDA index on the fourth line matters just as much. Installing without
it gets the CPU build, the card sits idle, and nothing says so - check the
llama.cpp banner on startup and confirm the layer count next to
`assigned to device CUDA0` is not zero. Always `cu124`, regardless of the
version `nvidia-smi` reports: it is the only one of `abetlen`'s CUDA wheel
indexes that currently publishes Windows wheels for a recent
`llama-cpp-python` release - `cu121`, `cu122` and `cu123` only carry
Windows wheels for much older releases, and installing against one of them
falls back to compiling from source. CUDA is driver-backward-compatible, so
a `cu124` wheel runs fine on a driver that advertises an older version.

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
py -m ctxworker --api http://192.168.1.10:3000/worker --project kurum
```

That opens a job over every file of `kurum` that still has no model summary,
then claims, describes and reports until the job is drained.

Name no project and it takes them all:

```text
py -m ctxworker --api http://192.168.1.10:3000/worker
```

It asks the stack what it has indexed, drops the projects the model has
already described, and works through the rest one job at a time, opening each
as its turn comes. One pass, then it exits - a schedule is the machine's own
business. A project someone unindexes mid-pass is logged and stepped over, and
one that already has a job open is joined rather than refused. `--auto` says
the same thing out loud, and overrides a `WORKER_PROJECT` the machine carries
in its environment.

Useful flags, all of which also read an environment variable:

| Flag                 | Variable                  | Default                        | What it is                           |
| -------------------- | ------------------------- | ------------------------------ | ------------------------------------ |
| `--api`              | `WORKER_API_URL`          | `http://127.0.0.1:3000/worker` | where the stack is                   |
| `--token`            | `WORKER_API_TOKEN`        | -                              | required                             |
| `--project`          | `WORKER_PROJECT`          | -                              | one project; unset means all of them |
| `--auto`             | `WORKER_AUTO`             | -                              | every project, over a named one      |
| `--job-id`           | -                         | -                              | join a job that is already open      |
| `--model`            | `WORKER_MODEL`            | `qwen-1.5b`                    | which weights, by catalogue name     |
| `--model-path`       | `WORKER_MODEL_PATH`       | -                              | a GGUF file directly                 |
| `--llama-server`     | `WORKER_LLAMA_SERVER`     | -                              | run the model on a server, not here  |
| `--llama-server-key` | `WORKER_LLAMA_SERVER_KEY` | -                              | key, if it was started with one      |
| `--batch`            | `WORKER_BATCH`            | 4                              | files per claim                      |
| `--gpu-layers`       | `WORKER_GPU_LAYERS`       | -1 (all)                       | 0 to stay on the CPU                 |
| `--ctx`              | `WORKER_CTX`              | 8192                           | context window                       |
| `--worker-id`        | `WORKER_ID`               | host-pid                       | what shows up in the job's file list |
| `--once`             | -                         | -                              | one batch, then stop                 |
| `--verbose`          | -                         | -                              | show llama.cpp's own startup output  |

Several workers may run against one job; each claim is exclusive.

## Three machines, or one

With `--llama-server` this process holds no model and loads no library, so
the three roles can sit wherever it suits:

| Role      | What runs there                                | What it needs                    |
| --------- | ---------------------------------------------- | -------------------------------- |
| the stack | `docker compose`, `/worker` on the entry point | the database and this repository |
| the loop  | `py -m ctxworker`                              | Python 3.10+, nothing installed  |
| the model | `llama-server`                                 | the GPU and the `.gguf`          |

The loop and the model on one machine is the common case, and then
`--host 127.0.0.1` keeps the server private and no key is needed. To drive a
server on another machine, three things change:

```bat
llama-server.exe -m <model.gguf> -c 8192 -ngl 99 ^
  --host 0.0.0.0 --port 8080 --parallel 1 --api-key <secret>

netsh advfirewall firewall add rule name="llama-server" ^
  dir=in action=allow protocol=TCP localport=8080

py -m ctxworker --api http://192.168.1.10:3000/worker --token <token> ^
  --project kurum --llama-server http://192.168.1.23:8080 ^
  --llama-server-key <secret>
```

`--api-key` is not optional once the port is open: llama-server has no
authentication of its own, and anything that reaches it can run the model and
read what is being described. It is plain HTTP either way, exactly like the
stack's own API, so this belongs on a trusted LAN or a VPN and nowhere else.

The weights only ever live on the machine running the server. That machine
needs no checkout beyond `py -m ctxworker.download` to fetch them - or just
the `.gguf`, copied there by hand.

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
  with "or one of its dependencies", and that is what it is: the CUDA
  runtime, not `llama.dll` itself. Run the two-line recipe in the Windows
  install section above (the two NVIDIA lines), or install a
  CUDA 12.x Toolkit, then check with `py -c "import llama_cpp"`.
  If instead `dir .venv\Lib\site-packages\llama_cpp\lib` shows no
  `llama.dll` at all - `libllama.so`, say - the installed wheel is for
  another platform, and the fix is to reinstall it from the `cu124` index.
  The worker prints whichever of the two applies when it starts.
- **`Windows Error 0xc000001d`, at `llama_init_from_model` or right after a
  `repack:` line** - an illegal instruction. The wheel is compiled for an
  instruction set this CPU does not have, typically AVX-512 on a CPU that
  stops at AVX2, and because ggml settles that at compile time there is
  nothing to switch off. Use the model server instead - that is what it is
  for. Two commands show the mismatch:

  ```bat
  py -c "import llama_cpp; print(llama_cpp.llama_print_system_info())"
  py -c "import ctypes;k=ctypes.windll.kernel32;print('AVX2',k.IsProcessorFeaturePresent(40),'AVX512F',k.IsProcessorFeaturePresent(41))"
  ```

  The first prints what the build claims, the second what the processor
  actually has. `AVX512 = 1` against `AVX512F 0` is this failure. It is not a
  permissions problem: running as administrator changes nothing.

- **`no llama.cpp server answered at ...`** - nothing is listening on that
  URL. Start the server, or check the port and the firewall rule if it is on
  another machine. The worker refuses at startup, before it opens a job.

- **`Could not open requirements file`** - the install commands above must
  be run from the `worker/` directory, not the repository root; `cd worker`
  first.
- **`a context window of N cannot hold M characters`** - the job was created
  to show the model more text than the window fits. Raise `--ctx`, or restart
  `llama-server` with a larger `-c`, or create the job with a smaller
  `input_chars`. The number reported is the one the model process really has:
  with `--llama-server` it is read from the server's `/props`, not taken from
  the flag.
- **401** - the token does not match the stack's `WORKER_API_TOKEN`.
- **A job that finishes instantly with everything skipped** - the project was
  indexed before file text was stored. Re-index it on the stack
  (the dashboard's Fresh button) and create the job again.
- **The GPU is idle** - the CPU wheel got installed. Reinstall with the CUDA
  index above.
