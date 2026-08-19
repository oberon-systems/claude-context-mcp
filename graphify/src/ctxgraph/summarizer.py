"""Summarize a file with a local GGUF model, and cache what it answered.

The model runs against weights mounted read-only at LLM_MODEL_PATH, and it is
slow enough - seconds per file on a CPU - that nothing here runs it by
default. An index run writes the summary the parsers have always written and
carries on; `make summarize` comes back afterwards and replaces those with the
model's, marking each one `summary_source: llm` so the pass can be stopped and
resumed without repeating itself.

Every answer is shaped into the same one line `summaries.extract_summary`
returns, so nothing downstream can tell which of the two wrote a given summary
apart from its quality.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
from pathlib import Path

from llama_cpp import Llama
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    LLM_CTX,
    LLM_INPUT_CHARS,
    LLM_MAX_TOKENS,
    LLM_THREADS,
    MAX_SUMMARY_LENGTH,
)
from ctxgraph.identifiers import truncate
from ctxgraph.storage import (
    get_cached_summary,
    put_cached_summary,
    save_llm_summary,
)

LOG = logging.getLogger(__name__)

GGUF_MAGIC = b"GGUF"
# Shorter than this and the model has said nothing a file name does not.
MIN_SUMMARY_LENGTH = 12
SYSTEM_PROMPT = (
    "You are a code summarizer. Answer with one plain sentence describing "
    "what the file is for. No markdown, no preamble, no line breaks."
)
# The model writes prose, and prose arrives with typographic punctuation the
# repository's ASCII rule does not allow. Mapped rather than dropped, or a
# quoted name loses its quotes.
ASCII_SUBSTITUTES = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
    0x2026: "...",
    0x00A0: " ",
}


def content_key(text: str) -> str:
    """Key the cache by the exact text the model is shown."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def ensure_model(model_path: str) -> None:
    """Check the weights are there before a run starts.

    Called once, so a missing model is one message rather than a traceback per
    file. The magic number is checked too: a download that answered 404 leaves
    a file of the right name holding HTML.
    """
    path = Path(model_path)
    if not path.is_file():
        raise RuntimeError(
            f"summarizer model not found at {model_path}; "
            "run `make llm-model-install` on the host"
        )
    with path.open("rb") as handle:
        magic = handle.read(len(GGUF_MAGIC))
    if magic != GGUF_MAGIC:
        raise RuntimeError(
            f"{model_path} is not a GGUF file ({magic!r}); "
            "delete it and run `make llm-model-install` again"
        )


def shape(reply: str) -> str:
    """Turn what the model said into the one line a summary is."""
    text = reply.strip().translate(ASCII_SUBSTITUTES)
    text = text.encode("ascii", "ignore").decode("ascii")
    for line in text.splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped:
            return truncate(stripped, MAX_SUMMARY_LENGTH)
    return ""


def useful(summary: str, rel_path: str) -> bool:
    """Reject an answer that says no more than the node id already does.

    A small model asked about a file it cannot read - a changelog, a lock
    file, a compose file of pure data - tends to answer with the file name.
    That is worse than the head-of-file summary it would replace.
    """
    if len(summary) < MIN_SUMMARY_LENGTH:
        return False
    spelled = "".join(char for char in summary.lower() if char.isalnum())
    for name in (rel_path.lower(), posixpath.basename(rel_path).lower()):
        if spelled == "".join(char for char in name if char.isalnum()):
            return False
    return True


class Summarizer:
    """One loaded model, and the cache in front of it."""

    def __init__(self, model_path: str, refresh: bool = False) -> None:
        """Load the weights. Raises when they are missing or not GGUF.

        `refresh` distrusts the cache, which is what a forced re-index means
        here: the model runs again over files it has already described.
        """
        ensure_model(model_path)
        self.llm = Llama(
            model_path=model_path,
            n_threads=LLM_THREADS,
            n_ctx=LLM_CTX,
            verbose=False,
        )
        self.refresh = refresh
        self.generated = 0
        self.cached = 0
        self.rejected = 0
        self.failed = 0

    def summarize(self, rel_path: str, text: str) -> str:
        """Ask the model about one file. Raises what llama_cpp raises."""
        response = self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"File: {rel_path}\n\n{text[:LLM_INPUT_CHARS]}",
                },
            ],
            max_tokens=LLM_MAX_TOKENS,
        )
        return shape(response["choices"][0]["message"]["content"] or "")

    def refine(self, cursor: Cursor, project: str, rel_path: str, text: str) -> bool:
        """Replace one file node's generated summary with the model's.

        The node already carries a summary written from the head of the file,
        so a model that fails on one file costs that file nothing but the
        better sentence: the failure is counted and the run goes on.

        Nothing is asked of the model twice. The cache is keyed by the text
        it was shown - rejected answers included, or the files it is worst at
        would be the ones it is asked about on every pass - so a second run
        pays only for the files that changed, and an empty file never reaches
        it at all.
        """
        if not text.strip():
            return False

        key = content_key(text)
        summary = None if self.refresh else get_cached_summary(cursor, project, key)
        if summary is not None:
            self.cached += 1
        else:
            try:
                summary = self.summarize(rel_path, text)
            except Exception as error:
                self.failed += 1
                LOG.warning(
                    "Summarizing %s failed (%s), keeping the head", rel_path, error
                )
                return False
            put_cached_summary(cursor, project, key, summary)
            self.generated += 1

        # Cached before it is judged, and judged on every pass: an answer the
        # model will give again is not worth asking for again, and the file
        # keeps the summary it had either way.
        if not useful(summary, rel_path):
            self.rejected += 1
            LOG.info("Summary of %s says nothing, keeping the head", rel_path)
            return False

        return save_llm_summary(cursor, project, rel_path, summary)

    def close(self) -> None:
        """Free the weights. A gigabyte of resident memory is worth releasing."""
        llm = getattr(self, "llm", None)
        if llm is not None and hasattr(llm, "close"):
            llm.close()
        self.llm = None

    def report(self) -> str:
        """One line on what the model actually did this run."""
        return (
            f"{self.generated} generated, {self.cached} from the cache, "
            f"{self.rejected} rejected, {self.failed} failed"
        )
