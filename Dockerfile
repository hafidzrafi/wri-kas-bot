FROM python:3.12-slim

# Install uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy project definition and source tree
COPY pyproject.toml README.md ./
COPY src/ src/

# Install package and dependencies system-wide
RUN uv pip install --system --no-cache -e .

# Create directory for persistent SQLite and receipts storage
RUN mkdir -p /app/data/receipts

EXPOSE 8000

# Container health probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "wri_kas_bot.app:app", "--host", "0.0.0.0", "--port", "8000"]
