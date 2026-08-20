"""Backfill pass: `python -m ctxgraph.summarize`.

Indexing is fast and writes a summary from the head of each file. The model
is slow - seconds per file - so it comes afterwards, over the file nodes no
model has described yet, and marks each one as its own. That makes the pass
resumable: stop it, run it again, and it picks up where it left off rather
than starting over.

It is a reader of the tree and a writer of two columns, so it can run while
an agent is querying the graph.
"""

from __future__ import annotations

import logging
import os

from ctxgraph.config import (
    FORCE_REEXTRACT,
    LLM_MODEL_PATH,
    PROJECT_PATH,
    SUMMARY_LIMIT,
)
from ctxgraph.discovery import read_source
from ctxgraph.indexer import SUMMARY_HEAD_LINES, resolve_project
from ctxgraph.storage import (
    get_db_connection,
    list_files_without_llm_summary,
)
from ctxgraph.summarizer import Summarizer

LOG = logging.getLogger(__name__)

# How often the pass reports where it is. A run over a large tree is measured
# in hours, and a silent hour is indistinguishable from a hung one.
PROGRESS_EVERY = 10


def summarize_project() -> None:
    """Describe every file node of the project the model has not yet."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError(f"{PROJECT_PATH} is not a directory")

    project, _ = resolve_project()
    summarizer = Summarizer(LLM_MODEL_PATH, FORCE_REEXTRACT)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            pending = list_files_without_llm_summary(cursor, project, FORCE_REEXTRACT)
        if SUMMARY_LIMIT:
            LOG.info("Stopping after %d files (SUMMARY_LIMIT)", SUMMARY_LIMIT)
            pending = pending[:SUMMARY_LIMIT]
        LOG.info("Summarizing %d files of project %s", len(pending), project)

        written = 0
        missing = 0
        for position, rel_path in enumerate(pending, start=1):
            content, _ = read_source(os.path.join(PROJECT_PATH, rel_path), rel_path)
            if content is None:
                missing += 1
                continue
            head = "\n".join(content.splitlines()[:SUMMARY_HEAD_LINES])

            # A commit per file, because the pass is meant to be interrupted:
            # what it has described is in the graph already.
            with conn.cursor() as cursor:
                try:
                    written += summarizer.refine(cursor, project, rel_path, head)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    LOG.exception("Failed to store the summary of %s", rel_path)

            if position % PROGRESS_EVERY == 0:
                LOG.info("  %d/%d (%s)", position, len(pending), summarizer.report())

        LOG.info(
            "Done with %s: %d summaries written, %d files unreadable (%s)",
            project,
            written,
            missing,
            summarizer.report(),
        )
    finally:
        summarizer.close()
        conn.close()


def main() -> None:
    """Configure logging and run."""
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    summarize_project()


if __name__ == "__main__":
    main()
