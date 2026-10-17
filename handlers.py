import io
import os
import re
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
)
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest

import config
from config import DOWNLOADS_DIR, BASE_DIR
from downloader import (
    download_instagram_video,
    extract_audio_from_video,
    DownloadException,
    FileTooLargeError,
    PrivateMediaError,
)
from video_converter import convert_to_video_note
from ai_service import ai_service
from tts_service import convert_text_to_voice_and_audio
from document_service import (
    generate_docx,
    generate_pdf,
    generate_presentation,
    generate_excel,
    extract_text_from_file,
)
from access_guard import access_guard

logger = logging.getLogger(__name__)
router = Router()

# Instagram havolalarini aniqlash uchun regex
INSTAGRAM_URL_REGEX = re.compile(
    r"https?:\/\/(?:www\.)?instagram\.com\/(?:[a-zA-Z0-9_\.]+\/)?(?:p|reel|reels|tv|share)\/[a-zA-Z0-9_\-]+[^\s]*",
    re.IGNORECASE
)

# Foydalanuvchilarning hozirgi faol bo'limi (user_id -> mode)
# Rejimlar: "NORMAL", "DOC_ANALYSIS", "DEEP_RESEARCH", "MEDIA", "SLIDES", "OFFICE_DOCS", "AI_CHAT"
user_active_mode: dict[int, str] = {}

# Format tanlash kutilayotgan audio xabarlar: audio_id -> {user_id, user_name, file_id, mime_type, caption}
pending_audio: dict[str, dict] = {}

# Bo'limlarga o'tkazish va tahrirlash uchun natijalar kesh xotirasi (res_id -> {title, content, user_name})
result_store: dict[str, dict] = {}


def store_result(title: str, content: str, user_name: str = "") -> str:
    """Natijani xotiraga saqlash va res_id qaytarish"""
    res_id = uuid.uuid4().hex[:8]
    result_store[res_id] = {
        "title": title or "Tahlil Natijasi",
        "content": content,
        "user_name": user_name,
        "created_at": datetime.now()
    }
    if len(result_store) > 400:
        oldest_key = next(iter(result_store))
        result_store.pop(oldest_key, None)
    return res_id


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Asosiy bo'limlar menyusi (Adminlar uchun Qo'riqchi tugmasi bilan)"""
    keyboard = [
        [
            KeyboardButton(text="📑 Hujjatlar tahlili"),
            KeyboardButton(text="🔬 Deep Research")
        ],
        [
            KeyboardButton(text="🎬 Musiqa va Video"),
            KeyboardButton(text="📽 Slaydlar, Prezentatsiya")
        ],
        [
            KeyboardButton(text="📊 PDF, WORD, EXCEL"),
            KeyboardButton(text="🤖 AI Chat")
        ]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛡 Qo'riqchi (Admin)"),
            KeyboardButton(text="ℹ️ Yordam")
        ])
    else:
        keyboard.append([
            KeyboardButton(text="ℹ️ Yordam"),
            KeyboardButton(text="🏠 Bosh menyu")
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_section_keyboard() -> ReplyKeyboardMarkup:
    """Bo'lim ichidagi qulay boshqaruv menyusi"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔄 Yangi so'rov"),
                KeyboardButton(text="🏠 Bosh menyu")
            ]
        ],
        resize_keyboard=True
    )


def get_content_actions_keyboard(res_id: str) -> InlineKeyboardMarkup:
    """Har bir natija tagida bo'limga o'tkazish va tahrirlash tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📂 Bo'limga o'tkazish", callback_data=f"act:tr:{res_id}"),
                InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"act:ed:{res_id}")
            ]
        ]
    )


def get_transfer_keyboard(res_id: str) -> InlineKeyboardMarkup:
    """Natijani tegishli bo'limlarga o'tkazish tanlovlari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 Word (.docx)", callback_data=f"tr:docx:{res_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf)", callback_data=f"tr:pdf:{res_id}")
            ],
            [
                InlineKeyboardButton(text="📊 Excel (.xlsx)", callback_data=f"tr:xlsx:{res_id}"),
                InlineKeyboardButton(text="📽 Prezentatsiya (.pptx)", callback_data=f"tr:pptx:{res_id}")
            ],
            [
                InlineKeyboardButton(text="🔬 Deep Research", callback_data=f"tr:deep:{res_id}"),
                InlineKeyboardButton(text="🎙 Ovozli qilish (TTS)", callback_data=f"tr:voice:{res_id}")
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"act:back:{res_id}")
            ]
        ]
    )


def get_edit_keyboard(res_id: str) -> InlineKeyboardMarkup:
    """Natijani tahrirlash va qayta ishlash tanlovlari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Kengaytirish", callback_data=f"ed:expand:{res_id}"),
                InlineKeyboardButton(text="✂️ Qisqartirish", callback_data=f"ed:summary:{res_id}")
            ],
            [
                InlineKeyboardButton(text="🔄 Qayta yozish", callback_data=f"ed:rewrite:{res_id}"),
                InlineKeyboardButton(text="🇺🇿 O'zbekchaga o'girish", callback_data=f"ed:truz:{res_id}")
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"act:back:{res_id}")
            ]
        ]
    )


def get_audio_choice_keyboard(audio_id: str) -> InlineKeyboardMarkup:
    """Audio tahlil va javob formatini tanlash tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Matn", callback_data=f"aud_mode:text:{audio_id}"),
                InlineKeyboardButton(text="🎙 Ovozli (Audio)", callback_data=f"aud_mode:voice:{audio_id}")
            ],
            [
                InlineKeyboardButton(text="📄 Word (.docx)", callback_data=f"aud_mode:docx:{audio_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf)", callback_data=f"aud_mode:pdf:{audio_id}")
            ],
            [
                InlineKeyboardButton(text="✨ Barchasi birga (Hammasi)", callback_data=f"aud_mode:all:{audio_id}")
            ],
            [
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"aud_mode:cancel:{audio_id}")
            ]
        ]
    )


def get_video_options_keyboard(video_path: str) -> InlineKeyboardMarkup:
    """Video yuklanganda dumaloq qilish yoki MP3 audio ajratish"""
    v_id = uuid.uuid4().hex[:8]
    # Yo'lni saqlash
    pending_audio[f"vid_{v_id}"] = {"path": video_path}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔘 Dumaloq video qilish", callback_data=f"vid:circle:{v_id}"),
                InlineKeyboardButton(text="🎵 MP3 audio ajratish", callback_data=f"vid:audio:{v_id}")
            ]
        ]
    )


def save_gemini_key_to_env(key: str):
    """Yangi Gemini API kalitini .env fayliga yozib qo'yish"""
    env_path = BASE_DIR / ".env"
    content = ""
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    
    if "GEMINI_API_KEY=" in content:
        content = re.sub(r"GEMINI_API_KEY=.*", f"GEMINI_API_KEY={key}", content)
    else:
        content += f"\nGEMINI_API_KEY={key}\n"
    
    env_path.write_text(content, encoding="utf-8")


