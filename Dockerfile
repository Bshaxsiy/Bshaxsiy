FROM python:3.11-slim

# FFmpeg va tizim vositalarini o'rnatish
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Koddagi modda havolalari laws/ kutubxonasida mavjudligini tekshirish: topilmasa build to'xtaydi
RUN python check_citations.py ai_service.py legal_factcheck.py document_service.py

CMD ["python", "main.py"]
