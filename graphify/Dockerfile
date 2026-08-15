FROM python:3.11-slim

# Unbuffered so `docker compose logs` shows progress while the job is running.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install build-essential for compiling tree-sitter bindings.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# psycopg2-binary ships a wheel with libpq statically linked, so no apt layer
# and no compiler are needed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY process_graph.py .

# The indexer only reads the mounted project, so it never needs root. The uid
# is given numerically so it resolves the same way on any host.
RUN useradd --create-home --uid 10001 graphify
USER 10001

CMD ["python", "process_graph.py"]
