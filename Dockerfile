# Production build: React and FastAPI served from the same HTTPS origin.
FROM node:22-alpine AS web_builder
WORKDIR /build/web
COPY web/ ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# Required for Python dependencies installed directly from GitHub
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv
COPY . /app
RUN uv sync --frozen --no-dev
COPY --from=web_builder /build/web/dist /app/web/dist
CMD ["/app/.venv/bin/python", "cloud_start.py"]
