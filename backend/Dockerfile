# ═══════════════════════════════════════════════════════════════════════════════
# Gelka Enerji API - Production Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════
#
# Multi-stage build for smaller image size
#
# Build:
#   docker build -t gelka-api .
#
# Run:
#   docker run -p 8000:8000 --env-file .env gelka-api
#
# ═══════════════════════════════════════════════════════════════════════════════

FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ═══════════════════════════════════════════════════════════════════════════════
# Production image
# ═══════════════════════════════════════════════════════════════════════════════
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    # WeasyPrint dependencies
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    # Playwright/Chromium dependencies (fallback PDF renderer)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Install Playwright Chromium (for PDF fallback)
RUN python -m playwright install chromium

# Copy application code
COPY . .

# Create storage directory
RUN mkdir -p /app/storage /app/data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
