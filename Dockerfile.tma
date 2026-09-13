# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY tma/frontend/package*.json ./
RUN npm ci --prefer-offline --include=dev
COPY tma/frontend/ .
RUN npm run build

# Stage 2: Python FastAPI + repo root (database.py, battle_engine.py, config/, etc.)
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y libpq-dev gcc postgresql-client && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pydantic-settings>=2.0.0 httpx>=0.24.0

# Copy full repo so database.py, battle_engine.py, config/ etc. are available
COPY . .

# Copy built React app from Stage 1
COPY --from=frontend-builder /frontend/dist tma/frontend/dist

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "tma.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