async def send_ai_response(message: Message, response_text: str, res_id: Optional[str] = None):
    """Javobni xavfsiz formatda va harakat tugmalari bilan jo'natish"""
    if not response_text:
        await message.reply("Javob bo'sh qaytdi.")
        return

    reply_markup = get_content_actions_keyboard(res_id) if res_id else None
    max_len = 4000
    chunks = [response_text[i:i + max_len] for i in range(0, len(response_text), max_len)]

    for idx, chunk in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        current_markup = reply_markup if is_last else None
        try:
            await message.answer(chunk, parse_mode="Markdown", reply_markup=current_markup)
        except TelegramBadRequest:
            try:
                await message.answer(chunk, parse_mode="HTML", reply_markup=current_markup)
            except TelegramBadRequest:
                await message.answer(chunk, parse_mode=None, reply_markup=current_markup)


# =====================================================================
# ASOSIY BUYRUQLAR VA BO'LIMLARGA O'TISH
# =====================================================================

@router.message(CommandStart())
@router.message(F.text == "🏠 Bosh menyu")
async def handle_start(message: Message):
    """Bosh menyu"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "NORMAL"
    user_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"

    is_admin = access_guard.is_admin(user_id)
    admin_badge = "\n👑 <b>Siz bot administratorsiz (Qo'riqchi faol).</b>\n" if is_admin else ""

    welcome_text = (
        f"👋 Assalomu alaykum, <b>{user_name}</b>!{admin_badge}\n\n"
        "🌟 <b>Ko'p funksiyali Intellektual Bot</b>ga xush kelibsiz!\n\n"
        "<b>Botning asosiy bo'limlari:</b>\n"
        "1. 📑 <b>Hujjatlar tahlili:</b> PDF, Word, Excel, TXT fayllarni chuqur o'rganish va hisobot olish.\n"
        "2. 🔬 <b>Deep Research:</b> Istalgan mavzuda to'liq ilmiy va tahliliy izlanish hisoboti.\n"
        "3. 🎬 <b>Musiqa va Video:</b> Instagram/video yuklash, dumaloq video (krujok) va MP3 ajratish.\n"
        "4. 📽 <b>Slaydlar, Prezentatsiya:</b> Mavzudan professional PowerPoint (<code>.pptx</code>) taqdimot yaratish.\n"
        "5. 📊 <b>PDF, WORD, EXCEL:</b> Hujjatlarni generatsiya qilish va jadvallar tuzish.\n"
        "6. 🤖 <b>AI Chat:</b> Erkin muloqot va maslahatlar.\n\n"
        "💡 <i>Har qanday tahlil natijasini boshqa bo'limlarga o'tkazishingiz yoki tahrirlashingiz mumkin!</i>\n\n"
        "👇 <i>Kerakli bo'limni tanlang:</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_keyboard(is_admin=is_admin))


@router.message(F.text == "ℹ️ Yordam")
@router.message(Command("help"))
async def handle_help(message: Message):
    """Qo'llanma"""
    help_text = (
        "📖 <b>Bot bo'limlaridan foydalanish qo'llanmasi:</b>\n\n"
        "📑 <b>1. Hujjatlar tahlili:</b>\n"
        "PDF, Word (.docx), Excel (.xlsx) yoki matnli fayl jo'nating. AI barcha muhim bandlarni, sanalar va raqamlarni tahlil qilib beradi.\n\n"
        "🔬 <b>2. Deep Research (Chuqur Izlanish):</b>\n"
        "Istalgan savol yoki mavzuni yuboring. AI keng qamrovli tahlil, statistika va strategik yechimlardan iborat to'liq hisobot tuzadi.\n\n"
        "🎬 <b>3. Musiqa va Video:</b>\n"
        "• Instagram havolasini yuboring -> videoni yuklab beradi.\n"
        "• Video yuboring -> dumaloq (Video Note) yoki MP3 audio qilib beradi.\n\n"
        "📽 <b>4. Slaydlar, Prezentatsiya:</b>\n"
        "Mavzu yoki matn yuboring -> bot tayyor <code>.pptx</code> PowerPoint taqdimotini yaratib beradi.\n\n"
        "📊 <b>5. PDF, WORD, EXCEL:</b>\n"
        "Matn yuborib uni chiroyli formatlangan Word, PDF yoki Excel jadvali sifatida yuklab oling.\n\n"
        "🔁 <b>O'tkazish va Tahrirlash:</b>\n"
        "Har bir natija ostidagi <b>«📂 Bo'limga o'tkazish»</b> tugmasi orqali hisobotni boshqa formatga o'tkazishingiz, <b>«✏️ Tahrirlash»</b> orqali kengaytirishingiz mumkin!"
    )
    await message.answer(help_text, parse_mode="HTML")


@router.message(F.text == "📑 Hujjatlar tahlili")
async def nav_doc_analysis(message: Message):
    user_active_mode[message.from_user.id] = "DOC_ANALYSIS"
    await message.answer(
        "📑 <b>Hujjatlar tahlili bo'limi</b>\n\n"
        "Menga istalgan <b>PDF, Word (.docx), Excel (.xlsx)</b> yoki <b>matnli fayl</b> jo'nating.\n\n"
        "AI hujjatni diqqat bilan o'rganib:\n"
        "• Asosiy maqsad va qisqacha mazmunini\n"
        "• Eng muhim shartlar va qoidalarni\n"
        "• Sanalar, summalar va majburiyatlarni\n"
        "• Tavsiyalar va xulosalarni chiqarib beradi.\n\n"
        "📤 <i>Hujjatni fayl shaklida yuborishingiz mumkin:</i>",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "🔬 Deep Research")
async def nav_deep_research(message: Message):
    user_active_mode[message.from_user.id] = "DEEP_RESEARCH"
    await message.answer(
        "🔬 <b>Deep Research (Chuqur Izlanish) bo'limi</b>\n\n"
        "O'zingizni qiziqtirgan istalgan mavzu, soha, loyiha, tahliliy savol yoki biznes g'oyani yozing.\n\n"
        "AI quyidagi tuzilishda to'liq professional hisobot tayyorlaydi:\n"
        "1. 📌 Kirish va Mavzuning Dolzarbligi\n"
        "2. 🔍 Chuqur Tahlil va Mexanizmlar\n"
        "3. 📊 Asosiy Ko'rsatkichlar va Statistika\n"
        "4. ⚠️ Xatarlar va Muammolar\n"
        "5. 💡 Strategik Yechimlar va Tavsiyalar\n\n"
        "✍️ <i>Tadqiqot mavzusini yozib qoldiring:</i>",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "🎬 Musiqa va Video")
