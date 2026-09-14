FROM node:22-alpine AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_ADMIN_PATH=panel-22652d9c5949747d27ba
ARG RAILWAY_PUBLIC_DOMAIN=localhost
ENV VITE_ADMIN_PATH=$VITE_ADMIN_PATH
ENV VITE_PUBLIC_URL=https://$RAILWAY_PUBLIC_DOMAIN
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
