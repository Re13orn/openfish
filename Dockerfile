FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/openai/codex/releases/latest/download/install.sh -o /tmp/install-codex.sh \
    && chmod +x /tmp/install-codex.sh \
    && CODEX_INSTALL_DIR=/usr/local/bin /tmp/install-codex.sh \
    && codex --version \
    && rm -f /tmp/install-codex.sh

COPY . /app

RUN python -m venv /app/mvp_scaffold/.venv \
    && /app/mvp_scaffold/.venv/bin/pip install --upgrade pip \
    && /app/mvp_scaffold/.venv/bin/pip install -r /app/mvp_scaffold/requirements.txt \
    && /app/mvp_scaffold/.venv/bin/pip install /app/mvp_scaffold

RUN chmod +x /app/docker/docker-entrypoint.sh

ENTRYPOINT ["/app/docker/docker-entrypoint.sh"]
