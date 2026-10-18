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
from archive_service import archive_service
from i18n import (
    get_user_lang,
    set_user_lang,
    has_user_selected_lang,
    t,
    btn,
    get_all_button_variants,
    get_main_keyboard_i18n,
    get_section_keyboard_i18n,
    get_lang_inline_keyboard,
    get_translator_directions_keyboard,
    LANGUAGES,
)

logger = logging.getLogger(__name__)
router = Router()

# Instagram havolalarini aniqlash uchun regex
INSTAGRAM_URL_REGEX = re.compile(
    r"https?:\/\/(?:www\.)?instagram\.com\/(?:[a-zA-Z0-9_\.]+\/)?(?:p|reel|reels|tv|share)\/[a-zA-Z0-9_\-]+[^\s]*",
    re.IGNORECASE
)

# Foydalanuvchilarning hozirgi faol bo'limi (user_id -> mode)
# Rejimlar: "NORMAL", "DOC_ANALYSIS", "DEEP_RESEARCH", "MEDIA", "SLIDES", "OFFICE_DOCS", "AI_CHAT", "TRANSLATOR"
user_active_mode: dict[int, str] = {}

# Foydalanuvchilarning hozirgi tarjima yo'nalishi (user_id -> "auto:uz", "uz:ru" va h.k.)
user_trans_direction: dict[int, str] = {}

# Format tanlash kutilayotgan audio xabarlar: audio_id -> {user_id, user_name, file_id, mime_type, caption}
pending_audio: dict[str, dict] = {}

# Bo'limlarga o'tkazish va tahrirlash uchun natijalar kesh xotirasi (res_id -> {title, content, user_name})
result_store: dict[str, dict] = {}

# Hujjat yuklanganda bo'lim tanlashni kutish: doc_id -> {user_id, user_name, filename, text, sections, caption, created_at}
pending_docs: dict[str, dict] = {}


def store_result(
    title: str,
    content: str,
    user_name: str = "",
    user_id: int = 0,
    category: str = "chat"
) -> str:
    """Natijani xotiraga va doimiy SQLite arxivga saqlash hamda res_id qaytarish"""
    res_id = uuid.uuid4().hex[:8]
    result_store[res_id] = {
        "title": title or "Tahlil Natijasi",
        "content": content,
        "user_name": user_name,
        "user_id": user_id,
        "category": category,
        "created_at": datetime.now()
    }
    if len(result_store) > 400:
        oldest_key = next(iter(result_store))
        result_store.pop(oldest_key, None)

    # Doimiy SQLite arxiv bazasiga saqlash
    if user_id:
        try:
            archive_service.save_item(
                res_id=res_id,
                user_id=user_id,
                user_name=user_name,
                title=title,
                content=content,
                category=category
            )
        except Exception as e:
            logger.warning(f"Arxivga saqlashda ogohlantirish: {e}")

    return res_id


def get_main_keyboard(is_admin: bool = False, lang: str = "uz") -> ReplyKeyboardMarkup:
    """Asosiy bo'limlar menyusi (Tanlangan til va Qo'riqchi tugmasi bilan)"""
    return get_main_keyboard_i18n(lang=lang, is_admin=is_admin)


def get_section_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    """Bo'lim ichidagi qulay boshqaruv menyusi (Tanlangan tilda)"""
    return get_section_keyboard_i18n(lang=lang)


