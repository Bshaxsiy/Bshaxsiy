# 🤖 Ko'p Funksiyali Telegram Bot (AI Chat, Audio Tahlil, Word & PDF, Instagram Yuklovchi & Video Note)

Ushbu Telegram boti quyidagi 4 ta asosiy yo'nalishda xizmat qiladi:
1. 🤖 **Google Gemini AI Chat:** Sun'iy intellekt bilan suhbatlashish, savollarga javob olish, tahlil qilish va rasm tahlili (multimodal AI).
2. 🎙 **Audio & Ovozli Xabar Tahlili (Matn, Ovoz, Word, PDF):** Ovozli xabar yuboring va natijani **matn**, **ovozli xabar**, **Word (.docx)** yoki **PDF (.pdf)** shaklida oling!
3. 📥 **Instagram Video Yuklash:** Instagram platformasidagi **Reels**, **Post videolar** va **IGTV** larni eng yuqori sifatda (MP4) yuklab beradi.
4. 🔘 **Dumaloq Video (Video Note):** Har qanday to'rtburchak videoni markazidan 1:1 formatda qirqib, Telegram uchun **dumaloq video xabar (krujok)** qilib beradi.

---

## ⚡ Asosiy Imkoniyatlari

- 🤖 **Google Gemini AI Chat**:
  - Kontekstni eslab qoluvchi muloqot tizimi (multi-turn conversation).
  - Rasmlarni tahlil qilish (rasm yuborib savol berish imkoniyati).
  - Tezkor savollar uchun `/ai <savol>` buyrug'i.
  - Suhbat tarixini tozalash (`/clear`) va bosh menyuga qaytish (`/exit`).
  - API kalitni chat orqali ham sozlash imkoniyati (`/setkey <kalit>`).
- 🎙 **Audio & Ovozli Xabarlar Tahlili (Format tanlash imkoniyati)**:
  - Foydalanuvchi ovozli xabar yoki audio fayl yuborganida, bot quyidagi tanlovlarni taqdim etadi:
    - 📝 **Matn:** Chatda yozma formatda javob olish.
    - 🎙 **Ovozli (Audio):** Tabiiy o'zbek ayol/erkak ovozida (`edge-tts`) ovozli xabar yoki audio fayl eshitish.
    - 📄 **Word (.docx):** Sarlavhalar va qalin matnlar bilan chiroyli shakllantirilgan Word hujjati.
    - 📑 **PDF (.pdf):** Chop etishga va ulashishga tayyor, to'liq formatlangan PDF hujjati.
    - ✨ **Barchasi birga (Hammasi):** Barcha 4 ta formatda hisobotni birdaniga qabul qilish!
- 📹 **Instagram yuklovchi**: Reels, IGTV va post videolari.
- 🔘 **Dumaloq video (Video Note)**:
  - Botga to'g'ridan-to'g'ri to'rtburchak video jo'natilsa, uni avtomatik dumaloq video xabarga aylantiradi.
  - Instagramdan yuklangan video tagidagi **«🔘 Dumaloq video qilish»** tugmasi orqali ham bitta bosishda dumaloq qilib olish mumkin.
- 🧹 **Avtomatik tozalash (Auto-cleanup)**: Barcha vaqtinchalik audio, video, word va pdf fayllar jo'natilgach, kompyuter xotirasini tejash maqsadida darhol tozalab tashlanadi.
- 🛡 **Aqlli xatoliklarni ushlash**:
  - Profil yopiq (private) bo'lsa ogohlantiradi.
  - Video hajmi 50MB dan oshib ketganda xabar beradi.
  - Ovozli xabarlar Telegram maxfiylik sozlamalarida cheklangan bo'lsa, avtomatik ravishda audio fayl yoki matn sifatida yetkazib beradi.
- ⏳ **Status ko'rsatish**: Yuklash, AI o'ylash, audio tayyorlash, hujjat shakllantirish jarayonida chat action (`typing`, `record_voice`, `upload_document`, `upload_video`) statuslarini ko'rsatadi.

---

## 📂 Loyiha Tuzilishi

```text
├── .env                # Bot tokeni va Gemini API kaliti (maxfiy)
├── .env.example        # Sozlamalar namunasi
├── config.py           # Konfiguratsiya va papkalarni boshqarish
├── ai_service.py       # Google Gemini AI integratsiyasi (matn, rasm, audio)
├── tts_service.py      # Matnni ovozga aylantirish (Edge TTS va FFmpeg)
├── document_service.py # Word (.docx) va PDF (.pdf) hisobot yaratuvchi modul
├── downloader.py       # yt-dlp orqali videolarni yuklash moduli
├── video_converter.py  # FFmpeg orqali to'rtburchak videoni dumaloq qilish moduli
├── handlers.py         # Bot buyruqlari, AI chat, audio, hujjat va video handlerlar
├── main.py             # Botni ishga tushiruvchi asosiy fayl
├── requirements.txt    # Kerakli Python kutubxonalari
└── README.md           # Qo'llanma
```

---

## 🚀 O'rnatish va Ishga Tushirish

### 1. Telegram Bot Tokenini Olish
1. Telegramda [@BotFather](https://t.me/BotFather) botiga kiring.
2. `/newbot` buyrug'i orqali yangi bot yarating va berilgan tokenni oling.

### 2. Google Gemini API Kalitini Olish
1. [Google AI Studio](https://aistudio.google.com/app/apikey) sahifasiga kiring.
2. Bepul API kalit yarating va nusxalang.

### 3. .env Faylini Sozlash
Loyiha papkasidagi `.env` faylini oching va ma'lumotlarni kiriting:
```env
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
GEMINI_MODEL=gemini-3.8-flash
```

### 4. Kutubxonalarni O'rnatish
```bash
pip install -r requirements.txt
```

### 5. Botni Ishga Tushirish
```bash
python main.py
```
