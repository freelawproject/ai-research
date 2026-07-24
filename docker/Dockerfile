FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /opt/extraction

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 8000
ENTRYPOINT ["/opt/extraction/docker/entrypoint.sh"]
CMD ["web-dev"]
