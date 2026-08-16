"""Entry point of the indexing job: `python -m ctxgraph`.

The package is named `ctxgraph` rather than `graphify` because the extractor
it now drives installs itself under `graphify`, and PYTHONPATH would shadow
it here.
"""

from __future__ import annotations

import logging

from ctxgraph.indexer import scan_and_build_graph


def main() -> None:
    """Configure logging and run."""
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    scan_and_build_graph()


if __name__ == "__main__":
    main()
