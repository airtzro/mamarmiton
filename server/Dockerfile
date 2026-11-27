# Build from the repository root (the server reuses the app's recipe model and importers):
#   docker build -f server/Dockerfile -t mamarmiton-api .
# Without the local model's library (smaller image, rule-based import only):
#   docker build -f server/Dockerfile --build-arg WITH_LLM=0 -t mamarmiton-api .
FROM python:3.13-slim

# 1 = install llama-cpp-python (prebuilt CPU wheel) so the import can use the desktop's local model
# when its file is in DATA_DIR/models; the model file itself is never part of the image
ARG WITH_LLM=1
ARG LLAMA_CPP_INDEX=https://abetlen.github.io/llama-cpp-python/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MAMARMITON_SERVER_DATA=/data

# Tesseract reads photos and scanned PDFs for the import (French + English); it also brings
# libstdc++, the only system library the llama-cpp-python wheel needs (it bundles its OpenMP)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server/pyproject.toml server/
COPY server/mamarmiton_server server/mamarmiton_server
RUN pip install ./server
RUN if [ "$WITH_LLM" = "1" ]; then \
        pip install --only-binary llama-cpp-python --extra-index-url "$LLAMA_CPP_INDEX" "llama-cpp-python>=0.3,<0.4"; \
    fi

# only the recipe model and the importers of the desktop app, without its UI dependencies
COPY pyproject.toml README.md ./
COPY mamarmiton/__init__.py mamarmiton/models.py mamarmiton/quantities.py mamarmiton/redact.py mamarmiton/config.py mamarmiton/
COPY mamarmiton/importers/__init__.py mamarmiton/importers/documents.py mamarmiton/importers/heuristic.py mamarmiton/importers/pdf_text.py mamarmiton/importers/llm.py mamarmiton/importers/pipeline.py mamarmiton/importers/
RUN pip install --no-deps . && rm -rf /app/server /app/mamarmiton /app/*.toml /app/*.md /app/build

RUN mkdir -p /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "-m", "mamarmiton_server", "serve", "--port", "8000"]
