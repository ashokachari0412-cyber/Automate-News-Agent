# ==============================================================================
# Multi-Container Base Dockerfile for Personal AI News Agent
# Services: API Orchestrator, Telegram Bot, Pipeline Scheduler
# ==============================================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files & enable unbuffered standard I/O
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies (ffmpeg, curl for healthchecks, fonts for 3:4 carousels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fontconfig \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Install Playwright browser dependencies (for Twitter collection if in Playwright mode)
RUN python -m playwright install --with-deps chromium || true

# Copy application codebase
COPY . /app/

# Ensure runtime directories exist
RUN mkdir -p /app/artifacts/videos /app/artifacts/carousels /app/artifacts/audio /app/assets /app/userdata

# Expose default API port
EXPOSE 8000

# Default command runs FastAPI server; overridden per service in docker-compose.yml
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
