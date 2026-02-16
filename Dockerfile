FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG GH_VERSION=2.86.0
ARG GOG_VERSION=0.11.0

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
       | tar xz -C /tmp \
    && mv /tmp/gh_${GH_VERSION}_linux_amd64/bin/gh /usr/local/bin/gh \
    && mkdir /tmp/gogcli \
    && curl -fsSL "https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
       | tar xz -C /tmp/gogcli \
    && mv /tmp/gogcli/gog /usr/local/bin/gog \
    && rm -rf /tmp/gh_* /tmp/gogcli /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY fgap/ fgap/
COPY main.py .

RUN uv pip install --system --no-cache .

EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8766/health')"

ENTRYPOINT ["python", "main.py"]
CMD ["--config", "/etc/fgap/config.json5"]
