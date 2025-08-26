FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code
COPY app app

# Port (configurable avec $PORT en PaaS)
EXPOSE 8000

# Lance uvicorn
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
