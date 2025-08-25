# backend/Dockerfile
FROM python:3.11-slim

# Evita pyc y buffers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Paquetes base (curl para healthcheck / debugging)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tini build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala deps primero (cache)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copia el código
COPY app ./app

# Puerto de FastAPI
EXPOSE 8000

# Comando por defecto: uvicorn con autoreload
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--reload"]
