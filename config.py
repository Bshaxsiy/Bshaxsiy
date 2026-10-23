import os
from pathlib import Path
from dotenv import load_dotenv

# .env faylini yuklash
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    raise ValueError(
        "DIQQAT: .env faylida BOT_TOKEN ko'rsatilmagan! "
        "Telegram @BotFather orqali token olib, uni .env fayliga yozing."
    )

# Google Gemini API sozlamalari
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Yuklab olingan fayllar vaqtincha saqlanadigan papka
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Ma'lumotlar va ruxsatlar bazasi papkasi
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Qo'riqchi (Access Control) sozlamalari
# Masalan: ADMIN_IDS=123456789,987654321
raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in raw_admin_ids.split(",") if i.strip().isdigit()]

# Admin bo'lish uchun maxfiy login parol
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

