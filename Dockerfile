FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser data ./data

USER 10001

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]
