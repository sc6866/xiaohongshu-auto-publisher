FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY agents /app/agents
COPY common /app/common
COPY config /app/config
COPY data /app/data
COPY scheduler /app/scheduler
COPY skills /app/skills
COPY webui /app/webui
COPY main.py /app/main.py

RUN python -m pip install --upgrade pip \
    && python -m pip install .

ARG INSTALL_PLAYWRIGHT=0
RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then python -m playwright install --with-deps chromium; fi

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import json,sys,urllib.request; payload=json.load(urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)); sys.exit(0 if payload.get('status') == 'ok' else 1)"

CMD ["python", "main.py", "web", "--host", "0.0.0.0", "--port", "8787"]
