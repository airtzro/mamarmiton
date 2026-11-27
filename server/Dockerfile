# Build from the repository root (the server reuses the app's recipe model):
#   docker build -f server/Dockerfile -t mamarmiton-api .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MAMARMITON_SERVER_DATA=/data

WORKDIR /app

COPY server/pyproject.toml server/
COPY server/mamarmiton_server server/mamarmiton_server
RUN pip install ./server

# only the recipe model of the desktop app, without its UI dependencies
COPY pyproject.toml README.md ./
COPY mamarmiton/__init__.py mamarmiton/models.py mamarmiton/quantities.py mamarmiton/redact.py mamarmiton/
RUN pip install --no-deps . && rm -rf /app/server /app/mamarmiton /app/*.toml /app/*.md /app/build

RUN mkdir -p /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "-m", "mamarmiton_server", "serve", "--port", "8000"]
