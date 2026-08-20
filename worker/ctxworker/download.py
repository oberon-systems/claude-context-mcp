"""Fetch the summarizer weights, on Windows as on Linux.

Downloaded beside the target name and moved into place only once the file
begins with the GGUF magic: an HTTP error page saved under the model's name
would otherwise be found by llama.cpp, a run later.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

from ctxworker.catalogue import DEFAULT_MODEL, default_dir, file_name, url

GGUF_MAGIC = b"GGUF"


def is_gguf(path: Path) -> bool:
    """Say whether a file begins with the GGUF magic number."""
    with path.open("rb") as handle:
        return handle.read(len(GGUF_MAGIC)) == GGUF_MAGIC


def fetch(model: str, directory: Path, force: bool = False) -> Path:
    """Download one model, and return where it landed."""
    target = directory / file_name(model)
    if target.is_file() and target.stat().st_size and not force:
        print(f"model: kept ({target})")
        return target

    directory.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    source = url(model)
    print(f"model: downloading {source}", file=sys.stderr)
    with urllib.request.urlopen(source) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)

    if not is_gguf(partial):
        raise SystemExit(f"not a GGUF file, kept as {partial}")
    partial.replace(target)
    print(f"model: written to {target}")
    return target


def main() -> None:
    """Download the weights named on the command line."""
    parser = argparse.ArgumentParser(description="Download summarizer weights.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dir", default=None, help="where to keep the weights")
    parser.add_argument("--force", action="store_true", help="download it again")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only say where the weights would be, and whether they are there",
    )
    args = parser.parse_args()

    directory = Path(args.dir) if args.dir else default_dir()
    target = directory / file_name(args.model)
    if args.check:
        state = "present" if target.is_file() and is_gguf(target) else "missing"
        print(f"{target} ({state})")
        return
    fetch(args.model, directory, args.force)


if __name__ == "__main__":
    main()
