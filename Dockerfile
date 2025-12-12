FROM python:3.11-slim

# FIX: Add DNS servers so that Playwright/httpx can resolve domains
RUN printf "nameserver 1.1.1.1\nnameserver 8.8.8.8\n" > /etc/resolv.conf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install all required dependencies for Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxkbcommon0 \
    libxshmfence1 \
    libgbm1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    fonts-liberation \
    fonts-unifont \
    wget \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser (Chromium only)
RUN playwright install chromium

COPY . .

EXPOSE 8000

# Use Railway dynamic port
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
