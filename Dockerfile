# Imagen unica para el portal (web) y el worker.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libmagic1 ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# torch CPU primero: evita descargar las ruedas CUDA (varios GB) que no se usan en un VPS sin GPU
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
# editable: config/ se resuelve relativo al codigo fuente (/app/config)
RUN pip install --no-cache-dir -e ".[all,portal]" \
    && python -m spacy download es_core_news_md

ENV AIAUDIT_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME ["/data", "/root/.cache"]
EXPOSE 8000
CMD ["uvicorn", "aiaudit.portal.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
