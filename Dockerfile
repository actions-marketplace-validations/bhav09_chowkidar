# Multi-stage production Dockerfile for Chowkidar
FROM python:3.10-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

FROM python:3.10-slim AS runner

WORKDIR /app

# Create a non-root user for security
RUN groupadd -g 10001 chowkidar && \
    useradd -u 10001 -g chowkidar -m -s /bin/bash chowkidar

COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin/chowkidar /usr/local/bin/chowkidar

# Set up configuration and data directories
RUN mkdir -p /app/.chowkidar && \
    chown -R chowkidar:chowkidar /app

USER chowkidar

ENV CHOWKIDAR_HOME=/app/.chowkidar
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["chowkidar"]
CMD ["daemon"]
