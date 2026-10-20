# QAX in a container: the web UI + CLI on Python 3.12 with Chromium ready.
# Build and run:  docker compose up --build   →  http://localhost:8900
# (The Playwright 1.44 base image ships Python 3.10; QAX needs 3.11+, so the browser is
#  installed into a Python 3.12 image instead.)
FROM python:3.12-slim-bookworm

WORKDIR /qax
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
COPY . .

# Listen inside the container; docker-compose publishes the port on the host's loopback only.
ENV QAX_WEB_HOST=0.0.0.0 \
    QAX_IN_CONTAINER=1 \
    QAX_WEB_PORT=8900 \
    DRY_RUN=true \
    PYTHONUNBUFFERED=1
EXPOSE 8900
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8900/api/config')" || exit 1
CMD ["python", "-m", "web.server"]
