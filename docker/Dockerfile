FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run migrations on startup, then launch gunicorn
# Use 2 workers on Pi to avoid OOM; timeout extended for SSE imports
EXPOSE 8001

CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--worker-class", "gevent", "--workers", "2", "--worker-connections", "20", "--timeout", "300", "wsgi:app"]
