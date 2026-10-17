# 🤖 Ko'p Funksiyali Telegram Bot (AI Tahlil, Hujjatlar, Deep Research, Slaydlar, Excel & Qo'riqchi)

Telegram boti: **@Bshaxsiy_bot**

Ushbu bot eng zamonaviy sun'iy intellekt (**Google Gemini**) va media vositalari bilan qurollangan bo'lib, **Qo'riqchi (Access Guard)** xavfsizlik tizimi, **5 ta ixtisoslashgan bo'lim** hamda **AI Chat** rejimida ishlaydi:

---

## 🛡 Qo'riqchi (Access Guard / Gatekeeper) Tizimi

Botdan faqat ruxsat berilgan (cheklangan) foydalanuvchilar foydalanishi uchun to'liq xavfsizlik tizimi joriy etildi:

1. **Ruxsatsiz foydalanuvchilar bloklanishi:**
   - Botga ruxsati yo'q odam yozganida barcha funksiyalar yopiq bo'ladi.
   - Foydalanuvchiga uning **Telegram ID** raqami ko'rsatilib, **«📩 Administratorga ruxsat so'rovi yuborish»** tugmasi beriladi.
2. **Bir bosishda tasdiqlash (Interactive Approval):**
   - Foydalanuvchi so'rov yuborganida, barcha administratorlarga tugmali xabar boradi:
     - `[✅ Ruxsat berish]`
     - `[❌ Rad etish]`
   - Admin ruxsat bergan zahoti, foydalanuvchiga avtomatik xabar boradi va bot ochiladi!
3. **Admin sifatida kirish:**
   - Botga `/admin_login <parol>` (standart parol `.env` dagi: `admin777`) yozgan foydalanuvchi darhol doimiy Administrator maqomini oladi.
4. **Qo'riqchi buyruqlari:**
   - `/admin` yoki menyudagi **«🛡 Qo'riqchi (Admin)»** tugmasi — boshqaruv paneli.
   - `/users` — Ruxsat berilgan barcha foydalanuvchilar ro'yxati.
   - `/add_user <id> [ism]` — ID orqali yangi foydalanuvchiga ruxsat berish.
   - `/remove_user <id>` — Foydalanuvchidan ruxsatni qaytarib olish.
   - `/add_admin <id> [ism]` — Yangi admin tayinlash.
   - `/toggle_guard` — Cheklangan (yopiq) yoki Ochiq rejimga o'tkazish.
   - `/my_id` — O'zining Telegram ID raqamini ko'rish.

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

## 📂 Loyiha Tuzilishi

```text
├── .env                # Bot tokeni, Gemini API kaliti va admin sozlamalari (maxfiy)
├── .env.example        # Sozlamalar namunasi
├── config.py           # Konfiguratsiya va doimiy parametrlar
├── access_guard.py     # Qo'riqchi (Access Guard / Gatekeeper) tizimi
├── guard_middleware.py # Aiogram middleware xavfsizlik tekshiruvi
├── ai_service.py       # Google Gemini AI (Chat, Deep Research, Hujjat tahlili, Tahrirlash)
├── tts_service.py      # Matnni ovozga aylantirish (Edge TTS va FFmpeg)
├── document_service.py # Word, PDF, Excel va PPTX taqdimot generatori
├── downloader.py       # Videolarni yuklash va MP3 ajratish
├── video_converter.py  # Dumaloq video tayyorlash
├── handlers.py         # Bot menyusi, bo'limlar va admin boshqaruvi
├── main.py             # Asosiy ishga tushirish moduli
├── requirements.txt    # Kerakli Python kutubxonalari
└── README.md           # Qo'llanma
```

---

## 🚀 O'rnatish va Ishga Tushirish

### 1. .env Faylini Sozlash
Loyiha papkasida `.env` faylini quyidagicha to'ldiring:
```env
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
GEMINI_MODEL=gemini-3.8-flash

# Qo'riqchi tizimi sozlamalari:
ADMIN_IDS=123456789
ADMIN_PASSWORD=admin777
```

### 2. Kutubxonalarni O'rnatish
```bash
pip install -r requirements.txt
```

### 3. Botni Ishga Tushirish

#### A) Noutbukda Antigravity yopiq bo'lganda ham orqa fonda ishlashi:
- **`start_bot.bat`** faylini 2 marta bosing: bot orqa fonda butunlay yashirin tarzda (`pythonw`) ishga tushadi. Antigravity yoki oynalarni yopsangiz ham bot ishlashda davom etadi!
- **`stop_bot.bat`** faylini bossangiz: fonda ishlayotgan bot to'xtatiladi.
- **`install_startup.bat`**: Windows yoqilishi (noutbuk yoqilishi) bilan bot avtomatik ishga tushishi uchun sozlangan.

#### B) Noutbuk butunlay o'chiq bo'lganda ham 24/7 uzluksiz ishlashi (Bulutli bepul server):
Noutbuk quvvati o'chirilganda ham bot to'xtamasdan ishlashi uchun [Render.com](https://render.com) (bepul 24/7 bulut) ga 2 daqiqada ulab qo'yish mumkin:
1. [Render.com](https://render.com) saytiga kiring va GitHub profilingiz orqali kiring.
2. **"New +"** -> **"Background Worker"** (yoki **"Web Service"**) tugmasini bosing.
3. O'zingizning **`Bshaxsiy`** nomli GitHub repozitoriyangizni tanlang.
4. **Environment Variables** bo'limiga quyidagilarni kiriting:
   - `BOT_TOKEN`: Sizning Telegram bot tokeningiz
   - `GEMINI_API_KEY`: Google Gemini API kalitingiz
   - `GEMINI_MODEL`: `gemini-3.8-flash`
   - `ADMIN_PASSWORD`: `admin777`
5. **"Create Service"** tugmasini bosing.
Bot bulutli serverda 24 soat, 7 kun, noutbukingiz o'chiq bo'lsa ham uzluksiz ishlaydi!
