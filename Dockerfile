# Playwright's image ships Chromium and its system libraries. The pinned
# playwright package must match the image version so it finds those browsers.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

WORKDIR /app
COPY pyproject.toml README.md ./
COPY uarb_agent ./uarb_agent
RUN pip install --no-cache-dir . "playwright==1.64.0"

ENV PYTHONUNBUFFERED=1
# Settings come from the environment, or from /app/.env.local mounted at run time:
#   docker run -d --restart=always -v $PWD/.env.local:/app/.env.local:ro uarb-agent
CMD ["python", "-m", "uarb_agent", "-v", "run"]
