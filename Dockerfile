# syntax=docker/dockerfile:1
# Imagem da aplicação FinCore. O banco roda em outro container (veja compose.yaml).

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependências primeiro, para reaproveitar o cache quando só o código mudar.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python:3.14-slim AS runtime
# Usuário sem privilégios e sem shell de login; o código pertence ao root e é só leitura.
RUN groupadd --system --gid 10001 fincore \
    && useradd --system --uid 10001 --gid fincore --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin fincore
WORKDIR /app
COPY --from=build /app /app
COPY templates ./templates
COPY static ./static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

USER fincore:fincore
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]
CMD ["fincore"]
