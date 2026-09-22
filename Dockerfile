# AgentCore Runtime image: ARM64 (Graviton) only; an x86 image will not start.
FROM --platform=linux/arm64 python:3.13-slim AS builder
WORKDIR /app
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM --platform=linux/arm64 python:3.13-slim
RUN useradd -m -u 1001 appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY *.py ./
# DOCKER_CONTAINER: BedrockAgentCoreApp only binds 0.0.0.0 when it detects a
#   container, and AgentCore's microVM has no /.dockerenv, so say so explicitly.
# XDG_CACHE_HOME: yfinance keeps a small timezone cache; keep it writable.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    XDG_CACHE_HOME=/tmp
USER appuser
EXPOSE 8080
CMD ["python", "app.py"]
