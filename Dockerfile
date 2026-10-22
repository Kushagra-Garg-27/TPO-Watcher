FROM node:24.11.0-bookworm-slim@sha256:0e5f9744e7d936536405761bb7bfedc61d9e8b4604aa34d0403a7f6c3e5a98f0 AS inert-web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.10-slim-bookworm@sha256:79082fdf411b897d64983893f44e8e1908e21cade2fc5a794d4f46de76c85275 AS inert
ARG TARGETARCH
RUN test "$TARGETARCH" = arm64
WORKDIR /opt/tpo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-linux-arm64.lock ./
RUN python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements-linux-arm64.lock && python -m pip check
COPY app/production_main.py app/production_main.py
COPY app/monitoring/deployment_policy.py app/monitoring/deployment_policy.py
COPY app/api/app.py app/api/app.py
COPY app/health/server.py app/health/server.py
COPY --from=inert-web /build/app/static/ app/static/
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-B", "-m", "app.production_main"]
CMD ["--policy", "/run/tpo/runtime-policy.json"]

# Keep the existing default build/startup behavior explicitly in the last stage.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS legacy

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copy application
COPY . .

# Environment variables
ENV PYTHONUNBUFFERED=1

# Run the watcher
CMD ["python", "-m", "app.main"]

# Source revision provenance metadata (placed at end to protect build cache)
ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/Kushagra-Garg-27/TPO-Watcher"

