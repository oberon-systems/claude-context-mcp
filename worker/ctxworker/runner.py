"""Load the weights and answer one prompt at a time.

The prompt and the system message both come from the API, so this module has
no opinion about what a summary is: changing the wording is a change to the
server, not a redeploy of the machine with the GPU.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llama_cpp import Llama

LOG = logging.getLogger(__name__)

GGUF_MAGIC = b"GGUF"
# Roughly how many characters of source one token covers. Only used to refuse
# a context window that cannot hold what the job intends to send.
CHARS_PER_TOKEN = 3.5

CUDA_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
# Shipped by the CUDA Toolkit and never by the wheel, and looked for only
# under CUDA_PATH: on Windows this is what a present llama.dll is missing.
CUDA_RUNTIME = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")


def _cuda_note() -> str:
    """Say where the CUDA runtime would be found, and whether it is there."""
    cuda = os.environ.get("CUDA_PATH", "")
    if not cuda:
        return "CUDA_PATH is not set, so the loader has nowhere to look"
    if (Path(cuda) / "bin" / CUDA_RUNTIME[1]).is_file():
        return f"CUDA_PATH is {cuda} and does hold them, so this is another one"
    return f"CUDA_PATH is {cuda}, and its bin directory does not hold them"


def describe_failure(lib_dir: Path | None, system: str, error: str) -> str:
    """Turn a refusal to load llama_cpp into the remedy for it."""
    install = f"pip install -r requirements.txt --extra-index-url {CUDA_INDEX}"
    if lib_dir is None or not lib_dir.is_dir():
        return (
            "llama-cpp-python is not installed. From the worker directory:\n"
            f"    {install}\n"
            f"({error})"
        )

    names = sorted(entry.name for entry in lib_dir.iterdir())
    held = ", ".join(names) or "nothing"
    windows = system.lower().startswith("win")

    if windows and "llama.dll" not in names:
        return (
            f"{lib_dir} has no llama.dll, so the wheel installed there is for\n"
            f"another platform. It holds: {held}.\n"
            "Reinstall on this machine, from the worker directory:\n"
            f"    {install}\n"
            f"({error})"
        )
    if windows:
        return (
            "llama.dll is installed but will not load, which on Windows means\n"
            "one of its dependencies is missing. The CUDA wheels ship\n"
            "llama.dll and ggml-cuda.dll but not the CUDA runtime itself:\n"
            f"    {', '.join(CUDA_RUNTIME)}\n"
            f"and {_cuda_note()}.\n"
            "Install the CUDA Toolkit 12.x, or copy those three DLLs into\n"
            f"    {lib_dir}\n"
            "or reinstall the CPU wheel and run with --gpu-layers 0.\n"
            f"({error})"
        )
    if not any(name.startswith("libllama.") for name in names):
        return (
            f"{lib_dir} has no libllama.so, so the wheel installed there is\n"
            f"for another platform. It holds: {held}.\n"
            f"({error})"
        )
    return f"{error}\n{lib_dir} holds: {held}"


def load_llama() -> type[Llama]:
    """Import the model class, or exit saying what is missing instead."""
    spec = importlib.util.find_spec("llama_cpp")
    package = Path(spec.origin).parent if spec and spec.origin else None
    try:
        from llama_cpp import Llama
    except (ImportError, OSError, RuntimeError) as error:
        lib_dir = package / "lib" if package is not None else None
        raise SystemExit(
            describe_failure(lib_dir, platform.system(), str(error))
        ) from error
    return Llama


class Runner:
    """One loaded model."""

    def __init__(
        self, model_path: str, n_ctx: int = 8192, gpu_layers: int = -1
    ) -> None:
        """Load the weights onto the GPU. Raises when they are not weights."""
        path = Path(model_path)
        if not path.is_file():
            raise SystemExit(
                f"no weights at {model_path}; run `python -m ctxworker.download`"
            )
        with path.open("rb") as handle:
            if handle.read(len(GGUF_MAGIC)) != GGUF_MAGIC:
                raise SystemExit(f"{model_path} is not a GGUF file")

        llama_cls = load_llama()

        LOG.info("Loading %s (n_ctx=%d, gpu_layers=%d)", path.name, n_ctx, gpu_layers)
        self.n_ctx = n_ctx
        self.llm = llama_cls(
            model_path=str(path),
            n_ctx=n_ctx,
            n_gpu_layers=gpu_layers,
            verbose=False,
        )

    def fits(self, input_chars: int, max_tokens: int) -> bool:
        """Say whether the context window can hold what the job will send."""
        return self.n_ctx >= input_chars / CHARS_PER_TOKEN + max_tokens

    def summarize(self, system_prompt: str, prompt: str, max_tokens: int) -> str:
        """Answer one prompt. Returns the reply exactly as the model wrote it."""
        response: dict[str, Any] = self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        return response["choices"][0]["message"]["content"] or ""

    def close(self) -> None:
        """Release the weights."""
        closer = getattr(self.llm, "close", None)
        if closer is not None:
            closer()
