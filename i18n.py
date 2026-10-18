import sqlite3
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "archive.db"

LANGUAGES = {
    "uz": "🇺🇿 O'zbekcha",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English"
}

# Foydalanuvchilar tilini tezkor xotirada kesh qilish (user_id -> lang)
_USER_LANG_CACHE: Dict[int, str] = {}


def init_i18n_db(db_path: Path = DB_PATH):
    """Foydalanuvchi sozlamalari (til) jadvalini yaratish"""
    try:
        with sqlite3.connect(str(db_path), timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL DEFAULT 'uz',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            conn.commit()
            logger.info("i18n ma'lumotlar bazasi tayyor.")
    except Exception as e:
        logger.exception(f"i18n bazasini yaratishda xatolik: {e}")


def get_user_lang(user_id: int) -> str:
    """Foydalanuvchi tanlagan tilni olish (baza yoki kesh)"""
    if user_id in _USER_LANG_CACHE:
        return _USER_LANG_CACHE[user_id]

    try:
        with sqlite3.connect(str(DB_PATH), timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT language FROM user_preferences WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                lang = row[0]
                _USER_LANG_CACHE[user_id] = lang
                return lang
    except Exception as e:
        logger.warning(f"Foydalanuvchi tilini o'qishda xatolik ({user_id}): {e}")

    # Agar hali tanlanmagan bo'lsa standart O'zbek tili
    return "uz"


def has_user_selected_lang(user_id: int) -> bool:
    """Foydalanuvchi avval til tanlaganmi yoki yo'qligini tekshirish"""
    if user_id in _USER_LANG_CACHE:
        return True
    try:
        with sqlite3.connect(str(DB_PATH), timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT language FROM user_preferences WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row[0]:
                _USER_LANG_CACHE[user_id] = row[0]
                return True
    except Exception as e:
        logger.warning(f"Til tekshiruvida xatolik ({user_id}): {e}")
    return False


def set_user_lang(user_id: int, lang: str):
    """Foydalanuvchi tilini o'rnatish va bazaga saqlash"""
    if lang not in LANGUAGES:
        lang = "uz"

    _USER_LANG_CACHE[user_id] = lang

    try:
        with sqlite3.connect(str(DB_PATH), timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO user_preferences (user_id, language, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                language = excluded.language,
                updated_at = CURRENT_TIMESTAMP
            """, (user_id, lang))
            conn.commit()
            logger.info(f"Foydalanuvchi {user_id} uchun til o'rnatildi: {lang}")
    except Exception as e:
        logger.exception(f"Tilni saqlashda xatolik ({user_id}): {e}")


def get_lang_inline_keyboard() -> InlineKeyboardMarkup:
    """Tilni tanlash uchun inline klaviatura"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="set_lang:uz"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en")
        ]
    ])


# =====================================================================
# MATNLAR LUG'ATI (LOCALIZATION DICTIONARY)
# =====================================================================

MESSAGES: Dict[str, Dict[str, str]] = {
    "choose_language": {
        "uz": (
            "🌐 <b>Iltimos, botdan foydalanish uchun tilni tanlang:</b>\n"
            "🇷🇺 Пожалуйста, выберите язык для использования бота:\n"
            "🇬🇧 Please select your language to use the bot:"
        ),
        "ru": (
            "🌐 <b>Выберите удобный язык:</b>\n"
            "🇺🇿 Qulay tilni tanlang:\n"
            "🇬🇧 Select preferred language:"
        ),
        "en": (
            "🌐 <b>Please choose your language:</b>\n"
            "🇺🇿 Iltimos, tilni tanlang:\n"
            "🇷🇺 Пожалуйста, выберите язык:"
        )
    },
    "lang_changed": {
        "uz": "✅ <b>Til muvaffaqiyatli o'zgartirildi:</b> 🇺🇿 O'zbekcha\n\nBarcha ma'lumotlar, hujjat tahlillari va tarjimalar ushbu tilda taqdim etiladi.",
        "ru": "✅ <b>Язык успешно изменен:</b> 🇷🇺 Русский\n\nВсе разделы, анализ документов и переводы теперь будут на русском языке.",
        "en": "✅ <b>Language successfully changed:</b> 🇬🇧 English\n\nAll features, document analysis, and translations are now in English."
    },
    "welcome": {
        "uz": (
            "👋 Assalomu alaykum, <b>{name}</b>!{admin_badge}\n\n"
            "🌟 <b>Ko'p funksiyali Intellektual Bot</b>ga xush kelibsiz!\n\n"
            "<b>Botning asosiy imkoniyatlari:</b>\n"
            "1. 📑 <b>Hujjatlar tahlili:</b> Rus, ingliz yoki boshqa tildagi PDF, Word, Excel hujjatlarni chuqur o'rganib, tanlangan tilga aniq va grammatik xatolarsiz tarjima qilib berish.\n"
            "2. 🌐 <b>Tarjimon (Translator):</b> Matn va hujjatlarni xalqaro darajada professional va ravon tarjima qilish.\n"
            "3. 🔬 <b>Deep Research:</b> Har qanday mavzuda to'liq ilmiy va tahliliy izlanish hisoboti.\n"
            "4. 🎬 <b>Musiqa va Video:</b> Instagram/video yuklash, dumaloq video (krujok) va MP3 ajratish.\n"
            "5. 📽 <b>Slaydlar:</b> Mavzudan professional PowerPoint (<code>.pptx</code>) taqdimot yaratish.\n"
            "6. 📊 <b>PDF, Word, Excel:</b> Hujjatlarni generatsiya qilish va tartibli jadvallar tuzish.\n"
            "7. 🤖 <b>AI Chat:</b> Erkin muloqot va professional maslahatlar.\n"
            "8. 🗄 <b>Arxiv:</b> Barcha so'rovlar, tahlillar va hujjatlar tarixi.\n\n"
            "👇 <i>Kerakli bo'limni tanlang:</i>"
        ),
        "ru": (
            "👋 Здравствуйте, <b>{name}</b>!{admin_badge}\n\n"
            "🌟 Добро пожаловать в <b>Многофункциональный Интеллектуальный Бот</b>!\n\n"
            "<b>Основные возможности бота:</b>\n"
            "1. 📑 <b>Анализ документов:</b> Глубокий анализ PDF, Word, Excel файлов на любых языках с безупречным переводом на русский язык.\n"
            "2. 🌐 <b>Переводчик (Translator):</b> Профессиональный литературный и точный перевод текстов и документов.\n"
            "3. 🔬 <b>Deep Research:</b> Комплексные научные и стратегические исследования по любой теме.\n"
            "4. 🎬 <b>Музыка и Видео:</b> Скачивание из Instagram, видеосообщения (кружочки) и MP3.\n"
            "5. 📽 <b>Слайды, Презентации:</b> Создание готовых презентаций PowerPoint (<code>.pptx</code>).\n"
            "6. 📊 <b>PDF, Word, Excel:</b> Генерация отчетов и умных таблиц.\n"
            "7. 🤖 <b>AI Чат:</b> Свободный диалог и экспертные консультации.\n"
            "8. 🗄 <b>Архив:</b> История всех ваших запросов, скачивание и удаление файлов.\n\n"
            "👇 <i>Выберите нужный раздел:</i>"
        ),
        "en": (
            "👋 Hello, <b>{name}</b>!{admin_badge}\n\n"
            "🌟 Welcome to the <b>Multi-functional Intelligent Bot</b>!\n\n"
            "<b>Key Features:</b>\n"
            "1. 📑 <b>Document Analysis:</b> In-depth legal and business analysis of PDF, Word, Excel files with flawless translation.\n"
            "2. 🌐 <b>Translator:</b> Professional, highly accurate, and context-aware translation.\n"
            "3. 🔬 <b>Deep Research:</b> Comprehensive analytical and scientific reports on any topic.\n"
            "4. 🎬 <b>Music & Video:</b> Instagram downloader, round video notes, and MP3 audio extraction.\n"
            "5. 📽 <b>Slides & Presentations:</b> Generate professional PowerPoint (<code>.pptx</code>) slides.\n"
            "6. 📊 <b>PDF, Word, Excel:</b> Automatic document generation and structured spreadsheets.\n"
            "7. 🤖 <b>AI Chat:</b> Free dialogue and advanced AI guidance.\n"
            "8. 🗄 <b>Archive:</b> Persistent query history, downloadable files, and clean management.\n\n"
            "👇 <i>Select a section to get started:</i>"
        )
    },
    "help": {
        "uz": (
            "📖 <b>Bot bo'limlaridan foydalanish qo'llanmasi:</b>\n\n"
            "📑 <b>1. Hujjatlar tahlili:</b>\n"
            "PDF, Word (.docx), Excel (.xlsx) yoki matnli fayl jo'nating. Agar hujjat rus yoki ingliz tilida bo'lsa, AI uni tahlil qilib, o'zbek tiliga aniq, ravon va grammatik xatolarsiz tarjima qilib beradi.\n\n"
            "🌐 <b>2. Tarjimon:</b>\n"
            "Istalgan matn yoki hujjatni o'zbek, rus, ingliz va boshqa tillar o'rtasida mukammal tarjima qiling. Tarjimani Word, PDF yoki ovozli shaklda yuklab olish mumkin.\n\n"
            "🔬 <b>3. Deep Research (Chuqur Izlanish):</b>\n"
            "Istalgan mavzuni yuboring. AI keng qamrovli tahlil, faktlar, statistika va strategik yechimlardan iborat to'liq hisobot tuzadi.\n\n"
            "🎬 <b>4. Musiqa va Video:</b>\n"
            "• Instagram havolasini yuboring -> videoni yuklab beradi.\n"
            "• Video yuboring -> dumaloq (Video Note) yoki MP3 audio qilib beradi.\n\n"
            "📽 <b>5. Slaydlar, Prezentatsiya:</b>\n"
            "Mavzu yoki matn yuboring -> bot tayyor <code>.pptx</code> PowerPoint taqdimotini yaratib beradi.\n\n"
            "📊 <b>6. PDF, WORD, EXCEL:</b>\n"
            "Matn yuborib uni chiroyli formatlangan Word, PDF yoki Excel jadvali sifatida yuklab oling.\n\n"
            "🗄 <b>7. Arxiv va Tarix:</b>\n"
            "Barcha so'rovlar doimiy saqlanadi. Ularni istalgan payt ko'rish, yuklab olish yoki o'chirish mumkin.\n\n"
            "🌐 <b>8. Tilni o'zgartirish:</b>\n"
            "Menyudagi <b>«🌐 Til / Язык / Lang»</b> tugmasi yoki <code>/lang</code> buyrug'i orqali tilni istalgan payt almashtirishingiz mumkin."
        ),
        "ru": (
            "📖 <b>Руководство по использованию бота:</b>\n\n"
            "📑 <b>1. Анализ документов:</b>\n"
            "Отправьте файл PDF, Word (.docx), Excel (.xlsx) на любом языке. AI проведет глубокий анализ и переведет все ключевые пункты на русский язык без грамматических ошибок.\n\n"
            "🌐 <b>2. Переводчик:</b>\n"
            "Переводите любые тексты и документы между русским, узбекским, английским и другими языками. Результат можно скачать в Word, PDF или прослушать голосом.\n\n"
            "🔬 <b>3. Deep Research (Глубокие исследования):</b>\n"
            "Отправьте интересующую тему. AI подготовит исчерпывающий аналитический отчет со статистикой и практическими выводами.\n\n"
            "🎬 <b>4. Музыка и Видео:</b>\n"
            "• Ссылка из Instagram -> скачивание видео.\n"
            "• Отправка видео -> кружочек (Video Note) или MP3 аудио.\n\n"
            "📽 <b>5. Слайды, Презентации:</b>\n"
            "Отправьте тему -> бот создаст презентацию PowerPoint (<code>.pptx</code>).\n\n"
            "📊 <b>6. PDF, WORD, EXCEL:</b>\n"
            "Генерация красивых документов Word, PDF или таблиц Excel.\n\n"
            "🗄 <b>7. Архив:</b>\n"
            "История всех ваших отчетов и документов с возможностью скачивания и удаления.\n\n"
            "🌐 <b>8. Смена языка:</b>\n"
            "Кнопка <b>«🌐 Til / Язык / Lang»</b> или команда <code>/lang</code> позволяет изменить язык в любое время."
        ),
        "en": (
            "📖 <b>Bot User Guide:</b>\n\n"
            "📑 <b>1. Document Analysis:</b>\n"
            "Send PDF, Word (.docx), Excel (.xlsx) files in any language. AI thoroughly analyzes clauses, dates, amounts, and translates everything into English with high precision.\n\n"
            "🌐 <b>2. Translator:</b>\n"
            "Accurate translation between English, Uzbek, Russian, and other languages. Export translations directly to Word, PDF, or voice notes.\n\n"
            "🔬 <b>3. Deep Research:</b>\n"
            "Send any subject or question. AI creates a detailed research report with data, statistics, and strategic advice.\n\n"
            "🎬 <b>4. Music & Video:</b>\n"
            "• Instagram link -> downloads media.\n"
            "• Send video -> converts to round video note or extracts MP3 audio.\n\n"
            "📽 <b>5. Slides & Presentations:</b>\n"
            "Send a topic -> bot generates ready-to-use PowerPoint (<code>.pptx</code>) slides.\n\n"
            "📊 <b>6. PDF, WORD, EXCEL:</b>\n"
            "Export text and summaries into styled Word, PDF, or Excel sheets.\n\n"
            "🗄 <b>7. Archive:</b>\n"
            "Browse your query history anytime, download files, or delete unnecessary records.\n\n"
            "🌐 <b>8. Change Language:</b>\n"
            "Use the <b>«🌐 Til / Язык / Lang»</b> button or <code>/lang</code> command to change language anytime."
        )
    },
    "translator_menu": {
        "uz": (
            "🌐 <b>Aqlli Tarjimon bo'limi</b>\n\n"
            "Menga istalgan matn, xabar yoki hujjat jo'nating. Sun'iy intellekt uni imlo va grammatika qoidalariga to'liq rioya qilgan holda, atamalarni buzmasdan professional tarjima qiladi.\n\n"
            "📍 <b>Hozirgi yo'nalish:</b> <b>{direction}</b>\n\n"
            "👇 <i>Tarjima yo'nalishini tanlang yoki to'g'ridan-to'g'ri matn yuboring:</i>"
        ),
        "ru": (
            "🌐 <b>Раздел «Умный Переводчик»</b>\n\n"
            "Отправьте мне любой текст или документ. Искусственный интеллект выполнит профессиональный литературный перевод с безупречной грамматикой.\n\n"
            "📍 <b>Текущее направление:</b> <b>{direction}</b>\n\n"
            "👇 <i>Выберите направление перевода или сразу отправьте текст:</i>"
        ),
        "en": (
            "🌐 <b>Smart Translator</b>\n\n"
            "Send me any text or document. AI will translate it with high precision, maintaining context, tone, and correct terminology.\n\n"
            "📍 <b>Current direction:</b> <b>{direction}</b>\n\n"
            "👇 <i>Select a translation direction or send text directly:</i>"
        )
    },
    "doc_analysis_menu": {
        "uz": (
            "📑 <b>Hujjatlar tahlili bo'limi</b>\n\n"
            "Menga istalgan <b>PDF, Word (.docx), Excel (.xlsx)</b> yoki <b>matnli fayl</b> jo'nating.\n\n"
            "💡 <b>Muhim imkoniyat:</b> Agar hujjat <b>Rus</b> yoki <b>Ingliz</b> tilida bo'lsa, bot uni tahlil qilib, <b>sof adabiy O'zbek tiliga</b> to'g'ri, ravon va grammatik xatolarsiz tarjima qiladi va to'liq yuridik/amaliy tahlilini beradi!\n\n"
            "📤 <i>Hujjatni fayl shaklida yuboring:</i>"
        ),
        "ru": (
            "📑 <b>Раздел «Анализ документов»</b>\n\n"
            "Отправьте мне любой файл <b>PDF, Word (.docx), Excel (.xlsx)</b> или текст.\n\n"
            "💡 <b>Особенность:</b> Если документ на <b>узбекском, английском</b> или другом языке, бот полностью проанализирует его и изложит все выводы на <b>безупречном русском языке</b>!\n\n"
            "📤 <i>Отправьте документ в виде файла:</i>"
        ),
        "en": (
            "📑 <b>Document Analysis Section</b>\n\n"
            "Send me any <b>PDF, Word (.docx), Excel (.xlsx)</b> or text file.\n\n"
            "💡 <b>Special Feature:</b> If your document is in <b>Uzbek, Russian</b> or any other language, AI will analyze it thoroughly and translate all insights into <b>flawless English</b>!\n\n"
            "📤 <i>Please send your document file:</i>"
        )
    },
    "deep_research_menu": {
        "uz": (
            "🔬 <b>Deep Research (Chuqur Izlanish) bo'limi</b>\n\n"
            "O'zingizni qiziqtirgan istalgan mavzu, ilmiy masala, bozor tahlili yoki biznes g'oyani yozing.\n\n"
            "AI barcha xalqaro manbalar va faktlar asosida to'liq professional hisobot tayyorlaydi.\n\n"
            "✍️ <i>Tadqiqot mavzusini yozib qoldiring:</i>"
        ),
        "ru": (
            "🔬 <b>Раздел «Deep Research»</b>\n\n"
            "Напишите интересующую тему, научную проблему, анализ рынка или бизнес-идею.\n\n"
            "AI подготовит подробный исследовательский отчет с фактами, статистикой и практическими решениями.\n\n"
            "✍️ <i>Введите тему исследования:</i>"
        ),
        "en": (
            "🔬 <b>Deep Research Section</b>\n\n"
            "Enter any topic, research query, market analysis, or strategy you want to explore.\n\n"
            "AI will generate a thorough analytical report with facts, statistics, and strategic recommendations.\n\n"
            "✍️ <i>Type the research topic:</i>"
        )
    },
    "media_menu": {
        "uz": (
            "🎬 <b>Musiqa va Video bo'limi</b>\n\n"
            "• <b>Instagram Reels / Post / IGTV</b> havolasini yuboring — videoni yuklab beradi.\n"
            "• <b>Video</b> fayl yuboring — uni dumaloq video xabarga (Video Note) yoki MP3 audio formatiga aylantirib beradi.\n\n"
            "🔗 <i>Instagram havolasi yoki video yuboring:</i>"
        ),
        "ru": (
            "🎬 <b>Раздел «Музыка и Видео»</b>\n\n"
            "• Отправьте ссылку на <b>Instagram Reels / Пост</b> — бот скачает видео без водяных знаков.\n"
            "• Отправьте <b>видео</b> — бот превратит его в видеосообщение (кружочек) или извлечет MP3 звук.\n\n"
            "🔗 <i>Отправьте ссылку Instagram или видео:</i>"
        ),
        "en": (
            "🎬 <b>Music & Video Section</b>\n\n"
            "• Send an <b>Instagram Reels / Post</b> link to download video directly.\n"
            "• Send a <b>video</b> to convert into a round video note or extract MP3 audio.\n\n"
            "🔗 <i>Send an Instagram link or video file:</i>"
        )
    },
    "slides_menu": {
        "uz": (
            "📽 <b>Slaydlar va Prezentatsiya bo'limi</b>\n\n"
            "Taqdimot mavzusini yoki matnini yuboring. AI uni professional slaydlarga ajratib, tayyor <b>PowerPoint (.pptx)</b> faylini yaratib beradi.\n\n"
            "✍️ <i>Taqdimot mavzusi yoki matnini kiriting:</i>"
        ),
        "ru": (
            "📽 <b>Раздел «Слайды и Презентации»</b>\n\n"
            "Отправьте тему или текст для презентации. AI структурирует слайды и сгенерирует готовый файл <b>PowerPoint (.pptx)</b>.\n\n"
            "✍️ <i>Введите тему или текст презентации:</i>"
        ),
        "en": (
            "📽 <b>Slides & Presentations Section</b>\n\n"
            "Send a topic or text content. AI will structure it into clean slides and generate a styled <b>PowerPoint (.pptx)</b> presentation.\n\n"
            "✍️ <i>Enter presentation topic or text:</i>"
        )
    },
    "office_docs_menu": {
        "uz": (
            "📊 <b>PDF, Word, Excel bo'limi</b>\n\n"
            "Menga matn yoki ma'lumotlar yuboring. Uni chiroyli rasmiy <b>Word (.docx)</b>, <b>PDF</b> yoki tartibli <b>Excel (.xlsx)</b> jadvali shaklida yaratib beraman.\n\n"
            "✍️ <i>Hujjatga aylanadigan matnni yozing:</i>"
        ),
        "ru": (
            "📊 <b>Раздел «PDF, Word, Excel»</b>\n\n"
            "Отправьте текст или структурированные данные. Бот сформирует официальный документ <b>Word (.docx)</b>, <b>PDF</b> или таблицу <b>Excel (.xlsx)</b>.\n\n"
            "✍️ <i>Введите текст для создания документа:</i>"
        ),
        "en": (
            "📊 <b>PDF, Word, Excel Section</b>\n\n"
            "Send text or data to generate formatted <b>Word (.docx)</b>, <b>PDF</b>, or <b>Excel (.xlsx)</b> spreadsheets.\n\n"
            "✍️ <i>Enter text to generate document:</i>"
        )
    },
    "ai_chat_menu": {
        "uz": (
            "🤖 <b>AI Chat bo'limi</b>\n\n"
            "Sun'iy intellekt bilan erkin muloqot qiling. Istalgan savol, masala yoki topshiriqni yozishingiz, shuningdek rasm yuborib tahlil qildirishingiz mumkin.\n\n"
            "💬 <i>Savolingizni yozing yoki rasm jo'nating:</i>"
        ),
        "ru": (
            "🤖 <b>Раздел «AI Чат»</b>\n\n"
            "Свободный диалог с искусственным интеллектом. Задавайте любые вопросы, решайте задачи или отправляйте фото для распознавания.\n\n"
            "💬 <i>Задайте вопрос или отправьте фото:</i>"
        ),
        "en": (
            "🤖 <b>AI Chat Section</b>\n\n"
            "Freely chat with AI. Ask any question, solve complex problems, or send an image for visual analysis.\n\n"
            "💬 <i>Type your question or send an image:</i>"
        )
    }
}


def t(key: str, lang: str = "uz", **kwargs) -> str:
    """Matnni tilga mos tarjimasini olish"""
    entry = MESSAGES.get(key, {})
    template = entry.get(lang) or entry.get("uz") or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


# =====================================================================
# KLAVIATURA ELEMENTLARI (BUTTONS)
# =====================================================================

BUTTON_TEXTS = {
    "btn_doc_analysis": {
        "uz": "📑 Hujjatlar tahlili",
        "ru": "📑 Анализ документов",
        "en": "📑 Document Analysis"
    },
    "btn_translator": {
        "uz": "🌐 Tarjimon",
        "ru": "🌐 Переводчик",
        "en": "🌐 Translator"
    },
    "btn_deep_research": {
        "uz": "🔬 Deep Research",
        "ru": "🔬 Deep Research",
        "en": "🔬 Deep Research"
    },
    "btn_media": {
        "uz": "🎬 Musiqa va Video",
        "ru": "🎬 Музыка и Видео",
        "en": "🎬 Music & Video"
    },
    "btn_slides": {
        "uz": "📽 Slaydlar, Prezentatsiya",
        "ru": "📽 Слайды, Презентации",
        "en": "📽 Slides, Presentations"
    },
    "btn_docs": {
        "uz": "📊 PDF, WORD, EXCEL",
        "ru": "📊 PDF, WORD, EXCEL",
        "en": "📊 PDF, WORD, EXCEL"
    },
    "btn_ai_chat": {
        "uz": "🤖 AI Chat",
        "ru": "🤖 AI Чат",
        "en": "🤖 AI Chat"
    },
    "btn_archive": {
        "uz": "🗄 Arxiv",
        "ru": "🗄 Архив",
        "en": "🗄 Archive"
    },
    "btn_help": {
        "uz": "ℹ️ Yordam",
        "ru": "ℹ️ Помощь",
        "en": "ℹ️ Help"
    },
    "btn_lang": {
        "uz": "🌐 Til / Язык / Lang",
        "ru": "🌐 Til / Язык / Lang",
        "en": "🌐 Til / Язык / Lang"
    },
    "btn_main_menu": {
        "uz": "🏠 Bosh menyu",
        "ru": "🏠 Главное меню",
        "en": "🏠 Main Menu"
    },
    "btn_new_request": {
        "uz": "🔄 Yangi so'rov",
        "ru": "🔄 Новый запрос",
        "en": "🔄 New Request"
    },
    "btn_guard": {
        "uz": "🛡 Qo'riqchi (Admin)",
        "ru": "🛡 Охрана (Админ)",
        "en": "🛡 Guard (Admin)"
    }
}


def btn(key: str, lang: str = "uz") -> str:
    """Tugma nomini olish"""
    entry = BUTTON_TEXTS.get(key, {})
    return entry.get(lang) or entry.get("uz") or key


def get_all_button_variants(key: str) -> set:
    """Ushbu tugmaning barcha tillardagi variantlari to'plami (Aiogram filtri uchun)"""
    entry = BUTTON_TEXTS.get(key, {})
    return set(entry.values())


def get_main_keyboard_i18n(lang: str = "uz", is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Tanlangan tildagi asosiy menyu klaviaturasi"""
    keyboard = [
        [
            KeyboardButton(text=btn("btn_doc_analysis", lang)),
            KeyboardButton(text=btn("btn_translator", lang))
        ],
        [
            KeyboardButton(text=btn("btn_deep_research", lang)),
            KeyboardButton(text=btn("btn_media", lang))
        ],
        [
            KeyboardButton(text=btn("btn_slides", lang)),
            KeyboardButton(text=btn("btn_docs", lang))
        ],
        [
            KeyboardButton(text=btn("btn_ai_chat", lang)),
            KeyboardButton(text=btn("btn_archive", lang))
        ]
    ]

    if is_admin:
        keyboard.append([
            KeyboardButton(text=btn("btn_guard", lang)),
            KeyboardButton(text=btn("btn_lang", lang))
        ])
        keyboard.append([
            KeyboardButton(text=btn("btn_help", lang)),
            KeyboardButton(text=btn("btn_main_menu", lang))
        ])
    else:
        keyboard.append([
            KeyboardButton(text=btn("btn_lang", lang)),
            KeyboardButton(text=btn("btn_help", lang))
        ])
        keyboard.append([
            KeyboardButton(text=btn("btn_main_menu", lang))
        ])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_section_keyboard_i18n(lang: str = "uz") -> ReplyKeyboardMarkup:
    """Bo'lim ichidagi boshqaruv klaviaturasi"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=btn("btn_new_request", lang)),
                KeyboardButton(text=btn("btn_main_menu", lang))
            ]
        ],
        resize_keyboard=True
    )


