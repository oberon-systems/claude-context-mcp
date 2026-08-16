FROM python:3.11-slim

# Unbuffered so `docker compose logs` shows progress while the job is running.
# PYTHONPATH points at the src root so `python -m ctxgraph` resolves the
# package from any working directory inside the container. Our package is
# `ctxgraph`, not `graphify`: the extractor installs itself under the latter,
# and this path would shadow it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Every pinned dependency ships a manylinux wheel: the tree-sitter grammars
# carry their compiled parser, and psycopg2-binary links libpq statically. No
# apt layer and no compiler are needed, which keeps the image around 250 MB
# smaller.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The graph page draws itself with vis-network, which upstream loads from a
# CDN. Vendored into the image instead, so the page renders on a host with no
# way out to the internet, and so a silent upstream release cannot change how
# the graph looks. The viewer rewrites the script tag to point here.
ARG VIS_NETWORK_VERSION=9.1.9
ARG VIS_NETWORK_URL=https://unpkg.com/vis-network@${VIS_NETWORK_VERSION}/standalone/umd/vis-network.min.js
RUN mkdir -p /app/vendor \
    && python -c "import sys, urllib.request; \
urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "${VIS_NETWORK_URL}" /app/vendor/vis-network.min.js \
    && test -s /app/vendor/vis-network.min.js
ENV VIS_NETWORK_PATH=/app/vendor/vis-network.min.js

COPY src ./src

# The indexer only reads the mounted project, so it never needs root. The uid
# is given numerically so it resolves the same way on any host.
#
# graphify-out is created here rather than left to the bind mount: the
# extractor refuses to read a graph from anywhere else, and a directory docker
# creates on demand belongs to root, which this user could not write into.
RUN useradd --create-home --uid 10001 graphify \
    && mkdir -p /app/graphify-out \
    && chown 10001:10001 /app/graphify-out
USER 10001

CMD ["python", "-m", "ctxgraph"]
