"""Entry point of the indexing job: `python -m ctxgraph`.

The package is named `ctxgraph` rather than `graphify` because the extractor
it now drives installs itself under `graphify`, and PYTHONPATH would shadow
it here.
"""

from __future__ import annotations

import logging

from ctxgraph.config import FORCE_REEXTRACT, PROJECT_TYPE, SUMMARIZE
from ctxgraph.indexer import resolve_project, scan_and_build_graph


def main() -> None:
    """Configure logging and run."""
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    project, root_path, _ = resolve_project()
    scan_and_build_graph(project, root_path, PROJECT_TYPE, FORCE_REEXTRACT, SUMMARIZE)


if __name__ == "__main__":
    main()
