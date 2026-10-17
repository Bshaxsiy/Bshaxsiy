import io
import os
import re
import uuid
import logging
from pathlib import Path

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
    DownloadException,
    FileTooLargeError,
    PrivateMediaError,
)
from video_converter import convert_to_video_note
from ai_service import ai_service
from tts_service import convert_text_to_voice_and_audio
from document_service import generate_docx, generate_pdf

logger = logging.getLogger(__name__)
router = Router()

# Instagram havolalarini aniqlash uchun regex
INSTAGRAM_URL_REGEX = re.compile(
    r"https?:\/\/(?:www\.)?instagram\.com\/(?:[a-zA-Z0-9_\.]+\/)?(?:p|reel|reels|tv|share)\/[a-zA-Z0-9_\-]+[^\s]*",
    re.IGNORECASE
)

# AI rejimida bo'lgan foydalanuvchilar to'plami
ai_active_users: set[int] = set()

# Format tanlash kutilayotgan audio xabarlar: audio_id -> {user_id, file_id, mime_type, caption}
pending_audio: dict[str, dict] = {}


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Asosiy menyu tugmalari"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🤖 AI Chat"),
                KeyboardButton(text="📥 Instagram Yuklash")
            ],
            [
                KeyboardButton(text="🔘 Dumaloq Video"),
                KeyboardButton(text="ℹ️ Yordam")
            ]
        ],
        resize_keyboard=True
    )


def get_ai_keyboard() -> ReplyKeyboardMarkup:
    """AI Chat rejimi uchun tugmalar"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔄 Yangi suhbat (/clear)"),
                KeyboardButton(text="❌ AI rejimdan chiqish")
            ]
        ],
        resize_keyboard=True
    )


def get_ai_inline_button() -> InlineKeyboardMarkup:
    """AI chatni boshlash uchun inline tugma"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤖 AI Chatni boshlash",
                    callback_data="start_ai_chat"
                )
            ]
        ]
    )


def get_circle_button() -> InlineKeyboardMarkup:
    """Videoni dumaloq formatga o'tkazish uchun tugma"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔘 Dumaloq video (Video Note) qilish",
                    callback_data="make_circle"
                )
            ]
        ]
    )


def get_audio_choice_keyboard(audio_id: str) -> InlineKeyboardMarkup:
    """Audio tahlil va javob formatini tanlash tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Matn",
                    callback_data=f"aud_mode:text:{audio_id}"
                ),
                InlineKeyboardButton(
                    text="🎙 Ovozli (Audio)",
                    callback_data=f"aud_mode:voice:{audio_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Word (.docx)",
                    callback_data=f"aud_mode:docx:{audio_id}"
                ),
                InlineKeyboardButton(
                    text="📑 PDF (.pdf)",
                    callback_data=f"aud_mode:pdf:{audio_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✨ Barchasi birga (Hammasi)",
                    callback_data=f"aud_mode:all:{audio_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=f"aud_mode:cancel:{audio_id}"
                )
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


async def send_ai_response(message: Message, response_text: str):
    """Javobni xavfsiz formatda jo'natish (4096 belgi limitini inobatga olgan holda)"""
    if not response_text:
        await message.reply("Javob bo'sh qaytdi.")
        return

    max_len = 4000
    chunks = [response_text[i:i + max_len] for i in range(0, len(response_text), max_len)]
    for chunk in chunks:
        try:
            await message.answer(chunk, parse_mode="Markdown")
        except TelegramBadRequest:
            try:
                await message.answer(chunk, parse_mode="HTML")
            except TelegramBadRequest:
                await message.answer(chunk, parse_mode=None)


