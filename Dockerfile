FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/IPandral/Matcharr" \
      org.opencontainers.image.title="Matcharr" \
      org.opencontainers.image.description="Swipe together and match movies from Plex or Jellyfin" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 matcharr
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY LICENSE run.py ./
COPY app/ app/
COPY migrations/ migrations/
RUN chown -R matcharr:matcharr /app
USER matcharr

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail http://127.0.0.1:5000/health || exit 1

CMD ["sh", "-c", "flask --app run.py db-migrate && exec gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 4 run:app"]
