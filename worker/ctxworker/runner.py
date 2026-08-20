"""Load the weights and answer one prompt at a time.

The prompt and the system message both come from the API, so this module has
no opinion about what a summary is: changing the wording is a change to the
server, not a redeploy of the machine with the GPU.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

GGUF_MAGIC = b"GGUF"
# Roughly how many characters of source one token covers. Only used to refuse
# a context window that cannot hold what the job intends to send.
CHARS_PER_TOKEN = 3.5


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

        from llama_cpp import Llama

        LOG.info("Loading %s (n_ctx=%d, gpu_layers=%d)", path.name, n_ctx, gpu_layers)
        self.n_ctx = n_ctx
        self.llm = Llama(
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
