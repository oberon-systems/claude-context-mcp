"""Start llama-server on this machine, fetching whatever is missing first.

One command from nothing to a model answering on a port: the binaries, the
weights and the flags that suit this stack's jobs. It is what
start-llama-server.bat runs.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

from ctxworker import getserver
from ctxworker.catalogue import DEFAULT_MODEL, default_dir, file_name
from ctxworker.download import fetch, is_gguf

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_CTX = 8192
DEFAULT_GPU_LAYERS = 99


def server_path(dest: Path) -> Path:
    """Where the executable sits, under whichever name this OS uses."""
    name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    return dest / name


def ensure_server(dest: Path, install: bool) -> Path:
    """Return the server, downloading a release first if it is not there."""
    server = server_path(dest)
    if server.is_file():
        return server
    if not install:
        raise SystemExit(
            f"no llama-server in {dest}. Run get-llama-server.bat, or pass "
            "--install to take one now."
        )
    if platform.system() != "Windows":
        raise SystemExit(
            f"no llama-server in {dest}, and the llama.cpp releases carry no "
            "Linux CUDA archive to install. Run the server from the project's "
            "own image instead:\n"
            "    docker run --rm --gpus all -p 8080:8080 "
            "-v ~/.local/share/context-mcp/models:/models \\\n"
            "        ghcr.io/ggml-org/llama.cpp:server-cuda -m /models/<file> "
            "-c 8192 -ngl 99 --host 0.0.0.0 --port 8080 --parallel 1"
        )
    return getserver.install(dest)


def ensure_model(model: str, directory: Path, install: bool) -> Path:
    """Return the weights, downloading them first if they are not there."""
    target = directory / file_name(model)
    if target.is_file() and is_gguf(target):
        return target
    if not install:
        raise SystemExit(
            f"no weights at {target}. Run `py -m ctxworker.download`, or pass "
            "--install to take them now."
        )
    return fetch(model, directory)


def command(server: Path, model: Path, args: argparse.Namespace) -> list[str]:
    """Assemble the command line, with the defaults this stack wants."""
    return [
        str(server),
        "-m",
        str(model),
        "-c",
        str(args.ctx),
        "-ngl",
        str(args.gpu_layers),
        "--host",
        args.host,
        "--port",
        str(args.port),
        # One slot, because -c is divided between slots and a job's text has
        # to fit in one of them.
        "--parallel",
        "1",
    ]


def main() -> None:
    """Start the server, and keep this process on it until it stops."""
    parser = argparse.ArgumentParser(
        prog="ctxworker.runserver",
        description="Start llama-server with the weights and flags this stack uses.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-dir", default="")
    parser.add_argument("--dest", default="", help="where llama-server lives")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ctx", type=int, default=DEFAULT_CTX)
    parser.add_argument("--gpu-layers", type=int, default=DEFAULT_GPU_LAYERS)
    parser.add_argument(
        "--install", action="store_true", help="download what is missing first"
    )
    args, extra = parser.parse_known_args()

    dest = Path(args.dest) if args.dest else getserver.default_dest()
    directory = Path(args.model_dir) if args.model_dir else default_dir()
    server = ensure_server(dest, args.install)
    model = ensure_model(args.model, directory, args.install)

    line = command(server, model, args) + extra
    print(" ".join(line), file=sys.stderr)
    raise SystemExit(subprocess.call(line))


if __name__ == "__main__":
    main()
