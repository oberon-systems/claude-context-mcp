FROM python:3.11-slim

# Unbuffered so `docker compose logs` shows progress while the job is running.
# PYTHONPATH points at the src root so `python -m graphify` resolves the
# package from any working directory inside the container.
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

COPY src ./src

# The indexer only reads the mounted project, so it never needs root. The uid
# is given numerically so it resolves the same way on any host.
RUN useradd --create-home --uid 10001 graphify
USER 10001

CMD ["python", "-m", "graphify"]
