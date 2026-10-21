# autobot-box: the box the autobot's worker codes in (BC thread, session BC8, 20 Oct 2026).
# Internet plus a toolbox. The box that runs the hidden check stays python:3.12-slim with no internet.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl git ripgrep jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests beautifulsoup4 html2text
COPY readweb /usr/local/bin/readweb
RUN chmod 755 /usr/local/bin/readweb
