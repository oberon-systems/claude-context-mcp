"""Answer prompts through a llama.cpp server instead of loading the weights.

A prebuilt llama-cpp-python wheel is compiled for one instruction set, and
ggml decides which kernels exist at compile time: a wheel built with AVX-512
claims AVX-512 on every machine, and a CPU without it dies on the first
kernel that uses one, with an illegal instruction and no message. No flag
avoids that. The llama.cpp release binaries ship one ggml-cpu library per
instruction set and choose at load time, so pointing the worker at
llama-server makes the CPU of the machine stop mattering.

It also unties the worker from the GPU: with this backend the process needs
neither weights nor llama-cpp-python, only a route to the server, so the
three roles - the stack, this loop, and the model - can sit on three
machines.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ctxworker.client import ApiError
from ctxworker.runner import CHARS_PER_TOKEN

LOG = logging.getLogger(__name__)

CHAT_PATH = "/v1/chat/completions"


def describe_refusal(base_url: str, error: str) -> str:
    """Turn a server that did not answer into the remedy for it."""
    return (
        f"no llama.cpp server answered at {base_url} ({error}).\n"
        "Start one on the machine with the GPU:\n"
        "    llama-server -m <model.gguf> -c 8192 -ngl 99 "
        "--host 127.0.0.1 --port 8080 --parallel 1\n"
        "and use --host 0.0.0.0 with --api-key if it is on another machine.\n"
        "worker/README.md has where to download it and how to check it."
    )


class ServerRunner:
    """One llama.cpp server, used as if it were a model loaded here."""

    widen_hint = "restart llama-server with a larger -c"

    def __init__(
        self, base_url: str, api_key: str = "", timeout: float = 300.0
    ) -> None:
        """Point at a server and read back what it is running."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.n_ctx = 0

        try:
            props = self.call("/props")
        except ApiError as error:
            if error.status == 401:
                raise SystemExit(
                    f"{self.base_url} refused the request (401). It was started "
                    "with --api-key, so pass the same key as "
                    "--llama-server-key."
                ) from error
            raise SystemExit(describe_refusal(self.base_url, str(error))) from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise SystemExit(describe_refusal(self.base_url, str(error))) from error

        settings = props.get("default_generation_settings") or {}
        self.n_ctx = int(settings.get("n_ctx") or props.get("n_ctx") or 0)
        LOG.info(
            "Model server at %s: %s, n_ctx=%s, %s slot(s)",
            self.base_url,
            props.get("model_path") or "model not named",
            self.n_ctx or "unknown",
            props.get("total_slots", "?"),
        )

    def call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make one request. Every failure arrives as ApiError or URLError."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST" if data is not None else "GET",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "ignore")[:200]
            raise ApiError(error.code, detail) from None

    def fits(self, input_chars: int, max_tokens: int) -> bool:
        """Say whether the server's context window holds what will be sent."""
        if self.n_ctx <= 0:
            return True
        return self.n_ctx >= input_chars / CHARS_PER_TOKEN + max_tokens

    def summarize(self, system_prompt: str, prompt: str, max_tokens: int) -> str:
        """Answer one prompt. Returns the reply exactly as the model wrote it."""
        answer = self.call(
            CHAT_PATH,
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        choices = answer.get("choices") or []
        if not choices:
            raise ApiError(200, f"no choices in the answer: {str(answer)[:200]}")
        return choices[0].get("message", {}).get("content") or ""

    def close(self) -> None:
        """Nothing is held here - the server owns the weights."""
