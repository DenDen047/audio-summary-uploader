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
    ffmpeg \
    fonts-noto-cjk \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Playwright browsers
RUN playwright install --with-deps chromium

# Copy application files
COPY src/ src/
COPY config/ config/

# Create directories for volumes
RUN mkdir -p credentials tmp data fonts

VOLUME ["/app/credentials", "/app/tmp", "/app/data", "/app/config"]

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["automator", "web", "--host", "0.0.0.0", "--port", "8080"]