# Translator yo'nalishlari uchun qulay inline klaviatura
def get_translator_directions_keyboard(lang: str = "uz", active_dir: str = "auto:uz") -> InlineKeyboardMarkup:
    """Tarjima yo'nalishlarini tanlash inline klaviaturasi"""
    pairs = [
        ("auto:uz", "🔄 Avto ➡️ 🇺🇿 O'zbekcha"),
        ("auto:ru", "🔄 Авто ➡️ 🇷🇺 Русский"),
        ("auto:en", "🔄 Auto ➡️ 🇬🇧 English"),
        ("uz:ru", "🇺🇿 O'zbek ➡️ 🇷🇺 Rus"),
        ("ru:uz", "🇷🇺 Rus ➡️ 🇺🇿 O'zbek"),
        ("uz:en", "🇺🇿 O'zbek ➡️ 🇬🇧 Ingliz"),
        ("en:uz", "🇬🇧 Ingliz ➡️ 🇺🇿 O'zbek"),
        ("ru:en", "🇷🇺 Rus ➡️ 🇬🇧 Ingliz"),
        ("en:ru", "🇬🇧 Ingliz ➡️ 🇷🇺 Rus"),
    ]

    buttons = []
    row = []
    for code, label in pairs:
        prefix = "✅ " if code == active_dir else ""
        row.append(InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"tr_dir:{code}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Qo'shimcha tugmalar
    back_text = "🏠 Bosh menyu" if lang == "uz" else ("🏠 Главное меню" if lang == "ru" else "🏠 Main Menu")
    buttons.append([
        InlineKeyboardButton(text=back_text, callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Dastlabki ishga tushirish
init_i18n_db()
