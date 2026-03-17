# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY src/ src/
COPY README.md ./
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    fonts-noto-cjk \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Playwright browsers (requires root)
RUN playwright install --with-deps chromium

# Create non-root user
RUN useradd --create-home appuser

# Copy application files
COPY src/ src/
COPY config/ config/

# Create directories for volumes and set ownership
RUN mkdir -p credentials tmp data fonts \
    && chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/credentials", "/app/tmp", "/app/data", "/app/config"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["automator", "web", "--host", "0.0.0.0", "--port", "8080"]
