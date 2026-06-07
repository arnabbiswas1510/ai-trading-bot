# ==========================================
# Stage 1: Build the React Frontend
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend

# Install dependencies
COPY frontend/package.json ./
RUN npm install

# Copy source code and build assets
COPY frontend/ ./
RUN npm run build

# ==========================================
# Stage 2: Package Python Backend & Assets
# ==========================================
FROM python:3.12-slim
WORKDIR /app

# Set env configurations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/trading_bot.db

# Install backend dependencies
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend python code
COPY backend/ ./backend/

# Copy compiled frontend assets from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Create persistent data directory for SQLite database
RUN mkdir -p /app/data

EXPOSE 8000

# Set working directory to backend so Python path imports function properly
WORKDIR /app/backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
