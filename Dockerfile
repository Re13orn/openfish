FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/mvp_scaffold

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python -m venv /app/mvp_scaffold/.venv \
    && /app/mvp_scaffold/.venv/bin/pip install --upgrade pip \
    && /app/mvp_scaffold/.venv/bin/pip install -r /app/mvp_scaffold/requirements.txt

CMD ["/app/mvp_scaffold/.venv/bin/python", "-m", "src.main"]
