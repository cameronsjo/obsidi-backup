FROM python:3.12-alpine AS builder

WORKDIR /build

# Install build dependencies
RUN apk add --no-cache gcc musl-dev

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Build wheel
RUN pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-alpine

# Install runtime dependencies
RUN apk add --no-cache \
    git \
    restic \
    tzdata \
    curl

# Install Python package
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Create app directory
WORKDIR /app

# Create non-root user
RUN adduser -D -u 1000 backup
RUN mkdir -p /app/state && chown -R backup:backup /app

# Default environment
ENV DEBOUNCE_SECONDS=300
ENV HEALTH_PORT=8080
ENV GIT_USER_NAME="Obsidian Backup"
ENV GIT_USER_EMAIL="backup@local"
ENV VAULT_PATH=/vault
ENV STATE_DIR=/app/state

EXPOSE 8080/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:${HEALTH_PORT}/health || exit 1

# Run as root to allow git operations on mounted volumes
# (git safe.directory requires matching ownership or root)
USER root

ENTRYPOINT ["python", "-m", "vault_backup"]
