# QAX in a container: the web UI + CLI with Chromium ready (Playwright 1.44 image).
# Build and run:  docker compose up --build   →  http://localhost:8900
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /qax
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
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
