"""The weights the summarizer can run, and where they come from.

The only copy of this table. The root Makefile reads it through
`python3 -m ctxworker.catalogue`, so the download the Linux host does and the
one the Windows worker does can never drift apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HUGGINGFACE = "https://huggingface.co/{repo}/resolve/main/{name}"

# Quantized to q4_k_m throughout: it is the size that fits a 6 GB laptop GPU
# with room for the context window, and the summaries are one sentence long.
MODELS: dict[str, tuple[str, str]] = {
    "qwen-1.5b": (
        "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
    ),
    "qwen-3b": (
        "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
    ),
    "qwen-7b": (
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    ),
    "qwen-0.5b": (
        "Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
    ),
    "smollm2": (
        "HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF",
        "smollm2-1.7b-instruct-q4_k_m.gguf",
    ),
}
DEFAULT_MODEL = "qwen-1.5b"


def file_name(model: str) -> str:
    """Return the GGUF file name of a model, or raise naming the choices."""
    if model not in MODELS:
        known = ", ".join(sorted(MODELS))
        raise SystemExit(f"unknown model {model!r}, pick one of: {known}")
    return MODELS[model][1]


def url(model: str) -> str:
    """Return where the weights of a model are downloaded from."""
    repo, name = MODELS[model]
    return HUGGINGFACE.format(repo=repo, name=name)


def default_dir() -> Path:
    """Return where weights are kept, which is not the same place on Windows."""
    if sys.platform == "win32":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "context-mcp" / "models"
    return Path.home() / ".local" / "share" / "context-mcp" / "models"


def main() -> None:
    """Answer one question about the table, for the Makefile to read."""
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m ctxworker.catalogue <file|url|list> [model]")
    question = sys.argv[1]
    if question == "list":
        print(" ".join(sorted(MODELS)))
        return
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL
    file_name(model)
    print(file_name(model) if question == "file" else url(model))


if __name__ == "__main__":
    main()
