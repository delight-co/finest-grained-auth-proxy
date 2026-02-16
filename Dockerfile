FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY fgap/ fgap/
COPY main.py .

RUN pip install --no-cache-dir .

EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8766/health')"

ENTRYPOINT ["python", "main.py"]
CMD ["--config", "/etc/fgap/config.json5"]
