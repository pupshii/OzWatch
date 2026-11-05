# Build context is the repo root (see docker-compose.yml) so the frontend can be copied in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/sql ./sql
COPY frontend /app/frontend

RUN useradd --create-home --uid 10001 ozwatch
USER ozwatch

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

# --proxy-headers so client IPs/HTTPS are right behind a reverse proxy in production.
# Live SSE streams never close on their own, so cap the graceful-shutdown wait;
# browsers reconnect automatically to the new process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "3"]
