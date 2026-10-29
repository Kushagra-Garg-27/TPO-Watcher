# Stage C2 standalone operator-tool image (isolated tooling context).
#
# BUILD CONTEXT IS operator_tool/ ITSELF (see docker-compose.operator.yml):
#   docker build -f operator_tool/Dockerfile -t tpo-operator-mfa:stage-c2 operator_tool/
# Only the files inside operator_tool/ are sent to the builder - the watcher
# sources, reports, credentials and scratch artifacts can never enter this
# image. The image is separate from the qualified watcher image: it contains
# ONLY the operator tool, runs as a one-shot non-daemon command, exposes no
# ports (no CDP/debugging endpoint), and performs a single operator-driven
# provisioning transaction per invocation.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="TPO-Watcher MFA operator tool"
LABEL org.opencontainers.image.description="Stage C2 standalone human-assisted MFA session provisioning tool"
LABEL org.opencontainers.image.licenses="UNLICENSED"

# chromium runtime dependencies for headless launch on Debian slim (incl. arm64)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libnspr4 \
        libnss3 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/operator_tool
COPY requirements.txt /opt/operator_tool/requirements.txt
# Browsers are installed to an explicit SHARED path (not the builder user's
# home cache): Playwright resolves $PLAYWRIGHT_BROWSERS_PATH at runtime, and
# a home-cache install under /root would be neither addressable nor readable
# by the non-root runtime user below (confirmed defect, run-2 correction).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright

COPY . /opt/operator_tool/

# Non-root runtime user (uid 1000); state files it creates stay owner-only.
# NOTE (volume compatibility, Stage C2 gate 4): a uid-1000 process CANNOT
# create the session directory inside a root:root 0755 volume root. The
# authorized one-time setup must create /app/data/session owned by uid 1000
# with mode 0700 BEFORE first use - see
# reports/TPO_WATCHER_STAGE_C2_ARM64_EXACT_TIP_PREPARATION.md section 4.
RUN useradd --create-home --uid 1000 operator
USER operator
WORKDIR /home/operator

# One-shot, human-operated, bounded entrypoint. No daemon, no ports.
ENTRYPOINT ["python", "/opt/operator_tool/mfa_operator.py"]