async def nav_media(message: Message):
    user_active_mode[message.from_user.id] = "MEDIA"
    await message.answer(
        "🎬 <b>Musiqa va Video bo'limi</b>\n\n"
        "• 📥 <b>Instagram video:</b> Reels yoki video post havolasini yuboring.\n"
        "• 🔘 <b>Dumaloq video (krujok):</b> Telefoningizdagi videoni yuboring.\n"
        "• 🎵 <b>MP3 ajratish:</b> Istalgan videoni yuboring va uning audiosini MP3 qilib oling.\n\n"
        "Havola yoki video yuboring:",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "📽 Slaydlar, Prezentatsiya")
async def nav_slides(message: Message):
    user_active_mode[message.from_user.id] = "SLIDES"
    await message.answer(
        "📽 <b>Slaydlar va Prezentatsiya bo'limi</b>\n\n"
        "Mavzu, reja yoki tayyor matnni yozib qoldiring.\n\n"
        "AI uni to'liq PowerPoint (<code>.pptx</code>) taqdimotiga aylantirib, chiroyli titul va mazmunli slaydlari bilan tayyor fayl shaklida yuboradi!\n\n"
        "✍️ <i>Prezentatsiya mavzusini yuboring:</i>",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "📊 PDF, WORD, EXCEL")
async def nav_office_docs(message: Message):
    user_active_mode[message.from_user.id] = "OFFICE_DOCS"
    await message.answer(
        "📊 <b>PDF, WORD, EXCEL Generator bo'limi</b>\n\n"
        "Istalgan ma'lumot, matn, hisobot yoki jadval g'oyasini yozing.\n"
        "Biz uni:\n"
        "• 📄 <b>Word (.docx)</b>\n"
        "• 📑 <b>PDF (.pdf)</b>\n"
        "• 📊 <b>Excel (.xlsx)</b> jadvaliga aylantirib beramiz!\n\n"
        "✍️ <i>Hujjat yaratmoqchi bo'lgan matningizni yuboring:</i>",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "🤖 AI Chat")