@router.message(CommandStart())
async def handle_start(message: Message):
    """
    /start buyrug'i uchun javob
    """
    user_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"
    welcome_text = (
        f"👋 Assalomu alaykum, <b>{user_name}</b>!\n\n"
        "🌟 <b>Ko'p funksiyali Telegram Bot</b>ga xush kelibsiz!\n\n"
        "<b>Botning asosiy imkoniyatlari:</b>\n"
        "1. 🤖 <b>Gemini AI Chat:</b> Istalgan mavzuda suhbatlashing, savollarga javob oling yoki rasm yuborib tahlil qildiring.\n"
        "2. 🎙 <b>Audio & Ovozli Xabar Tahlili:</b> Ovozli xabar yuboring va natijani <b>Matn</b>, <b>Ovozli</b>, <b>Word (.docx)</b> yoki <b>PDF (.pdf)</b> shaklida oling!\n"
        "3. 📥 <b>Instagram Video Yuklash:</b> Reels, IGTV yoki post havolasini yuboring.\n"
        "4. 🔘 <b>Dumaloq Video (Video Note):</b> To'rtburchak videoni yuboring — bot uni Telegram doiraviy videosiga (krujok) aylantirib beradi!\n\n"
        "👇 <i>Kerakli bo'limni quyidagi menyudan tanlang:</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_keyboard())


@router.message(F.text == "ℹ️ Yordam")
@router.message(Command("help"))
async def handle_help(message: Message):
    """
    /help buyrug'i uchun javob
    """
    help_text = (
        "📖 <b>Botdan foydalanish qo'llanmasi:</b>\n\n"
        "🤖 <b>1. Gemini AI Chat:</b>\n"
        "• <b>«🤖 AI Chat»</b> tugmasini bosing yoki <code>/ai</code> yuboring — AI rejimiga o'tasiz.\n"
        "• Rasm yuborsangiz, AI uni tahlil qilib javob beradi.\n"
        "• Tezkor savol berish: <code>/ai Siz haqingizda aytib bering</code>\n"
        "• Tarixni tozalash: <code>/clear</code> | Rejimdan chiqish: <code>/exit</code>\n\n"
        "🎙 <b>2. Ovozli (Audio) xabarlar tahlili:</b>\n"
        "• Menga ovozli xabar (voice) yoki audio fayl jo'nating.\n"
        "• Bot sizga <b>📝 Matn</b>, <b>🎙 Ovoz</b>, <b>📄 Word (.docx)</b> yoki <b>📑 PDF (.pdf)</b> shaklida hisobot olish tanlovini beradi!\n\n"
        "📥 <b>3. Instagram Video Yuklash:</b>\n"
        "• Instagram video/reels havolasini yuboring.\n\n"
        "🔘 <b>4. Dumaloq video (Video Note) tayyorlash:</b>\n"
        "• Telefoningizdagi videoni botga yuboring yoki yuklangan video tagidagi tugmani bosing.\n\n"
        "🔑 <b>API Kalit sozlash:</b>\n"
        "• Gemini API kalitini o'rnatish uchun: <code>/setkey SIZNING_KALITINGIZ</code>"
    )
    await message.answer(help_text, parse_mode="HTML")


@router.message(F.text == "📥 Instagram Yuklash")
async def handle_instagram_menu(message: Message):
    await message.answer(
        "📥 <b>Instagram video yuklash:</b>\n\n"
        "Menga Instagram Reels, IGTV yoki video post havolasini yuboring. Men uni yuklab beraman!",
        parse_mode="HTML"
    )


@router.message(F.text == "🔘 Dumaloq Video")
async def handle_circle_video_menu(message: Message):
    await message.answer(
        "🔘 <b>Dumaloq video (Video Note) tayyorlash:</b>\n\n"
        "Istalgan to'rtburchak videoni botga yuboring. Bot uni avtomatik tarzda Telegram dumaloq videosiga (krujok) aylantirib beradi (maksimal 60 soniya).",
        parse_mode="HTML"
    )


@router.message(Command("setkey"))
async def handle_setkey_command(message: Message):
    """
    Gemini API kalitini chat orqali sozlash
    """
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "ℹ️ <b>Foydalanish:</b> <code>/setkey SIZNING_GEMINI_API_KALITINGIZ</code>\n\n"
            "Misol:\n<code>/setkey AIzaSyC...</code>\n\n"
            "🔑 API kalitni bepul olish: https://aistudio.google.com/app/apikey",
            parse_mode="HTML"
        )
        return

    new_key = parts[1].strip()
    ai_service.update_api_key(new_key)
    try:
        save_gemini_key_to_env(new_key)
        await message.reply(
            "✅ <b>Gemini API kaliti muvaffaqiyatli saqlandi va faollashtirildi!</b>\n\n"
            "Endi <b>«🤖 AI Chat»</b> bo'limi orqali sun'iy intellektdan to'liq foydalanishingiz mumkin.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f".env ga saqlashda xatolik: {e}")
        await message.reply("✅ Kalit vaqtincha xotiraga yuklandi, ammo .env ga yozishda muammo bo'ldi.")


@router.message(F.text == "🤖 AI Chat")
@router.message(Command("ai"))
async def handle_ai_entry_or_prompt(message: Message):
    """
    AI Chat rejimiga kirish yoki /ai orqali bir martalik savol berish
    """
    user_id = message.from_user.id
    text = message.text or ""

    # Agar /ai <savol> shaklida bo'lsa
    if text.startswith("/ai"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            prompt = parts[1].strip()
            status_msg = await message.reply("🤔 <i>O'ylamoqda...</i>", parse_mode="HTML")
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            response = await ai_service.ask_gemini(user_id=user_id, prompt=prompt)
            try:
                await status_msg.delete()
            except Exception:
                pass
            await send_ai_response(message, response)
            return

    # Aks holda AI rejimiga o'tkazish
    ai_active_users.add(user_id)
    await message.answer(
        "🤖 <b>Gemini AI Chat faollashtirildi!</b>\n\n"
        "Menga istalgan savol, matn, rasm yoki ovozli xabar yuborishingiz mumkin. "
        "Men siz bilan muloqot qilaman va suhbat tarixini eslab qolaman!\n\n"
        "• 🔄 Suhbat tarixini tozalash: <code>/clear</code>\n"
        "• ❌ Chiqish: <code>/exit</code> yoki pastdagi tugma",
        parse_mode="HTML",
        reply_markup=get_ai_keyboard()
    )


@router.callback_query(F.data == "start_ai_chat")
async def handle_start_ai_callback(callback: CallbackQuery):
    """Inline tugma orqali AI chatni boshlash"""
    user_id = callback.from_user.id
    ai_active_users.add(user_id)
    await callback.answer("🤖 AI Chat faollashtirildi!")
    await callback.message.answer(
        "🤖 <b>Gemini AI Chat faollashtirildi!</b>\n\n"
        "Menga istalgan savol, mavzu, rasm yoki audio xabar yuborishingiz mumkin. "
        "Men sizga yordam berishga tayyorman!\n\n"
        "• 🔄 Suhbatni tozalash: <code>/clear</code>\n"
        "• ❌ Chiqish uchun: <code>/exit</code> yoki «❌ AI rejimdan chiqish» tugmasi",
        parse_mode="HTML",
        reply_markup=get_ai_keyboard()
    )


@router.message(F.text == "❌ AI rejimdan chiqish")
@router.message(Command("exit"))
@router.message(Command("stop"))
async def handle_exit_ai(message: Message):
    """AI chat rejimidan chiqish"""
    user_id = message.from_user.id
    if user_id in ai_active_users:
        ai_active_users.remove(user_id)
    await message.answer(
        "✅ <b>AI Chat rejimidan chiqildi.</b> Bosh menyuga qaytdingiz.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "🔄 Yangi suhbat (/clear)")
@router.message(Command("clear"))
async def handle_clear_chat(message: Message):
    """Foydalanuvchi suhbat tarixini tozalash"""
    user_id = message.from_user.id
    ai_service.reset_chat(user_id)
    await message.answer(
        "🧹 <b>Suhbat tarixi tozalandi!</b>\n\nYangi mavzuda savolingizni berishingiz mumkin.",
        parse_mode="HTML"
    )


@router.message(F.voice)
@router.message(F.audio)
async def handle_audio_message(message: Message):
    """
    Ovozli xabar yoki audio fayl qabul qilinganda format tanlash imkonini berish:
    - 📝 Matn
    - 🎙 Ovozli (Audio)
    - 📄 Word (.docx)
    - 📑 PDF (.pdf)
    - ✨ Barchasi birga
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
        "• 📝 <b>Matn:</b> Chatda yozma javob olish\n"
        "• 🎙 <b>Ovozli:</b> Audio / ovozli xabar shaklida eshitish\n"
        "• 📄 <b>Word:</b> Chiroyli tartiblangan <code>.docx</code> fayli\n"
        "• 📑 <b>PDF:</b> Chop etishga tayyor <code>.pdf</code> hujjati\n"
        "• ✨ <b>Barchasi birga:</b> Barcha formatlarda birdaniga olish\n\n"
        "👇 <i>Quyidagi tugmalardan birini tanlang:</i>"
    )

    await message.reply(
        choice_text,
        parse_mode="HTML",
        reply_markup=get_audio_choice_keyboard(audio_id)
    )


@router.callback_query(F.data.startswith("aud_mode:"))
async def handle_audio_mode_callback(callback: CallbackQuery):
    """
    Foydalanuvchi audio javob formatini tanlaganda ishga tushuvchi callback
    """
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov!")
        return

    action = parts[1]  # "text", "voice", "docx", "pdf", "all", "cancel"
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
        try:
            await callback.message.edit_text("⚠️ <i>Ushbu audio so'rov muddati o'tgan. Iltimos, audioni qaytadan yuboring.</i>", parse_mode="HTML")
        except Exception:
            pass
        return

    await callback.answer("Tanlovingiz qabul qilindi!")

    status_msg = await callback.message.edit_text(
        "⏳ <i>Audio Gemini AI tomonidan tahlil qilinmoqda, iltimos kuting...</i>",
        parse_mode="HTML"
    )

    try:
        # Telegramdan audio faylni yuklab olish
        file_info = await callback.bot.get_file(item["file_id"])
        audio_bytes_io = io.BytesIO()
        await callback.bot.download_file(file_info.file_path, destination=audio_bytes_io)
        audio_bytes = audio_bytes_io.getvalue()

        # Chat action ko'rsatish
        if action == "voice":
            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)
        elif action in ("docx", "pdf"):
            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        else:
            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        # Gemini AI dan audio tahlili va javob olish
        response = await ai_service.ask_gemini_audio(
            user_id=item["user_id"],
            audio_bytes=audio_bytes,
            mime_type=item["mime_type"],
            prompt=item["caption"]
        )

        user_name = item.get("user_name", "Foydalanuvchi")

        # 1. Matn ko'rinishida javob berish
        if action in ("text", "all"):
            try:
                await status_msg.delete()
            except Exception:
                pass
            await send_ai_response(callback.message, response)

        # 2. Word (.docx) ko'rinishida javob berish
        if action in ("docx", "all"):
            if action == "docx":
                try:
                    await status_msg.edit_text("📄 <i>Word hujjati (.docx) shakllantirilmoqda...</i>", parse_mode="HTML")
                except Exception:
                    pass

            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
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

            if action == "docx":
                try:
                    await status_msg.delete()
                except Exception:
                    pass

        # 3. PDF (.pdf) ko'rinishida javob berish
        if action in ("pdf", "all"):
            if action == "pdf":
                try:
                    await status_msg.edit_text("📑 <i>PDF hujjati (.pdf) shakllantirilmoqda...</i>", parse_mode="HTML")
                except Exception:
                    pass

            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
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

            if action == "pdf":
                try:
                    await status_msg.delete()
                except Exception:
                    pass

        # 4. Ovozli (Audio) ko'rinishida javob berish
        if action in ("voice", "all"):
            if action == "voice":
                try:
                    await status_msg.edit_text("🎙 <i>Ovozli javob tayyorlanmoqda...</i>", parse_mode="HTML")
                except Exception:
                    pass

            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)
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
                        logger.warning("Foydalanuvchida VOICE_MESSAGES_FORBIDDEN yoqilgan, audio fayl sifatida yuborilmoqda.")
                        try:
                            await callback.message.answer_audio(
                                audio=FSInputFile(mp3_path),
                                caption="🎙 <b>AI Ovozli Javobi</b>\n<i>(Telegram sozlamalaringizda ovozli xabarlar cheklanganligi sababli audio fayl shaklida yuborildi)</i>",
                                parse_mode="HTML"
                            )
                        except Exception:
                            await callback.message.answer(
                                "ℹ️ <i>Telegram maxfiylik sozlamalaringiz sababli ovozli xabar yuborib bo'lmadi. Javob matn sifatida taqdim etilmoqda:</i>"
                            )
                            await send_ai_response(callback.message, response)
                    else:
                        raise
            finally:
                for p in (ogg_path, mp3_path):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

            if action == "voice":
                try:
                    await status_msg.delete()
                except Exception:
                    pass

    except Exception as e:
        logger.exception(f"Audio xabarni tahlil qilishda xatolik: {e}")
        try:
            await status_msg.edit_text("❌ Audio tahlil qilishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.", parse_mode="HTML")
        except Exception:
            pass
    finally:
        pending_audio.pop(audio_id, None)


@router.message(F.photo)
async def handle_photo_message(message: Message):
    """
    Rasm qabul qilinganda uni AI ga yuborish
    """
    user_id = message.from_user.id
    caption = message.caption or ""

    if user_id in ai_active_users or caption.startswith("/ai"):
        prompt = caption.replace("/ai", "").strip()
        status_msg = await message.reply("🤔 <i>Rasm tahlil qilinmoqda, iltimos kuting...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        try:
            photo = message.photo[-1]
            file_info = await message.bot.get_file(photo.file_id)
            photo_bytes_io = io.BytesIO()
            await message.bot.download_file(file_info.file_path, destination=photo_bytes_io)
            photo_bytes = photo_bytes_io.getvalue()

            response = await ai_service.ask_gemini(
                user_id=user_id,
                prompt=prompt,
                image_bytes=photo_bytes,
                mime_type="image/jpeg"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
            await send_ai_response(message, response)
        except Exception as e:
            logger.exception(f"Rasmni tahlil qilishda xatolik: {e}")
            await status_msg.edit_text("❌ Rasmni tahlil qilishda xatolik yuz berdi.", parse_mode="HTML")
    else:
        await message.reply(
            "ℹ️ <b>Rasm qabul qilindi.</b>\n\n"
            "Ushbu rasm bo'yicha sun'iy intellektdan tahlil yoki javob olish uchun <b>«🤖 AI Chat»</b> rejimiga o'ting yoki rasm izohiga <code>/ai savolingiz</code> deb yozing.",
            parse_mode="HTML",
            reply_markup=get_ai_inline_button()
        )


@router.message(F.video)
async def handle_video_message(message: Message):
    """
    Foydalanuvchi to'g'ridan-to'g'ri to'rtburchak video yuborganda,
    uni Telegram dumaloq (video note) formatiga aylantirish
    """
    status_msg = await message.reply("⏳ <i>Video qabul qilindi. Dumaloq (Video Note) formatiga aylantirilmoqda...</i>", parse_mode="HTML")

    input_path = None
    circle_path = None

    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VIDEO_NOTE)

        unique_id = uuid.uuid4().hex[:8]
        input_path = str(DOWNLOADS_DIR / f"raw_{unique_id}.mp4")

        # Telegramdan videoni yuklab olish
        file_info = await message.bot.get_file(message.video.file_id)
        await message.bot.download_file(file_info.file_path, destination=input_path)

        # Dumaloq formatga o'tkazish
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
        circle_path, duration = await convert_to_video_note(input_path, max_duration=60)

        # Dumaloq video (krujok) qilib jo'natish
        await message.answer_video_note(
            video_note=FSInputFile(circle_path),
            length=384,
            duration=duration
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except TelegramBadRequest as e:
        if "VOICE_MESSAGES_FORBIDDEN" in str(e):
            logger.warning("Foydalanuvchida ovozli/dumaloq xabarlar maxfiyligi yoqilgan, doiraviy video sifatida yuborilmoqda.")
            if circle_path and os.path.exists(circle_path):
                await message.answer_video(
                    video=FSInputFile(circle_path),
                    caption="🔘 <b>Dumaloq video tayyor!</b>",
                    parse_mode="HTML"
                )
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        else:
            logger.exception(f"TelegramBadRequest: {e}")
            await status_msg.edit_text("❌ Telegram xatoligi yuz berdi.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Videoni dumaloq qilishda xatolik: {e}")
        await status_msg.edit_text(
            "❌ Videoni dumaloq formatga aylantirishda xatolik yuz berdi. Iltimos, video formatini tekshirib qayta urinib ko'ring.",
            parse_mode="HTML"
        )
    finally:
        for path in (input_path, circle_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


@router.callback_query(F.data == "make_circle")
async def handle_make_circle_callback(callback: CallbackQuery):
    """
    Yuklangan video ostidagi «🔘 Dumaloq video qilish» tugmasi bosilganda
    """
    msg = callback.message
    if not msg or not msg.video:
        await callback.answer("Video topilmadi!", show_alert=True)
        return

    await callback.answer("Dumaloq video tayyorlanmoqda...")
    status_msg = await msg.reply("⏳ <i>Video dumaloq formatga aylantirilmoqda...</i>", parse_mode="HTML")

    input_path = None
    circle_path = None

    try:
        await callback.bot.send_chat_action(chat_id=msg.chat.id, action=ChatAction.RECORD_VIDEO_NOTE)

        unique_id = uuid.uuid4().hex[:8]
        input_path = str(DOWNLOADS_DIR / f"cb_raw_{unique_id}.mp4")

        # Telegramdan videoni yuklab olish
        file_info = await callback.bot.get_file(msg.video.file_id)
        await callback.bot.download_file(file_info.file_path, destination=input_path)

        # Dumaloq formatga o'tkazish
        await callback.bot.send_chat_action(chat_id=msg.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
        circle_path, duration = await convert_to_video_note(input_path, max_duration=60)

        # Dumaloq video qilib jo'natish
        await msg.answer_video_note(
            video_note=FSInputFile(circle_path),
            length=384,
            duration=duration
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except TelegramBadRequest as e:
        if "VOICE_MESSAGES_FORBIDDEN" in str(e):
            logger.warning("Foydalanuvchida ovozli/dumaloq xabarlar maxfiyligi yoqilgan, doiraviy video sifatida yuborilmoqda.")
            if circle_path and os.path.exists(circle_path):
                await msg.answer_video(
                    video=FSInputFile(circle_path),
                    caption="🔘 <b>Dumaloq video tayyor!</b>",
                    parse_mode="HTML"
                )
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        else:
            logger.exception(f"TelegramBadRequest: {e}")
            await status_msg.edit_text("❌ Telegram xatoligi yuz berdi.", parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Callback dumaloq video xatoligi: {e}")
        await status_msg.edit_text(
            "❌ Videoni dumaloq formatga o'tkazib bo'lmadi.",
            parse_mode="HTML"
        )
    finally:
        for path in (input_path, circle_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


async def process_instagram_download(message: Message, url: str):
    """Instagram havolasini yuklash jarayoni"""
    status_msg = await message.reply("⏳ <i>Video yuklab olinmoqda, iltimos kuting...</i>", parse_mode="HTML")
    file_path = None
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)

        # Videoni yuklab olish
        video_data = await download_instagram_video(url)
        file_path = video_data['file_path']

        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)

        bot_info = await message.bot.get_me()
        caption = (
            f"🎬 <b>{video_data['title']}</b>\n\n"
            f"🤖 <i>@{bot_info.username} orqali yuklandi</i>"
        )

        # Videoni foydalanuvchiga jo'natish
        video_file = FSInputFile(file_path)
        await message.answer_video(
            video=video_file,
            caption=caption,
            parse_mode="HTML",
            duration=video_data.get('duration'),
            width=video_data.get('width'),
            height=video_data.get('height'),
            supports_streaming=True,
            reply_markup=get_circle_button()
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except FileTooLargeError as e:
        await status_msg.edit_text(
            f"❌ <b>Hajm bo'yicho cheklov:</b>\n{str(e)}",
            parse_mode="HTML"
        )
    except PrivateMediaError as e:
        await status_msg.edit_text(
            f"🔒 <b>Yopiq hisob:</b>\n{str(e)}",
            parse_mode="HTML"
        )
    except DownloadException as e:
        logger.error(f"Yuklab olishda xatolik: {e}")
        await status_msg.edit_text(
            "❌ <b>Xatolik yuz berdi:</b>\nVideoni yuklab bo'lmadi. Havola to'g'riligini va post o'chirilmaganligini tekshiring.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
        await status_msg.edit_text(
            "❌ Kutilmagan texnik xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.",
            parse_mode="HTML"
        )
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Vaqtinchalik faylni o'chirishda xatolik: {e}")


@router.message(F.text)
async def handle_text_message(message: Message):
    """
    Umumiy matnli xabarlarni qayta ishlash:
    - Agar Instagram havolasi bo'lsa -> yuklab beradi
    - Agar foydalanuvchi AI rejimida bo'lsa -> Gemini AI ga yuboradi
    - Aks holda -> yordam va menyu taklif qiladi
    """
    user_id = message.from_user.id
    text = message.text or ""

    # 1. Instagram havolasi bormi tekshiramiz
    match = INSTAGRAM_URL_REGEX.search(text)
    if match:
        await process_instagram_download(message, match.group(0))
        return

    # 2. Agar foydalanuvchi AI rejimida bo'lsa
    if user_id in ai_active_users:
        status_msg = await message.reply("🤔 <i>O'ylamoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        response = await ai_service.ask_gemini(user_id=user_id, prompt=text)
        try:
            await status_msg.delete()
        except Exception:
            pass
        await send_ai_response(message, response)
        return

    # 3. Agar AI rejimida bo'lmasa va Instagram havolasi bo'lmasa
    if "instagram.com" in text.lower():
        await message.reply(
            "❌ Havola noto'g'ri ko'rinmoqda. Iltimos, video yoki Reels havolasini to'g'ri formatda yuboring.\n"
            "Misol: <code>https://www.instagram.com/reel/Cxxxxxx/</code>",
            parse_mode="HTML"
        )
    else:
        await message.reply(
            "ℹ️ <b>Nima qilishni xohlaysiz?</b>\n\n"
            "• 🤖 <b>Sun'iy intellekt bilan suhbatlashish</b> uchun <b>«🤖 AI Chat»</b> tugmasini bosing yoki <code>/ai savolingiz</code> deb yozing.\n"
            "• 🎙 <b>Audio xabar yuboring</b> va matn yoki ovozli shaklda tahlil oling.\n"
            "• 📥 <b>Instagram video yuklash</b> uchun havolani yuboring.\n"
            "• 🔘 <b>Dumaloq video</b> tayyorlash uchun video jo'nating.\n\n"
            "Batafsil ma'lumot uchun: /help",
            parse_mode="HTML",
            reply_markup=get_ai_inline_button()
        )
