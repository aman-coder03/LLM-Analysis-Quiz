FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends         curl git fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2         libdbus-1-3 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3         libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libgtk-3-0         && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