@router.message(Command("ai"))
async def nav_ai_chat(message: Message):
    user_active_mode[message.from_user.id] = "AI_CHAT"
    text = message.text or ""

    if text.startswith("/ai"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            prompt = parts[1].strip()
            status_msg = await message.reply("🤔 <i>O'ylamoqda...</i>", parse_mode="HTML")
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            response = await ai_service.ask_gemini(user_id=message.from_user.id, prompt=prompt)
            try:
                await status_msg.delete()
            except Exception:
                pass
            res_id = store_result(title=prompt[:30], content=response, user_name=message.from_user.full_name)
            await send_ai_response(message, response, res_id=res_id)
            return

    await message.answer(
        "🤖 <b>Gemini AI Chat faollashtirildi!</b>\n\n"
        "Istalgan savol berishingiz, suhbatlashishingiz yoki rasm yuborishingiz mumkin. "
        "Muloqot xotirada saqlanadi.\n\n"
        "• 🔄 Suhbatni tozalash: <code>/clear</code>\n"
        "• 🏠 Bosh menyu: quyidagi tugma",
        parse_mode="HTML",
        reply_markup=get_section_keyboard()
    )


@router.message(F.text == "🔄 Yangi so'rov")
@router.message(Command("clear"))
async def handle_clear_command(message: Message):
    user_id = message.from_user.id
    ai_service.reset_chat(user_id)
    await message.answer(
        "🧹 <b>Suhbat va so'rovlar tozalandi!</b>\n\nYangi so'rovingizni yuborishingiz mumkin.",
        parse_mode="HTML"
    )


@router.message(Command("setkey"))
async def handle_setkey(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply("ℹ️ <b>Foydalanish:</b> <code>/setkey SIZNING_GEMINI_API_KALITINGIZ</code>", parse_mode="HTML")
        return
    new_key = parts[1].strip()
    ai_service.update_api_key(new_key)
    save_gemini_key_to_env(new_key)
    await message.reply("✅ <b>Gemini API kaliti saqlandi va faollashtirildi!</b>", parse_mode="HTML")


# =====================================================================
# HUJJATLARNI QABUL QILISH VA TAHLIL (PDF, Word, Excel, TXT)
# =====================================================================

@router.message(F.document)
async def handle_document_upload(message: Message):
    """
    Foydalanuvchi hujjat (PDF, Word, Excel, TXT va h.k.) yuborganida uni tahlil qilish
    """
    user_id = message.from_user.id
    doc = message.document
    filename = doc.file_name or "hujjat.pdf"
    caption = message.caption or ""

    status_msg = await message.reply(f"⏳ <i>'{filename}' hujjati yuklanmoqda va tahlil qilinmoqda...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    unique_id = uuid.uuid4().hex[:8]
    ext = Path(filename).suffix.lower()
    temp_path = DOWNLOADS_DIR / f"upload_{unique_id}{ext}"

    try:
        file_info = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(file_info.file_path, destination=str(temp_path))

        # Matnni ajratib olish
        extracted_text = extract_text_from_file(str(temp_path), filename)
        if not extracted_text or len(extracted_text) < 10:
            await status_msg.edit_text("❌ Ushbu hujjatdan matn ajratib olib bo'lmadi yoki fayl bo'sh.", parse_mode="HTML")
            return

        # AI orqali tahlil qilish
        analysis = await ai_service.analyze_document(
            user_id=user_id,
            doc_text=extracted_text,
            filename=filename,
            custom_prompt=caption
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

        title = f"{filename} tahlili"
        res_id = store_result(title=title, content=analysis, user_name=message.from_user.full_name)
        await send_ai_response(message, analysis, res_id=res_id)

    except Exception as e:
        logger.exception(f"Hujjat yuklashda xatolik: {e}")
        await status_msg.edit_text("❌ Hujjatni tahlil qilishda xatolik yuz berdi.", parse_mode="HTML")
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


# =====================================================================
# OVOZLI XABARLAR (VOICE & AUDIO) HANDLERI
# =====================================================================

@router.message(F.voice)
@router.message(F.audio)
async def handle_audio_message(message: Message):
    """
    Ovozli xabar qabul qilinganda format tanlash imkonini berish:
    - 📝 Matn, 🎙 Ovozli, 📄 Word, 📑 PDF, ✨ Barchasi
    """
    user_id = message.from_user.id
    user_name = message.from_user.full_name if message.from_user else "Foydalanuvchi"
    audio_obj = message.voice or message.audio
    file_id = audio_obj.file_id
    mime_type = getattr(audio_obj, "mime_type", "audio/ogg") or "audio/ogg"
    caption = message.caption or ""

    audio_id = uuid.uuid4().hex[:8]
    pending_audio[audio_id] = {
        "user_id": user_id,
        "user_name": user_name,
        "file_id": file_id,
        "mime_type": mime_type,
        "caption": caption
    }

    choice_text = (
        "🎙 <b>Audio xabar qabul qilindi!</b>\n\n"
        "Ushbu audio xabarni tahlil qilib, natijani qaysi formatda olishni xohlaysiz?\n\n"
        "• 📝 <b>Matn:</b> Chatda yozma javob\n"
        "• 🎙 <b>Ovozli:</b> Audio shaklida eshitish\n"
        "• 📄 <b>Word:</b> Formatlangan <code>.docx</code> fayli\n"
        "• 📑 <b>PDF:</b> Chop etishga tayyor <code>.pdf</code> hujjati\n"
        "• ✨ <b>Barchasi birga:</b> Barcha formatlarda olish\n\n"
        "👇 <i>Quyidagi tugmalardan birini tanlang:</i>"
    )

    await message.reply(choice_text, parse_mode="HTML", reply_markup=get_audio_choice_keyboard(audio_id))


@router.callback_query(F.data.startswith("aud_mode:"))
async def handle_audio_mode_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov!")
        return

    action = parts[1]
    audio_id = parts[2]

    if action == "cancel":
        pending_audio.pop(audio_id, None)
        await callback.answer("Bekor qilindi.")
        try:
            await callback.message.edit_text("❌ Audio tahlili bekor qilindi.", parse_mode="HTML")
        except Exception:
            pass
        return

    item = pending_audio.get(audio_id)
    if not item:
        await callback.answer("Ushbu so'rov muddati o'tgan. Iltimos, audioni qaytadan yuboring.", show_alert=True)
        return

    await callback.answer("Tanlovingiz qabul qilindi!")
    status_msg = await callback.message.edit_text("⏳ <i>Audio Gemini AI tomonidan tahlil qilinmoqda, iltimos kuting...</i>", parse_mode="HTML")

    try:
        file_info = await callback.bot.get_file(item["file_id"])
        audio_bytes_io = io.BytesIO()
        await callback.bot.download_file(file_info.file_path, destination=audio_bytes_io)
        audio_bytes = audio_bytes_io.getvalue()

        response = await ai_service.ask_gemini_audio(
            user_id=item["user_id"],
            audio_bytes=audio_bytes,
            mime_type=item["mime_type"],
            prompt=item["caption"]
        )

        user_name = item.get("user_name", "Foydalanuvchi")
        res_id = store_result(title="Audio Tahlil", content=response, user_name=user_name)

        if action in ("text", "all"):
            try:
                await status_msg.delete()
            except Exception:
                pass
            await send_ai_response(callback.message, response, res_id=res_id)

        if action in ("docx", "all"):
            docx_path = generate_docx(title="Audio Xabar Tahlili", content=response, user_name=user_name)
            try:
                await callback.message.answer_document(
                    document=FSInputFile(docx_path, filename=f"Audio_Tahlil_{audio_id}.docx"),
                    caption="📄 <b>Audio xabar tahlili (Word hujjati)</b>\n<i>Tayyorlovchi: @Bshaxsiy_bot</i>",
                    parse_mode="HTML"
                )
            finally:
                if docx_path and os.path.exists(docx_path):
                    try:
                        os.remove(docx_path)
                    except Exception:
                        pass

        if action in ("pdf", "all"):
            pdf_path = generate_pdf(title="Audio Xabar Tahlili", content=response, user_name=user_name)
            try:
                await callback.message.answer_document(
                    document=FSInputFile(pdf_path, filename=f"Audio_Tahlil_{audio_id}.pdf"),
                    caption="📑 <b>Audio xabar tahlili (PDF hujjati)</b>\n<i>Tayyorlovchi: @Bshaxsiy_bot</i>",
                    parse_mode="HTML"
                )
            finally:
                if pdf_path and os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass

        if action in ("voice", "all"):
            ogg_path, mp3_path = await convert_text_to_voice_and_audio(response)
            try:
                try:
                    await callback.message.answer_voice(
                        voice=FSInputFile(ogg_path),
                        caption="🎙 <b>AI Ovozli Javobi</b>",
                        parse_mode="HTML"
                    )
                except TelegramBadRequest as e:
                    if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                        await callback.message.answer_audio(
                            audio=FSInputFile(mp3_path),
                            caption="🎙 <b>AI Ovozli Javobi</b>",
                            parse_mode="HTML"
                        )
                    else:
                        raise
            finally:
                for p in (ogg_path, mp3_path):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

        if action != "text":
            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as e:
        logger.exception(f"Audio xabarni tahlil qilishda xatolik: {e}")
        try:
            await status_msg.edit_text("❌ Audio tahlil qilishda xatolik yuz berdi.", parse_mode="HTML")
        except Exception:
            pass
    finally:
        pending_audio.pop(audio_id, None)


# =====================================================================
# INTERAKTIV BO'LIMGA O'TKAZISH VA TAHRIRLASH CALLBACKLARI
# =====================================================================

@router.callback_query(F.data.startswith("act:"))
async def handle_content_action_callback(callback: CallbackQuery):
    """
    Natija tagidagi «📂 Bo'limga o'tkazish» va «✏️ Tahrirlash» tugmalari
    """
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Xatolik!")
        return

    sub_act = parts[1]
    res_id = parts[2]

    if res_id not in result_store:
        await callback.answer("Ma'lumot xotirada eskirgan.", show_alert=True)
        return

    if sub_act == "tr":
        await callback.message.edit_reply_markup(reply_markup=get_transfer_keyboard(res_id))
        await callback.answer("Bo'limni tanlang:")
    elif sub_act == "ed":
        await callback.message.edit_reply_markup(reply_markup=get_edit_keyboard(res_id))
        await callback.answer("Tahrirlash turini tanlang:")
    elif sub_act == "back":
        await callback.message.edit_reply_markup(reply_markup=get_content_actions_keyboard(res_id))
        await callback.answer("Asosiy holatga qaytdi.")


@router.callback_query(F.data.startswith("tr:"))
async def handle_transfer_execution(callback: CallbackQuery):
    """
    Natijani tanlangan bo'lim formatiga o'tkazish (Word, PDF, Excel, PPTX, Deep, Voice)
    """
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Xatolik!")
        return

    target = parts[1]
    res_id = parts[2]

    item = result_store.get(res_id)
    if not item:
        await callback.answer("Ushbu ma'lumot xotirada topilmadi.", show_alert=True)
        return

    content = item["content"]
    title = item["title"]
    user_name = item.get("user_name", "Foydalanuvchi")

    await callback.answer(f"O'tkazilmoqda...")

    if target == "docx":
        docx_path = generate_docx(title=title, content=content, user_name=user_name)
        try:
            await callback.message.answer_document(
                document=FSInputFile(docx_path, filename=f"{title[:20]}.docx"),
                caption=f"📄 <b>Word hujjati yaratildi</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            if docx_path and os.path.exists(docx_path):
                os.remove(docx_path)

    elif target == "pdf":
        pdf_path = generate_pdf(title=title, content=content, user_name=user_name)
        try:
            await callback.message.answer_document(
                document=FSInputFile(pdf_path, filename=f"{title[:20]}.pdf"),
                caption=f"📑 <b>PDF hujjati yaratildi</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)

    elif target == "pptx":
        status_msg = await callback.message.reply("📽 <i>Prezentatsiya slaydlari shakllantirilmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        slides_text = await ai_service.format_for_presentation(callback.from_user.id, content)
        pptx_path = generate_presentation(title=title, content=slides_text, user_name=user_name)
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            await callback.message.answer_document(
                document=FSInputFile(pptx_path, filename=f"{title[:20]}.pptx"),
                caption=f"📽 <b>PowerPoint taqdimoti yaratildi!</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            if pptx_path and os.path.exists(pptx_path):
                os.remove(pptx_path)

    elif target == "xlsx":
        status_msg = await callback.message.reply("📊 <i>Excel jadvali shakllantirilmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        table_text = await ai_service.format_for_excel(callback.from_user.id, content)
        xlsx_path = generate_excel(title=title, content=table_text, user_name=user_name)
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            await callback.message.answer_document(
                document=FSInputFile(xlsx_path, filename=f"{title[:20]}.xlsx"),
                caption=f"📊 <b>Excel jadvali yaratildi!</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            if xlsx_path and os.path.exists(xlsx_path):
                os.remove(xlsx_path)

    elif target == "deep":
        status_msg = await callback.message.reply("🔬 <i>Ushbu mavzu bo'yicha Deep Research o'tkazilmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        deep_res = await ai_service.deep_research(callback.from_user.id, f"{title}\n{content[:500]}")
        try:
            await status_msg.delete()
        except Exception:
            pass
        new_res_id = store_result(title=f"Deep Research: {title}", content=deep_res, user_name=user_name)
        await send_ai_response(callback.message, deep_res, res_id=new_res_id)

    elif target == "voice":
        status_msg = await callback.message.reply("🎙 <i>Ovozli xabar tayyorlanmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)
        ogg_path, mp3_path = await convert_text_to_voice_and_audio(content)
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            try:
                await callback.message.answer_voice(voice=FSInputFile(ogg_path), caption=f"🎙 <b>Ovozli javob: {title}</b>", parse_mode="HTML")
            except TelegramBadRequest:
                await callback.message.answer_audio(audio=FSInputFile(mp3_path), caption=f"🎙 <b>Audio fayl: {title}</b>", parse_mode="HTML")
        finally:
            for p in (ogg_path, mp3_path):
                if p and os.path.exists(p):
                    os.remove(p)


@router.callback_query(F.data.startswith("ed:"))
async def handle_edit_execution(callback: CallbackQuery):
    """
    Natijani tahrirlash (kengaytirish, qisqartirish, qayta yozish, tarjima)
    """
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Xatolik!")
        return

    edit_type = parts[1]
    res_id = parts[2]

    item = result_store.get(res_id)
    if not item:
        await callback.answer("Ma'lumot topilmadi.", show_alert=True)
        return

    content = item["content"]
    title = item["title"]
    user_name = item.get("user_name", "Foydalanuvchi")

    instructions = {
        "expand": "Ushbu tahlilni yanada kengaytirib, ko'proq tafsilotlar, misollar va tushuntirishlar bilan boyiting.",
        "summary": "Ushbu tahlilni eng muhim xulosalarga qisqartiring, punktlar bilan lo'nda qilib bering.",
        "rewrite": "Ushbu tahlilni yanada professional, ravon va ta'sirchan uslubda qayta shakllantiring.",
        "truz": "Ushbu matnni toza, ravon va adabiy o'zbek tiliga o'giring."
    }

    instruction = instructions.get(edit_type, "Matnni qayta ishlang.")
    await callback.answer("Tahrirlanmoqda...")
    status_msg = await callback.message.reply("✏️ <i>Tahrirlanmoqda, iltimos kuting...</i>", parse_mode="HTML")
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

    new_content = await ai_service.refine_content(callback.from_user.id, content, instruction)
    try:
        await status_msg.delete()
    except Exception:
        pass

    new_res_id = store_result(title=f"{title} (Tahrirlangan)", content=new_content, user_name=user_name)
    await send_ai_response(callback.message, new_content, res_id=new_res_id)


# =====================================================================
# VIDEO VA RASM HANDLERLARI
# =====================================================================

@router.message(F.video)
async def handle_video_message(message: Message):
    """Video qabul qilinganda dumaloq qilish yoki MP3 audio ajratish taklifi"""
    status_msg = await message.reply("⏳ <i>Video qabul qilindi. Dumaloq (Video Note) formatiga aylantirilmoqda...</i>", parse_mode="HTML")
    input_path = None
    circle_path = None

    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VIDEO_NOTE)
        unique_id = uuid.uuid4().hex[:8]
        input_path = str(DOWNLOADS_DIR / f"raw_{unique_id}.mp4")

        file_info = await message.bot.get_file(message.video.file_id)
        await message.bot.download_file(file_info.file_path, destination=input_path)

        circle_path, duration = await convert_to_video_note(input_path, max_duration=60)
        await message.answer_video_note(video_note=FSInputFile(circle_path), length=384, duration=duration)
        try:
            await status_msg.delete()
        except Exception:
            pass

    except TelegramBadRequest as e:
        if "VOICE_MESSAGES_FORBIDDEN" in str(e):
            if circle_path and os.path.exists(circle_path):
                await message.answer_video(video=FSInputFile(circle_path), caption="🔘 <b>Dumaloq video tayyor!</b>", parse_mode="HTML")
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        else:
            await status_msg.edit_text("❌ Telegram xatoligi yuz berdi.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Videoni dumaloq qilishda xatolik: {e}")
        await status_msg.edit_text("❌ Videoni aylantirishda xatolik yuz berdi.", parse_mode="HTML")
    finally:
        for path in (input_path, circle_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


@router.callback_query(F.data.startswith("vid:"))
async def handle_video_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    action = parts[1]
    v_id = parts[2]
    item = pending_audio.get(f"vid_{v_id}")

    if not item or "path" not in item:
        await callback.answer("Video eskirgan.", show_alert=True)
        return

    video_path = item["path"]
    if action == "audio":
        await callback.answer("Audio ajratilmoqda...")
        status_msg = await callback.message.reply("🎵 <i>Audioni MP3 formatida ajratib olinmoqda...</i>", parse_mode="HTML")
        try:
            mp3_path = await extract_audio_from_video(video_path)
            try:
                await status_msg.delete()
            except Exception:
                pass
            await callback.message.answer_audio(audio=FSInputFile(mp3_path), caption="🎵 <b>Videodan ajratilgan MP3 audio</b>", parse_mode="HTML")
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Audio ajratishda xatolik: {e}", parse_mode="HTML")

    elif action == "circle":
        await callback.answer("Dumaloq video tayyorlanmoqda...")
        status_msg = await callback.message.reply("⏳ <i>Dumaloq formatga o'tkazilmoqda...</i>", parse_mode="HTML")
        try:
            circle_path, duration = await convert_to_video_note(video_path, max_duration=60)
            await callback.message.answer_video_note(video_note=FSInputFile(circle_path), length=384, duration=duration)
            try:
                await status_msg.delete()
            except Exception:
                pass
            if os.path.exists(circle_path):
                os.remove(circle_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Dumaloq qilishda xatolik: {e}", parse_mode="HTML")


@router.message(F.photo)
async def handle_photo_message(message: Message):
    """Rasm qabul qilinganda uni AI ga yuborish"""
    user_id = message.from_user.id
    caption = message.caption or ""

    status_msg = await message.reply("🤔 <i>Rasm tahlil qilinmoqda...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await message.bot.download_file(file_info.file_path, destination=photo_bytes_io)
        photo_bytes = photo_bytes_io.getvalue()

        response = await ai_service.ask_gemini(
            user_id=user_id,
            prompt=caption or "Ushbu rasmni chuqur tahlil qiling va nimalar aks etganini yozing.",
            image_bytes=photo_bytes,
            mime_type="image/jpeg"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title="Rasm tahlili", content=response, user_name=message.from_user.full_name)
        await send_ai_response(message, response, res_id=res_id)

    except Exception as e:
        logger.exception(f"Rasmni tahlil qilishda xatolik: {e}")
        await status_msg.edit_text("❌ Rasmni tahlil qilishda xatolik yuz berdi.", parse_mode="HTML")


# =====================================================================
# MATNLI XABARLAR (BO'LIMLAR BO'YICHA YO'NALTIRISH)
# =====================================================================

@router.message(F.text)
async def handle_text_message(message: Message):
    user_id = message.from_user.id
    text = message.text or ""
    current_mode = user_active_mode.get(user_id, "NORMAL")
    user_name = message.from_user.full_name if message.from_user else "Foydalanuvchi"

    # 1. Instagram havolasi bo'lsa
    match = INSTAGRAM_URL_REGEX.search(text)
    if match:
        await process_instagram_download(message, match.group(0))
        return

    # 2. Rejim: DEEP_RESEARCH
    if current_mode == "DEEP_RESEARCH":
        status_msg = await message.reply("🔬 <i>Mavzu bo'yicha chuqur izlanish (Deep Research) olib borilmoqda, iltimos kuting...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        report = await ai_service.deep_research(user_id=user_id, query=text)
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title=text[:35], content=report, user_name=user_name)
        await send_ai_response(message, report, res_id=res_id)
        return

    # 3. Rejim: SLIDES (Prezentatsiya)
    if current_mode == "SLIDES":
        status_msg = await message.reply("📽 <i>Taqdimot slaydlari rejalashtirilmoqda va PowerPoint (.pptx) fayli yaratilmoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        slides_text = await ai_service.format_for_presentation(user_id=user_id, text=text)
        pptx_path = generate_presentation(title=text[:40], content=slides_text, user_name=user_name)
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title=f"Prezentatsiya: {text[:30]}", content=slides_text, user_name=user_name)
        try:
            await message.answer_document(
                document=FSInputFile(pptx_path, filename=f"Prezentatsiya_{res_id}.pptx"),
                caption=f"📽 <b>PowerPoint taqdimotingiz tayyor!</b>\n<i>Mavzu: {text[:45]}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            if pptx_path and os.path.exists(pptx_path):
                os.remove(pptx_path)
        return

    # 4. Rejim: OFFICE_DOCS (Word, PDF, Excel)
    if current_mode == "OFFICE_DOCS":
        status_msg = await message.reply("📊 <i>Ma'lumotlar qayta ishlanmoqda va hujjatlar tayyorlanmoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        docx_path = generate_docx(title=text[:35], content=text, user_name=user_name)
        pdf_path = generate_pdf(title=text[:35], content=text, user_name=user_name)
        table_text = await ai_service.format_for_excel(user_id, text)
        xlsx_path = generate_excel(title=text[:35], content=table_text, user_name=user_name)

        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title=text[:30], content=text, user_name=user_name)
        try:
            await message.answer_document(
                document=FSInputFile(docx_path, filename=f"Hujjat_{res_id}.docx"),
                caption="📄 <b>Word formati (.docx)</b>",
                parse_mode="HTML"
            )
            await message.answer_document(
                document=FSInputFile(pdf_path, filename=f"Hujjat_{res_id}.pdf"),
                caption="📑 <b>PDF formati (.pdf)</b>",
                parse_mode="HTML"
            )
            await message.answer_document(
                document=FSInputFile(xlsx_path, filename=f"Jadval_{res_id}.xlsx"),
                caption="📊 <b>Excel jadvali (.xlsx)</b>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id)
            )
        finally:
            for p in (docx_path, pdf_path, xlsx_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        return

    # 5. Rejim: DOC_ANALYSIS (Matn shaklida tahlil so'ralsa)
    if current_mode == "DOC_ANALYSIS":
        status_msg = await message.reply("📑 <i>Hujjat matni tahlil qilinmoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        analysis = await ai_service.analyze_document(user_id=user_id, doc_text=text, filename="Matnli Hujjat")
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title="Hujjat tahlili", content=analysis, user_name=user_name)
        await send_ai_response(message, analysis, res_id=res_id)
        return

    # 6. Rejim: AI_CHAT yoki oddiy suhbat
    status_msg = await message.reply("🤔 <i>O'ylamoqda...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    response = await ai_service.ask_gemini(user_id=user_id, prompt=text)
    try:
        await status_msg.delete()
    except Exception:
        pass

    res_id = store_result(title=text[:30], content=response, user_name=user_name)
    await send_ai_response(message, response, res_id=res_id)


async def process_instagram_download(message: Message, url: str):
    """Instagram videosini yuklash jarayoni"""
    status_msg = await message.reply("⏳ <i>Instagram video yuklab olinmoqda, iltimos kuting...</i>", parse_mode="HTML")
    file_path = None
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)
        video_data = await download_instagram_video(url)
        file_path = video_data['file_path']

        bot_info = await message.bot.get_me()
        caption = (
            f"🎬 <b>{video_data['title']}</b>\n\n"
            f"🤖 <i>@{bot_info.username} orqali yuklandi</i>"
        )

        video_file = FSInputFile(file_path)
        await message.answer_video(
            video=video_file,
            caption=caption,
            parse_mode="HTML",
            duration=video_data.get('duration'),
            width=video_data.get('width'),
            height=video_data.get('height'),
            supports_streaming=True,
            reply_markup=get_video_options_keyboard(file_path)
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except FileTooLargeError as e:
        await status_msg.edit_text(f"❌ <b>Hajm bo'yicha cheklov:</b>\n{str(e)}", parse_mode="HTML")
    except PrivateMediaError as e:
        await status_msg.edit_text(f"🔒 <b>Yopiq hisob:</b>\n{str(e)}", parse_mode="HTML")
    except DownloadException as e:
        await status_msg.edit_text("❌ <b>Xatolik yuz berdi:</b>\nVideoni yuklab bo'lmadi.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
        await status_msg.edit_text("❌ Texnik xatolik yuz berdi.", parse_mode="HTML")
    finally:
        # Faylni darhol o'chirmaymiz, foydalanuvchi "audio ajratish" tugmasini bosishi mumkin (yoki 10 daqiqadan keyin tozalanadi)
        pass


# =====================================================================
# QO'RIQCHI (ACCESS GUARD) VA ADMIN BOSHQARUV HANDLERLARI
# =====================================================================

def get_admin_panel_text() -> str:
    """Qo'riqchi boshqaruv paneli ma'lumotlari"""
    is_restricted = access_guard.is_restricted_mode()
    mode_str = "🔒 <b>Cheklangan rejim (Faqat ruxsat berilganlar)</b>" if is_restricted else "🔓 <b>Ochiq rejim (Barcha foydalanuvchilar)</b>"
    users_count = len(access_guard.get_allowed_users())
    admins_count = len(access_guard.get_admins())
    return (
        "🛡 <b>QO'RIQCHI (ACCESS GUARD) BOSHQARUV PANELI</b>\n\n"
        f"📊 <b>Tizim holati:</b> {mode_str}\n"
        f"👥 <b>Ruxsat berilgan foydalanuvchilar:</b> {users_count} nafar\n"
        f"👑 <b>Administratorlar soni:</b> {admins_count} nafar\n\n"
        "Quyidagi boshqaruv buyruqlaridan foydalanishingiz mumkin:\n"
        "• <code>/users</code> — Ruxsat berilganlar ro'yxati\n"
        "• <code>/add_user &lt;id&gt; [ism]</code> — Yangi foydalanuvchi qo'shish\n"
        "• <code>/remove_user &lt;id&gt;</code> — Foydalanuvchidan ruxsatni olish\n"
        "• <code>/add_admin &lt;id&gt; [ism]</code> — Yangi admin tayinlash\n"
        "• <code>/toggle_guard</code> — Rejimni o'zgartirish (Ochiq/Cheklangan)\n"
        "• <code>/my_id</code> — O'z ID raqamingizni ko'rish"
    )


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Qo'riqchi boshqaruv tugmalari"""
    is_restricted = access_guard.is_restricted_mode()
    toggle_text = "🔓 Ochiq rejimga o'tish" if is_restricted else "🔒 Cheklangan rejimga o'tish"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Foydalanuvchilar ro'yxati", callback_data="adm:list_users"),
                InlineKeyboardButton(text=toggle_text, callback_data="adm:toggle_mode")
            ],
            [
                InlineKeyboardButton(text="➕ User qo'shish yo'riqnomasi", callback_data="adm:help_add"),
                InlineKeyboardButton(text="➖ User o'chirish yo'riqnomasi", callback_data="adm:help_remove")
            ]
        ]
    )


@router.message(Command("admin"))
@router.message(Command("qoriqchi"))
@router.message(F.text == "🛡 Qo'riqchi (Admin)")
async def handle_admin_panel(message: Message):
    """Admin paneli"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 <b>Kechirasiz, ushbu bo'lim faqat administratorlar uchun!</b>", parse_mode="HTML")
        return
    await message.answer(
        get_admin_panel_text(),
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard()
    )


@router.callback_query(F.data == "adm:toggle_mode")
async def handle_adm_toggle_mode(callback: CallbackQuery):
    """Cheklangan / Ochiq rejimni almashtirish"""
    if not access_guard.is_admin(callback.from_user.id):
        await callback.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        return
    new_mode = access_guard.toggle_restricted_mode()
    mode_str = "🔒 Cheklangan rejim faollashdi" if new_mode else "🔓 Ochiq rejim faollashdi"
    await callback.answer(mode_str, show_alert=True)
    try:
        await callback.message.edit_text(
            get_admin_panel_text(),
            parse_mode="HTML",
            reply_markup=get_admin_panel_keyboard()
        )
    except Exception:
        pass


@router.callback_query(F.data == "adm:list_users")
@router.message(Command("users"))
async def handle_list_users(event: Message | CallbackQuery):
    """Ruxsat berilganlar ro'yxati"""
    user_id = event.from_user.id
    if not access_guard.is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        else:
            await event.reply("🔒 Faqat adminlar uchun!")
        return

    users = access_guard.get_allowed_users()
    if not users:
        msg = "ℹ️ Hozircha ruxsat berilgan qo'shimcha foydalanuvchilar mavjud emas."
    else:
        msg = f"👥 <b>Ruxsat berilgan foydalanuvchilar ({len(users)} ta):</b>\n\n"
        for idx, (uid, info) in enumerate(users.items(), 1):
            uname = f"@{info.get('username')}" if info.get('username') else "username yo'q"
            msg += (
                f"<b>{idx}. {info.get('name', 'Nomsiz')}</b> ({uname})\n"
                f"   🆔 ID: <code>{uid}</code>\n"
                f"   📅 Qo'shildi: {info.get('added_at', '-')}\n"
                f"   👑 Qo'shdi: {info.get('added_by', '-')}\n\n"
            )

    if isinstance(event, CallbackQuery):
        await event.answer()
        await event.message.answer(msg, parse_mode="HTML")
    else:
        await event.answer(msg, parse_mode="HTML")


@router.callback_query(F.data == "adm:help_add")
async def handle_adm_help_add(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "➕ <b>Foydalanuvchi qo'shish yo'riqnomasi:</b>\n\n"
        "Foydalanuvchini botga kiritish uchun quyidagi buyruqni yuboring:\n"
        "<code>/add_user &lt;TELEGRAM_ID&gt; [ism]</code>\n\n"
        "Misol: <code>/add_user 123456789 Ali</code>\n\n"
        "💡 <i>Foydalanuvchi o'z ID raqamini bilish uchun botga <code>/my_id</code> deb yozishi kifoya.</i>",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "adm:help_remove")
async def handle_adm_help_remove(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "➖ <b>Foydalanuvchini o'chirish yo'riqnomasi:</b>\n\n"
        "Foydalanuvchining ruxsatini bekor qilish uchun quyidagi buyruqni yuboring:\n"
        "<code>/remove_user &lt;TELEGRAM_ID&gt;</code>\n\n"
        "Misol: <code>/remove_user 123456789</code>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("auth_approve:"))
async def handle_auth_approve(callback: CallbackQuery):
    """Admin yangi foydalanuvchiga ruxsat berishi"""
    if not access_guard.is_admin(callback.from_user.id):
        await callback.answer("🔒 Faqat administratorlar tasdiqlashi mumkin!", show_alert=True)
        return

    target_id_str = callback.data.split(":")[1]
    target_id = int(target_id_str)

    req_info = access_guard.data.get("pending_requests", {}).get(target_id_str, {})
    target_name = req_info.get("name", f"Foydalanuvchi_{target_id}")
    target_username = req_info.get("username", "")

    access_guard.add_user(
        user_id=target_id,
        name=target_name,
        username=target_username,
        added_by=callback.from_user.full_name
    )

    await callback.message.edit_text(
        f"✅ <b>Ruxsat berildi!</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {target_name}\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
        f"👑 <b>Tasdiqlagan admin:</b> {callback.from_user.full_name}",
        parse_mode="HTML"
    )

    try:
        await callback.bot.send_message(
            chat_id=target_id,
            text=(
                "🎉 <b>Tabriklaymiz!</b>\n\n"
                "Administrator sizga botdan foydalanish uchun ruxsat berdi.\n\n"
                "Boshlash uchun: /start"
            ),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Foydalanuvchiga ({target_id}) tasdiq xabarini jo'natishda xatolik: {e}")


@router.callback_query(F.data.startswith("auth_deny:"))
async def handle_auth_deny(callback: CallbackQuery):
    """Admin foydalanuvchi so'rovini rad etishi"""
    if not access_guard.is_admin(callback.from_user.id):
        await callback.answer("🔒 Faqat administratorlar rad eta oladi!", show_alert=True)
        return

    target_id_str = callback.data.split(":")[1]
    target_id = int(target_id_str)

    if target_id_str in access_guard.data.get("pending_requests", {}):
        del access_guard.data["pending_requests"][target_id_str]
        access_guard.save()

    await callback.message.edit_text(
        f"❌ <b>Ruxsat so'rovi rad etildi.</b>\n\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
        f"👑 <b>Admin:</b> {callback.from_user.full_name}",
        parse_mode="HTML"
    )

    try:
        await callback.bot.send_message(
            chat_id=target_id,
            text=(
                "❌ <b>Ruxsat so'rovingiz rad etildi</b>\n\n"
                "Kechirasiz, administrator ushbu botdan foydalanish so'rovingizni rad etdi."
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.message(Command("add_user"))
async def handle_add_user_cmd(message: Message):
    """Admin qo'lda user qo'shishi: /add_user <id> [ism]"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply(
            "⚠️ <b>Noto'g'ri format!</b>\n"
            "Format: <code>/add_user &lt;TELEGRAM_ID&gt; [ism]</code>\n"
            "Misol: <code>/add_user 123456789 Ali</code>",
            parse_mode="HTML"
        )
        return

    target_id = int(parts[1])
    target_name = parts[2] if len(parts) > 2 else f"Foydalanuvchi_{target_id}"

    access_guard.add_user(
        user_id=target_id,
        name=target_name,
        added_by=message.from_user.full_name
    )

    await message.reply(
        f"✅ <b>Foydalanuvchi muvaffaqiyatli qo'shildi!</b>\n\n"
        f"👤 <b>Ism:</b> {target_name}\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>",
        parse_mode="HTML"
    )

    try:
        await message.bot.send_message(
            chat_id=target_id,
            text=(
                "🎉 <b>Tabriklaymiz!</b>\n\n"
                "Administrator sizga botdan foydalanish uchun ruxsat berdi.\n\n"
                "Boshlash uchun: /start"
            ),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.message(Command("remove_user"))
async def handle_remove_user_cmd(message: Message):
    """Admin user ruxsatini bekor qilishi: /remove_user <id>"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply(
            "⚠️ <b>Noto'g'ri format!</b>\n"
            "Format: <code>/remove_user &lt;TELEGRAM_ID&gt;</code>\n"
            "Misol: <code>/remove_user 123456789</code>",
            parse_mode="HTML"
        )
        return

    target_id = int(parts[1])
    if access_guard.remove_user(target_id):
        await message.reply(
            f"✅ Foydalanuvchi (ID: <code>{target_id}</code>) ro'yxatdan o'chirildi va ruxsati bekor qilindi.",
            parse_mode="HTML"
        )
    else:
        await message.reply(
            f"ℹ️ Bunday ID (<code>{target_id}</code>) ga ega foydalanuvchi topilmadi.",
            parse_mode="HTML"
        )


@router.message(Command("add_admin"))
async def handle_add_admin_cmd(message: Message):
    """Yangi admin tayinlash: /add_admin <id> [ism]"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply(
            "⚠️ <b>Noto'g'ri format!</b>\n"
            "Format: <code>/add_admin &lt;TELEGRAM_ID&gt; [ism]</code>\n"
            "Misol: <code>/add_admin 123456789 Adminbek</code>",
            parse_mode="HTML"
        )
        return

    target_id = int(parts[1])
    target_name = parts[2] if len(parts) > 2 else f"Admin_{target_id}"

    access_guard.add_admin(target_id, name=target_name)
    await message.reply(
        f"👑 <b>Yangi administrator tayinlandi!</b>\n\n"
        f"👤 <b>Ism:</b> {target_name}\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>",
        parse_mode="HTML"
    )


@router.message(Command("toggle_guard"))
async def handle_toggle_guard_cmd(message: Message):
    """Qo'riqchi rejimini o'zgartirish buyrug'i"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    new_mode = access_guard.toggle_restricted_mode()
    mode_text = (
        "🔒 <b>Cheklangan rejim yoqildi:</b> Endi faqat ruxsat berilgan foydalanuvchilar kira oladi."
        if new_mode else
        "🔓 <b>Ochiq rejim yoqildi:</b> Endi barcha foydalanuvchilar botdan foydalanishi mumkin."
    )
    await message.reply(mode_text, parse_mode="HTML")


@router.message(Command("my_id"))
@router.message(Command("id"))
async def handle_my_id_cmd(message: Message):
    """Foydalanuvchining o'z ID sini ko'rish buyrug'i"""
    await message.reply(
        f"🆔 <b>Sizning Telegram ID raqamingiz:</b> <code>{message.from_user.id}</code>",
        parse_mode="HTML"
    )