def get_content_actions_keyboard(res_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Har bir natija tagida bo'limga o'tkazish va tahrirlash tugmalari"""
    tr_label = "📂 Bo'limga o'tkazish" if lang == "uz" else ("📂 В другие форматы" if lang == "ru" else "📂 Transfer")
    ed_label = "✏️ Tahrirlash" if lang == "uz" else ("✏️ Редактировать" if lang == "ru" else "✏️ Edit")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr_label, callback_data=f"act:tr:{res_id}"),
                InlineKeyboardButton(text=ed_label, callback_data=f"act:ed:{res_id}")
            ]
        ]
    )


def get_document_sections_keyboard(doc_id: str, sections: list, lang: str = "uz") -> InlineKeyboardMarkup:
    """Hujjat yuklanganda qaysi bo'limni tahlil qilishni tanlash klaviaturasi (tanlangan tilda)"""
    kb = []
    # Aniqlangan asosiy bo'limlar (har biri alohida qatorda)
    for idx, sec in enumerate(sections[:5]):
        clean_name = sec[:38]
        kb.append([
            InlineKeyboardButton(text=f"📌 {clean_name}", callback_data=f"doc_sec:{doc_id}:{idx}")
        ])

    all_label = (
        "🌐 Barcha bo'limlar (To'liq tahlil)" if lang == "uz" else
        ("🌐 Все разделы (Полный анализ)" if lang == "ru" else "🌐 All Sections (Full Analysis)")
    )
    risks_label = (
        "⚖️ Huquqlar va amaliy xavflar" if lang == "uz" else
        ("⚖️ Права и скрытые риски" if lang == "ru" else "⚖️ Rights & Hidden Risks")
    )
    deadlines_label = (
        "⏱ Muddatlar va jarimalar" if lang == "uz" else
        ("⏱ Сроки и санкции" if lang == "ru" else "⏱ Deadlines & Penalties")
    )
    custom_label = (
        "✍️ O'z savolim / Boshqa bo'lim" if lang == "uz" else
        ("✍️ Свой вопрос / Раздел" if lang == "ru" else "✍️ Custom Query / Other Section")
    )
    cancel_label = (
        "❌ Bekor qilish" if lang == "uz" else ("❌ Отмена" if lang == "ru" else "❌ Cancel")
    )

    kb.append([InlineKeyboardButton(text=all_label, callback_data=f"doc_sec:{doc_id}:all")])
    kb.append([
        InlineKeyboardButton(text=risks_label, callback_data=f"doc_sec:{doc_id}:risks"),
        InlineKeyboardButton(text=deadlines_label, callback_data=f"doc_sec:{doc_id}:deadlines")
    ])
    kb.append([InlineKeyboardButton(text=custom_label, callback_data=f"doc_sec:{doc_id}:custom")])
    kb.append([InlineKeyboardButton(text=cancel_label, callback_data=f"doc_sec:{doc_id}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_document_result_keyboard(res_id: str, doc_id: str = "", current_sec_idx: Optional[int] = None, has_next: bool = False, lang: str = "uz") -> InlineKeyboardMarkup:
    """Hujjat tahlili natijasi tagidagi tugmalar: formatlar, keyingi bo'lim va qayta tanlash"""
    tr_label = "📂 Bo'limga o'tkazish" if lang == "uz" else ("📂 В другие форматы" if lang == "ru" else "📂 Transfer")
    ed_label = "✏️ Tahrirlash" if lang == "uz" else ("✏️ Редактировать" if lang == "ru" else "✏️ Edit")
    kb = [
        [
            InlineKeyboardButton(text=tr_label, callback_data=f"act:tr:{res_id}"),
            InlineKeyboardButton(text=ed_label, callback_data=f"act:ed:{res_id}")
        ]
    ]
    if doc_id:
        nav_row = []
        if has_next and current_sec_idx is not None:
            next_label = "⏩ Keyingi bo'lim" if lang == "uz" else ("⏩ Следующий раздел" if lang == "ru" else "⏩ Next Section")
            nav_row.append(InlineKeyboardButton(text=next_label, callback_data=f"doc_sec:{doc_id}:next:{current_sec_idx + 1}"))
        other_label = "📑 Boshqa bo'lim" if lang == "uz" else ("📑 Другой раздел" if lang == "ru" else "📑 Other Section")
        nav_row.append(InlineKeyboardButton(text=other_label, callback_data=f"doc_sec:{doc_id}:menu"))
        kb.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_transfer_keyboard(res_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Natijani tegishli bo'limlarga o'tkazish tanlovlari"""
    voice_label = "🎙 Ovozli qilish (TTS)" if lang == "uz" else ("🎙 Озвучить (TTS)" if lang == "ru" else "🎙 Voice (TTS)")
    trans_label = "🌐 Boshqa tilga tarjima" if lang == "uz" else ("🌐 Перевести на другой язык" if lang == "ru" else "🌐 Translate")
    back_label = "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back")

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
                InlineKeyboardButton(text=voice_label, callback_data=f"tr:voice:{res_id}")
            ],
            [
                InlineKeyboardButton(text=trans_label, callback_data=f"tr:trans:{res_id}"),
                InlineKeyboardButton(text=back_label, callback_data=f"act:back:{res_id}")
            ]
        ]
    )


def get_edit_keyboard(res_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Natijani tahrirlash va qayta ishlash tanlovlari"""
    exp_label = "➕ Kengaytirish" if lang == "uz" else ("➕ Расширить" if lang == "ru" else "➕ Expand")
    sum_label = "✂️ Qisqartirish" if lang == "uz" else ("✂️ Сократить" if lang == "ru" else "✂️ Summarize")
    rew_label = "🔄 Qayta yozish" if lang == "uz" else ("🔄 Переписать" if lang == "ru" else "🔄 Rewrite")
    tr_label = "🌐 Tarjima qilish" if lang == "uz" else ("🌐 Перевести" if lang == "ru" else "🌐 Translate")
    back_label = "🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=exp_label, callback_data=f"ed:expand:{res_id}"),
                InlineKeyboardButton(text=sum_label, callback_data=f"ed:summary:{res_id}")
            ],
            [
                InlineKeyboardButton(text=rew_label, callback_data=f"ed:rewrite:{res_id}"),
                InlineKeyboardButton(text=tr_label, callback_data=f"tr:trans:{res_id}")
            ],
            [
                InlineKeyboardButton(text=back_label, callback_data=f"act:back:{res_id}")
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


def get_archive_list_keyboard(items: list, page: int, total_count: int, per_page: int = 5) -> InlineKeyboardMarkup:
    """Arxiv ro'yxati va sahifalash klaviaturasi"""
    kb = []
    category_icons = {
        "doc": "📑",
        "research": "🔬",
        "audio": "🎙",
        "image": "📸",
        "slides": "📽",
        "office": "📊",
        "chat": "🤖",
        "trans": "🌐"
    }

    for item in items:
        cat_icon = category_icons.get(item.get("category", "chat"), "📌")
        title_snippet = item["title"][:28]
        created_str = str(item.get("created_at", ""))
        date_str = created_str[5:16] if len(created_str) >= 16 else created_str
        btn_text = f"{cat_icon} [{date_str}] {title_snippet}"
        kb.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"arx:view:{item['id']}")
        ])

    # Sahifalash (Pagination) tugmalari
    nav_row = []
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"arx:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="arx:noop"))
    if (page + 1) * per_page < total_count:
        nav_row.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"arx:p:{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    # Boshqaruv tugmalari
    if total_count > 0:
        kb.append([
            InlineKeyboardButton(text="🗑 Barcha arxivni tozalash", callback_data="arx:confirm_clear")
        ])
    kb.append([
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"arx:p:{page}"),
        InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="arx:home")
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_archive_item_keyboard(item_id: str) -> InlineKeyboardMarkup:
    """Tanlangan bitta arxiv yozuvini ko'rish va boshqarish tugmalari"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📖 To'liq matnni o'qish", callback_data=f"arx:read:{item_id}")
            ],
            [
                InlineKeyboardButton(text="📄 Word (.docx)", callback_data=f"tr:docx:{item_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf)", callback_data=f"tr:pdf:{item_id}")
            ],
            [
                InlineKeyboardButton(text="📊 Excel (.xlsx)", callback_data=f"tr:xlsx:{item_id}"),
                InlineKeyboardButton(text="📽 Prezentatsiya", callback_data=f"tr:pptx:{item_id}")
            ],
            [
                InlineKeyboardButton(text="🎙 Ovozli qilish", callback_data=f"tr:voice:{item_id}"),
                InlineKeyboardButton(text="🔬 Deep Research", callback_data=f"tr:deep:{item_id}")
            ],
            [
                InlineKeyboardButton(text="📂 Bo'limga o'tkazish", callback_data=f"act:tr:{item_id}"),
                InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"act:ed:{item_id}")
            ],
            [
                InlineKeyboardButton(text="🗑 Ushbu so'rovni o'chirish", callback_data=f"arx:confirm_del:{item_id}")
            ],
            [
                InlineKeyboardButton(text="🔙 Arxiv ro'yxatiga qaytish", callback_data="arx:p:0")
            ]
        ]
    )


def get_archive_delete_confirm_keyboard(item_id: str) -> InlineKeyboardMarkup:
    """Yozuvni o'chirishni tasdiqlash klaviaturasi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ha, o'chirilsin", callback_data=f"arx:do_del:{item_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"arx:view:{item_id}")
            ]
        ]
    )


def get_archive_clear_confirm_keyboard() -> InlineKeyboardMarkup:
    """Butun arxivni tozalashni tasdiqlash klaviaturasi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Ha, barchasini tozalash", callback_data="arx:do_clear"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="arx:p:0")
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


async def send_ai_response(
    message: Message,
    response_text: str,
    res_id: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    lang: Optional[str] = None
):
    """Javobni xavfsiz formatda va harakat tugmalari bilan jo'natish"""
    if not response_text:
        await message.reply("Javob bo'sh qaytdi.")
        return

    if lang is None:
        u_id = message.from_user.id if message.from_user else 0
        lang = get_user_lang(u_id) if u_id else "uz"

    markup = reply_markup if reply_markup else (get_content_actions_keyboard(res_id, lang=lang) if res_id else None)
    max_len = 4000
    chunks = [response_text[i:i + max_len] for i in range(0, len(response_text), max_len)]

    for idx, chunk in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        current_markup = markup if is_last else None
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

# =====================================================================
# ASOSIY BUYRUQLAR VA BO'LIMLARGA O'TISH (MULTILINGUAL)
# =====================================================================

@router.message(CommandStart())
async def handle_start(message: Message):
    """Bosh menyu yoki til tanlash"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "NORMAL"
    user_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"
    is_admin = access_guard.is_admin(user_id)

    # Agar foydalanuvchi hali til tanlamagan bo'lsa -> avval til tanlash menyusi
    if not has_user_selected_lang(user_id):
        await message.answer(
            t("choose_language", "uz"),
            parse_mode="HTML",
            reply_markup=get_lang_inline_keyboard()
        )
        return

    lang = get_user_lang(user_id)
    admin_badge = "\n👑 <b>Siz bot administratorsiz (Qo'riqchi faol).</b>\n" if is_admin and lang == "uz" else (
        "\n👑 <b>Вы администратор бота.</b>\n" if is_admin and lang == "ru" else (
            "\n👑 <b>You are a bot administrator.</b>\n" if is_admin else ""
        )
    )
    welcome_text = t("welcome", lang, name=user_name, admin_badge=admin_badge)
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_keyboard(is_admin=is_admin, lang=lang))


@router.callback_query(F.data.startswith("set_lang:"))
async def handle_set_language_callback(callback: CallbackQuery):
    """Foydalanuvchi tilini saqlash va bosh menyuni chiqarish"""
    user_id = callback.from_user.id
    new_lang = callback.data.split(":", 1)[1]
    set_user_lang(user_id, new_lang)
    await callback.answer()

    is_admin = access_guard.is_admin(user_id)
    admin_badge = "\n👑 <b>Siz bot administratorsiz.</b>\n" if is_admin and new_lang == "uz" else (
        "\n👑 <b>Вы администратор бота.</b>\n" if is_admin and new_lang == "ru" else (
            "\n👑 <b>You are a bot administrator.</b>\n" if is_admin else ""
        )
    )
    user_name = callback.from_user.first_name or "Foydalanuvchi"

    await callback.message.answer(
        t("lang_changed", new_lang),
        parse_mode="HTML"
    )
    welcome_text = t("welcome", new_lang, name=user_name, admin_badge=admin_badge)
    await callback.message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_main_keyboard(is_admin=is_admin, lang=new_lang)
    )


@router.message(Command("lang"))
@router.message(Command("language"))
@router.message(F.text.in_(get_all_button_variants("btn_lang")))
async def handle_change_language_cmd(message: Message):
    """Tilni o'zgartirish menyusi"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    await message.answer(
        t("choose_language", lang),
        parse_mode="HTML",
        reply_markup=get_lang_inline_keyboard()
    )


