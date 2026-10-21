# autobot-browser: the box a browser autobot works in (BC thread, session BC8, 20 Oct 2026).
# A hidden Chromium browser driven by browser-use. Telemetry and cloud sync switched off.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-liberation ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir browser-use==0.13.11
ENV ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false BROWSER_USE_LOGGING_LEVEL=info
COPY bot_runner.py practice_server.py /opt/bbot/
