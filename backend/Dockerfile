# Multi-stage Dockerfile for FastAPI backend
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Flush logs straight to Cloud Logging
ENV PYTHONUNBUFFERED=1

# Cloud Run sets PORT env; default to 8080
ENV PORT=8080

# Run uvicorn with dynamic port binding
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