@router.message(F.text.in_(get_all_button_variants("btn_main_menu")))
async def handle_back_to_main_menu(message: Message):
    """Bosh menyuga qaytish"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "NORMAL"
    lang = get_user_lang(user_id)
    is_admin = access_guard.is_admin(user_id)
    user_name = message.from_user.first_name if message.from_user else "Foydalanuvchi"
    admin_badge = "\n👑 <b>Siz bot administratorsiz.</b>\n" if is_admin and lang == "uz" else (
        "\n👑 <b>Вы администратор бота.</b>\n" if is_admin and lang == "ru" else (
            "\n👑 <b>You are a bot administrator.</b>\n" if is_admin else ""
        )
    )
    welcome_text = t("welcome", lang, name=user_name, admin_badge=admin_badge)
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_keyboard(is_admin=is_admin, lang=lang))


@router.message(F.text.in_(get_all_button_variants("btn_help")))
@router.message(Command("help"))
async def handle_help(message: Message):
    """Qo'llanma (tanlangan tilda)"""
    lang = get_user_lang(message.from_user.id)
    await message.answer(t("help", lang), parse_mode="HTML")


@router.message(F.text.in_(get_all_button_variants("btn_doc_analysis")))
async def nav_doc_analysis(message: Message):
    """Hujjatlar tahlili bo'limi"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "DOC_ANALYSIS"
    lang = get_user_lang(user_id)
    await message.answer(
        t("doc_analysis_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_translator")))
@router.message(Command("translate"))
@router.message(Command("translator"))
async def nav_translator(message: Message):
    """🌐 Tarjimon bo'limi"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "TRANSLATOR"
    lang = get_user_lang(user_id)
    active_dir = user_trans_direction.get(user_id, f"auto:{lang}")

    dir_labels = {
        "auto:uz": "🔄 Avto ➡️ 🇺🇿 O'zbekcha",
        "auto:ru": "🔄 Авто ➡️ 🇷🇺 Русский",
        "auto:en": "🔄 Auto ➡️ 🇬🇧 English",
        "uz:ru": "🇺🇿 O'zbek ➡️ 🇷🇺 Rus",
        "ru:uz": "🇷🇺 Rus ➡️ 🇺🇿 O'zbek",
        "uz:en": "🇺🇿 O'zbek ➡️ 🇬🇧 Ingliz",
        "en:uz": "🇬🇧 Ingliz ➡️ 🇺🇿 O'zbek",
        "ru:en": "🇷🇺 Rus ➡️ 🇬🇧 Ingliz",
        "en:ru": "🇬🇧 Ingliz ➡️ 🇷🇺 Rus"
    }
    dir_name = dir_labels.get(active_dir, active_dir)

    await message.answer(
        t("translator_menu", lang, direction=dir_name),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )
    direction_prompt = (
        "👇 <b>Tarjima yo'nalishini tanlang:</b>" if lang == "uz" else
        ("👇 <b>Выберите направление перевода:</b>" if lang == "ru" else "👇 <b>Select translation direction:</b>")
    )
    await message.answer(
        direction_prompt,
        parse_mode="HTML",
        reply_markup=get_translator_directions_keyboard(lang=lang, active_dir=active_dir)
    )


@router.callback_query(F.data.startswith("tr_dir:"))
async def handle_trans_direction_callback(callback: CallbackQuery):
    """Tarjima yo'nalishini o'zgartirish callbacki"""
    user_id = callback.from_user.id
    direction_code = callback.data.split(":", 1)[1]
    user_trans_direction[user_id] = direction_code
    lang = get_user_lang(user_id)

    dir_labels = {
        "auto:uz": "🔄 Avto ➡️ 🇺🇿 O'zbekcha",
        "auto:ru": "🔄 Авто ➡️ 🇷🇺 Русский",
        "auto:en": "🔄 Auto ➡️ 🇬🇧 English",
        "uz:ru": "🇺🇿 O'zbek ➡️ 🇷🇺 Rus",
        "ru:uz": "🇷🇺 Rus ➡️ 🇺🇿 O'zbek",
        "uz:en": "🇺🇿 O'zbek ➡️ 🇬🇧 Ingliz",
        "en:uz": "🇬🇧 Ingliz ➡️ 🇺🇿 O'zbek",
        "ru:en": "🇷🇺 Rus ➡️ 🇬🇧 Ingliz",
        "en:ru": "🇬🇧 Ingliz ➡️ 🇷🇺 Rus"
    }
    dir_name = dir_labels.get(direction_code, direction_code)
    await callback.answer(f"Tanlandi: {dir_name}")
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_translator_directions_keyboard(lang=lang, active_dir=direction_code)
        )
    except Exception:
        pass

    confirm_txt = (
        f"✅ <b>Tarjima yo'nalishi belgilandi:</b> {dir_name}\n\n"
        f"Endi tarjima qilmoqchi bo'lgan matn, xabar yoki hujjatni yuboring:"
        if lang == "uz" else
        (f"✅ <b>Направление перевода установлено:</b> {dir_name}\n\nОтправьте текст или документ для перевода:"
         if lang == "ru" else
         f"✅ <b>Translation direction set:</b> {dir_name}\n\nSend the text or document to translate:")
    )
    await callback.message.answer(confirm_txt, parse_mode="HTML")


@router.message(F.text.in_(get_all_button_variants("btn_deep_research")))
async def nav_deep_research(message: Message):
    user_id = message.from_user.id
    user_active_mode[user_id] = "DEEP_RESEARCH"
    lang = get_user_lang(user_id)
    await message.answer(
        t("deep_research_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_media")))
async def nav_media(message: Message):
    user_id = message.from_user.id
    user_active_mode[user_id] = "MEDIA"
    lang = get_user_lang(user_id)
    await message.answer(
        t("media_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_slides")))
async def nav_slides(message: Message):
    user_id = message.from_user.id
    user_active_mode[user_id] = "SLIDES"
    lang = get_user_lang(user_id)
    await message.answer(
        t("slides_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_docs")))
async def nav_office_docs(message: Message):
    user_id = message.from_user.id
    user_active_mode[user_id] = "OFFICE_DOCS"
    lang = get_user_lang(user_id)
    await message.answer(
        t("office_docs_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_ai_chat")))
