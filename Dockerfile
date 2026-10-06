# One image for both the API and the worker (different commands in docker-compose).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONPATH=/app PATH="/app/.venv/bin:$PATH"

# Dependencies first (cached layer). The heavy `ml` extra (PyTorch) is NOT installed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app app
COPY config config
COPY scripts scripts
COPY data data
COPY dashboard/dist dashboard/dist

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
