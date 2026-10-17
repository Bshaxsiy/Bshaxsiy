# 🤖 Ko'p Funksiyali Telegram Bot (AI Tahlil, Hujjatlar, Deep Research, Slaydlar, Excel & Video)

Telegram boti: **@Bshaxsiy_bot**

Ushbu bot eng zamonaviy sun'iy intellekt (**Google Gemini**) va media vositalari bilan qurollangan bo'lib, quyidagi **5 ta ixtisoslashgan bo'lim** hamda **AI Chat** rejimida ishlaydi:

---

## 📑 Asosiy Bo'limlar

### 1. 📂 &lt;Hujjatlar tahlili&gt;
- **Fayllarni qabul qilish va chuqur tahlil qilish:** PDF (`.pdf`), Word (`.docx`), Excel (`.xlsx`), matnli fayllar (`.txt`) va hujjat rasmlari.
- **Tahlil tarkibi:**
  - Hujjatning asosiy mavzusi va maqsadi.
  - Muhim sanalar, shaxslar, tashkilotlar va miqdoriy ko'rsatkichlar.
  - Asosiy xulosalar, tavsiyalar va xatarlar.

### 2. 🔬 &lt;deep research&gt;
- Har qanday mavzu, fan yoki biznes g'oya bo'yicha **chuqur tadqiqot va batafsil tahliliy hisobot**.
- **Hisobot strukturasi:**
  - Kirish va soha konteksti.
  - Asosiy omillar, faktlar va statistik tahlil.
  - Muammolar, cheklovlar va xavf-xatarlar.
  - Strategik takliflar va kelajak istiqbollari.

### 3. 🎵 &lt;musiqa va video&gt;
- 📥 **Instagram Reels, Video va Postlarni yuklash:** Havolani yuborish orqali to'g'ridan-to'g'ri MP4 video yuklab olish.
- 🎵 **Videodan MP3 musiqani ajratish:** Yuklangan yoki botga yuborilgan videodan toza audio trek (`.mp3`) ajratib berish.
- 🔘 **Dumaloq video (Video Note / Krujok):** Videoni 1:1 formatda dumaloq video xabarga aylantirish.

### 4. 📊 &lt;slaydlar, prezentatsiya&gt;
- Har qanday matn yoki mavzu bo'yicha professional **PowerPoint taqdimot (`.pptx`)** faylini avtomatik yaratish.
- Zamonaviy dizayn, sarlavha slaydi, reja, asosiy bandlar va xulosa slaydlari bilan to'liq tayyor taqdimot.

### 5. 📑 &lt;PDF,WORD,EXCEL&gt;
- **Word (.docx):** Chiroyli shriftlar, sarlavhalar va tuzilmali Word hujjatlarini yaratish.
- **PDF (.pdf):** Chop etishga tayyor, to'liq formatlangan PDF fayllari.
- **Excel (.xlsx):** Mavzu yoki ma'lumotlar asosida tartibli jadvallar, sarlavha qatorlari va avtomatik ustun kengliklari bilan Excel faylini yaratish.

---

## 🔄 Bo'limlarga O'tkazish va Tahrirlash (Universal Actions)

Botda hosil qilingan **har qanday natija** ostida 2 ta qulay tugma taqdim etiladi:

1. **📂 Bo'limga o'tkazish:**
   - 📄 **Word (.docx) ga o'tkazish**
   - 📑 **PDF (.pdf) ga o'tkazish**
   - 📊 **PowerPoint (.pptx) ga o'tkazish**
   - 📈 **Excel (.xlsx) jadvalga o'tkazish**
   - 🔬 **Deep Research tahlilga yo'naltirish**
   - 🎙 **Ovozli xabar / Audio MP3 ga aylantirish**
2. **✏️ Tahrirlash:**
   - ➕ **Kengaytirish:** Ma'lumotni yanada chuqurlashtirish va yangi misollar bilan to'ldirish.
   - ✂️ **Qisqartirish:** Asosiy xulosalarni saqlagan holda ixcham qilib berish.
   - 🔄 **Qayta yozish:** Professional va tushunarli uslubda qayta shakllantirish.
   - 🇺🇿 **O'zbekchaga tarjima:** Matnni ravon o'zbek tiliga tarjima qilish.

---

## 🎙 Audio va Ovozli Xabarlar Tahlili
- Botga ovozli xabar yoki audio fayl yuborilganda, uni quyidagi formatlarda qabul qilish imkoniyati:
  - 📝 **Matn:** Matn ko'rinishida yozma tahlil.
  - 🎙 **Ovozli (Audio):** Tabiiy o'zbek ovozida (`edge-tts`) ovozli xabar.
  - 📄 **Word (.docx):** Word fayli.
  - 📑 **PDF (.pdf):** PDF hujjati.
  - ✨ **Barchasi birga (Hammasi):** Barcha formatlarni birdaniga qabul qilish.

---

## 📂 Loyiha Tuzilishi

```text
├── .env                # Bot tokeni va Gemini API kaliti (maxfiy)
├── .env.example        # Sozlamalar namunasi
├── config.py           # Konfiguratsiya va doimiy parametrlar
├── ai_service.py       # Google Gemini AI (Chat, Deep Research, Hujjat tahlili, Tahrirlash)
├── tts_service.py      # Matnni ovozga aylantirish (Edge TTS va FFmpeg)
├── document_service.py # Word (.docx), PDF (.pdf), Excel (.xlsx) va PPTX taqdimot generatori
├── downloader.py       # yt-dlp orqali videolarni yuklash va MP3 ajratish
├── video_converter.py  # FFmpeg orqali dumaloq video tayyorlash
├── handlers.py         # Bot menyusi, bo'limlar, transfer va edit tugmalari
├── main.py             # Asosiy ishga tushirish moduli
├── requirements.txt    # Kerakli Python kutubxonalari
└── README.md           # Qo'llanma
```

---

## 🚀 O'rnatish va Ishga Tushirish

### 1. Telegram Bot Tokenini Olish
Telegramda [@BotFather](https://t.me/BotFather) botiga kiring va `/newbot` buyrug'i orqali yangi bot yarating.

### 2. Google Gemini API Kalitini Olish
[Google AI Studio](https://aistudio.google.com/app/apikey) sahifasidan bepul API kalit oling.

### 3. .env Faylini Sozlash
Loyiha papkasida `.env` faylini quyidagicha to'ldiring:
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