@router.message(Command("ai"))
async def nav_ai_chat(message: Message):
    user_id = message.from_user.id
    user_active_mode[user_id] = "AI_CHAT"
    lang = get_user_lang(user_id)
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
            res_id = store_result(
                title=prompt[:30],
                content=response,
                user_name=message.from_user.full_name or "Foydalanuvchi",
                user_id=message.from_user.id,
                category="chat"
            )
            await send_ai_response(message, response, res_id=res_id)
            return

    await message.answer(
        t("ai_chat_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )


@router.message(F.text.in_(get_all_button_variants("btn_archive")))
@router.message(Command("arxiv"))
@router.message(Command("archive"))
async def nav_archive(message: Message):
    """🗄 Arxiv bo'limi - barcha saqlangan so'rovlarni ko'rish va boshqarish"""
    user_id = message.from_user.id
    total_count = archive_service.get_user_count(user_id)
    lang = get_user_lang(user_id)

    if total_count == 0:
        empty_msg = (
            "🗄 <b>Sizning arxivingiz hozircha bo'sh.</b>\n\n"
            "Botdan foydalanib so'rovlar yuborganingizda, tarjimalar yoki hujjatlar tahlil qilganingizda, "
            "barcha natijalar avtomatik ravishda shu yerda saqlanadi.\n\n"
            "Keyinchalik ularni istalgan vaqtda qayta ko'rishingiz, yuklab olishingiz yoki o'chirib tashlashingiz mumkin."
            if lang == "uz" else
            ("🗄 <b>Ваш архив пока пуст.</b>\n\nВсе ваши запросы, переводы и отчеты будут автоматически сохраняться здесь."
             if lang == "ru" else
             "🗄 <b>Your archive is currently empty.</b>\n\nAll your queries, translations, and reports will be saved here automatically.")
        )
        await message.answer(empty_msg, parse_mode="HTML")
        return

    items = archive_service.get_user_items(user_id=user_id, limit=5, offset=0)
    text = (
        f"🗄 <b>Sizning so'rovlar arxivingiz</b>\n"
        f"📊 <b>Jami saqlangan so'rovlar:</b> {total_count} ta\n\n"
        f"👇 <i>Kerakli so'rovni tanlang: uni qayta o'qishingiz, Word/PDF/Excel/Audio formatda yuklab olishingiz "
        f"yoki keraksiz bo'lsa o'chirib tashlashingiz mumkin:</i>"
        if lang == "uz" else
        (f"🗄 <b>Ваш архив запросов</b>\n📊 <b>Всего записей:</b> {total_count}\n\n👇 <i>Выберите запись для чтения, скачивания или удаления:</i>"
         if lang == "ru" else
         f"🗄 <b>Your Query Archive</b>\n📊 <b>Total items:</b> {total_count}\n\n👇 <i>Select an item to read, export, or delete:</i>")
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_archive_list_keyboard(items=items, page=0, total_count=total_count)
    )


@router.message(F.text.in_(get_all_button_variants("btn_new_request")))
@router.message(Command("clear"))
async def handle_clear_command(message: Message):
    user_id = message.from_user.id
    ai_service.reset_chat(user_id)
    lang = get_user_lang(user_id)
    clear_msg = (
        "🧹 <b>Suhbat va so'rovlar tozalandi!</b>\n\nYangi so'rovingizni yuborishingiz mumkin."
        if lang == "uz" else
        ("🧹 <b>История диалога очищена!</b>\n\nМожете отправить новый запрос."
         if lang == "ru" else
         "🧹 <b>Conversation cleared!</b>\n\nYou can now send a new query.")
    )
    await message.answer(clear_msg, parse_mode="HTML")


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
    Foydalanuvchi hujjat (PDF, Word, Excel, TXT va h.k.) yuborganida:
    - Agar TRANSLATOR rejimida bo'lsa: fayl matnini belgilangan yo'nalish bo'yicha to'liq tarjima qilish!
    - Agar boshqa rejimda bo'lsa: Uni yuklab olib, bo'limlarini aniqlash va qaysi bo'limni tahlil qilishni so'rash!
    """
    user_id = message.from_user.id
    current_mode = user_active_mode.get(user_id, "NORMAL")
    lang = get_user_lang(user_id)
    doc = message.document
    filename = doc.file_name or "hujjat.pdf"
    caption = message.caption or ""

    status_msg = await message.reply(
        f"⏳ <i>«{filename}» qabul qilinmoqda va tayyorlanmoqda...</i>" if lang == "uz" else
        (f"⏳ <i>«{filename}» принимается и обрабатывается...</i>" if lang == "ru" else
         f"⏳ <i>Receiving and processing «{filename}»...</i>"),
        parse_mode="HTML"
    )
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
            err_msg = (
                "❌ Ushbu hujjatdan matn ajratib olib bo'lmadi yoki fayl bo'sh." if lang == "uz" else
                ("❌ Не удалось извлечь текст из документа или файл пуст." if lang == "ru" else
                 "❌ Could not extract text from document or file is empty.")
            )
            await status_msg.edit_text(err_msg, parse_mode="HTML")
            return

        # 1. TRANSLATOR rejimida hujjat yuborilgan bo'lsa
        if current_mode == "TRANSLATOR":
            dir_code = user_trans_direction.get(user_id, f"auto:{lang}")
            if ":" in dir_code:
                src, tgt = dir_code.split(":", 1)
            else:
                src, tgt = "auto", lang

            await status_msg.edit_text(
                f"🌐 <i>«{filename}» hujjati tarjima qilinmoqda, iltimos kuting...</i>" if lang == "uz" else
                (f"🌐 <i>Перевод документа «{filename}», пожалуйста подождите...</i>" if lang == "ru" else
                 f"🌐 <i>Translating document «{filename}», please wait...</i>"),
                parse_mode="HTML"
            )
            translated = await ai_service.translate_text(
                text=extracted_text,
                target_lang=tgt,
                source_lang=src
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            target_lang_names = {"uz": "O'zbekcha", "ru": "Русский", "en": "English"}
            tgt_name = target_lang_names.get(tgt, tgt)
            title = f"Tarjima ({tgt_name}): {filename}"
            user_name = message.from_user.full_name if message.from_user else "Foydalanuvchi"
            res_id = store_result(title=title, content=translated, user_name=user_name, user_id=user_id, category="trans")
            await send_ai_response(message, translated, res_id=res_id, lang=lang)
            return

        # 2. Standart Hujjat tahlili rejimi
        doc_id = uuid.uuid4().hex[:8]
        sections = await ai_service.detect_document_sections(extracted_text, filename, lang=lang)

        pending_docs[doc_id] = {
            "user_id": user_id,
            "user_name": message.from_user.full_name if message.from_user else "Foydalanuvchi",
            "filename": filename,
            "text": extracted_text,
            "sections": sections,
            "caption": caption,
            "created_at": datetime.now()
        }

        # Keshni nazorat qilish
        if len(pending_docs) > 100:
            oldest = next(iter(pending_docs))
            pending_docs.pop(oldest, None)

        char_count = len(extracted_text)
        prompt_text = (
            f"📄 <b>«{filename}» muvaffaqiyatli qabul qilindi!</b>\n\n"
            f"📊 <b>Hajmi:</b> taxminan {char_count:,} ta belgi matn\n"
            f"🎯 <b>Ushbu hujjatning qaysi bo'limi yoki yo'nalishini tahlil qilishni xohlaysiz?</b>\n\n"
            f"👇 <i>Quyidagi bo'limlardan birini tanlang yoki o'z maxsus so'rovingizni yozing:</i>"
            if lang == "uz" else
            (f"📄 <b>«{filename}» успешно принят!</b>\n\n"
             f"📊 <b>Объем:</b> около {char_count:,} знаков текста\n"
             f"🎯 <b>Какой раздел или направление документа вы хотите проанализировать?</b>\n\n"
             f"👇 <i>Выберите один из разделов ниже или напишите свой запрос:</i>"
             if lang == "ru" else
             f"📄 <b>«{filename}» received successfully!</b>\n\n"
             f"📊 <b>Size:</b> approx. {char_count:,} characters\n"
             f"🎯 <b>Which section or aspect of this document would you like to analyze?</b>\n\n"
             f"👇 <i>Choose a section below or type your custom query:</i>")
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

        await message.reply(
            prompt_text,
            parse_mode="HTML",
            reply_markup=get_document_sections_keyboard(doc_id, sections, lang=lang)
        )

    except Exception as e:
        logger.exception(f"Hujjat yuklashda xatolik: {e}")
        await status_msg.edit_text("❌ Hujjatni qabul qilishda xatolik yuz berdi.", parse_mode="HTML")
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.callback_query(F.data.startswith("doc_sec:"))
async def handle_document_section_callback(callback: CallbackQuery):
    """
    Hujjat bo'limini tanlash callbacki
    Format: doc_sec:{doc_id}:{action_or_idx} yoki doc_sec:{doc_id}:next:{next_idx}
    """
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Noto'g'ri so'rov!")
        return

    doc_id = parts[1]
    action = parts[2]

    doc_data = pending_docs.get(doc_id)
    if not doc_data:
        await callback.answer("Hujjat ma'lumotlari xotirada eskirgan. Iltimos, faylni qayta yuboring.", show_alert=True)
        return

    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    filename = doc_data["filename"]
    text = doc_data["text"]
    sections = doc_data.get("sections", [])
    user_name = doc_data.get("user_name", "Foydalanuvchi")

    if action == "cancel":
        pending_docs.pop(doc_id, None)
        await callback.answer("Bekor qilindi." if lang == "uz" else ("Отменено." if lang == "ru" else "Cancelled."))
        try:
            await callback.message.edit_text("❌ Hujjat tahlili bekor qilindi.", parse_mode="HTML")
        except Exception:
            pass
        return

    if action == "menu":
        await callback.answer("Bo'limlar ro'yxati:")
        char_count = len(text)
        prompt_text = (
            f"📄 <b>«{filename}» hujjati</b>\n\n"
            f"📊 <b>Hajmi:</b> taxminan {char_count:,} belgi\n"
            f"🎯 <b>Qaysi bo'limni tahlil qilishni xohlaysiz?</b>"
            if lang == "uz" else
            (f"📄 <b>Документ «{filename}»</b>\n\n📊 <b>Объем:</b> около {char_count:,} знаков\n🎯 <b>Какой раздел проанализировать?</b>"
             if lang == "ru" else
             f"📄 <b>Document «{filename}»</b>\n\n📊 <b>Size:</b> approx. {char_count:,} chars\n🎯 <b>Which section to analyze?</b>")
        )
        try:
            await callback.message.edit_text(
                prompt_text,
                parse_mode="HTML",
                reply_markup=get_document_sections_keyboard(doc_id, sections, lang=lang)
            )
        except Exception:
            await callback.message.answer(
                prompt_text,
                parse_mode="HTML",
                reply_markup=get_document_sections_keyboard(doc_id, sections, lang=lang)
            )
        return

    if action == "custom":
        user_active_mode[user_id] = f"DOC_CUSTOM:{doc_id}"
        await callback.answer("O'z so'rovingizni yozing:")
        custom_msg = (
            f"✍️ <b>«{filename}» bo'yicha aniq so'rov:</b>\n\n"
            "Iltimos, hujjatning qaysi moddasi, bo'limi yoki mavzusi bo'yicha tahlil kerakligini yozib yuboring.\n\n"
            "<i>(Masalan: «22-moddadan 38-moddagacha bo'lgan qismini tahlil qilib ber» yoki «Mol-mulkni xatlash va auksion qoidalari»)</i>"
            if lang == "uz" else
            (f"✍️ <b>Точный запрос по «{filename}»:</b>\n\n"
             "Пожалуйста, напишите, какую статью, раздел или тему документа проанализировать.\n\n"
             "<i>(Например: «Анализ с 22 по 38 статью» или «Правила описи имущества и аукциона»)</i>"
             if lang == "ru" else
             f"✍️ <b>Specific query for «{filename}»:</b>\n\n"
             "Please specify which article, section, or topic of the document you want analyzed.\n\n"
             "<i>(Example: «Analyze articles 22 to 38» or «Asset seizure and auction procedures»)</i>")
        )
        await callback.message.answer(custom_msg, parse_mode="HTML")
        return

    # Bo'lim nomini aniqlash
    current_sec_idx = None
    has_next = False

    if action == "next" and len(parts) >= 4 and parts[3].isdigit():
        current_sec_idx = int(parts[3])
        if 0 <= current_sec_idx < len(sections):
            section_title = sections[current_sec_idx]
            has_next = (current_sec_idx + 1 < len(sections))
        else:
            section_title = "Yakuniy qism va xulosalar"
            has_next = False
    elif action.isdigit():
        current_sec_idx = int(action)
        if 0 <= current_sec_idx < len(sections):
            section_title = sections[current_sec_idx]
            has_next = (current_sec_idx + 1 < len(sections))
        else:
            section_title = "Tanlangan bo'lim"
            has_next = False
    elif action == "all":
        section_title = "Barcha bo'limlar bo'yicha to'liq va umumiy amaliy tahlil"
    elif action == "risks":
        section_title = "Taraflarning huquqlari, majburiyatlari va amaliy yuridik xavflar"
    elif action == "deadlines":
        section_title = "Qat'iy muddatlar, moliyaviy raqamlar, jarimalar va javobgarlik"
    else:
        section_title = action

    await callback.answer("Tahlil boshlanmoqda...")
    status_msg = await callback.message.reply(
        f"⏳ <b>«{section_title}»</b> bo'yicha chuqur tahlil tayyorlanmoqda...",
        parse_mode="HTML"
    )
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

    try:
        analysis = await ai_service.analyze_document_section(
            user_id=user_id,
            doc_text=text,
            filename=filename,
            section_name=section_title,
            custom_prompt=doc_data.get("caption"),
            lang=lang
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

        if analysis.startswith("⚠️") or analysis.startswith("❌"):
            await callback.message.answer(analysis, parse_mode="HTML")
            return

        title = f"{filename}: {section_title[:30]}"
        res_id = store_result(title=title, content=analysis, user_name=user_name, user_id=user_id, category="doc")
        result_markup = get_document_result_keyboard(
            res_id=res_id,
            doc_id=doc_id,
            current_sec_idx=current_sec_idx,
            has_next=has_next,
            lang=lang
        )
        await send_ai_response(callback.message, analysis, res_id=res_id, reply_markup=result_markup, lang=lang)

    except Exception as e:
        logger.exception(f"Bo'lim tahlilida xatolik: {e}")
        await status_msg.edit_text("❌ Bo'limni tahlil qilishda xatolik yuz berdi.", parse_mode="HTML")


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
        res_id = store_result(title="Audio Tahlil", content=response, user_name=user_name, user_id=item["user_id"], category="audio")

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
                    caption="📄 <b>Audio xabar tahlili (Word hujjati)</b>",
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
                    caption="📑 <b>Audio xabar tahlili (PDF hujjati)</b>",
                    parse_mode="HTML"
                )
            finally:
                if pdf_path and os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass

        if action in ("voice", "all"):
            user_lang = get_user_lang(item["user_id"])
            ogg_path, mp3_path = await convert_text_to_voice_and_audio(response, lang=user_lang)
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
        arch_item = archive_service.get_item(res_id)
        if arch_item:
            result_store[res_id] = {
                "title": arch_item["title"],
                "content": arch_item["content"],
                "user_name": arch_item["user_name"],
                "user_id": arch_item["user_id"],
                "category": arch_item.get("category", "chat"),
                "created_at": arch_item["created_at"]
            }
        else:
            await callback.answer("Ma'lumot xotirada eskirgan.", show_alert=True)
            return

    user_id = callback.from_user.id
    lang = get_user_lang(user_id)

    if sub_act == "tr":
        await callback.message.edit_reply_markup(reply_markup=get_transfer_keyboard(res_id, lang=lang))
        await callback.answer("Bo'limni tanlang:" if lang == "uz" else ("Выберите раздел:" if lang == "ru" else "Select section:"))
    elif sub_act == "ed":
        await callback.message.edit_reply_markup(reply_markup=get_edit_keyboard(res_id, lang=lang))
        await callback.answer("Tahrirlash turini tanlang:" if lang == "uz" else ("Выберите тип:" if lang == "ru" else "Select type:"))
    elif sub_act == "back":
        await callback.message.edit_reply_markup(reply_markup=get_content_actions_keyboard(res_id, lang=lang))
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
        arch_item = archive_service.get_item(res_id)
        if arch_item:
            item = {
                "title": arch_item["title"],
                "content": arch_item["content"],
                "user_name": arch_item["user_name"],
                "user_id": arch_item["user_id"],
                "category": arch_item.get("category", "chat"),
                "created_at": arch_item["created_at"]
            }
            result_store[res_id] = item
        else:
            await callback.answer("Ushbu ma'lumot xotirada topilmadi.", show_alert=True)
            return

    content = item["content"]
    title = item["title"]
    user_name = item.get("user_name", "Foydalanuvchi")
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)

    await callback.answer(f"O'tkazilmoqda...")

    if target == "docx":
        docx_path = generate_docx(title=title, content=content, user_name=user_name)
        try:
            await callback.message.answer_document(
                document=FSInputFile(docx_path, filename=f"{title[:20]}.docx"),
                caption=f"📄 <b>Word hujjati yaratildi</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
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
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
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
                caption=f"📽 <b>PowerPoint taqdimoti yaratildi</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
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
                caption=f"📊 <b>Excel jadvali yaratildi</b>\n<i>Mavzu: {title}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if xlsx_path and os.path.exists(xlsx_path):
                os.remove(xlsx_path)

    elif target == "deep":
        status_msg = await callback.message.reply("🔬 <i>Ushbu mavzu bo'yicha Deep Research o'tkazilmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        deep_res = await ai_service.deep_research(callback.from_user.id, f"{title}\n{content[:500]}", lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass
        new_res_id = store_result(
            title=f"Deep Research: {title}",
            content=deep_res,
            user_name=user_name,
            user_id=callback.from_user.id,
            category="research"
        )
        await send_ai_response(callback.message, deep_res, res_id=new_res_id, lang=lang)

    elif target == "voice":
        status_msg = await callback.message.reply("🎙 <i>Ovozli xabar tayyorlanmoqda...</i>", parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)
        ogg_path, mp3_path = await convert_text_to_voice_and_audio(content, lang=lang)
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

    elif target == "trans":
        choose_tr_text = (
            "🌐 <b>Ushbu matnni qaysi tilga tarjima qilmoqchisiz?</b>\n\n"
            "Kerakli tilni tanlang:"
            if lang == "uz" else
            ("🌐 <b>На какой язык вы хотите перевести этот текст?</b>\n\nВыберите язык:"
             if lang == "ru" else
             "🌐 <b>Which language would you like to translate this text into?</b>\n\nSelect language:")
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data=f"do_tr:uz:{res_id}"),
                    InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"do_tr:ru:{res_id}")
                ],
                [
                    InlineKeyboardButton(text="🇬🇧 English", callback_data=f"do_tr:en:{res_id}"),
                    InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"act:back:{res_id}")
                ]
            ]
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            await callback.message.answer(choose_tr_text, parse_mode="HTML", reply_markup=kb)
        return


@router.callback_query(F.data.startswith("do_tr:"))
async def handle_direct_translation_callback(callback: CallbackQuery):
    """Natijani to'g'ridan-to'g'ri tanlangan tilga tarjima qilish"""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Xatolik!")
        return

    target_lang = parts[1]
    res_id = parts[2]
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)

    item = result_store.get(res_id)
    if not item:
        arch_item = archive_service.get_item(res_id)
        if arch_item:
            item = arch_item
        else:
            await callback.answer("Ma'lumot topilmadi.", show_alert=True)
            return

    content = item["content"]
    title = item["title"]

    await callback.answer("Tarjima qilinmoqda...")
    status_msg = await callback.message.reply(
        "🌐 <i>Tarjima qilinmoqda, iltimos kuting...</i>" if lang == "uz" else
        ("🌐 <i>Выполняется перевод, пожалуйста подождите...</i>" if lang == "ru" else
         "🌐 <i>Translating, please wait...</i>"),
        parse_mode="HTML"
    )
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

    translated = await ai_service.translate_text(
        text=content,
        target_lang=target_lang,
        source_lang="auto"
    )
    try:
        await status_msg.delete()
    except Exception:
        pass

    target_lang_names = {"uz": "O'zbekcha", "ru": "Русский", "en": "English"}
    new_res_id = store_result(
        title=f"Tarjima ({target_lang_names.get(target_lang, target_lang)}): {title[:20]}",
        content=translated,
        user_name=callback.from_user.full_name or "Foydalanuvchi",
        user_id=user_id,
        category="trans"
    )
    await send_ai_response(callback.message, translated, res_id=new_res_id, lang=target_lang)


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
        arch_item = archive_service.get_item(res_id)
        if arch_item:
            item = {
                "title": arch_item["title"],
                "content": arch_item["content"],
                "user_name": arch_item["user_name"],
                "user_id": arch_item["user_id"],
                "category": arch_item.get("category", "chat"),
                "created_at": arch_item["created_at"]
            }
            result_store[res_id] = item
        else:
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

    new_res_id = store_result(
        title=f"{title} (Tahrirlangan)",
        content=new_content,
        user_name=user_name,
        user_id=callback.from_user.id,
        category="chat"
    )
    await send_ai_response(callback.message, new_content, res_id=new_res_id)


# =====================================================================
# 🗄 ARXIV INTERAKTIV CALLBACK HANDLERLARI
# =====================================================================

@router.callback_query(F.data.startswith("arx:"))
async def handle_archive_callback(callback: CallbackQuery):
    """Arxiv bo'limi interaktiv boshqaruv tugmalari"""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    action = parts[1]
    user_id = callback.from_user.id

    if action == "noop":
        await callback.answer()
        return

    if action == "home":
        await callback.answer("Bosh menyu")
        is_admin = access_guard.is_admin(user_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer("🏠 <b>Bosh menyu</b>", parse_mode="HTML", reply_markup=get_main_keyboard(is_admin=is_admin))
        return

    # Sahifalash: arx:p:{page}
    if action == "p" and len(parts) >= 3:
        page = int(parts[2]) if parts[2].isdigit() else 0
        total_count = archive_service.get_user_count(user_id)
        per_page = 5

        if total_count == 0:
            try:
                await callback.message.edit_text(
                    "🗄 <b>Arxivingiz bo'sh.</b>\n\nYangi so'rovlaringiz avtomatik ravishda saqlanib boradi.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await callback.answer("Arxiv bo'sh.")
            return

        offset = page * per_page
        items = archive_service.get_user_items(user_id=user_id, limit=per_page, offset=offset)

        text = (
            f"🗄 <b>Sizning so'rovlar arxivingiz</b>\n"
            f"📊 <b>Jami saqlangan so'rovlar:</b> {total_count} ta\n\n"
            f"👇 <i>Kerakli so'rovni tanlang:</i>"
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_archive_list_keyboard(items=items, page=page, total_count=total_count, per_page=per_page)
            )
        except Exception:
            pass
        await callback.answer()
        return

    # So'rov tafsilotini ko'rish: arx:view:{item_id}
    if action == "view" and len(parts) >= 3:
        item_id = parts[2]
        item = archive_service.get_item(item_id)
        if not item:
            await callback.answer("Ushbu yozuv arxivdan topilmadi yoki o'chirilgan.", show_alert=True)
            return

        # Natijalar keshiga kiritish (Word, PDF, Excel, Ovozli qilish tugmalari ishlashi uchun)
        result_store[item_id] = {
            "title": item["title"],
            "content": item["content"],
            "user_name": item["user_name"],
            "user_id": item["user_id"],
            "created_at": item["created_at"]
        }

        lang = get_user_lang(user_id)
        category_labels = {
            "doc": "📑 Hujjat tahlili" if lang == "uz" else ("📑 Анализ документа" if lang == "ru" else "📑 Document Analysis"),
            "research": "🔬 Deep Research",
            "audio": "🎙 Audio tahlil" if lang == "uz" else ("🎙 Анализ аудио" if lang == "ru" else "🎙 Audio Analysis"),
            "image": "📸 Rasm tahlili" if lang == "uz" else ("📸 Анализ изображения" if lang == "ru" else "📸 Image Analysis"),
            "slides": "📽 Slaydlar, Prezentatsiya" if lang == "uz" else ("📽 Слайды, Презентация" if lang == "ru" else "📽 Slides, Presentation"),
            "office": "📊 Hujjatlar va jadvallar" if lang == "uz" else ("📊 Документы и таблицы" if lang == "ru" else "📊 Office Documents"),
            "chat": "🤖 AI Chat suhbati" if lang == "uz" else ("🤖 AI Чат" if lang == "ru" else "🤖 AI Chat"),
            "trans": "🌐 Tarjima" if lang == "uz" else ("🌐 Перевод" if lang == "ru" else "🌐 Translation")
        }
        cat_title = category_labels.get(item.get("category", "chat"), "📌 Saqlangan tahlil")

        preview_text = item["content"][:400].strip()
        if len(item["content"]) > 400:
            preview_text += "..."

        msg = (
            f"🗄 <b>Arxivdagi so'rov:</b>\n\n"
            f"🏷 <b>Bo'lim:</b> {cat_title}\n"
            f"📅 <b>Vaqt:</b> <code>{item['created_at']}</code>\n"
            f"📌 <b>Sarlavha:</b> <b>{item['title']}</b>\n\n"
            f"📝 <b>Qisqacha mazmun:</b>\n<i>{preview_text}</i>\n\n"
            f"👇 <i>Hujjatni yuklab olish, tahrirlash, ovozli eshitish yoki o'chirish:</i>"
        )

        try:
            await callback.message.edit_text(
                msg,
                parse_mode="HTML",
                reply_markup=get_archive_item_keyboard(item_id)
            )
        except Exception:
            await callback.message.answer(
                msg,
                parse_mode="HTML",
                reply_markup=get_archive_item_keyboard(item_id)
            )
        await callback.answer()
        return

    # To'liq matnni o'qish: arx:read:{item_id}
    if action == "read" and len(parts) >= 3:
        item_id = parts[2]
        item = archive_service.get_item(item_id)
        if not item:
            await callback.answer("Yozuv topilmadi.", show_alert=True)
            return

        result_store[item_id] = {
            "title": item["title"],
            "content": item["content"],
            "user_name": item["user_name"],
            "user_id": item["user_id"],
            "created_at": item["created_at"]
        }
        await callback.answer("To'liq matn yuborilmoqda...")
        await send_ai_response(
            callback.message,
            item["content"],
            res_id=item_id,
            reply_markup=get_archive_item_keyboard(item_id)
        )
        return

    # O'chirishni tasdiqlash so'rovi: arx:confirm_del:{item_id}
    if action == "confirm_del" and len(parts) >= 3:
        item_id = parts[2]
        item = archive_service.get_item(item_id)
        title = item["title"] if item else "Ushbu so'rov"
        confirm_text = (
            f"❓ <b>Haqiqatan ham ushbu yozuvni arxivdan o'chirmoqchimisiz?</b>\n\n"
            f"📌 <i>«{title}»</i>\n\n"
            f"⚠️ <i>O'chirilgandan so'ng ma'lumotni qayta tiklab bo'lmaydi.</i>"
        )
        try:
            await callback.message.edit_text(
                confirm_text,
                parse_mode="HTML",
                reply_markup=get_archive_delete_confirm_keyboard(item_id)
            )
        except Exception:
            pass
        await callback.answer()
        return

    # O'chirishni bajarish: arx:do_del:{item_id}
    if action == "do_del" and len(parts) >= 3:
        item_id = parts[2]
        success = archive_service.delete_item(item_id, user_id=user_id)
        result_store.pop(item_id, None)

        if success:
            await callback.answer("✅ Yozuv arxivdan muvaffaqiyatli o'chirildi!", show_alert=True)
        else:
            await callback.answer("⚠️ Yozuv topilmadi yoki allaqachon o'chirilgan.", show_alert=True)

        # Yangilangan ro'yxatga qaytarish
        total_count = archive_service.get_user_count(user_id)
        if total_count == 0:
            try:
                await callback.message.edit_text(
                    "🗄 <b>Sizning arxivingiz tozalandi (bo'sh).</b>\n\nYangi so'rovlaringiz avtomatik tarzda saqlanadi.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

        items = archive_service.get_user_items(user_id=user_id, limit=5, offset=0)
        text = (
            f"🗄 <b>Sizning so'rovlar arxivingiz</b>\n"
            f"📊 <b>Jami saqlangan so'rovlar:</b> {total_count} ta\n\n"
            f"👇 <i>Kerakli so'rovni tanlang:</i>"
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_archive_list_keyboard(items=items, page=0, total_count=total_count)
            )
        except Exception:
            pass
        return

    # Butun arxivni tozalashni tasdiqlash so'rovi: arx:confirm_clear
    if action == "confirm_clear":
        total_count = archive_service.get_user_count(user_id)
        confirm_text = (
            f"⚠️ <b>DIQQAT: Butun arxivni tozalash!</b>\n\n"
            f"Barcha <b>{total_count} ta</b> saqlangan so'rov va natijalaringiz arxivdan butunlay o'chiriladi.\n\n"
            f"Arxivni tozalashni tasdiqlaysizmi?"
        )
        try:
            await callback.message.edit_text(
                confirm_text,
                parse_mode="HTML",
                reply_markup=get_archive_clear_confirm_keyboard()
            )
        except Exception:
            pass
        await callback.answer()
        return

    # Butun arxivni tozalashni bajarish: arx:do_clear
    if action == "do_clear":
        deleted_count = archive_service.clear_user_archive(user_id)
        await callback.answer(f"✅ Arxiv to'liq tozalandi ({deleted_count} ta so'rov o'chirildi)!", show_alert=True)
        try:
            await callback.message.edit_text(
                "🗄 <b>Arxiv to'liq tozalandi.</b>\n\nYangi so'rovlaringiz avtomatik tarzda qaytadan saqlanadi.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return


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

        res_id = store_result(
            title="Rasm tahlili",
            content=response,
            user_name=message.from_user.full_name or "Foydalanuvchi",
            user_id=user_id,
            category="image"
        )
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

    # 0. Hujjat bo'yicha maxsus bo'lim/modda so'rovi (DOC_CUSTOM:{doc_id})
    if current_mode.startswith("DOC_CUSTOM:"):
        doc_id = current_mode.split(":", 1)[1]
        doc_data = pending_docs.get(doc_id)
        user_active_mode[user_id] = "NORMAL"
        lang = get_user_lang(user_id)

        if doc_data:
            filename = doc_data["filename"]
            text_content = doc_data["text"]
            user_name = doc_data.get("user_name", "Foydalanuvchi")
            custom_query = text.strip()

            status_msg = await message.reply(
                f"⏳ <b>«{filename}»</b> hujjati bo'yicha <i>«{custom_query[:40]}»</i> talabi tahlil qilinmoqda..." if lang == "uz" else
                (f"⏳ По документу <b>«{filename}»</b> выполняется анализ <i>«{custom_query[:40]}»</i>..." if lang == "ru" else
                 f"⏳ Analyzing <i>«{custom_query[:40]}»</i> for <b>«{filename}»</b>..."),
                parse_mode="HTML"
            )
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

            try:
                analysis = await ai_service.analyze_document_section(
                    user_id=user_id,
                    doc_text=text_content,
                    filename=filename,
                    section_name=custom_query,
                    lang=lang
                )
                try:
                    await status_msg.delete()
                except Exception:
                    pass

                if analysis.startswith("⚠️") or analysis.startswith("❌"):
                    await message.answer(analysis, parse_mode="HTML")
                    return

                title = f"{filename}: {custom_query[:25]}"
                res_id = store_result(title=title, content=analysis, user_name=user_name, user_id=user_id, category="doc")
                result_markup = get_document_result_keyboard(res_id=res_id, doc_id=doc_id, current_sec_idx=None, has_next=False, lang=lang)
                await send_ai_response(message, analysis, res_id=res_id, reply_markup=result_markup, lang=lang)
                return
            except Exception as e:
                logger.exception(f"Maxsus so'rov tahlilida xatolik: {e}")
                await status_msg.edit_text("❌ Tahlil jarayonida xatolik yuz berdi.", parse_mode="HTML")
                return

    # 1. Instagram havolasi bo'lsa
    match = INSTAGRAM_URL_REGEX.search(text)
    if match:
        await process_instagram_download(message, match.group(0))
        return

    # 1.5. Rejim: TRANSLATOR (Tarjimon)
    if current_mode == "TRANSLATOR":
        lang = get_user_lang(user_id)
        dir_code = user_trans_direction.get(user_id, f"auto:{lang}")
        if ":" in dir_code:
            src, tgt = dir_code.split(":", 1)
        else:
            src, tgt = "auto", lang

        status_msg = await message.reply(
            "🌐 <i>Tarjima qilinmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("🌐 <i>Выполняется перевод, пожалуйста подождите...</i>" if lang == "ru" else
             "🌐 <i>Translating, please wait...</i>"),
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        translated = await ai_service.translate_text(
            text=text,
            target_lang=tgt,
            source_lang=src
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

        if translated.startswith("⚠️") or translated.startswith("❌"):
            await message.answer(translated, parse_mode="HTML")
            return

        target_lang_names = {"uz": "O'zbekcha", "ru": "Русский", "en": "English"}
        tgt_name = target_lang_names.get(tgt, tgt)
        title = f"Tarjima ({tgt_name}): {text[:25]}"
        res_id = store_result(title=title, content=translated, user_name=user_name, user_id=user_id, category="trans")
        await send_ai_response(message, translated, res_id=res_id, lang=lang)
        return

    # 2. Rejim: DEEP_RESEARCH
    if current_mode == "DEEP_RESEARCH":
        lang = get_user_lang(user_id)
        status_msg = await message.reply(
            "🔬 <i>Mavzu bo'yicha chuqur izlanish (Deep Research) olib borilmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("🔬 <i>Проводится глубокое исследование (Deep Research), пожалуйста подождите...</i>" if lang == "ru" else
             "🔬 <i>Conducting Deep Research, please wait...</i>"),
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        report = await ai_service.deep_research(user_id=user_id, query=text, lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title=text[:35], content=report, user_name=user_name, user_id=user_id, category="research")
        await send_ai_response(message, report, res_id=res_id, lang=lang)
        return

    # 3. Rejim: SLIDES (Prezentatsiya)
    if current_mode == "SLIDES":
        lang = get_user_lang(user_id)
        status_msg = await message.reply("📽 <i>Taqdimot slaydlari rejalashtirilmoqda va PowerPoint (.pptx) fayli yaratilmoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        slides_text = await ai_service.format_for_presentation(user_id=user_id, text=text)
        pptx_path = generate_presentation(title=text[:40], content=slides_text, user_name=user_name)
        try:
            await status_msg.delete()
        except Exception:
            pass

        res_id = store_result(title=f"Prezentatsiya: {text[:30]}", content=slides_text, user_name=user_name, user_id=user_id, category="slides")
        try:
            await message.answer_document(
                document=FSInputFile(pptx_path, filename=f"Prezentatsiya_{res_id}.pptx"),
                caption=f"📽 <b>PowerPoint taqdimotingiz tayyor!</b>\n<i>Mavzu: {text[:45]}</i>",
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if pptx_path and os.path.exists(pptx_path):
                os.remove(pptx_path)
        return

    # 4. Rejim: OFFICE_DOCS (Word, PDF, Excel)
    if current_mode == "OFFICE_DOCS":
        lang = get_user_lang(user_id)
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

        res_id = store_result(title=text[:30], content=text, user_name=user_name, user_id=user_id, category="office")
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
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
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
        lang = get_user_lang(user_id)
        if len(text) > 1200:
            doc_id = uuid.uuid4().hex[:8]
            sections = await ai_service.detect_document_sections(text, "Matnli Hujjat", lang=lang)
            pending_docs[doc_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "filename": "Matnli Hujjat",
                "text": text,
                "sections": sections,
                "caption": "",
                "created_at": datetime.now()
            }
            prompt_text = (
                f"📄 <b>Matnli hujjat qabul qilindi!</b>\n\n"
                f"📊 <b>Hajmi:</b> taxminan {len(text):,} ta belgi\n"
                f"🎯 <b>Ushbu matnning qaysi bo'limi yoki yo'nalishini tahlil qilishni xohlaysiz?</b>\n\n"
                f"👇 <i>Quyidagi bo'limlardan birini tanlang yoki o'z savolingizni yozing:</i>"
                if lang == "uz" else
                (f"📄 <b>Текстовый документ принят!</b>\n\n"
                 f"📊 <b>Объем:</b> около {len(text):,} знаков\n"
                 f"🎯 <b>Какой раздел вы хотите проанализировать?</b>\n\n"
                 f"👇 <i>Выберите раздел ниже или напишите вопрос:</i>"
                 if lang == "ru" else
                 f"📄 <b>Text document received!</b>\n\n"
                 f"📊 <b>Size:</b> approx. {len(text):,} characters\n"
                 f"🎯 <b>Which section would you like to analyze?</b>\n\n"
                 f"👇 <i>Select a section below or type your question:</i>")
            )
            await message.reply(
                prompt_text,
                parse_mode="HTML",
                reply_markup=get_document_sections_keyboard(doc_id, sections, lang=lang)
            )
            return

        status_msg = await message.reply("📑 <i>Hujjat matni tahlil qilinmoqda...</i>", parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        analysis = await ai_service.analyze_document(user_id=user_id, doc_text=text, filename="Matnli Hujjat", lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if analysis.startswith("⚠️") or analysis.startswith("❌"):
            await message.answer(analysis, parse_mode="HTML")
            return

        res_id = store_result(title="Hujjat tahlili", content=analysis, user_name=user_name, user_id=user_id, category="doc")
        await send_ai_response(message, analysis, res_id=res_id, lang=lang)
        return

    # 6. Rejim: AI_CHAT yoki oddiy suhbat
    lang = get_user_lang(user_id)
    status_msg = await message.reply("🤔 <i>O'ylamoqda...</i>", parse_mode="HTML")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    response = await ai_service.ask_gemini(user_id=user_id, prompt=text)
    try:
        await status_msg.delete()
    except Exception:
        pass

    if response.startswith("⚠️") or response.startswith("❌"):
        await message.answer(response, parse_mode="HTML")
        return

    res_id = store_result(title=text[:30], content=response, user_name=user_name, user_id=user_id, category="chat")
    await send_ai_response(message, response, res_id=res_id, lang=lang)


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
