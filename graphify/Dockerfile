FROM python:3.11-slim

# Unbuffered so `docker compose logs` shows progress while the job is running.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Every pinned dependency ships a manylinux wheel: the tree-sitter grammars
# carry their compiled parser, and psycopg2-binary links libpq statically. No
# apt layer and no compiler are needed, which keeps the image around 250 MB
# smaller.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY process_graph.py .

# The indexer only reads the mounted project, so it never needs root. The uid
# is given numerically so it resolves the same way on any host.
RUN useradd --create-home --uid 10001 graphify
USER 10001

CMD ["python", "process_graph.py"]
