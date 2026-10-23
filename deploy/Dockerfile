FROM node:22-bookworm-slim AS search-dependencies
ENV ONNXRUNTIME_NODE_INSTALL_CUDA=skip
WORKDIR /smart-search
COPY smart-search/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOSS_DATA_DIR=/data MOSS_SEARCH_ENABLED=1 MOSS_ENV=production
COPY --from=search-dependencies /usr/local/bin/node /usr/local/bin/node
COPY --from=search-dependencies /smart-search/node_modules /app/smart-search/node_modules
COPY backend/requirements.lock.txt /app/backend/requirements.lock.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.lock.txt
COPY --chown=10001:10001 backend /app/backend
COPY --chown=10001:10001 moss-prototype/*.js moss-prototype/index.html moss-prototype/style.css moss-prototype/assets.json /app/moss-prototype/
COPY --chown=10001:10001 moss-prototype/vendor/ /app/moss-prototype/vendor/
COPY --chown=10001:10001 smart-search /app/smart-search
RUN groupadd --gid 10001 moss && useradd --uid 10001 --gid moss --no-create-home moss && mkdir -p /data && chown moss:moss /data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["python", "-m", "backend.serve"]
