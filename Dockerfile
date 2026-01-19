FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
 && rm -rf /var/lib/apt/lists/*

# deps primeiro (cache)
COPY requirements.txt .
RUN pip install -r requirements.txt

# copia projeto
COPY . .

EXPOSE 8000

# healthcheck (precisa existir GET /health)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
