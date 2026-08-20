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
import re
from pathlib import Path

from llama_cpp import Llama
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    LLM_CTX,
    LLM_INPUT_CHARS,
    LLM_MAX_TOKENS,
    LLM_MODEL_DIR,
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
    "You are a code summarizer. Answer with one plain sentence saying what "
    "the file does, in the present tense, starting with a verb. Never repeat "
    "the file name or its path - the reader already has it. No markdown, no "
    "preamble, no line breaks."
)
# Cuts at the end of the first sentence. A model told to write one still
# writes two now and then, and the second is what the length cap would slice
# through mid-word.
SENTENCE = re.compile(r"(.+?[.!?])(\s|$)")
# "The file `x.py` defines ...", "The x.py file defines ...", "This file
# defines ...". The model opens this way whatever the prompt says, and the
# node already carries the path, so the opening is 30 of the 300 characters
# spent saying nothing.
PREAMBLE = re.compile(
    r"^(?:the|this)\s+(?:file\s+)?[`'\"]?([\w./+-]+)[`'\"]?\s*(?:file\s+)?"
    r"(?:in\s+the\s+[\w./+-]+\s+directory\s+)?",
    re.IGNORECASE,
)
BARE_PREAMBLE = re.compile(r"^(?:the|this)\s+file\s+", re.IGNORECASE)
# What the subject left behind: "... is a build script" reads as "Is a build
# script" once the subject is gone, and as "A build script" once this is too.
DANGLING_VERB = re.compile(r"^is\s+", re.IGNORECASE)
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


def resolve_model(configured: str = "", directory: str = LLM_MODEL_DIR) -> str:
    """Settle on which weights to load, and check that they are weights.

    Every `make` target that runs the model names the file it downloaded, so
    `configured` is normally set. Without it the mount is searched, and two
    models there is a question rather than a default: an A/B leaves both on
    disk, and picking one by sort order would make the comparison a lie.
    """
    if configured:
        ensure_model(configured)
        return configured

    found = sorted(Path(directory).glob("*.gguf"))
    if not found:
        raise RuntimeError(
            f"no GGUF weights under {directory}; "
            "run `make llm-model-install` on the host"
        )
    if len(found) > 1:
        names = ", ".join(path.name for path in found)
        raise RuntimeError(
            f"{directory} holds several models ({names}); "
            "set LLM_MODEL_PATH to the one to use"
        )
    ensure_model(str(found[0]))
    return str(found[0])


def shape(reply: str) -> str:
    """Turn what the model said into the one line a summary is."""
    text = reply.strip().translate(ASCII_SUBSTITUTES)
    text = text.encode("ascii", "ignore").decode("ascii")
    for line in text.splitlines():
        stripped = line.strip().strip("`").strip()
        if not stripped:
            continue
        if len(stripped) > MAX_SUMMARY_LENGTH:
            match = SENTENCE.match(stripped)
            stripped = match.group(1) if match else stripped
        if len(stripped) > MAX_SUMMARY_LENGTH:
            # On a word, not through one: a summary cut mid-word reads as
            # damage rather than as a summary.
            stripped = stripped[:MAX_SUMMARY_LENGTH].rsplit(" ", 1)[0] + "..."
        return truncate(stripped, MAX_SUMMARY_LENGTH)
    return ""


def strip_preamble(summary: str, rel_path: str) -> str:
    """Drop an opening that only names the file the summary is attached to.

    A summary that names a different file is saying something, so only the
    file this one belongs to is dropped - and a sentence that would be left
    too short to be a summary keeps its opening instead.
    """
    names = {rel_path.lower(), posixpath.basename(rel_path).lower()}
    match = PREAMBLE.match(summary)
    named = match.group(1).lower() if match else ""

    if match is not None and named in names:
        rest = summary[match.end() :]
    elif "." in named or "/" in named:
        return summary
    else:
        bare = BARE_PREAMBLE.match(summary)
        if bare is None:
            return summary
        rest = summary[bare.end() :]

    rest = DANGLING_VERB.sub("", rest.strip()).strip()
    if len(rest) < MIN_SUMMARY_LENGTH:
        return summary
    return rest[0].upper() + rest[1:]


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

    def __init__(self, model_path: str = "", refresh: bool = False) -> None:
        """Load the weights. Raises when they are missing or not GGUF.

        An empty `model_path` means "whatever is mounted", which only answers
        when exactly one model is. `refresh` distrusts the cache, which is what
        a forced run means here: the model describes files it already has.
        """
        self.model_path = resolve_model(model_path)
        LOG.info("Model: %s", self.model_path)
        self.llm = Llama(
            model_path=self.model_path,
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
        return strip_preamble(
            shape(response["choices"][0]["message"]["content"] or ""), rel_path
        )

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
