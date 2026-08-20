"""The text half of summarizing: the prompt, the shaping, the cache key.

Split out of `summarizer` because that module loads llama_cpp at import time.
The worker API applies the same prompt and the same gates to a sentence a
remote worker sends back, and it has no reason to carry a copy of llama.cpp to
do it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import re

from ctxgraph.config import (
    CONTENT_DENIED_NAMES,
    CONTENT_STORE_CHARS,
    MAX_SUMMARY_LENGTH,
)
from ctxgraph.identifiers import truncate

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
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def body_for_storage(rel_path: str, content: str) -> str:
    """Return the text of a file as graph_nodes.content should hold it.

    Empty for a run that stores no content, and for a file whose text must not
    leave this machine: the worker API serves this column over the network,
    and a tree without a .ctxignore is the case this exists for.
    """
    if CONTENT_STORE_CHARS <= 0:
        return ""
    name = posixpath.basename(rel_path)
    for pattern in CONTENT_DENIED_NAMES:
        if fnmatch.fnmatch(name, pattern):
            return ""
    return content[:CONTENT_STORE_CHARS]
