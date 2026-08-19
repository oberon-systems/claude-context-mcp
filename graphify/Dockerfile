# Two stages for one dependency. Every other pinned package ships a manylinux
# wheel - the tree-sitter grammars carry their compiled parser, psycopg2-binary
# links libpq statically - but llama-cpp-python is published to PyPI as a
# source distribution and compiles llama.cpp. The compiler stays in the builder
# so the image that runs is the one without it.
FROM python:3.11-slim AS builder

WORKDIR /build

# Unpinned on purpose: none of these survives into the image that runs, so a
# pin would only make the build fail the day the base image moves. git is not
# optional despite nothing being cloned: llama.cpp's CMake looks it up with
# find_program(... REQUIRED) to stamp the build info.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake build-essential git \
    && rm -rf /var/lib/apt/lists/*

# GGML_NATIVE=OFF matters most: the default bakes this host's instruction set
# into the image, which then crashes on an older CPU. BLAS and CUDA are off
# because the indexer summarizes short heads on the CPU it was given.
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_BLAS=OFF -DGGML_CUDA=OFF"

COPY requirements.txt .
# The extra index carries prebuilt CPU wheels of llama-cpp-python. When it has
# none for this interpreter, pip falls back to the sdist and the toolchain
# above builds it.
RUN pip wheel --no-cache-dir --wheel-dir /wheels \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    -r requirements.txt

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

# The compiled llama.cpp links against OpenMP, and the wheel does not carry it.
# The one apt package the runtime image needs, and the reason to check
# `import llama_cpp` after a build rather than only at index time.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt \
    && rm -rf /wheels

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
