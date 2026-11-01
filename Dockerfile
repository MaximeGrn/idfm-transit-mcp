FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.13-slim
RUN useradd --create-home --uid 10001 mcp
COPY --from=build /build/.venv /build/.venv
USER mcp
ENV PATH="/build/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ENTRYPOINT ["idfm-transit-mcp"]
