# ==========================================
# Stage 1: Build the React Frontend
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend

# Install dependencies
COPY frontend/package.json ./
RUN npm install

# Copy source code (including verify-build.mjs in scripts/) and build
COPY frontend/ ./
RUN npm run build
# ↑ `npm run build` = `vite build && node scripts/verify-build.mjs`
# The verify script greps the compiled bundle for feature fingerprints.
# If any fingerprint is missing the build exits non-zero → layer fails here,
# preventing a stale/partial bundle from ever reaching the final image.

# ==========================================
# Stage 2: Package Python Backend & Assets
# ==========================================
FROM python:3.12-slim
WORKDIR /app

# Build args injected by GitHub Actions (git SHA + UTC timestamp).
# Stored as env vars so /api/version can return them at runtime.
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown

# Set env configurations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/trading_bot.db \
    GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME}

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
