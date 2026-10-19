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
from config import DOWNLOADS_DIR, BASE_DIR, ADMIN_IDS
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

# Shartnoma/hujjat yuklanganda da'vo yoki talabnoma tuzishni kutish: legal_id -> {user_id, user_name, filename, text, caption, created_at}
legal_pending_docs: dict[str, dict] = {}


def format_doc_title(filename: str, section_title: str = "", lang: str = "uz") -> str:
    """Hujjat va bo'lim nomini chiroyli, bo'shliqlari to'g'ri, imlo va tilga mos formatlash"""
    base = re.sub(r"\.[a-zA-Z0-9]+$", "", filename or "")
    base = re.sub(r"_+", " ", base).strip()
    base = re.sub(r"\s+", " ", base)

    sec = re.sub(r"_+", " ", section_title or "").strip()
    sec = re.sub(r"\s+", " ", sec)

    if sec and base:
        if len(base) > 35:
            base = base[:35].rstrip() + "..."
        return f"{base}: {sec}"
    elif sec:
        return sec
    elif base:
        return base
    else:
        return "Hujjat tahlili" if lang == "uz" else ("Анализ документа" if lang == "ru" else "Document Analysis")


def get_safe_file_title(title: str, default: str = "Document") -> str:
    """Fayl tizimi va Telegram uchun xavfsiz va chiroyli fayl nomi (tagchiziqlarsiz va so'zlarni yopishtirmasdan)"""
    safe = re.sub(r'[\\/*?:"<>|]', "", title or "")
    safe = re.sub(r"_+", " ", safe)
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:35] or default


def store_result(
    title: str,
    content: str,
    user_name: str = "",
    user_id: int = 0,
    category: str = "chat"
) -> str:
    """Natijani xotiraga va doimiy SQLite arxivga saqlash hamda res_id qaytarish"""
    res_id = uuid.uuid4().hex[:8]
    clean_t = re.sub(r"_+", " ", title or "").strip()
    clean_t = re.sub(r"\s+", " ", clean_t) or ("Tahlil Natijasi" if category != "doc" else "Hujjat Tahlili")
    result_store[res_id] = {
        "title": clean_t,
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
    legal_lawsuit_btn = (
        "⚖️ Hujjatdan Da'vo arizasi" if lang == "uz" else
        ("⚖️ Составить Иск по документу" if lang == "ru" else "⚖️ Lawsuit from Document")
    )
    legal_demand_btn = (
        "📑 Talabnoma (Pretenziya)" if lang == "uz" else
        ("📑 Составить Претензию" if lang == "ru" else "📑 Demand Letter")
    )
    legal_debt_cert_btn = (
        "📜 Qarzdorlik ma'lumotnomasi + Ilova xati" if lang == "uz" else
        ("📜 Справка о задолженности + Сопроводительное" if lang == "ru" else "📜 Debt Certificate + Cover Letter")
    )
    legal_notary_btn = (
        "📜 Notariusdan Ijro xati (3757)" if lang == "uz" else
        ("📜 Исполнительная надпись (3757)" if lang == "ru" else "📜 Notary Execution Inscription (3757)")
    )
    kb.append([
        InlineKeyboardButton(text=legal_lawsuit_btn, callback_data=f"doc_sec:{doc_id}:legal_lawsuit"),
        InlineKeyboardButton(text=legal_demand_btn, callback_data=f"doc_sec:{doc_id}:legal_demand")
    ])
    kb.append([InlineKeyboardButton(text=legal_debt_cert_btn, callback_data=f"doc_sec:{doc_id}:legal_debt_cert")])
    kb.append([InlineKeyboardButton(text=legal_notary_btn, callback_data=f"doc_sec:{doc_id}:legal_notary_exec")])
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


def get_legal_menu_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """Sudga da'vo va talabnoma bosh menyusi klaviaturasi"""
    kb = [
        [
            InlineKeyboardButton(
                text="📎 Shartnoma yuklash (Word, PDF)" if lang == "uz" else ("📎 Загрузить договор (Word, PDF)" if lang == "ru" else "📎 Upload Contract (Word, PDF)"),
                callback_data="legal_type:upload"
            )
        ],
        [
            InlineKeyboardButton(
                text="📜 Notariusdan Ijro xati (3757-son)" if lang == "uz" else ("📜 Исполнительная надпись (3757)" if lang == "ru" else "📜 Notary Inscription (3757)"),
                callback_data="notary_tpl:both"
            )
        ],
        [
            InlineKeyboardButton(
                text="📜 Qarzdorlik ma'lumotnomasi + Ilova xati" if lang == "uz" else ("📜 Справка о задолженности + Сопроводительное" if lang == "ru" else "📜 Debt Certificate + Cover Letter"),
                callback_data="legal_tpl:debt_cert"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏛 Sudga Da'vo arizasi" if lang == "uz" else ("🏛 Исковое заявление в суд" if lang == "ru" else "🏛 Court Lawsuit"),
                callback_data="legal_type:lawsuit"
            ),
            InlineKeyboardButton(
                text="📑 Talabnoma (Pretenziya)" if lang == "uz" else ("📑 Претензия (Досудебная)" if lang == "ru" else "📑 Formal Demand Letter"),
                callback_data="legal_type:demand"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Qarz undirish" if lang == "uz" else ("💰 Взыскание долга" if lang == "ru" else "💰 Debt Recovery"),
                callback_data="legal_tpl:debt"
            ),
            InlineKeyboardButton(
                text="🏢 Shartnoma nizosi" if lang == "uz" else ("🏢 Спор по договору" if lang == "ru" else "🏢 Contract Dispute"),
                callback_data="legal_tpl:contract"
            )
        ],
        [
            InlineKeyboardButton(
                text="👨‍👩‍👦 Aliment undirish" if lang == "uz" else ("👨‍👩‍👦 Взыскание алиментов" if lang == "ru" else "👨‍👩‍👦 Alimony Claim"),
                callback_data="legal_tpl:alimony"
            ),
            InlineKeyboardButton(
                text="💼 Mehnat / Oylik" if lang == "uz" else ("💼 Трудовой спор / Зарплата" if lang == "ru" else "💼 Labor / Salary Claim"),
                callback_data="legal_tpl:labor"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Barcha namunalar" if lang == "uz" else ("📋 Все шаблоны" if lang == "ru" else "📋 All Templates"),
                callback_data="legal_type:templates"
            ),
            InlineKeyboardButton(
                text="✍️ Erkin bayon qilish" if lang == "uz" else ("✍️ Свой запрос" if lang == "ru" else "✍️ Custom Case"),
                callback_data="legal_type:free"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_legal_doc_action_keyboard(legal_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Yuklangan shartnoma/hujjat asosida qanday yuridik hujjat tayyorlashni tanlash klaviaturasi"""
    kb = [
        [
            InlineKeyboardButton(
                text="📜 Notariusdan Ijro xati olish (3757-son)" if lang == "uz" else ("📜 Исполнительная надпись нотариуса (3757)" if lang == "ru" else "📜 Notary Execution Inscription (3757)"),
                callback_data=f"legal_act:{legal_id}:notary_exec"
            )
        ],
        [
            InlineKeyboardButton(
                text="📜 Qarzdorlik ma'lumotnomasi + Ilova xati" if lang == "uz" else ("📜 Справка о задолженности + Сопроводительное" if lang == "ru" else "📜 Debt Certificate + Cover Letter"),
                callback_data=f"legal_act:{legal_id}:debt_cert"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏛 Sudga Da'vo arizasi tuzish" if lang == "uz" else ("🏛 Составить Исковое заявление в суд" if lang == "ru" else "🏛 Draft Court Lawsuit"),
                callback_data=f"legal_act:{legal_id}:lawsuit"
            )
        ],
        [
            InlineKeyboardButton(
                text="📑 Sudgacha Talabnoma (Pretenziya)" if lang == "uz" else ("📑 Составить Досудебную Претензию" if lang == "ru" else "📑 Draft Pre-trial Demand Letter"),
                callback_data=f"legal_act:{legal_id}:demand"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡️ Da'vo va Talabnoma (Ikkalasi birga)" if lang == "uz" else ("⚡️ Иск и Претензия (Вместе)" if lang == "ru" else "⚡️ Both Lawsuit & Demand Letter"),
                callback_data=f"legal_act:{legal_id}:both"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Shartnoma tahlili (Risklar va Muddatlar)" if lang == "uz" else ("📊 Анализ рисков и сроков" if lang == "ru" else "📊 Contract Risk Analysis"),
                callback_data=f"legal_act:{legal_id}:analysis"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_legal_templates_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """Mashhur da'vo va talabnoma turlari ro'yxati"""
    kb = [
        [
            InlineKeyboardButton(
                text="📜 Notariusdan Ijro xati (3757-son)" if lang == "uz" else ("📜 Исполнительная надпись (3757)" if lang == "ru" else "📜 Notary Inscription (3757)"),
                callback_data="notary_tpl:both"
            )
        ],
        [
            InlineKeyboardButton(
                text="📜 Qarzdorlik ma'lumotnomasi + Ilova xati" if lang == "uz" else ("📜 Справка о задолженности + Сопроводительное" if lang == "ru" else "📜 Debt Certificate + Cover Letter"),
                callback_data="legal_tpl:debt_cert"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Qarz va penya undirish" if lang == "uz" else ("💰 Долг и неустойка" if lang == "ru" else "💰 Debt & Penalties"),
                callback_data="legal_tpl:debt"
            ),
            InlineKeyboardButton(
                text="🏢 Shartnoma buzilishi" if lang == "uz" else ("🏢 Нарушение договора" if lang == "ru" else "🏢 Breach of Contract"),
                callback_data="legal_tpl:contract"
            )
        ],
        [
            InlineKeyboardButton(
                text="👨‍👩‍👦 Aliment undirish" if lang == "uz" else ("👨‍👩‍👦 Взыскание алиментов" if lang == "ru" else "👨‍👩‍👦 Child Support / Alimony"),
                callback_data="legal_tpl:alimony"
            ),
            InlineKeyboardButton(
                text="💔 Nikohni bekor qilish" if lang == "uz" else ("💔 Расторжение брака" if lang == "ru" else "💔 Divorce Petition"),
                callback_data="legal_tpl:divorce"
            )
        ],
        [
            InlineKeyboardButton(
                text="💼 Ishga tiklash / Oylik" if lang == "uz" else ("💼 Восстановление на работе" if lang == "ru" else "💼 Reinstatement & Salary"),
                callback_data="legal_tpl:labor"
            ),
            InlineKeyboardButton(
                text="🚗 Moddiy zararni qoplash" if lang == "uz" else ("🚗 Возмещение ущерба" if lang == "ru" else "🚗 Property Damage"),
                callback_data="legal_tpl:damage"
            )
        ],
        [
            InlineKeyboardButton(
                text="✉️ Qarz to'lash Talabnomasi" if lang == "uz" else ("✉️ Претензия об уплате долга" if lang == "ru" else "✉️ Debt Demand Notice"),
                callback_data="legal_tpl:demand_debt"
            ),
            InlineKeyboardButton(
                text="📦 Sifatsiz tovar Talabnomasi" if lang == "uz" else ("📦 Претензия по качеству" if lang == "ru" else "📦 Defective Goods Notice"),
                callback_data="legal_tpl:demand_quality"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back"),
                callback_data="legal_nav:menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_notary_menu_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """Notariusdan ijro xati olish bosh menyusi klaviaturasi (3757-sonli qaror)"""
    kb = [
        [
            InlineKeyboardButton(
                text="📎 Shartnoma/Hujjat yuklash (Word, PDF)" if lang == "uz" else ("📎 Загрузить договор/акт (Word, PDF)" if lang == "ru" else "📎 Upload Contract/Act (Word, PDF)"),
                callback_data="notary_act:upload"
            )
        ],
        [
            InlineKeyboardButton(
                text="📝 Notariusga Ijro xati arizasi" if lang == "uz" else ("📝 Заявление нотариусу" if lang == "ru" else "📝 Application to Notary"),
                callback_data="notary_tpl:application"
            ),
            InlineKeyboardButton(
                text="✉️ Qarzdorga ogohlantirish xati" if lang == "uz" else ("✉️ Предупреждение должнику" if lang == "ru" else "✉️ Warning to Debtor"),
                callback_data="notary_tpl:notice"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡️ To'liq to'plam (Ariza + Ogohlantirish)" if lang == "uz" else ("⚡️ Полный комплект (Заявление + Предупреждение)" if lang == "ru" else "⚡️ Full Package (Application + Notice)"),
                callback_data="notary_tpl:both"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 3757-sonli talablar ro'yxati (Kategoriyalar)" if lang == "uz" else ("📋 Категории требований по приказу № 3757" if lang == "ru" else "📋 Decree 3757 Categories"),
                callback_data="notary_tpl:categories"
            )
        ],
        [
            InlineKeyboardButton(
                text="✍️ Erkin bayon qilish" if lang == "uz" else ("✍️ Свой запрос" if lang == "ru" else "✍️ Custom Details"),
                callback_data="notary_tpl:free"
            ),
            InlineKeyboardButton(
                text="🏠 Bosh menyu" if lang == "uz" else ("🏠 Главное меню" if lang == "ru" else "🏠 Main Menu"),
                callback_data="notary_nav:main"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_notary_categories_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """3757-sonli qaror bo'yicha asosiy talablar yo'nalishi"""
    kb = [
        [
            InlineKeyboardButton(
                text="💰 Qarz shartnomasi / Tilxat" if lang == "uz" else ("💰 Договор займа / Расписка" if lang == "ru" else "💰 Loan Agreement / Note"),
                callback_data="notary_sub:debt"
            ),
            InlineKeyboardButton(
                text="🏢 Yetkazib berish / Xizmatlar" if lang == "uz" else ("🏢 Поставка / Услуги" if lang == "ru" else "🏢 Supply / Services"),
                callback_data="notary_sub:supply"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏠 Bino va joylar ijarasi" if lang == "uz" else ("🏠 Аренда помещений" if lang == "ru" else "🏠 Property Lease"),
                callback_data="notary_sub:rent"
            ),
            InlineKeyboardButton(
                text="⚡️ Kommunal va servis to'lovlari" if lang == "uz" else ("⚡️ Коммунальные платежи / УК" if lang == "ru" else "⚡️ Utility & Service Fees"),
                callback_data="notary_sub:utilities"
            )
        ],
        [
            InlineKeyboardButton(
                text="🚗 Lizing va transport" if lang == "uz" else ("🚗 Лизинг и транспорт" if lang == "ru" else "🚗 Leasing & Transport"),
                callback_data="notary_sub:leasing"
            ),
            InlineKeyboardButton(
                text="🏦 Bank krediti / Mikroqarz" if lang == "uz" else ("🏦 Кредит / Микрозайм" if lang == "ru" else "🏦 Bank Credit / Loan"),
                callback_data="notary_sub:credit"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Orqaga" if lang == "uz" else ("🔙 Назад" if lang == "ru" else "🔙 Back"),
                callback_data="notary_nav:menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_notary_guidance_text(subtype: str, lang: str = "uz") -> str:
    """Notarius ijro xati bo'yicha yo'riqnoma va ma'lumot so'rash matni"""
    if lang == "ru":
        return (
            "📜 <b>Получение исполнительной надписи нотариуса (Приказ Минюста № 3757):</b>\n\n"
            "Пожалуйста, прикрепите файл договора/акта сверки (Word или PDF) либо напишите следующие данные в сообщении:\n"
            "1. <b>Взыскатель:</b> Ваше Ф.И.О. или название фирмы, ИНН/ПИНФЛ, банковские реквизиты, телефон;\n"
            "2. <b>Должник:</b> Ф.И.О. или название организации должника, ИНН/ПИНФЛ, адрес, контакты;\n"
            "3. <b>Основание требования:</b> Номер и дата договора/расписки, наличие акта сверки взаиморасчетов, счетов-фактур;\n"
            "4. <b>Сумма долга:</b> Размер основного долга и период просрочки;\n"
            "5. <b>Нотариальный округ:</b> Город или район, где находится нотариус или должник.\n\n"
            "✍️ <i>Отправьте файл или текст — бот сформирует полный официальный комплект: Заявление нотариусу + Предупреждение должнику + Проект исполнительной надписи!</i>"
        )
    elif lang == "en":
        return (
            "📜 <b>Notarial Execution Inscription (Ministry of Justice Decree No. 3757):</b>\n\n"
            "Please upload your contract/reconciliation act (Word or PDF) or enter details in text:\n"
            "1. <b>Creditor:</b> Name / Company, Tax ID, address, bank details, phone;\n"
            "2. <b>Debtor:</b> Name / Company, Tax ID, address;\n"
            "3. <b>Grounds:</b> Agreement date & number, invoices, reconciliation act;\n"
            "4. <b>Debt Amount:</b> Principal debt amount and overdue period;\n"
            "5. <b>Notary Jurisdiction:</b> City or district.\n\n"
            "✍️ <i>Send details now — AI will draft the complete enforcement package ready for the Notary Public and MIB!</i>"
        )
    else:
        return (
            "📜 <b>Notariusdan Ijro xati olish (Adliya vazirligining 3757-sonli qarori asosida):</b>\n\n"
            "Iltimos, mavjud shartnoma, solishtirma dalolatnoma (акт сверки), tilxat yoki hisob-fakturalarni Word/PDF shaklda yuboring yoki quyidagilarni yozing:\n"
            "1. <b>Undiruvchi (Kreditor):</b> Korxona nomi yoki F.I.Sh., STIR/JShShIR, bank hisob-raqami, manzil va telefon;\n"
            "2. <b>Qarzdor:</b> Korxona nomi yoki F.I.Sh., STIR/JShShIR, yuridik yoki yashash manzili;\n"
            "3. <b>Talab asosi:</b> Shartnoma sanasi va raqami, o'zaro hisob-kitob solishtiruvi dalolatnomasi, qarz tan olinganligi;\n"
            "4. <b>Qarzdorlik summasi:</b> Asosiy qarz va shartnomaviy foiz/penya miqdori;\n"
            "5. <b>Notarial okrug:</b> Qaysi tuman/shahar notariusi (agar ma'lum bo'lsa).\n\n"
            "✍️ <i>Faylni yuboring yoki ma'lumotlarni yozing — bot darhol Notariusga ariza, Qarzdorga 10 kunlik rasmiy ogohlantirish xati va Ijro xati loyihasini to'liq tayyorlab beradi!</i>"
        )


def get_legal_result_keyboard(res_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Tayyorlangan da'vo arizasi / talabnoma tagidagi boshqaruv va yuklab olish tugmalari"""
    voice_label = "🎙 Ovozli eshitish" if lang == "uz" else ("🎙 Озвучить (TTS)" if lang == "ru" else "🎙 Voice (TTS)")
    edit_label = "✏️ Tahrirlash / Fakt qo'shish" if lang == "uz" else ("✏️ Редактировать / Дополнить" if lang == "ru" else "✏️ Edit / Add Facts")
    new_label = "⚖️ Yangi ariza tuzish" if lang == "uz" else ("⚖️ Новый иск / Претензия" if lang == "ru" else "⚖️ New Lawsuit / Claim")
    more_label = "📂 Boshqa formatlar" if lang == "uz" else ("📂 Другие форматы" if lang == "ru" else "📂 More Formats")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 Word (.docx) yuklab olish", callback_data=f"tr:docx:{res_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf) yuklab olish", callback_data=f"tr:pdf:{res_id}")
            ],
            [
                InlineKeyboardButton(text=voice_label, callback_data=f"tr:voice:{res_id}"),
                InlineKeyboardButton(text=edit_label, callback_data=f"act:ed:{res_id}")
            ],
            [
                InlineKeyboardButton(text=more_label, callback_data=f"act:tr:{res_id}"),
                InlineKeyboardButton(text=new_label, callback_data="legal_nav:menu")
            ]
        ]
    )


def get_legal_guidance_text(subtype: str, lang: str = "uz") -> str:
    """Da'vo arizasi va talabnoma turlari bo'yicha yo'riqnoma va so'rov matni"""
    if subtype in ("debt_cert", "debt_certificate", "malumotnoma"):
        if lang == "ru":
            return (
                "📜 <b>Справка о задолженности + Сопроводительное письмо:</b>\n\n"
                "Пожалуйста, отправьте мне файл (договор, акт сверки, накладные, счет-фактуры в Word/PDF) или напишите данные текстом:\n"
                "1. <b>Кредитор:</b> Наименование вашей организации, ИНН, банковские реквизиты, Ф.И.О. руководителя и главного бухгалтера;\n"
                "2. <b>Должник / Адресат:</b> Наименование суда или организации должника, ИНН, адрес;\n"
                "3. <b>Основание долга:</b> Номер и дата договора, накладных, акта сверки взаиморасчетов;\n"
                "4. <b>Сумма долга:</b> Основной долг, период просрочки, расчет пени;\n"
                "5. <b>Исходящий номер и дата</b> (если необходимо).\n\n"
                "✍️ <i>Отправьте файл или опишите ситуацию — бот сформирует полный официальный комплект: Сопроводительное письмо + Справку о задолженности с таблицей и подписями!</i>"
            )
        elif lang == "en":
            return (
                "📜 <b>Certificate of Indebtedness + Formal Transmittal/Cover Letter:</b>\n\n"
                "Please upload your contract, reconciliation act, or invoices (Word/PDF) or enter details:\n"
                "1. <b>Creditor:</b> Company name, Tax ID, bank details, Director & Chief Accountant names;\n"
                "2. <b>Debtor / Recipient:</b> Court or debtor organization details;\n"
                "3. <b>Basis:</b> Contract/agreement numbers and dates, invoice references;\n"
                "4. <b>Debt Amount:</b> Principal debt, overdue period, penalty calculation;\n"
                "5. <b>Outgoing letter reference & date</b>.\n\n"
                "✍️ <i>Upload file or send details — AI will prepare a complete package: Cover Letter + Accounting Certificate of Debt!</i>"
            )
        else:
            return (
                "📜 <b>Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati shakllantirish:</b>\n\n"
                "Iltimos, mavjud shartnoma, solishtirma dalolatnoma (акт сверки), hisob-fakturalarni Word yoki PDF formatda yuboring yoki quyidagilarni yozing:\n"
                "1. <b>Kreditor (Talab qiluvchi):</b> Tashkilotingiz nomi, STIR (INN), hisob-raqami, bank nomi, Rahbar va Bosh buxgalter F.I.Sh.;\n"
                "2. <b>Kimga (Qarzdor / Sud):</b> Qarzdor korxona nomi (yoki tegishli sud nomi), manzili, STIR;\n"
                "3. <b>Asos hujjatlar:</b> Shartnoma raqami va sanasi, o'zaro hisob-kitob solishtiruvi dalolatnomasi, schyot-fakturalar;\n"
                "4. <b>Qarzdorlik miqdori:</b> Asosiy qarz summasi, to'lov muddati o'tgan kunlar va penya hisob-kitobi;\n"
                "5. <b>Chiquvchi xat raqami va sana</b> (agar mavjud bo'lsa).\n\n"
                "✍️ <i>Faylni yuboring yoki ma'lumotlarni yozing — bot 2 ta rasmiy hujjat: Ilova xati + Qarzdorlik ma'lumotnomasini jadvallari va imzolari bilan tayyorlab beradi!</i>"
            )

    elif subtype in ("lawsuit", "debt"):
        if lang == "ru":
            return (
                "🏛 <b>Составление Искового заявления в суд (Взыскание долга):</b>\n\n"
                "Пожалуйста, отправьте мне факты вашей ситуации текстом или голосовым сообщением (или прикрепите фото/документ расписки/договора):\n"
                "1. <b>Истец:</b> Ваше Ф.И.О. или название фирмы, адрес, телефон;\n"
                "2. <b>Ответчик:</b> Ф.И.О. или название организации должника, адрес, контакты;\n"
                "3. <b>Сумма долга:</b> Размер основного долга и желаемый размер неустойки/пени;\n"
                "4. <b>Основание:</b> Расписка, договор займа, платежное поручение, дата и срок возврата;\n"
                "5. <b>Суд:</b> Город или район суда (если известно).\n\n"
                "✍️ <i>Опишите детали в одном сообщении — AI составит иск со всеми нормами права и расчетом госпошлины!</i>"
            )
        elif lang == "en":
            return (
                "🏛 <b>Court Lawsuit / Statement of Claim (Debt Recovery):</b>\n\n"
                "Please describe your case (or upload a contract/agreement file):\n"
                "1. <b>Plaintiff:</b> Your name / company, address, phone;\n"
                "2. <b>Defendant:</b> Debtor's name / company, address;\n"
                "3. <b>Claim Amount:</b> Principal debt + penalties/interest;\n"
                "4. <b>Legal Grounds:</b> Promissory note, loan agreement, invoice dates;\n"
                "5. <b>Court:</b> Jurisdiction / court district (if known).\n\n"
                "✍️ <i>Send the details now — AI will generate an official court claim ready for filing!</i>"
            )
        else:
            return (
                "🏛 <b>Sudga Da'vo arizasi tuzish (Qarz va penya undirish):</b>\n\n"
                "Iltimos, vaziyatingiz bo'yicha ma'lumotlarni yozing (yoki tilxat/shartnoma rasmi yoki faylini yuboring):\n"
                "1. <b>Da'vogar:</b> Ism-familiyangiz yoki korxona nomi, manzil va telefon raqami;\n"
                "2. <b>Javobgar:</b> Qarzdorning ismi yoki tashkilot nomi, manzili;\n"
                "3. <b>Qarz summasi:</b> Asosiy qarz va hisoblangan penya/foiz miqdori;\n"
                "4. <b>Asos:</b> Qarz tilxati, qarz shartnomasi yoki bank o'tkazmasi sanasi va muddati;\n"
                "5. <b>Sud:</b> Qaysi tuman/shahar sudi (agar ma'lum bo'lsa).\n\n"
                "✍️ <i>Barchasini bitta xabarda erkin bayon qiling yoki shartnoma faylini jo'nating!</i>"
            )

    elif subtype in ("demand", "demand_debt", "demand_quality"):
        if lang == "ru":
            return (
                "📑 <b>Составление Досудебной Претензии (Требования):</b>\n\n"
                "Пожалуйста, отправьте данные для оформления претензии:\n"
                "1. <b>Кому (Должник):</b> Ф.И.О. или название фирмы-должника, адрес, контакты;\n"
                "2. <b>От кого (Кредитор):</b> Ваши реквизиты, расчетный счет/карта для возврата;\n"
                "3. <b>Суть нарушения:</b> По какому договору/счету не поступила оплата или поставлен некачественный товар;\n"
                "4. <b>Сумма требования:</b> Сколько подлежит возврату;\n"
                "5. <b>Срок для ответа:</b> Сколько дней вы даете на добровольное погашение (например: 10 дней).\n\n"
                "✍️ <i>Отправьте сообщение — будет сформирована официальная претензия!</i>"
            )
        elif lang == "en":
            return (
                "📑 <b>Formal Demand Letter (Pre-trial Notice of Claim):</b>\n\n"
                "Please provide the demand letter details:\n"
                "1. <b>Debtor / Recipient:</b> Name, company, address;\n"
                "2. <b>Creditor / Sender:</b> Your details and payment account;\n"
                "3. <b>Breach:</b> Specific agreement, unpaid invoices, or defective products;\n"
                "4. <b>Amount Demanded:</b> Outstanding balance;\n"
                "5. <b>Grace Period:</b> Number of days given to cure default (e.g. 10 days).\n\n"
                "✍️ <i>Send your details now!</i>"
            )
        else:
            return (
                "📑 <b>Sudgacha Talabnoma (Pretenziya) tuzish:</b>\n\n"
                "Iltimos, talabnoma uchun quyidagi ma'lumotlarni yozib yuboring:\n"
                "1. <b>Kimga (Qarzdor):</b> Qarzdor shaxs yoki korxona nomi, manzili;\n"
                "2. <b>Kimdan (Kreditor):</b> Sizning F.I.Sh. yoki korxona nomi, pul o'tkazish rekvizitlari;\n"
                "3. <b>Buzilgan majburiyat:</b> Qaysi shartnoma yoki mahsulot bo'yicha to'lov to'lanmadi?\n"
                "4. <b>Talab summasi:</b> Qancha summa to'lanishi talab qilinadi?\n"
                "5. <b>Muddat:</b> Ixtiyoriy to'lash uchun necha kun muddat bermoqchisiz (odatda 7-10 kun)?\n\n"
                "✍️ <i>Ma'lumotlarni yozib yuboring!</i>"
            )

    elif subtype == "contract":
        if lang == "ru":
            return (
                "🏢 <b>Иск по нарушению договора и взысканию неустойки:</b>\n\n"
                "1. Стороны договора (Истец и Ответчик);\n"
                "2. Номер и дата договора, предмет сделки (поставка, подряд, аренда, услуги);\n"
                "3. Какие пункты договора нарушены (не поставлен товар, не выполнена работа, не оплачены услуги);\n"
                "4. Сумма долга, расчет пени и ущерба.\n\n"
                "✍️ <i>Отправьте информацию или файл договора!</i>"
            )
        elif lang == "en":
            return (
                "🏢 <b>Breach of Contract Lawsuit:</b>\n\n"
                "1. Parties to the contract;\n"
                "2. Contract number, date, subject (sales, lease, services, construction);\n"
                "3. Breached clauses and damages;\n"
                "4. Demanded sum and contractual penalties.\n\n"
                "✍️ <i>Send the facts or contract document!</i>"
            )
        else:
            return (
                "🏢 <b>Shartnoma majburiyatlarini buzganlik bo'yicha Da'vo arizasi:</b>\n\n"
                "1. Shartnoma taraflari (Da'vogar va Javobgar rekvizitlari);\n"
                "2. Shartnoma raqami, sanasi va predmeti (oldi-sotdi, ijara, xizmat ko'rsatish, pudrat);\n"
                "3. Qaysi shartlar buzildi (tovar yetkazilmadi, ish qilinmadi yoki puli to'lanmadi);\n"
                "4. Qarzdorlik summasi va shartnomada belgilangan penya foizi.\n\n"
                "✍️ <i>Ma'lumotlarni yozing yoki shartnoma faylini yuboring!</i>"
            )

    elif subtype == "alimony":
        if lang == "ru":
            return (
                "👨‍👩‍👦 <b>Исковое заявление о взыскании алиментов:</b>\n\n"
                "1. Истец и Ответчик (Ф.И.О., адреса, телефоны);\n"
                "2. Дата заключения брака (или свидетельство о расторжении);\n"
                "3. Ф.И.О. и даты рождения несовершеннолетних детей;\n"
                "4. С какого момента ответчик уклоняется от содержания детей;\n"
                "5. Требование: размер алиментов (1/4 на одного ребенка, 1/3 на двоих или в твердой сумме).\n\n"
                "✍️ <i>Отправьте данные для составления иска!</i>"
            )
        elif lang == "en":
            return (
                "👨‍👩‍👦 <b>Child Support / Alimony Claim:</b>\n\n"
                "1. Plaintiff and Defendant details;\n"
                "2. Marriage / divorce certificate details;\n"
                "3. Children full names and dates of birth;\n"
                "4. Requested monthly support share or fixed sum.\n\n"
                "✍️ <i>Send the details to draft the petition!</i>"
            )
        else:
            return (
                "👨‍👩‍👦 <b>Aliment undirish to'g'risida Da'vo arizasi:</b>\n\n"
                "1. Da'vogar va Javobgar (F.I.Sh., yashash manzili, telefonlari);\n"
                "2. Nikoh tuzilgan sana (yoki nikohni bekor qilish haqidagi guvohnoma);\n"
                "3. Voyaga yetmagan farzandlarning F.I.Sh. va tug'ilgan sanalari;\n"
                "4. Javobgar qachondan beri moddiy yordam bermayotgani;\n"
                "5. Talab: Aliment miqdori (1 bola uchun 1/4 qism, 2 bola uchun 1/3 qism yoki qat'iy summada).\n\n"
                "✍️ <i>Ma'lumotlarni yozib yuboring!</i>"
            )

    elif subtype == "divorce":
        if lang == "ru":
            return (
                "💔 <b>Исковое заявление о расторжении брака:</b>\n\n"
                "1. Истец и Ответчик (Ф.И.О., адреса, телефоны);\n"
                "2. Дата и орган ЗАГС регистрации брака;\n"
                "3. Наличие несовершеннолетних детей;\n"
                "4. Причины расторжения брака (не сошлись характерами, совместное хозяйство не ведется);\n"
                "5. Наличие имущественных споров.\n\n"
                "✍️ <i>Отправьте информацию для составления иска!</i>"
            )
        elif lang == "en":
            return (
                "💔 <b>Divorce Petition:</b>\n\n"
                "1. Spouses full names, addresses;\n"
                "2. Date and place of marriage registration;\n"
                "3. Minor children info;\n"
                "4. Grounds for marriage dissolution.\n\n"
                "✍️ <i>Send details to draft the divorce petition!</i>"
            )
        else:
            return (
                "💔 <b>Nikohni bekor qilish to'g'risida Da'vo arizasi:</b>\n\n"
                "1. Da'vogar va Javobgar (F.I.Sh., manzillari, telefonlari);\n"
                "2. Nikoh qachon va qaysi FHDYo bo'limida qayd etilgan;\n"
                "3. Voyaga yetmagan farzandlar bormi yoki yo'qmi;\n"
                "4. Oilaning ajralish sababi (birga yashamaslik, xarakterlar to'g'ri kelmasligi);\n"
                "5. Mol-mulk taqsimoti bo'yicha da'vo bormi?\n\n"
                "✍️ <i>Ma'lumotlarni yozib yuboring!</i>"
            )

    elif subtype == "labor":
        if lang == "ru":
            return (
                "💼 <b>Трудовой спор (Восстановление на работе, взыскание зарплаты):</b>\n\n"
                "1. Работодатель: название предприятия, адрес;\n"
                "2. Работник: ваше Ф.И.О., должность, стаж работы;\n"
                "3. Дата и основание незаконного увольнения (номер приказа) или период невыплаты зарплаты;\n"
                "4. Сумма невыплаченной заработной платы и компенсации.\n\n"
                "✍️ <i>Отправьте информацию или файл приказа/трудового договора!</i>"
            )
        elif lang == "en":
            return (
                "💼 <b>Labor Dispute (Wrongful termination / Unpaid wages):</b>\n\n"
                "1. Employer company name, address;\n"
                "2. Employee name, position, employment period;\n"
                "3. Termination date and grounds, or unpaid wage period;\n"
                "4. Total unpaid wages, compensation demanded.\n\n"
                "✍️ <i>Send the facts or employment contract!</i>"
            )
        else:
            return (
                "💼 <b>Mehnat nizosi (Ishga tiklash va oylik maosh undirish):</b>\n\n"
                "1. Ish beruvchi: Korxona/tashkilot nomi, manzili;\n"
                "2. Xodim: F.I.Sh., egallagan lavozimi, ishlagan davri;\n"
                "3. Ishdan bo'shatish sanasi va asosi (buyruq raqami) yoki oylik to'lanmagan oylar;\n"
                "4. Undirilishi so'ralayotgan oylik maosh summasi va ma'naviy zarar miqdori.\n\n"
                "✍️ <i>Ma'lumotlarni yozing yoki mehnat shartnomasi/buyruq faylini yuboring!</i>"
            )

    elif subtype == "damage":
        if lang == "ru":
            return (
                "🚗 <b>Иск о возмещении материального и морального ущерба:</b>\n\n"
                "1. Истец и Ответчик (виновник ущерба);\n"
                "2. Обстоятельства происшествия (ДТП, залив квартиры, повреждение имущества);\n"
                "3. Документы, подтверждающие вину (протокол ГАИ, акт управляющей компании);\n"
                "4. Размер ущерба (сумма по оценке эксперта или чекам ремонта).\n\n"
                "✍️ <i>Отправьте детали для составления иска!</i>"
            )
        elif lang == "en":
            return (
                "🚗 <b>Property Damage / Tort Claim:</b>\n\n"
                "1. Plaintiff and Defendant info;\n"
                "2. Incident details (traffic accident, water flooding, property damage);\n"
                "3. Police or technical incident report;\n"
                "4. Assessed damages amount and repair estimates.\n\n"
                "✍️ <i>Send incident details!</i>"
            )
        else:
            return (
                "🚗 <b>Yetkazilgan moddiy va ma'naviy zararni qoplash da'vosi:</b>\n\n"
                "1. Da'vogar (jabrlanuvchi) va Javobgar (zarar yetkazuvchi shaxs);\n"
                "2. Hodisa tafsilotlari (YTH / avariya, xonadonni suv bosishi, mulkka shikast yetkazish);\n"
                "3. Aybni tasdiqlovchi hujjat (YPX bayonnomasi, MIB qarori, dalolatnoma);\n"
                "4. Yetkazilgan moddiy zarar summasi (ekspert baholash xulosasi yoki ta'mirlash xarajatlari).\n\n"
                "✍️ <i>Ma'lumotlarni yozib yuboring!</i>"
            )

    else:
        if lang == "ru":
            return (
                "⚖️ <b>Составление Искового заявления или Претензии:</b>\n\n"
                "Пожалуйста, подробно опишите вашу ситуацию текстом или голосовым сообщением (или отправьте файл документа):\n"
                "• Кто вы и кто ответчик / должник?\n"
                "• Что произошло, какие права или договоренности нарушены?\n"
                "• Какую сумму или требования вы хотите заявить?\n\n"
                "✍️ <i>AI подготовит официальный юридический документ со всеми статьями закона!</i>"
            )
        elif lang == "en":
            return (
                "⚖️ <b>Legal Lawsuit / Demand Letter Drafting:</b>\n\n"
                "Please describe your situation in detail:\n"
                "• Parties involved (Plaintiff and Defendant / Debtor);\n"
                "• Chronological facts and breached obligations;\n"
                "• Specific claims and relief sought.\n\n"
                "✍️ <i>AI will generate a complete legal document compliant with court standards!</i>"
            )
        else:
            return (
                "⚖️ <b>Sudga Da'vo arizasi yoki Talabnoma tuzish:</b>\n\n"
                "Iltimos, vaziyatingizni batafsil yozib yuboring (yoki ovozli xabar/hujjat jo'nating):\n"
                "• Kim da'vogar va kim javobgar / qarzdor?\n"
                "• Nima voqea sodir bo'lgan, qaysi huquq yoki shartnoma buzilgan?\n"
                "• Qancha summa yoki qanday talab qo'ymoqchisiz?\n\n"
                "✍️ <i>Sun'iy intellekt barcha qonuniy moddalar, davlat boji hisobi va ilovalari bilan rasmiy hujjat tuzib beradi!</i>"
            )


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


def get_audio_choice_keyboard(audio_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Audio tahlil va javob formatini tanlash tugmalari"""
    text_label = "📝 Matn" if lang == "uz" else ("📝 Текст" if lang == "ru" else "📝 Text")
    voice_label = "🎙 Ovozli (Audio)" if lang == "uz" else ("🎙 Голос (Аудио)" if lang == "ru" else "🎙 Voice (Audio)")
    all_label = "✨ Barchasi birga (Hammasi)" if lang == "uz" else ("✨ Все форматы вместе" if lang == "ru" else "✨ All Formats Together")
    cancel_label = "❌ Bekor qilish" if lang == "uz" else ("❌ Отмена" if lang == "ru" else "❌ Cancel")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text_label, callback_data=f"aud_mode:text:{audio_id}"),
                InlineKeyboardButton(text=voice_label, callback_data=f"aud_mode:voice:{audio_id}")
            ],
            [
                InlineKeyboardButton(text="📄 Word (.docx)", callback_data=f"aud_mode:docx:{audio_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf)", callback_data=f"aud_mode:pdf:{audio_id}")
            ],
            [
                InlineKeyboardButton(text=all_label, callback_data=f"aud_mode:all:{audio_id}")
            ],
            [
                InlineKeyboardButton(text=cancel_label, callback_data=f"aud_mode:cancel:{audio_id}")
            ]
        ]
    )


def get_video_options_keyboard(video_path: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Video yuklanganda dumaloq qilish yoki MP3 audio ajratish"""
    v_id = uuid.uuid4().hex[:8]
    pending_audio[f"vid_{v_id}"] = {"path": video_path}
    circle_label = "🔘 Dumaloq video qilish" if lang == "uz" else ("🔘 Сделать видео-кружочек" if lang == "ru" else "🔘 Convert to Video Note")
    audio_label = "🎵 MP3 audio ajratish" if lang == "uz" else ("🎵 Извлечь MP3 аудио" if lang == "ru" else "🎵 Extract MP3 Audio")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=circle_label, callback_data=f"vid:circle:{v_id}"),
                InlineKeyboardButton(text=audio_label, callback_data=f"vid:audio:{v_id}")
            ]
        ]
    )


def get_archive_list_keyboard(items: list, page: int, total_count: int, per_page: int = 5, lang: str = "uz") -> InlineKeyboardMarkup:
    """Arxiv ro'yxati va sahifalash klaviaturasi"""
    kb = []
    category_icons = {
        "doc": "📑",
        "legal": "⚖️",
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
    prev_label = "⬅️ Oldingi" if lang == "uz" else ("⬅️ Назад" if lang == "ru" else "⬅️ Prev")
    next_label = "Keyingi ➡️" if lang == "uz" else ("Вперед ➡️" if lang == "ru" else "Next ➡️")

    if page > 0:
        nav_row.append(InlineKeyboardButton(text=prev_label, callback_data=f"arx:p:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="arx:noop"))
    if (page + 1) * per_page < total_count:
        nav_row.append(InlineKeyboardButton(text=next_label, callback_data=f"arx:p:{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    # Boshqaruv tugmalari
    clear_label = "🗑 Barcha arxivni tozalash" if lang == "uz" else ("🗑 Очистить весь архив" if lang == "ru" else "🗑 Clear Entire Archive")
    refresh_label = "🔄 Yangilash" if lang == "uz" else ("🔄 Обновить" if lang == "ru" else "🔄 Refresh")
    home_label = "🏠 Bosh menyu" if lang == "uz" else ("🏠 Главное меню" if lang == "ru" else "🏠 Main Menu")

    if total_count > 0:
        kb.append([
            InlineKeyboardButton(text=clear_label, callback_data="arx:confirm_clear")
        ])
    kb.append([
        InlineKeyboardButton(text=refresh_label, callback_data=f"arx:p:{page}"),
        InlineKeyboardButton(text=home_label, callback_data="arx:home")
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_archive_item_keyboard(item_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Tanlangan bitta arxiv yozuvini ko'rish va boshqarish tugmalari"""
    read_label = "📖 To'liq matnni o'qish" if lang == "uz" else ("📖 Читать полностью" if lang == "ru" else "📖 Read Full Text")
    voice_label = "🎙 Ovozli qilish" if lang == "uz" else ("🎙 Озвучить" if lang == "ru" else "🎙 Voice (TTS)")
    trans_label = "🌐 Boshqa tilga tarjima" if lang == "uz" else ("🌐 Перевести на другой язык" if lang == "ru" else "🌐 Translate")
    tr_label = "📂 Bo'limga o'tkazish" if lang == "uz" else ("📂 В другие форматы" if lang == "ru" else "📂 Transfer")
    ed_label = "✏️ Tahrirlash" if lang == "uz" else ("✏️ Редактировать" if lang == "ru" else "✏️ Edit")
    del_label = "🗑 Ushbu so'rovni o'chirish" if lang == "uz" else ("🗑 Удалить этот запрос" if lang == "ru" else "🗑 Delete This Item")
    back_label = "🔙 Arxiv ro'yxatiga qaytish" if lang == "uz" else ("🔙 Назад к списку" if lang == "ru" else "🔙 Back to List")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=read_label, callback_data=f"arx:read:{item_id}")
            ],
            [
                InlineKeyboardButton(text="📄 Word (.docx)", callback_data=f"tr:docx:{item_id}"),
                InlineKeyboardButton(text="📑 PDF (.pdf)", callback_data=f"tr:pdf:{item_id}")
            ],
            [
                InlineKeyboardButton(text="📊 Excel (.xlsx)", callback_data=f"tr:xlsx:{item_id}"),
                InlineKeyboardButton(text="📽 Prezentatsiya (.pptx)", callback_data=f"tr:pptx:{item_id}")
            ],
            [
                InlineKeyboardButton(text=voice_label, callback_data=f"tr:voice:{item_id}"),
                InlineKeyboardButton(text=trans_label, callback_data=f"tr:trans:{item_id}")
            ],
            [
                InlineKeyboardButton(text=tr_label, callback_data=f"act:tr:{item_id}"),
                InlineKeyboardButton(text=ed_label, callback_data=f"act:ed:{item_id}")
            ],
            [
                InlineKeyboardButton(text=del_label, callback_data=f"arx:confirm_del:{item_id}")
            ],
            [
                InlineKeyboardButton(text=back_label, callback_data="arx:p:0")
            ]
        ]
    )


def get_archive_delete_confirm_keyboard(item_id: str, lang: str = "uz") -> InlineKeyboardMarkup:
    """Yozuvni o'chirishni tasdiqlash klaviaturasi"""
    yes_label = "✅ Ha, o'chirilsin" if lang == "uz" else ("✅ Да, удалить" if lang == "ru" else "✅ Yes, delete")
    no_label = "❌ Bekor qilish" if lang == "uz" else ("❌ Отмена" if lang == "ru" else "❌ Cancel")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"arx:do_del:{item_id}"),
                InlineKeyboardButton(text=no_label, callback_data=f"arx:view:{item_id}")
            ]
        ]
    )


def get_archive_clear_confirm_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    """Butun arxivni tozalashni tasdiqlash klaviaturasi"""
    yes_label = "🗑 Ha, barchasini tozalash" if lang == "uz" else ("🗑 Да, очистить всё" if lang == "ru" else "🗑 Yes, clear all")
    no_label = "❌ Bekor qilish" if lang == "uz" else ("❌ Отмена" if lang == "ru" else "❌ Cancel")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data="arx:do_clear"),
                InlineKeyboardButton(text=no_label, callback_data="arx:p:0")
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
    if lang is None:
        u_id = message.from_user.id if message.from_user else 0
        lang = get_user_lang(u_id) if u_id else "uz"

    if not response_text:
        await message.reply(
            "Javob bo'sh qaytdi." if lang == "uz" else
            ("Ответ пустой." if lang == "ru" else "Empty response.")
        )
        return

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


@router.message(F.text.in_(get_all_button_variants("btn_legal")))
@router.message(Command("davo"))
@router.message(Command("legal"))
@router.message(Command("talabnoma"))
async def nav_legal_menu(message: Message):
    """⚖️ Sudga da'vo va talabnoma bo'limi"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "LEGAL_DOC:upload"
    lang = get_user_lang(user_id)
    await message.answer(
        t("legal_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )
    prompt_txt = (
        "⚖️ <b>Sudga Da'vo arizasi va Talabnoma yaratish markazi:</b>\n\n"
        "📎 Sizda tayyor shartnoma, tilxat yoki dalolatnoma bormi? <b>Faylni (Word .docx yoki PDF .pdf) to'g'ridan-to'g'ri shu yerga yuboring!</b> Bot shartnoma shartlarini to'liq o'rganib, rasmiy da'vo arizasi yoki talabnoma tayyorlab beradi.\n\n"
        "👇 <b>Quyidagi yo'nalishlardan birini tanlang, shartnoma faylini (Word, PDF) yuboring yoki vaziyatingizni yozing:</b>"
        if lang == "uz" else
        ("⚖️ <b>Центр составления Исковых заявлений и Претензий:</b>\n\n"
         "📎 У вас есть готовый договор, акт или расписка? <b>Отправьте файл (Word .docx или PDF .pdf) прямо сюда!</b> Бот изучит условия сделки и подготовит готовый иск в суд или претензию.\n\n"
         "👇 <b>Выберите категорию, отправьте файл договора (Word, PDF) или напишите детали:</b>"
         if lang == "ru" else
         "⚖️ <b>Court Lawsuits & Demand Letters Center:</b>\n\n"
         "📎 Do you have an existing agreement, promissory note, or contract? <b>Simply send the file (Word .docx or PDF .pdf) here!</b> AI will analyze clauses and draft an official lawsuit or demand letter.\n\n"
         "👇 <b>Select an option below, upload your contract (Word, PDF), or describe your case:</b>")
    )
    await message.answer(
        prompt_txt,
        parse_mode="HTML",
        reply_markup=get_legal_menu_keyboard(lang=lang)
    )


@router.callback_query(F.data.startswith("legal_type:"))
@router.callback_query(F.data.startswith("legal_tpl:"))
@router.callback_query(F.data.startswith("legal_nav:"))
async def handle_legal_callback(callback: CallbackQuery):
    """Yuridik bo'limning inline tugmalarini qayta ishlash"""
    data = callback.data
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    await callback.answer()

    if data == "legal_nav:menu":
        user_active_mode[user_id] = "LEGAL_DOC:upload"
        prompt_txt = (
            "⚖️ <b>Sudga Da'vo arizasi va Talabnoma tuzish markazi:</b>\n\n"
            "Mavjud shartnoma faylini (Word, PDF) yuboring yoki kerakli yo'nalishni tanlang:"
            if lang == "uz" else
            ("⚖️ <b>Центр составления Исковых заявлений и Претензий:</b>\n\n"
             "Отправьте файл договора (Word, PDF) или выберите направление:"
             if lang == "ru" else
             "⚖️ <b>Legal Lawsuits & Demand Letters:</b>\n\n"
             "Upload a contract file (Word, PDF) or select an area:")
        )
        try:
            await callback.message.edit_text(prompt_txt, parse_mode="HTML", reply_markup=get_legal_menu_keyboard(lang=lang))
        except Exception:
            await callback.message.answer(prompt_txt, parse_mode="HTML", reply_markup=get_legal_menu_keyboard(lang=lang))
        return

    if data == "legal_type:upload":
        user_active_mode[user_id] = "LEGAL_DOC:upload"
        upload_txt = (
            "📎 <b>Shartnoma yoki yuridik hujjat faylini yuboring (Word .docx, PDF .pdf):</b>\n\n"
            "Mavjud shartnoma, tilxat, dalolatnoma yoki kelishuv faylini botga jo'nating.\n\n"
            "💡 <b>Bot qanday yordam beradi?</b>\n"
            "1. Shartnoma taraflarini (Da'vogar va Javobgar rekvizitlari, STIR, JShShIR) aniqlaydi;\n"
            "2. Shartnoma predmeti, muddati va to'lov shartlarini o'rganadi;\n"
            "3. Muddati o'tgan qarz, penya yoki zararni hisoblaydi;\n"
            "4. Qonunchilik moddalari va davlat boji hisob-kitobi bilan sudga tayyor <b>Da'vo arizasi</b> yoki <b>Talabnoma</b> tayyorlaydi!\n\n"
            "👇 <i>Hozir shartnoma faylini (Word yoki PDF) shu yerga jo'nating:</i>"
            if lang == "uz" else
            ("📎 <b>Отправьте файл договора или документа (Word .docx, PDF .pdf):</b>\n\n"
             "Прикрепите и отправьте файл договора, расписки, соглашения или акта.\n\n"
             "💡 <b>Что сделает бот:</b>\n"
             "1. Определит стороны (Истец и Ответчик, реквизиты, ИНН, ПИНФЛ);\n"
             "2. Проанализирует условия сделки, сроки и порядок оплаты;\n"
             "3. Рассчитает сумму долга, штрафных санкций и убытков;\n"
             "4. Подготовит <b>Исковое заявление в суд</b> или <b>Претензию</b> со всеми нормами законов и расчетом госпошлины!\n\n"
             "👇 <i>Отправьте файл договора (Word или PDF) прямо сейчас:</i>"
             if lang == "ru" else
             "📎 <b>Upload your contract or legal document (Word .docx, PDF .pdf):</b>\n\n"
             "Upload your contract, agreement, or breach notice.\n\n"
             "💡 <b>What AI will do:</b>\n"
             "1. Extract Plaintiff and Defendant details;\n"
             "2. Analyze terms, obligations, and deadlines;\n"
             "3. Calculate outstanding debt, interest, and damages;\n"
             "4. Draft an official <b>Court Lawsuit</b> or <b>Pre-trial Demand Letter</b> ready for filing!\n\n"
             "👇 <i>Send your file (Word or PDF) now:</i>")
        )
        try:
            await callback.message.edit_text(upload_txt, parse_mode="HTML")
        except Exception:
            await callback.message.answer(upload_txt, parse_mode="HTML")
        return

    if data == "legal_type:templates":
        tpl_txt = (
            "📋 <b>Mashhur da'vo va talabnoma namunalari:</b>\n\nKerakli yo'nalishni tanlang:" if lang == "uz" else
            ("📋 <b>Популярные шаблоны исков и претензий:</b>\n\nВыберите нужное направление:" if lang == "ru" else
             "📋 <b>Popular Legal Templates:</b>\n\nSelect a litigation area:")
        )
        try:
            await callback.message.edit_text(tpl_txt, parse_mode="HTML", reply_markup=get_legal_templates_keyboard(lang=lang))
        except Exception:
            await callback.message.answer(tpl_txt, parse_mode="HTML", reply_markup=get_legal_templates_keyboard(lang=lang))
        return

    subtype = data.split(":", 1)[1]
    user_active_mode[user_id] = f"LEGAL_DOC:{subtype}"
    guide_text = get_legal_guidance_text(subtype, lang=lang)
    await callback.message.answer(guide_text, parse_mode="HTML")


@router.message(F.text.in_(get_all_button_variants("btn_notary")))
@router.message(Command("notarius"))
@router.message(Command("notary"))
@router.message(Command("ijroxati"))
@router.message(Command("ijro_xati"))
async def nav_notary_menu(message: Message):
    """📜 Notariusdan ijro xati olish bo'limi (3757-sonli qaror)"""
    user_id = message.from_user.id
    user_active_mode[user_id] = "NOTARY:both"
    lang = get_user_lang(user_id)
    await message.answer(
        t("notary_menu", lang),
        parse_mode="HTML",
        reply_markup=get_section_keyboard(lang=lang)
    )
    prompt_txt = (
        "📜 <b>Notariusdan Ijro xati olish bo'limi:</b>\n\n"
        "🏛 O'zbekiston Respublikasining <i>«Notariat to'g'risida»gi Qonuni</i> (76-80-moddalar) hamda Adliya vazirligi tomonidan tasdiqlangan <b>3757-sonli qarorga</b> muvofiq, notariuslar nizosiz pul undirish to'g'risida <b>Ijro xati</b> yozish huquqiga ega!\n\n"
        "⚡️ Notarius ijro xati sud muhokamasisiz to'g'ridan-to'g'ri <b>MIB (Majburiy ijro byurosi)</b> tomonidan ijro etiladigan qonuniy ijro hujjati hisoblanadi.\n\n"
        "📎 Mavjud shartnoma, akt, tilxat yoki hisob-faktura faylini (Word .docx yoki PDF .pdf) to'g'ridan-to'g'ri yuboring yoki quyidagi tugmalardan birini tanlang:"
        if lang == "uz" else
        ("📜 <b>Центр получения исполнительной надписи нотариуса:</b>\n\n"
         "🏛 В соответствии с Законом РУз <i>«О нотариате»</i> (статьи 76-80) и <b>Приказом Минюста № 3757</b>, нотариусы уполномочены совершать <b>исполнительные надписи</b> о взыскании бесспорной задолженности!\n\n"
         "⚡️ Исполнительная надпись является исполнительным документом прямого действия и исполняется органами <b>БПИ (МИБ)</b> без судебного разбирательства.\n\n"
         "📎 Отправьте файл договора, акта сверки или расписки (Word .docx или PDF .pdf) либо выберите действие:"
         if lang == "ru" else
         "📜 <b>Notarial Execution Inscription Center:</b>\n\n"
         "🏛 In accordance with the Law of Uzbekistan <i>«On Notaries»</i> (Articles 76-80) and <b>Ministry of Justice Decree No. 3757</b>, notaries issue <b>execution inscriptions</b> for undisputed debt recovery!\n\n"
         "⚡️ This notarial inscription is an immediate enforcement writ enforceable by the <b>Compulsory Enforcement Bureau (MIB)</b> without litigation.\n\n"
         "📎 Upload your contract or reconciliation file (Word or PDF), or select an option below:")
    )
    await message.answer(
        prompt_txt,
        parse_mode="HTML",
        reply_markup=get_notary_menu_keyboard(lang=lang)
    )


@router.callback_query(F.data.startswith("notary_act:"))
@router.callback_query(F.data.startswith("notary_tpl:"))
@router.callback_query(F.data.startswith("notary_sub:"))
@router.callback_query(F.data.startswith("notary_nav:"))
async def handle_notary_callback(callback: CallbackQuery):
    """Notarius ijro xati bo'limining inline tugmalarini qayta ishlash"""
    data = callback.data
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    await callback.answer()

    if data == "notary_nav:main":
        user_active_mode[user_id] = "NOTARY:both"
        await callback.message.answer(
            t("welcome", lang, name=callback.from_user.full_name),
            reply_markup=get_main_keyboard(user_id)
        )
        return

    if data == "notary_nav:menu":
        user_active_mode[user_id] = "NOTARY:both"
        prompt_txt = (
            "📜 <b>Notariusdan Ijro xati olish markazi (3757-sonli qaror):</b>\n\n"
            "Mavjud shartnoma yoki dalolatnoma faylini (Word, PDF) yuboring yoki kerakli bo'limni tanlang:"
            if lang == "uz" else
            ("📜 <b>Центр совершения исполнительной надписи нотариуса (Приказ № 3757):</b>\n\n"
             "Отправьте файл договора/акта (Word, PDF) или выберите пункт меню:"
             if lang == "ru" else
             "📜 <b>Notary Execution Inscription (Decree No. 3757):</b>\n\n"
             "Upload your contract/act file (Word, PDF) or select an option:")
        )
        try:
            await callback.message.edit_text(prompt_txt, parse_mode="HTML", reply_markup=get_notary_menu_keyboard(lang=lang))
        except Exception:
            await callback.message.answer(prompt_txt, parse_mode="HTML", reply_markup=get_notary_menu_keyboard(lang=lang))
        return

    if data == "notary_act:upload":
        user_active_mode[user_id] = "NOTARY:both"
        upload_txt = (
            "📎 <b>Shartnoma, tilxat yoki hisob-kitob dalolatnomasini yuboring (Word .docx, PDF .pdf):</b>\n\n"
            "Notariusdan ijro xati chiqarish uchun asos bo'ladigan shartnoma, tilxat, tovar-transport yukxati yoki o'zaro hisob-kitob solishtiruvi dalolatnomasini (акт сверки) yuboring.\n\n"
            "💡 <b>Bot nima qilib beradi?</b>\n"
            "1. Shartnoma va to'lov majburiyatini to'liq tekshiradi;\n"
            "2. Qarzdorlikning nizosizligini va 3757-sonli qaror ro'yxatiga muvofiqligini tasdiqlaydi;\n"
            "3. <b>Notariusga ijro xati yozish to'g'risida rasmiy Ariza</b> tuzadi;\n"
            "4. Qarzdorga yuborilishi shart bo'lgan <b>10 kunlik qat'iy Ogohlantirish xati</b>ni shakllantiradi;\n"
            "5. Notarius uchun tayyor <b>Ijro xati loyihasi</b>ni yaratib beradi!\n\n"
            "👇 <i>Hozir faylni (Word yoki PDF) shu yerga jo'nating:</i>"
            if lang == "uz" else
            ("📎 <b>Отправьте файл договора, расписки или акта сверки (Word .docx, PDF .pdf):</b>\n\n"
             "Отправьте документ, подтверждающий бесспорную задолженность согласно Приказу Минюста № 3757.\n\n"
             "💡 <b>Что сделает бот:</b>\n"
             "1. Проверит бесспорность задолженности и реквизиты сторон;\n"
             "2. Подготовит <b>Заявление нотариусу о совершении исполнительной надписи</b>;\n"
             "3. Оформит обязательное <b>Предупреждение должнику (срок 10 дней)</b>;\n"
             "4. Составит готовый <b>Проект исполнительной надписи</b> для нотариуса и БПИ (МИБ)!\n\n"
             "👇 <i>Отправьте файл прямо сейчас:</i>"
             if lang == "ru" else
             "📎 <b>Upload your contract, promissory note, or reconciliation act (Word, PDF):</b>\n\n"
             "Send the undisputed debt document pursuant to Decree No. 3757.\n\n"
             "💡 <b>AI will generate:</b>\n"
             "1. Formal <b>Application to Notary Public</b>;\n"
             "2. Mandatory <b>10-day Pre-enforcement Warning Notice to Debtor</b>;\n"
             "3. Official <b>Draft Execution Inscription</b> ready for enforcement by MIB!\n\n"
             "👇 <i>Send your file now:</i>")
        )
        try:
            await callback.message.edit_text(upload_txt, parse_mode="HTML")
        except Exception:
            await callback.message.answer(upload_txt, parse_mode="HTML")
        return

    if data == "notary_tpl:categories":
        cat_txt = (
            "📋 <b>Adliya vazirligi 3757-sonli qarori bo'yicha nizosiz talablar toifalari:</b>\n\n"
            "Quyidagi sohalardan birini tanlang va ma'lumotlarni yuboring:"
            if lang == "uz" else
            ("📋 <b>Категории бесспорных требований по Приказу Минюста № 3757:</b>\n\n"
             "Выберите категорию задолженности:"
             if lang == "ru" else
             "📋 <b>Categories of undisputed claims under Decree No. 3757:</b>\n\n"
             "Select a debt category:")
        )
        try:
            await callback.message.edit_text(cat_txt, parse_mode="HTML", reply_markup=get_notary_categories_keyboard(lang=lang))
        except Exception:
            await callback.message.answer(cat_txt, parse_mode="HTML", reply_markup=get_notary_categories_keyboard(lang=lang))
        return

    subtype = data.split(":", 1)[1]
    user_active_mode[user_id] = f"NOTARY:{subtype}"
    guide_text = get_notary_guidance_text(subtype, lang=lang)
    await callback.message.answer(guide_text, parse_mode="HTML")


@router.callback_query(F.data.startswith("legal_act:"))
async def handle_legal_action_callback(callback: CallbackQuery):
    """Yuklangan shartnoma bo'yicha harakat (da'vo, talabnoma, ikkalasi yoki tahlil) callbacki"""
    data = callback.data
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    await callback.answer()

    parts = data.split(":")
    if len(parts) < 3:
        return
    legal_id = parts[1]
    action = parts[2]

    pending = legal_pending_docs.get(legal_id)
    if not pending:
        await callback.message.answer(
            "❌ Hujjat keshdan o'chirilgan yoki muddati tugagan. Iltimos, faylni qayta yuboring." if lang == "uz" else
            ("❌ Документ устарел или не найден. Пожалуйста, отправьте файл снова." if lang == "ru" else
             "❌ Document expired. Please upload the file again.")
        )
        return

    if action == "analysis":
        status_msg = await callback.message.answer(
            "📊 <i>Shartnoma bo'limlari tahlil qilinmoqda...</i>" if lang == "uz" else
            ("📊 <i>Анализ разделов договора...</i>" if lang == "ru" else "📊 <i>Analyzing contract sections...</i>"),
            parse_mode="HTML"
        )
        doc_id = uuid.uuid4().hex[:8]
        filename = pending["filename"]
        extracted_text = pending["text"]
        sections = await ai_service.detect_document_sections(extracted_text, filename, lang=lang)
        pending_docs[doc_id] = {
            "user_id": user_id,
            "user_name": pending.get("user_name", "Foydalanuvchi"),
            "filename": filename,
            "text": extracted_text,
            "sections": sections,
            "caption": pending.get("caption", ""),
            "created_at": datetime.now()
        }
        try:
            await status_msg.delete()
        except Exception:
            pass
        await callback.message.answer(
            f"📄 <b>«{filename}» bo'limlari:</b>",
            parse_mode="HTML",
            reply_markup=get_document_sections_keyboard(doc_id, sections, lang=lang)
        )
        return

    status_msg = await callback.message.answer(
        "⚖️ <i>Yuridik hujjat tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
        ("⚖️ <i>Формируется юридический документ, пожалуйста подождите...</i>" if lang == "ru" else
         "⚖️ <i>Drafting legal document, please wait...</i>"),
        parse_mode="HTML"
    )
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    await generate_legal_from_file(callback.message, status_msg, legal_id, action, lang=lang)


async def generate_legal_from_file(message: Message, status_msg: Message, legal_id: str, doc_subtype: str, lang: str = "uz"):
    """Yuklangan shartnoma matnidan AI orqali yuridik hujjat shakllantirish"""
    data = legal_pending_docs.get(legal_id)
    if not data:
        await message.answer("❌ Hujjat ma'lumotlari topilmadi yoki eskirgan. Iltimos, faylni qayta yuboring.")
        return

    filename = data["filename"]
    extracted_text = data["text"]
    caption = data.get("caption", "")
    user_id = data["user_id"]
    user_name = data.get("user_name", "Foydalanuvchi")

    target_name = (
        "Notariusdan Ijro xati to'plami (3757-son)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
        ("Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
        ("Sudga Da'vo arizasi" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
        ("Sudgacha Talabnoma (Pretenziya)" if doc_subtype in ("demand", "demand_debt", "demand_quality") else
         "Da'vo arizasi va Talabnoma to'plami")))
    )
    if lang == "ru":
        target_name = (
            "Комплект для исполнительной надписи нотариуса (Приказ № 3757)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
            ("Справка о задолженности и Сопроводительное письмо" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
            ("Исковое заявление в суд" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
            ("Досудебная претензия" if doc_subtype in ("demand", "demand_debt", "demand_quality") else
             "Комплект: Иск и Претензия")))
        )
    elif lang == "en":
        target_name = (
            "Notarial Execution Inscription Package (Decree 3757)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
            ("Certificate of Indebtedness and Cover Letter" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
            ("Court Lawsuit" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
            ("Demand Letter" if doc_subtype in ("demand", "demand_debt", "demand_quality") else
             "Lawsuit & Demand Package")))
        )

    proc_txt = (
        f"⚖️ <i>«{filename}» hujjati asosida <b>«{target_name}»</b> tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
        (f"⚖️ <i>На основе «{filename}» готовится <b>«{target_name}»</b>, пожалуйста подождите...</i>" if lang == "ru" else
         f"⚖️ <i>Drafting <b>«{target_name}»</b> based on «{filename}», please wait...</i>")
    )
    try:
        await status_msg.edit_text(proc_txt, parse_mode="HTML")
    except Exception:
        status_msg = await message.answer(proc_txt, parse_mode="HTML")

    legal_details = (
        f"Ilova qilingan shartnoma/hujjat fayli: {filename}\n"
        f"Hujjatning to'liq matni:\n{extracted_text}\n"
        f"Foydalanuvchi izohi va qo'shimcha faktlari: {caption}"
    )

    legal_doc = await ai_service.generate_legal_document(
        user_id=user_id,
        doc_type=doc_subtype,
        details=legal_details,
        lang=lang
    )

    try:
        await status_msg.delete()
    except Exception:
        pass

    if legal_doc.startswith("⚠️") or legal_doc.startswith("❌"):
        await message.answer(legal_doc, parse_mode="HTML")
        return

    title_prefix = (
        "Notarius Ijro xati" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
        ("Qarzdorlik ma'lumotnomasi va Ilova xati" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
        ("Da'vo arizasi" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
        ("Talabnoma" if doc_subtype != "both" else "Da'vo va Talabnoma")))
    )
    if lang == "ru":
        title_prefix = (
            "Исполнительная надпись нотариуса" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
            ("Справка о задолженности и Сопроводительное" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
            ("Исковое заявление" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
            ("Претензия" if doc_subtype != "both" else "Иск и Претензия")))
        )
    elif lang == "en":
        title_prefix = (
            "Notary Execution Inscription" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
            ("Debt Certificate & Cover Letter" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
            ("Court Claim" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
            ("Demand Letter" if doc_subtype != "both" else "Lawsuit & Demand")))
        )

    clean_fn = format_doc_title(filename, "", lang=lang)
    title = f"{title_prefix}: {clean_fn}"
    res_id = store_result(title=title, content=legal_doc, user_name=user_name, user_id=user_id, category="legal")
    result_markup = get_legal_result_keyboard(res_id=res_id, lang=lang)
    await send_ai_response(message, legal_doc, res_id=res_id, reply_markup=result_markup, lang=lang)


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
            thinking_txt = (
                "🤔 <i>O'ylamoqda...</i>" if lang == "uz" else
                ("🤔 <i>Думает...</i>" if lang == "ru" else "🤔 <i>Thinking...</i>")
            )
            status_msg = await message.reply(thinking_txt, parse_mode="HTML")
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
            await send_ai_response(message, response, res_id=res_id, lang=lang)
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
        reply_markup=get_archive_list_keyboard(items=items, page=0, total_count=total_count, lang=lang)
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

        # 1. LEGAL_DOC yoki NOTARY rejimida hujjat (shartnoma, tilxat, dalolatnoma va h.k.) yuborilgan bo'lsa
        if current_mode.startswith("LEGAL_DOC") or current_mode.startswith("NOTARY"):
            doc_subtype = current_mode.split(":", 1)[1] if ":" in current_mode else "upload"
            legal_id = uuid.uuid4().hex[:8]
            legal_pending_docs[legal_id] = {
                "user_id": user_id,
                "user_name": message.from_user.full_name if message.from_user else "Foydalanuvchi",
                "filename": filename,
                "text": extracted_text[:35000],
                "caption": caption,
                "created_at": datetime.now()
            }
            if len(legal_pending_docs) > 100:
                oldest = next(iter(legal_pending_docs))
                legal_pending_docs.pop(oldest, None)

            # Agar izoh (caption) orqali yoki aniq shablon orqali ma'lum bo'lsa:
            auto_type = None
            cap_lower = caption.lower()
            if any(w in cap_lower for w in ["ijro xati", "ijro_xati", "notarius", "notary", "нотариус", "исполнительн", "3757"]):
                auto_type = "notary_exec"
            elif any(w in cap_lower for w in ["malumotnoma", "ma'lumotnoma", "справка", "solishtiruv", "akt sverki", "акт сверки", "ilova xati", "сопроводительн"]):
                auto_type = "debt_cert"
            elif any(w in cap_lower for w in ["talabnoma", "претензия", "demand"]):
                auto_type = "demand"
            elif any(w in cap_lower for w in ["da'vo", "davo", "иск", "lawsuit", "claim"]):
                auto_type = "lawsuit"
            elif current_mode.startswith("NOTARY"):
                auto_type = "notary_exec"
            elif doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma"):
                auto_type = "debt_cert"
            elif doc_subtype in ("debt", "contract", "alimony", "divorce", "labor", "damage"):
                auto_type = doc_subtype
            elif doc_subtype in ("demand", "demand_debt", "demand_quality"):
                auto_type = doc_subtype

            if auto_type:
                await generate_legal_from_file(message, status_msg, legal_id, auto_type, lang=lang)
                return

            char_count = len(extracted_text)
            choice_prompt = (
                f"📄 <b>«{filename}» shartnomasi / hujjati qabul qilindi!</b>\n\n"
                f"📊 <b>Hajmi:</b> taxminan {char_count:,} ta belgi matn\n\n"
                f"🎯 <b>Ushbu shartnoma/hujjat asosida qanday yuridik hujjat tayyorlaymiz?</b>\n\n"
                f"1️⃣ 📜 <b>Notariusdan Ijro xati to'plami (3757-son)</b> — sud muhokamasisiz to'g'ridan-to'g'ri MIBga topshirish uchun Notariusga ariza, Qarzdorga ogohlantirish va Ijro xati loyihasi;\n"
                f"2️⃣ 📜 <b>Qarzdorlik ma'lumotnomasi + Ilova xati</b> — shartnoma yoki solishtiruv dalolatnomasi asosida rasmiy ma'lumotnoma (jadvali bilan) va sud/debitorga kuzatuv xati;\n"
                f"3️⃣ 🏛 <b>Sudga Da'vo arizasi</b> — shartnoma bo'yicha qarz, penya va zararlarni sud orqali majburiy undirish;\n"
                f"4️⃣ 📑 <b>Sudgacha Talabnoma (Pretenziya)</b> — ikkinchi tarafga 10 kun ichida to'lash yoki kamchilikni bartaraf etish talabi;\n"
                f"5️⃣ ⚡️ <b>Da'vo va Talabnoma (Ikkalasi birga)</b> — ikkala hujjatni to'liq komplekt holda tayyorlash;\n"
                f"6️⃣ 📊 <b>Shartnoma tahlili</b> — huquqlar, majburiyatlar va risklar tahlili.\n\n"
                f"👇 <i>Kerakli amalni tanlang:</i>"
                if lang == "uz" else
                (f"📄 <b>Договор / документ «{filename}» успешно принят!</b>\n\n"
                 f"📊 <b>Объем:</b> около {char_count:,} знаков текста\n\n"
                 f"🎯 <b>Какой юридический документ составить на основе этого файла?</b>\n\n"
                 f"1️⃣ 📜 <b>Комплект для исполнительной надписи нотариуса (Приказ № 3757)</b> — заявление нотариусу, предупреждение должнику и проект надписи для прямого взыскания через БПИ (МИБ);\n"
                 f"2️⃣ 📜 <b>Справка о задолженности + Сопроводительное письмо</b> — официальная справка с таблицей расчетов и письмо в суд или должнику;\n"
                 f"3️⃣ 🏛 <b>Исковое заявление в суд</b> — принудительное взыскание долга, неустойки и убытков;\n"
                 f"4️⃣ 📑 <b>Досудебная претензия</b> — официальное требование контрагенту о погашении задолженности;\n"
                 f"5️⃣ ⚡️ <b>Иск и Претензия (Вместе)</b> — сформировать оба документа одновременно;\n"
                 f"6️⃣ 📊 <b>Анализ договора</b> — анализ рисков, санкций и сроков.\n\n"
                 f"👇 <i>Выберите действие:</i>"
                 if lang == "ru" else
                 f"📄 <b>Document «{filename}» received successfully!</b>\n\n"
                 f"📊 <b>Size:</b> approx. {char_count:,} characters\n\n"
                 f"🎯 <b>Which legal document should we draft based on this file?</b>\n\n"
                 f"1️⃣ 📜 <b>Notary Execution Inscription Package (Decree 3757)</b> — Application to Notary, warning notice to debtor, and draft inscription for direct MIB recovery;\n"
                 f"2️⃣ 📜 <b>Certificate of Indebtedness + Cover Letter</b> — official accounting statement with itemized table and cover letter;\n"
                 f"3️⃣ 🏛 <b>Court Lawsuit</b> — formal court claim for debt, interest, and damages;\n"
                 f"4️⃣ 📑 <b>Pre-trial Demand Letter</b> — formal breach notice with cure period;\n"
                 f"5️⃣ ⚡️ <b>Both Lawsuit & Demand Letter</b> — draft the complete litigation package;\n"
                 f"6️⃣ 📊 <b>Contract Risk Analysis</b> — examine rights, liabilities, and risks.\n\n"
                 f"👇 <i>Choose an action below:</i>")
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
            await message.reply(choice_prompt, parse_mode="HTML", reply_markup=get_legal_doc_action_keyboard(legal_id, lang=lang))
            return

        # 1.5. TRANSLATOR rejimida hujjat yuborilgan bo'lsa
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
            clean_fn = format_doc_title(filename, "", lang=lang)
            title = (
                f"Tarjima ({tgt_name}): {clean_fn}" if lang == "uz" else
                (f"Перевод ({tgt_name}): {clean_fn}" if lang == "ru" else
                 f"Translation ({tgt_name}): {clean_fn}")
            )
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

    if action == "legal_lawsuit":
        legal_id = uuid.uuid4().hex[:8]
        legal_pending_docs[legal_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "filename": filename,
            "text": text[:35000],
            "caption": doc_data.get("caption", ""),
            "created_at": datetime.now()
        }
        status_msg = await callback.message.answer(
            "⚖️ <i>Ushbu hujjat asosida Sudga Da'vo arizasi tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("⚖️ <i>Формируется Исковое заявление в суд на основе документа...</i>" if lang == "ru" else
             "⚖️ <i>Drafting Court Lawsuit based on the document...</i>"),
            parse_mode="HTML"
        )
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        await generate_legal_from_file(callback.message, status_msg, legal_id, "lawsuit", lang=lang)
        return

    if action == "legal_demand":
        legal_id = uuid.uuid4().hex[:8]
        legal_pending_docs[legal_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "filename": filename,
            "text": text[:35000],
            "caption": doc_data.get("caption", ""),
            "created_at": datetime.now()
        }
        status_msg = await callback.message.answer(
            "📑 <i>Ushbu hujjat asosida Sudgacha Talabnoma (Pretenziya) tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("📑 <i>Формируется Досудебная претензия на основе документа...</i>" if lang == "ru" else
             "📑 <i>Drafting Pre-trial Demand Letter based on the document...</i>"),
            parse_mode="HTML"
        )
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        await generate_legal_from_file(callback.message, status_msg, legal_id, "demand", lang=lang)
        return

    if action == "legal_debt_cert":
        legal_id = uuid.uuid4().hex[:8]
        legal_pending_docs[legal_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "filename": filename,
            "text": text[:35000],
            "caption": doc_data.get("caption", ""),
            "created_at": datetime.now()
        }
        status_msg = await callback.message.answer(
            "📜 <i>Ushbu hujjat asosida Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("📜 <i>Формируется Справка о задолженности и Сопроводительное письмо на основе документа...</i>" if lang == "ru" else
             "📜 <i>Drafting Certificate of Indebtedness and Cover Letter based on the document...</i>"),
            parse_mode="HTML"
        )
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        await generate_legal_from_file(callback.message, status_msg, legal_id, "debt_cert", lang=lang)
        return

    if action == "legal_notary_exec":
        legal_id = uuid.uuid4().hex[:8]
        legal_pending_docs[legal_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "filename": filename,
            "text": text[:35000],
            "caption": doc_data.get("caption", ""),
            "created_at": datetime.now()
        }
        status_msg = await callback.message.answer(
            "📜 <i>Ushbu hujjat asosida Notariusdan Ijro xati to'plami (3757-sonli qaror) tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
            ("📜 <i>Формируется комплект для исполнительной надписи нотариуса (Приказ № 3757)...</i>" if lang == "ru" else
             "📜 <i>Drafting Notary Execution Inscription package (Decree No. 3757)...</i>"),
            parse_mode="HTML"
        )
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
        await generate_legal_from_file(callback.message, status_msg, legal_id, "notary_exec", lang=lang)
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
        section_title = (
            "Barcha bo'limlar bo'yicha to'liq va umumiy amaliy tahlil" if lang == "uz" else
            ("Полный и общий практический анализ по всем разделам" if lang == "ru" else
             "Comprehensive and practical analysis across all sections")
        )
    elif action == "risks":
        section_title = (
            "Taraflarning huquqlari, majburiyatlari va amaliy yuridik xavflar" if lang == "uz" else
            ("Права, обязанности сторон и практические юридические риски" if lang == "ru" else
             "Rights, obligations of parties and practical legal risks")
        )
    elif action == "deadlines":
        section_title = (
            "Qat'iy muddatlar, moliyaviy raqamlar, jarimalar va javobgarlik" if lang == "uz" else
            ("Строгие сроки, финансовые показатели, штрафы и ответственность" if lang == "ru" else
             "Strict deadlines, financial numbers, fines and liability")
        )
    else:
        section_title = action

    await callback.answer(
        "Tahlil boshlanmoqda..." if lang == "uz" else
        ("Начинается анализ..." if lang == "ru" else "Starting analysis...")
    )
    status_msg = await callback.message.reply(
        f"⏳ <b>«{section_title}»</b> bo'yicha chuqur tahlil tayyorlanmoqda..." if lang == "uz" else
        (f"⏳ Готовится глубокий анализ <b>«{section_title}»</b>..." if lang == "ru" else
         f"⏳ Preparing deep analysis for <b>«{section_title}»</b>..."),
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

        title = format_doc_title(filename, section_title, lang=lang)
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
        err_msg = (
            "❌ Bo'limni tahlil qilishda xatolik yuz berdi." if lang == "uz" else
            ("❌ Ошибка при анализе раздела." if lang == "ru" else
             "❌ Error analyzing section.")
        )
        await status_msg.edit_text(err_msg, parse_mode="HTML")


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
    lang = get_user_lang(user_id)
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
        if lang == "uz" else
        ("🎙 <b>Аудиосообщение принято!</b>\n\n"
         "В каком формате вы хотите получить результат анализа?\n\n"
         "• 📝 <b>Текст:</b> Письменный ответ в чате\n"
         "• 🎙 <b>Голос:</b> Голосовое сообщение\n"
         "• 📄 <b>Word:</b> Документ <code>.docx</code>\n"
         "• 📑 <b>PDF:</b> Готовый документ <code>.pdf</code>\n"
         "• ✨ <b>Все форматы:</b> Получить во всех форматах вместе\n\n"
         "👇 <i>Выберите формат ниже:</i>"
         if lang == "ru" else
         "🎙 <b>Audio message received!</b>\n\n"
         "In which format would you like to receive the analysis?\n\n"
         "• 📝 <b>Text:</b> Written response in chat\n"
         "• 🎙 <b>Voice:</b> Voice message\n"
         "• 📄 <b>Word:</b> Formatted <code>.docx</code> file\n"
         "• 📑 <b>PDF:</b> Ready <code>.pdf</code> document\n"
         "• ✨ <b>All formats:</b> Get in all formats together\n\n"
         "👇 <i>Select format below:</i>")
    )

    await message.reply(choice_text, parse_mode="HTML", reply_markup=get_audio_choice_keyboard(audio_id, lang=lang))


@router.callback_query(F.data.startswith("aud_mode:"))
async def handle_audio_mode_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov!")
        return

    action = parts[1]
    audio_id = parts[2]
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)

    if action == "cancel":
        pending_audio.pop(audio_id, None)
        await callback.answer("Bekor qilindi." if lang == "uz" else ("Отменено." if lang == "ru" else "Cancelled."))
        try:
            cancel_txt = (
                "❌ Audio tahlili bekor qilindi." if lang == "uz" else
                ("❌ Анализ аудио отменен." if lang == "ru" else "❌ Audio analysis cancelled.")
            )
            await callback.message.edit_text(cancel_txt, parse_mode="HTML")
        except Exception:
            pass
        return

    item = pending_audio.get(audio_id)
    if not item:
        expired_txt = (
            "Ushbu so'rov muddati o'tgan. Iltimos, audioni qaytadan yuboring." if lang == "uz" else
            ("Срок действия запроса истек. Пожалуйста, отправьте аудио снова." if lang == "ru" else
             "Request expired. Please send audio again.")
        )
        await callback.answer(expired_txt, show_alert=True)
        return

    await callback.answer(
        "Tanlovingiz qabul qilindi!" if lang == "uz" else
        ("Выбор принят!" if lang == "ru" else "Selection accepted!")
    )
    analyzing_audio_txt = (
        "⏳ <i>Audio Gemini AI tomonidan tahlil qilinmoqda, iltimos kuting...</i>" if lang == "uz" else
        ("⏳ <i>Аудио анализируется с помощью Gemini AI, пожалуйста подождите...</i>" if lang == "ru" else
         "⏳ <i>Analyzing audio with Gemini AI, please wait...</i>")
    )
    status_msg = await callback.message.edit_text(analyzing_audio_txt, parse_mode="HTML")

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
        title_audio = "Audio Tahlil" if lang == "uz" else ("Анализ аудио" if lang == "ru" else "Audio Analysis")
        res_id = store_result(title=title_audio, content=response, user_name=user_name, user_id=item["user_id"], category="audio")

        if action in ("text", "all"):
            try:
                await status_msg.delete()
            except Exception:
                pass
            await send_ai_response(callback.message, response, res_id=res_id, lang=lang)

        if action in ("docx", "all"):
            docx_path = generate_docx(title=title_audio, content=response, user_name=user_name, lang=lang)
            caption_docx = (
                "📄 <b>Audio xabar tahlili (Word hujjati)</b>" if lang == "uz" else
                ("📄 <b>Анализ аудиосообщения (Документ Word)</b>" if lang == "ru" else
                 "📄 <b>Audio analysis (Word document)</b>")
            )
            audio_fn_docx = f"Audio_Tahlil_{audio_id}.docx" if lang == "uz" else (f"Audio_Analiz_{audio_id}.docx" if lang == "ru" else f"Audio_Analysis_{audio_id}.docx")
            try:
                await callback.message.answer_document(
                    document=FSInputFile(docx_path, filename=audio_fn_docx),
                    caption=caption_docx,
                    parse_mode="HTML"
                )
            finally:
                if docx_path and os.path.exists(docx_path):
                    try:
                        os.remove(docx_path)
                    except Exception:
                        pass

        if action in ("pdf", "all"):
            pdf_path = generate_pdf(title=title_audio, content=response, user_name=user_name, lang=lang)
            caption_pdf = (
                "📑 <b>Audio xabar tahlili (PDF hujjati)</b>" if lang == "uz" else
                ("📑 <b>Анализ аудиосообщения (Документ PDF)</b>" if lang == "ru" else
                 "📑 <b>Audio analysis (PDF document)</b>")
            )
            audio_fn_pdf = f"Audio_Tahlil_{audio_id}.pdf" if lang == "uz" else (f"Audio_Analiz_{audio_id}.pdf" if lang == "ru" else f"Audio_Analysis_{audio_id}.pdf")
            try:
                await callback.message.answer_document(
                    document=FSInputFile(pdf_path, filename=audio_fn_pdf),
                    caption=caption_pdf,
                    parse_mode="HTML"
                )
            finally:
                if pdf_path and os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass

        if action in ("voice", "all"):
            ogg_path, mp3_path = await convert_text_to_voice_and_audio(response, lang=lang)
            caption_voice = (
                "🎙 <b>AI Ovozli Javobi</b>" if lang == "uz" else
                ("🎙 <b>Голосовой ответ AI</b>" if lang == "ru" else
                 "🎙 <b>AI Voice Response</b>")
            )
            try:
                try:
                    await callback.message.answer_voice(
                        voice=FSInputFile(ogg_path),
                        caption=caption_voice,
                        parse_mode="HTML"
                    )
                except TelegramBadRequest as e:
                    if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                        await callback.message.answer_audio(
                            audio=FSInputFile(mp3_path),
                            caption=caption_voice,
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
        await callback.answer("Asosiy holatga qaytdi." if lang == "uz" else ("Возврат назад." if lang == "ru" else "Back to original."))


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

    await callback.answer(
        "O'tkazilmoqda..." if lang == "uz" else
        ("Экспорт..." if lang == "ru" else "Transferring...")
    )

    safe_title = get_safe_file_title(title, "Dokument" if lang == "ru" else ("Document" if lang == "en" else "Hujjat"))
    display_title = format_doc_title("", title, lang=lang)

    if target == "docx":
        docx_path = generate_docx(title=display_title, content=content, user_name=user_name, lang=lang)
        caption_docx = (
            f"📄 <b>Word hujjati yaratildi</b>\n<i>Mavzu: {display_title}</i>" if lang == "uz" else
            (f"📄 <b>Документ Word создан</b>\n<i>Тема: {display_title}</i>" if lang == "ru" else
             f"📄 <b>Word document created</b>\n<i>Topic: {display_title}</i>")
        )
        try:
            await callback.message.answer_document(
                document=FSInputFile(docx_path, filename=f"{safe_title}.docx"),
                caption=caption_docx,
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if docx_path and os.path.exists(docx_path):
                os.remove(docx_path)

    elif target == "pdf":
        pdf_path = generate_pdf(title=display_title, content=content, user_name=user_name, lang=lang)
        caption_pdf = (
            f"📑 <b>PDF hujjati yaratildi</b>\n<i>Mavzu: {display_title}</i>" if lang == "uz" else
            (f"📑 <b>Документ PDF создан</b>\n<i>Тема: {display_title}</i>" if lang == "ru" else
             f"📑 <b>PDF document created</b>\n<i>Topic: {display_title}</i>")
        )
        try:
            await callback.message.answer_document(
                document=FSInputFile(pdf_path, filename=f"{safe_title}.pdf"),
                caption=caption_pdf,
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)

    elif target == "pptx":
        wait_pptx = (
            "📽 <i>Prezentatsiya slaydlari shakllantirilmoqda...</i>" if lang == "uz" else
            ("📽 <i>Формирование слайдов презентации...</i>" if lang == "ru" else
             "📽 <i>Generating presentation slides...</i>")
        )
        status_msg = await callback.message.reply(wait_pptx, parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        slides_text = await ai_service.format_for_presentation(callback.from_user.id, content, lang=lang)
        pptx_path = generate_presentation(title=display_title, content=slides_text, user_name=user_name, lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass
        caption_pptx = (
            f"📽 <b>PowerPoint taqdimoti yaratildi</b>\n<i>Mavzu: {display_title}</i>" if lang == "uz" else
            (f"📽 <b>Презентация PowerPoint создана</b>\n<i>Тема: {display_title}</i>" if lang == "ru" else
             f"📽 <b>PowerPoint presentation created</b>\n<i>Topic: {display_title}</i>")
        )
        try:
            await callback.message.answer_document(
                document=FSInputFile(pptx_path, filename=f"{safe_title}.pptx"),
                caption=caption_pptx,
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if pptx_path and os.path.exists(pptx_path):
                os.remove(pptx_path)

    elif target == "xlsx":
        wait_xlsx = (
            "📊 <i>Excel jadvali shakllantirilmoqda...</i>" if lang == "uz" else
            ("📊 <i>Формирование таблицы Excel...</i>" if lang == "ru" else
             "📊 <i>Generating Excel spreadsheet...</i>")
        )
        status_msg = await callback.message.reply(wait_xlsx, parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
        table_text = await ai_service.format_for_excel(callback.from_user.id, content, lang=lang)
        xlsx_path = generate_excel(title=display_title, content=table_text, user_name=user_name, lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass
        caption_xlsx = (
            f"📊 <b>Excel jadvali yaratildi</b>\n<i>Mavzu: {display_title}</i>" if lang == "uz" else
            (f"📊 <b>Таблица Excel создана</b>\n<i>Тема: {display_title}</i>" if lang == "ru" else
             f"📊 <b>Excel table created</b>\n<i>Topic: {display_title}</i>")
        )
        try:
            await callback.message.answer_document(
                document=FSInputFile(xlsx_path, filename=f"{safe_title}.xlsx"),
                caption=caption_xlsx,
                parse_mode="HTML",
                reply_markup=get_content_actions_keyboard(res_id, lang=lang)
            )
        finally:
            if xlsx_path and os.path.exists(xlsx_path):
                os.remove(xlsx_path)

    elif target == "deep":
        wait_deep = (
            "🔬 <i>Ushbu mavzu bo'yicha Deep Research o'tkazilmoqda...</i>" if lang == "uz" else
            ("🔬 <i>Проводится исследование Deep Research по этой теме...</i>" if lang == "ru" else
             "🔬 <i>Conducting Deep Research on this topic...</i>")
        )
        status_msg = await callback.message.reply(wait_deep, parse_mode="HTML")
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
        wait_voice_msg = (
            "🎙 <i>Ovozli xabar tayyorlanmoqda...</i>" if lang == "uz" else
            ("🎙 <i>Подготовка голосового сообщения...</i>" if lang == "ru" else
             "🎙 <i>Preparing voice message...</i>")
        )
        status_msg = await callback.message.reply(wait_voice_msg, parse_mode="HTML")
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)
        ogg_path, mp3_path = await convert_text_to_voice_and_audio(content, lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass
        caption_voice = (
            f"🎙 <b>Ovozli javob: {title}</b>" if lang == "uz" else
            (f"🎙 <b>Голосовой ответ: {title}</b>" if lang == "ru" else
             f"🎙 <b>Voice response: {title}</b>")
        )
        caption_audio = (
            f"🎙 <b>Audio fayl: {title}</b>" if lang == "uz" else
            (f"🎙 <b>Аудиофайл: {title}</b>" if lang == "ru" else
             f"🎙 <b>Audio file: {title}</b>")
        )
        try:
            try:
                await callback.message.answer_voice(voice=FSInputFile(ogg_path), caption=caption_voice, parse_mode="HTML")
            except TelegramBadRequest:
                await callback.message.answer_audio(audio=FSInputFile(mp3_path), caption=caption_audio, parse_mode="HTML")
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

    user_id = callback.from_user.id
    lang = get_user_lang(user_id)

    instructions = {
        "expand": (
            "Ushbu tahlilni yanada kengaytirib, ko'proq tafsilotlar, misollar va tushuntirishlar bilan boyiting." if lang == "uz" else
            ("Расширьте этот анализ, добавьте больше деталей, практических примеров и подробных разъяснений." if lang == "ru" else
             "Expand this analysis with more details, practical examples, and comprehensive explanations.")
        ),
        "summary": (
            "Ushbu tahlilni eng muhim xulosalarga qisqartiring, punktlar bilan lo'nda qilib bering." if lang == "uz" else
            ("Сократите этот анализ до ключевых выводов, оформите кратко и четко по пунктам." if lang == "ru" else
             "Summarize this analysis to the key takeaways and bullet points.")
        ),
        "rewrite": (
            "Ushbu tahlilni yanada professional, ravon va ta'sirchan uslubda qayta shakllantiring." if lang == "uz" else
            ("Перепишите этот анализ в более профессиональном, грамотном и убедительном стиле." if lang == "ru" else
             "Rewrite this analysis in a professional, fluent, and compelling style.")
        ),
        "truz": "Ushbu matnni toza, ravon va adabiy o'zbek tiliga o'giring."
    }

    default_instruction = (
        "Matnni qayta ishlang." if lang == "uz" else
        ("Переработайте текст." if lang == "ru" else "Refine the text.")
    )
    instruction = instructions.get(edit_type, default_instruction)
    await callback.answer(
        "Tahrirlanmoqda..." if lang == "uz" else
        ("Редактирование..." if lang == "ru" else "Editing...")
    )
    status_msg = await callback.message.reply(
        "✏️ <i>Tahrirlanmoqda, iltimos kuting...</i>" if lang == "uz" else
        ("✏️ <i>Редактируется, пожалуйста подождите...</i>" if lang == "ru" else
         "✏️ <i>Editing, please wait...</i>"),
        parse_mode="HTML"
    )
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

    new_content = await ai_service.refine_content(user_id, content, instruction)
    try:
        await status_msg.delete()
    except Exception:
        pass

    edit_suffix = "Tahrirlangan" if lang == "uz" else ("Отредактировано" if lang == "ru" else "Edited")
    new_res_id = store_result(
        title=f"{title} ({edit_suffix})",
        content=new_content,
        user_name=user_name,
        user_id=user_id,
        category="chat"
    )
    await send_ai_response(callback.message, new_content, res_id=new_res_id, lang=lang)


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
    lang = get_user_lang(user_id)

    if action == "noop":
        await callback.answer()
        return

    if action == "home":
        await callback.answer("Bosh menyu" if lang == "uz" else ("Главное меню" if lang == "ru" else "Main menu"))
        is_admin = access_guard.is_admin(user_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        menu_title = "🏠 <b>Bosh menyu</b>" if lang == "uz" else ("🏠 <b>Главное меню</b>" if lang == "ru" else "🏠 <b>Main menu</b>")
        await callback.message.answer(menu_title, parse_mode="HTML", reply_markup=get_main_keyboard(is_admin=is_admin, lang=lang))
        return

    # Sahifalash: arx:p:{page}
    if action == "p" and len(parts) >= 3:
        page = int(parts[2]) if parts[2].isdigit() else 0
        total_count = archive_service.get_user_count(user_id)
        per_page = 5

        if total_count == 0:
            empty_txt = (
                "🗄 <b>Arxivingiz bo'sh.</b>\n\nYangi so'rovlaringiz avtomatik ravishda saqlanib boradi."
                if lang == "uz" else
                ("🗄 <b>Ваш архив пуст.</b>\n\nНовые запросы будут автоматически сохраняться."
                 if lang == "ru" else
                 "🗄 <b>Your archive is empty.</b>\n\nYour new requests will be saved automatically.")
            )
            try:
                await callback.message.edit_text(empty_txt, parse_mode="HTML")
            except Exception:
                pass
            await callback.answer("Arxiv bo'sh." if lang == "uz" else ("Архив пуст." if lang == "ru" else "Archive is empty."))
            return

        offset = page * per_page
        items = archive_service.get_user_items(user_id=user_id, limit=per_page, offset=offset)

        text = (
            f"🗄 <b>Sizning so'rovlar arxivingiz</b>\n"
            f"📊 <b>Jami saqlangan so'rovlar:</b> {total_count} ta\n\n"
            f"👇 <i>Kerakli so'rovni tanlang:</i>"
            if lang == "uz" else
            (f"🗄 <b>Ваш архив запросов</b>\n"
             f"📊 <b>Всего сохранено:</b> {total_count}\n\n"
             f"👇 <i>Выберите нужный запрос:</i>"
             if lang == "ru" else
             f"🗄 <b>Your request archive</b>\n"
             f"📊 <b>Total saved:</b> {total_count}\n\n"
             f"👇 <i>Select a request:</i>")
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_archive_list_keyboard(items=items, page=page, total_count=total_count, per_page=per_page, lang=lang)
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
            err_not_found = (
                "Ushbu yozuv arxivdan topilmadi yoki o'chirilgan." if lang == "uz" else
                ("Запись не найдена в архиве или уже удалена." if lang == "ru" else
                 "Record not found in archive or already deleted.")
            )
            await callback.answer(err_not_found, show_alert=True)
            return

        # Natijalar keshiga kiritish (Word, PDF, Excel, Ovozli qilish tugmalari ishlashi uchun)
        result_store[item_id] = {
            "title": item["title"],
            "content": item["content"],
            "user_name": item["user_name"],
            "user_id": item["user_id"],
            "created_at": item["created_at"]
        }

        category_labels = {
            "doc": "📑 Hujjat tahlili" if lang == "uz" else ("📑 Анализ документа" if lang == "ru" else "📑 Document Analysis"),
            "research": "🔬 Deep Research",
            "audio": "🎙 Audio tahlil" if lang == "uz" else ("🎙 Анализ аудио" if lang == "ru" else "🎙 Audio Analysis"),
            "image": "📸 Rasm tahlili" if lang == "uz" else ("📸 Анализ изображения" if lang == "ru" else "📸 Image Analysis"),
            "slides": "📽 Slaydlar, Prezentatsiya" if lang == "uz" else ("📽 Слайды, Презентация" if lang == "ru" else "📽 Slides, Presentation"),
            "office": "📊 Hujjatlar va jadvallar" if lang == "uz" else ("📊 Документы и таблицы" if lang == "ru" else "📊 Office Documents"),
            "legal": "⚖️ Sudga da'vo va Talabnoma" if lang == "uz" else ("⚖️ Иски и Претензии" if lang == "ru" else "⚖️ Lawsuits & Claims"),
            "chat": "🤖 AI Chat suhbati" if lang == "uz" else ("🤖 AI Чат" if lang == "ru" else "🤖 AI Chat"),
            "trans": "🌐 Tarjima" if lang == "uz" else ("🌐 Перевод" if lang == "ru" else "🌐 Translation")
        }
        default_cat = "📌 Saqlangan tahlil" if lang == "uz" else ("📌 Сохраненный анализ" if lang == "ru" else "📌 Saved analysis")
        cat_title = category_labels.get(item.get("category", "chat"), default_cat)

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
            if lang == "uz" else
            (f"🗄 <b>Запрос из архива:</b>\n\n"
             f"🏷 <b>Раздел:</b> {cat_title}\n"
             f"📅 <b>Время:</b> <code>{item['created_at']}</code>\n"
             f"📌 <b>Заголовок:</b> <b>{item['title']}</b>\n\n"
             f"📝 <b>Краткое содержание:</b>\n<i>{preview_text}</i>\n\n"
             f"👇 <i>Скачать документ, редактировать, прослушать или удалить:</i>"
             if lang == "ru" else
             f"🗄 <b>Archived request:</b>\n\n"
             f"🏷 <b>Section:</b> {cat_title}\n"
             f"📅 <b>Time:</b> <code>{item['created_at']}</code>\n"
             f"📌 <b>Title:</b> <b>{item['title']}</b>\n\n"
             f"📝 <b>Summary:</b>\n<i>{preview_text}</i>\n\n"
             f"👇 <i>Download document, edit, listen or delete:</i>")
        )

        try:
            await callback.message.edit_text(
                msg,
                parse_mode="HTML",
                reply_markup=get_archive_item_keyboard(item_id, lang=lang)
            )
        except Exception:
            await callback.message.answer(
                msg,
                parse_mode="HTML",
                reply_markup=get_archive_item_keyboard(item_id, lang=lang)
            )
        await callback.answer()
        return

    # To'liq matnni o'qish: arx:read:{item_id}
    if action == "read" and len(parts) >= 3:
        item_id = parts[2]
        item = archive_service.get_item(item_id)
        if not item:
            err_not_found = (
                "Yozuv topilmadi." if lang == "uz" else
                ("Запись не найдена." if lang == "ru" else "Record not found.")
            )
            await callback.answer(err_not_found, show_alert=True)
            return

        result_store[item_id] = {
            "title": item["title"],
            "content": item["content"],
            "user_name": item["user_name"],
            "user_id": item["user_id"],
            "created_at": item["created_at"]
        }
        await callback.answer(
            "To'liq matn yuborilmoqda..." if lang == "uz" else
            ("Отправка полного текста..." if lang == "ru" else "Sending full text...")
        )
        await send_ai_response(
            callback.message,
            item["content"],
            res_id=item_id,
            reply_markup=get_archive_item_keyboard(item_id, lang=lang),
            lang=lang
        )
        return

    # O'chirishni tasdiqlash so'rovi: arx:confirm_del:{item_id}
    if action == "confirm_del" and len(parts) >= 3:
        item_id = parts[2]
        item = archive_service.get_item(item_id)
        title = item["title"] if item else ("Ushbu so'rov" if lang == "uz" else ("Этот запрос" if lang == "ru" else "This request"))
        confirm_text = (
            f"❓ <b>Haqiqatan ham ushbu yozuvni arxivdan o'chirmoqchimisiz?</b>\n\n"
            f"📌 <i>«{title}»</i>\n\n"
            f"⚠️ <i>O'chirilgandan so'ng ma'lumotni qayta tiklab bo'lmaydi.</i>"
            if lang == "uz" else
            (f"❓ <b>Вы действительно хотите удалить эту запись из архива?</b>\n\n"
             f"📌 <i>«{title}»</i>\n\n"
             f"⚠️ <i>После удаления восстановить данные невозможно.</i>"
             if lang == "ru" else
             f"❓ <b>Are you sure you want to delete this record from the archive?</b>\n\n"
             f"📌 <i>«{title}»</i>\n\n"
             f"⚠️ <i>Once deleted, this cannot be recovered.</i>")
        )
        try:
            await callback.message.edit_text(
                confirm_text,
                parse_mode="HTML",
                reply_markup=get_archive_delete_confirm_keyboard(item_id, lang=lang)
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
            del_ok = (
                "✅ Yozuv arxivdan muvaffaqiyatli o'chirildi!" if lang == "uz" else
                ("✅ Запись успешно удалена из архива!" if lang == "ru" else
                 "✅ Record deleted successfully from archive!")
            )
            await callback.answer(del_ok, show_alert=True)
        else:
            del_err = (
                "⚠️ Yozuv topilmadi yoki allaqachon o'chirilgan." if lang == "uz" else
                ("⚠️ Запись не найдена или уже удалена." if lang == "ru" else
                 "⚠️ Record not found or already deleted.")
            )
            await callback.answer(del_err, show_alert=True)

        # Yangilangan ro'yxatga qaytarish
        total_count = archive_service.get_user_count(user_id)
        if total_count == 0:
            cleared_txt = (
                "🗄 <b>Sizning arxivingiz tozalandi (bo'sh).</b>\n\nYangi so'rovlaringiz avtomatik tarzda saqlanadi."
                if lang == "uz" else
                ("🗄 <b>Ваш архив очищен (пуст).</b>\n\nНовые запросы будут автоматически сохраняться."
                 if lang == "ru" else
                 "🗄 <b>Your archive has been cleared (empty).</b>\n\nNew requests will be saved automatically.")
            )
            try:
                await callback.message.edit_text(cleared_txt, parse_mode="HTML")
            except Exception:
                pass
            return

        items = archive_service.get_user_items(user_id=user_id, limit=5, offset=0)
        text = (
            f"🗄 <b>Sizning so'rovlar arxivingiz</b>\n"
            f"📊 <b>Jami saqlangan so'rovlar:</b> {total_count} ta\n\n"
            f"👇 <i>Kerakli so'rovni tanlang:</i>"
            if lang == "uz" else
            (f"🗄 <b>Ваш архив запросов</b>\n"
             f"📊 <b>Всего сохранено:</b> {total_count}\n\n"
             f"👇 <i>Выберите нужный запрос:</i>"
             if lang == "ru" else
             f"🗄 <b>Your request archive</b>\n"
             f"📊 <b>Total saved:</b> {total_count}\n\n"
             f"👇 <i>Select a request:</i>")
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_archive_list_keyboard(items=items, page=0, total_count=total_count, lang=lang)
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
            if lang == "uz" else
            (f"⚠️ <b>ВНИМАНИЕ: Очистка всего архива!</b>\n\n"
             f"Все ваши <b>{total_count}</b> сохраненных запросов и результатов будут безвозвратно удалены.\n\n"
             f"Подтверждаете очистку архива?"
             if lang == "ru" else
             f"⚠️ <b>WARNING: Clear entire archive!</b>\n\n"
             f"All your <b>{total_count}</b> saved requests and results will be permanently deleted.\n\n"
             f"Do you confirm clearing the archive?")
        )
        try:
            await callback.message.edit_text(
                confirm_text,
                parse_mode="HTML",
                reply_markup=get_archive_clear_confirm_keyboard(lang=lang)
            )
        except Exception:
            pass
        await callback.answer()
        return

    # Butun arxivni tozalashni bajarish: arx:do_clear
    if action == "do_clear":
        deleted_count = archive_service.clear_user_archive(user_id)
        clear_ok = (
            f"✅ Arxiv to'liq tozalandi ({deleted_count} ta so'rov o'chirildi)!" if lang == "uz" else
            (f"✅ Архив полностью очищен ({deleted_count} запросов удалено)!" if lang == "ru" else
             f"✅ Archive completely cleared ({deleted_count} requests deleted)!")
        )
        await callback.answer(clear_ok, show_alert=True)
        cleared_msg = (
            "🗄 <b>Arxiv to'liq tozalandi.</b>\n\nYangi so'rovlaringiz avtomatik tarzda qaytadan saqlanadi."
            if lang == "uz" else
            ("🗄 <b>Архив полностью очищен.</b>\n\nНовые запросы будут автоматически сохраняться."
             if lang == "ru" else
             "🗄 <b>Archive completely cleared.</b>\n\nNew requests will be saved automatically.")
        )
        try:
            await callback.message.edit_text(cleared_msg, parse_mode="HTML")
        except Exception:
            pass
        return


# =====================================================================
# VIDEO VA RASM HANDLERLARI
# =====================================================================

@router.message(F.video)
async def handle_video_message(message: Message):
    """Video qabul qilinganda dumaloq qilish yoki MP3 audio ajratish taklifi"""
    lang = get_user_lang(message.from_user.id)
    wait_video = (
        "⏳ <i>Video qabul qilindi. Dumaloq (Video Note) formatiga aylantirilmoqda...</i>" if lang == "uz" else
        ("⏳ <i>Видео принято. Преобразование в формат видеосообщения (кружок)...</i>" if lang == "ru" else
         "⏳ <i>Video received. Converting to video note...</i>")
    )
    status_msg = await message.reply(wait_video, parse_mode="HTML")
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
                caption_circle = (
                    "🔘 <b>Dumaloq video tayyor!</b>" if lang == "uz" else
                    ("🔘 <b>Круглое видео готово!</b>" if lang == "ru" else
                     "🔘 <b>Video note ready!</b>")
                )
                await message.answer_video(video=FSInputFile(circle_path), caption=caption_circle, parse_mode="HTML")
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        else:
            err_tg = (
                "❌ Telegram xatoligi yuz berdi." if lang == "uz" else
                ("❌ Произошла ошибка Telegram." if lang == "ru" else
                 "❌ Telegram error occurred.")
            )
            await status_msg.edit_text(err_tg, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Videoni dumaloq qilishda xatolik: {e}")
        err_conv = (
            "❌ Videoni aylantirishda xatolik yuz berdi." if lang == "uz" else
            ("❌ Ошибка при конвертации видео." if lang == "ru" else
             "❌ Error converting video.")
        )
        await status_msg.edit_text(err_conv, parse_mode="HTML")
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
    lang = get_user_lang(callback.from_user.id)

    if not item or "path" not in item:
        err_exp = (
            "Video eskirgan." if lang == "uz" else
            ("Видео устарело." if lang == "ru" else "Video expired.")
        )
        await callback.answer(err_exp, show_alert=True)
        return

    video_path = item["path"]
    if action == "audio":
        await callback.answer(
            "Audio ajratilmoqda..." if lang == "uz" else
            ("Извлечение аудио..." if lang == "ru" else "Extracting audio...")
        )
        wait_aud = (
            "🎵 <i>Audioni MP3 formatida ajratib olinmoqda...</i>" if lang == "uz" else
            ("🎵 <i>Извлечение аудио в формате MP3...</i>" if lang == "ru" else
             "🎵 <i>Extracting audio in MP3 format...</i>")
        )
        status_msg = await callback.message.reply(wait_aud, parse_mode="HTML")
        try:
            mp3_path = await extract_audio_from_video(video_path)
            try:
                await status_msg.delete()
            except Exception:
                pass
            caption_mp3 = (
                "🎵 <b>Videodan ajratilgan MP3 audio</b>" if lang == "uz" else
                ("🎵 <b>MP3 аудио из видео</b>" if lang == "ru" else
                 "🎵 <b>MP3 audio extracted from video</b>")
            )
            await callback.message.answer_audio(audio=FSInputFile(mp3_path), caption=caption_mp3, parse_mode="HTML")
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except Exception as e:
            err_aud = (
                f"❌ Audio ajratishda xatolik: {e}" if lang == "uz" else
                (f"❌ Ошибка извлечения аудио: {e}" if lang == "ru" else
                 f"❌ Error extracting audio: {e}")
            )
            await status_msg.edit_text(err_aud, parse_mode="HTML")

    elif action == "circle":
        await callback.answer(
            "Dumaloq video tayyorlanmoqda..." if lang == "uz" else
            ("Подготовка круглого видео..." if lang == "ru" else "Preparing video note...")
        )
        wait_circ = (
            "⏳ <i>Dumaloq formatga o'tkazilmoqda...</i>" if lang == "uz" else
            ("⏳ <i>Преобразование в круглое видео...</i>" if lang == "ru" else
             "⏳ <i>Converting to video note...</i>")
        )
        status_msg = await callback.message.reply(wait_circ, parse_mode="HTML")
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
            err_circ = (
                f"❌ Dumaloq qilishda xatolik: {e}" if lang == "uz" else
                (f"❌ Ошибка при конвертации в кружок: {e}" if lang == "ru" else
                 f"❌ Error converting to video note: {e}")
            )
            await status_msg.edit_text(err_circ, parse_mode="HTML")


@router.message(F.photo)
async def handle_photo_message(message: Message):
    """Rasm qabul qilinganda uni AI ga yuborish"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    caption = message.caption or ""
    current_mode = user_active_mode.get(user_id, "NORMAL")

    # Agar yuridik (LEGAL_DOC yoki NOTARY) rejimida rasm (tilxat, shartnoma, chek) yuborilgan bo'lsa:
    if current_mode.startswith("LEGAL_DOC") or current_mode.startswith("NOTARY"):
        doc_subtype = current_mode.split(":", 1)[1] if ":" in current_mode else ("notary_both" if current_mode.startswith("NOTARY") else "lawsuit")
        if current_mode.startswith("NOTARY") and not doc_subtype.startswith("notary"):
            doc_subtype = f"notary_{doc_subtype}"
        target_name = (
            "Notariusdan Ijro xati to'plami (3757-son)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
            ("Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
            ("Sudga Da'vo arizasi" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
            ("Talabnoma (Pretenziya)" if doc_subtype != "both" else "Da'vo va Talabnoma")))
        )
        if lang == "ru":
            target_name = (
                "Комплект для исполнительной надписи нотариуса (Приказ № 3757)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
                ("Справка о задолженности и Сопроводительное" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
                ("Исковое заявление в суд" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
                ("Досудебная претензия" if doc_subtype != "both" else "Иск и Претензия")))
            )
        elif lang == "en":
            target_name = (
                "Notarial Execution Inscription Package (Decree 3757)" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
                ("Debt Certificate and Cover Letter" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
                ("Court Lawsuit" if doc_subtype in ("lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage") else
                ("Demand Letter" if doc_subtype != "both" else "Lawsuit & Demand")))
            )

        wait_legal = (
            f"⚖️ <i>Rasm / tilxat o'rganilib, «{target_name}» tayyorlanmoqda, iltimos kuting...</i>" if lang == "uz" else
            (f"⚖️ <i>Анализируется фото / расписка и составляется «{target_name}»...</i>" if lang == "ru" else
             f"⚖️ <i>Analyzing photo / promissory note and drafting «{target_name}»...</i>")
        )
        status_msg = await message.reply(wait_legal, parse_mode="HTML")
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        try:
            photo = message.photo[-1]
            file_info = await message.bot.get_file(photo.file_id)
            photo_bytes_io = io.BytesIO()
            await message.bot.download_file(file_info.file_path, destination=photo_bytes_io)
            photo_bytes = photo_bytes_io.getvalue()

            legal_extract_prompt = (
                f"Ushbu rasmda yuridik hujjat (qarz tilxati, shartnoma, dalolatnoma yoki chek) aks etgan.\n"
                f"Foydalanuvchi izohi: {caption}\n"
                f"Iltimos, rasmdagi barcha yozuvlarni, sanalarni, taraflarning ism-familiyalarini, qarz yoki to'lov summasini to'liq o'qib bering."
            )
            extracted_facts = await ai_service.ask_gemini(
                user_id=user_id,
                prompt=legal_extract_prompt,
                image_bytes=photo_bytes,
                mime_type="image/jpeg"
            )

            legal_details = f"Rasmda aks etgan hujjat/tilxat ma'lumotlari:\n{extracted_facts}\nFoydalanuvchi izohi: {caption}"
            legal_doc = await ai_service.generate_legal_document(
                user_id=user_id,
                doc_type=doc_subtype,
                details=legal_details,
                lang=lang
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            title = f"{target_name}: Rasm / Tilxat asosida"
            user_name = message.from_user.full_name if message.from_user else "Foydalanuvchi"
            res_id = store_result(title=title, content=legal_doc, user_name=user_name, user_id=user_id, category="legal")
            result_markup = get_legal_result_keyboard(res_id=res_id, lang=lang)
            await send_ai_response(message, legal_doc, res_id=res_id, reply_markup=result_markup, lang=lang)
            return
        except Exception as e:
            logger.exception(f"Rasmdan yuridik hujjat tuzishda xatolik: {e}")
            await status_msg.edit_text("❌ Rasm orqali da'vo/talabnoma tuzishda xatolik yuz berdi.", parse_mode="HTML")
            return

    wait_photo = (
        "🤔 <i>Rasm tahlil qilinmoqda...</i>" if lang == "uz" else
        ("🤔 <i>Анализ изображения...</i>" if lang == "ru" else
         "🤔 <i>Analyzing image...</i>")
    )
    status_msg = await message.reply(wait_photo, parse_mode="HTML")
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await message.bot.download_file(file_info.file_path, destination=photo_bytes_io)
        photo_bytes = photo_bytes_io.getvalue()

        default_prompt = (
            "Ushbu rasmni chuqur tahlil qiling va nimalar aks etganini yozing." if lang == "uz" else
            ("Проведите глубокий анализ этого изображения и подробно опишите, что на нем изображено." if lang == "ru" else
             "Analyze this image in detail and describe everything shown.")
        )
        response = await ai_service.ask_gemini(
            user_id=user_id,
            prompt=caption or default_prompt,
            image_bytes=photo_bytes,
            mime_type="image/jpeg"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

        title_img = "Rasm tahlili" if lang == "uz" else ("Анализ изображения" if lang == "ru" else "Image Analysis")
        res_id = store_result(
            title=title_img,
            content=response,
            user_name=message.from_user.full_name or "Foydalanuvchi",
            user_id=user_id,
            category="image"
        )
        await send_ai_response(message, response, res_id=res_id, lang=lang)

    except Exception as e:
        logger.exception(f"Rasmni tahlil qilishda xatolik: {e}")
        err_photo = (
            "❌ Rasmni tahlil qilishda xatolik yuz berdi." if lang == "uz" else
            ("❌ Ошибка при анализе изображения." if lang == "ru" else
             "❌ Error analyzing image.")
        )
        await status_msg.edit_text(err_photo, parse_mode="HTML")


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

                title = format_doc_title(filename, custom_query, lang=lang)
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

    # 1.2. Rejim: LEGAL_DOC yoki NOTARY (Sudga Da'vo, Talabnoma, Notarius Ijro xati)
    if current_mode.startswith("LEGAL_DOC") or current_mode.startswith("NOTARY"):
        doc_subtype = current_mode.split(":", 1)[1] if ":" in current_mode else ("notary_both" if current_mode.startswith("NOTARY") else "lawsuit")
        if current_mode.startswith("NOTARY") and not doc_subtype.startswith("notary"):
            doc_subtype = f"notary_{doc_subtype}"
        lang = get_user_lang(user_id)

        wait_text = (
            "📜 <i>Notariat to'g'risidagi qonun va 3757-sonli qaror talablari asosida Ijro xati to'plami tuzilmoqda, iltimos kuting...</i>"
            if current_mode.startswith("NOTARY") and lang == "uz" else
            ("📜 <i>Формируется комплект для исполнительной надписи нотариуса (Приказ № 3757)...</i>"
             if current_mode.startswith("NOTARY") and lang == "ru" else
             ("📜 <i>Drafting Notary Execution Inscription package (Decree No. 3757)...</i>"
              if current_mode.startswith("NOTARY") and lang == "en" else
              ("⚖️ <i>Qonun moddalari va rasmiy talablar asosida hujjat tuzilmoqda, iltimos kuting...</i>" if lang == "uz" else
               ("⚖️ <i>Формируется официальный юридический документ со ссылками на законы...</i>" if lang == "ru" else
                "⚖️ <i>Drafting official legal document with statutory grounds...</i>"))))
        )
        status_msg = await message.reply(
            wait_text,
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        try:
            legal_doc = await ai_service.generate_legal_document(
                user_id=user_id,
                doc_type=doc_subtype,
                details=text,
                lang=lang
            )
            try:
                await status_msg.delete()
            except Exception:
                pass

            if legal_doc.startswith("⚠️") or legal_doc.startswith("❌"):
                await message.answer(legal_doc, parse_mode="HTML")
                return

            title_prefix = (
                "Notarius Ijro xati" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
                ("Qarzdorlik ma'lumotnomasi va Ilova xati" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
                ("Da'vo arizasi" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
                ("Talabnoma" if doc_subtype != "both" else "Da'vo va Talabnoma")))
            )
            if lang == "ru":
                title_prefix = (
                    "Исполнительная надпись нотариуса" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
                    ("Справка о задолженности и Сопроводительное" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
                    ("Исковое заявление" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
                    ("Претензия" if doc_subtype != "both" else "Иск и Претензия")))
                )
            elif lang == "en":
                title_prefix = (
                    "Notary Execution Inscription" if doc_subtype.startswith("notary") or doc_subtype in ("notary_exec", "notary_app", "notary_notice", "notary_both") else
                    ("Debt Certificate & Cover Letter" if doc_subtype in ("debt_cert", "debt_certificate", "malumotnoma") else
                    ("Court Claim" if any(k in doc_subtype for k in ["lawsuit", "debt", "contract", "alimony", "divorce", "labor", "damage"]) else
                    ("Demand Letter" if doc_subtype != "both" else "Lawsuit & Demand")))
                )

            clean_snippet = format_doc_title("", text[:30], lang=lang)
            title = f"{title_prefix}: {clean_snippet}"
            res_id = store_result(title=title, content=legal_doc, user_name=user_name, user_id=user_id, category="legal")
            result_markup = get_legal_result_keyboard(res_id=res_id, lang=lang)
            await send_ai_response(message, legal_doc, res_id=res_id, reply_markup=result_markup, lang=lang)
            return
        except Exception as e:
            logger.exception(f"Yuridik hujjat tuzishda xatolik: {e}")
            await status_msg.edit_text("❌ Hujjat tuzish jarayonida xatolik yuz berdi.", parse_mode="HTML")
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
        status_msg = await message.reply(
            "📽 <i>Taqdimot slaydlari rejalashtirilmoqda va PowerPoint (.pptx) fayli yaratilmoqda...</i>" if lang == "uz" else
            ("📽 <i>Формирование слайдов и создание файла PowerPoint (.pptx)...</i>" if lang == "ru" else
             "📽 <i>Planning slides and creating PowerPoint (.pptx) file...</i>"),
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        slides_text = await ai_service.format_for_presentation(user_id=user_id, text=text, lang=lang)
        clean_slide_title = format_doc_title("", text[:40], lang=lang)
        pptx_path = generate_presentation(title=clean_slide_title, content=slides_text, user_name=user_name, lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass

        title_slides = (
            f"Prezentatsiya: {clean_slide_title[:30]}" if lang == "uz" else
            (f"Презентация: {clean_slide_title[:30]}" if lang == "ru" else
             f"Presentation: {clean_slide_title[:30]}")
        )
        res_id = store_result(title=title_slides, content=slides_text, user_name=user_name, user_id=user_id, category="slides")
        caption_pptx = (
            f"📽 <b>PowerPoint taqdimotingiz tayyor!</b>\n<i>Mavzu: {clean_slide_title[:45]}</i>" if lang == "uz" else
            (f"📽 <b>Ваша презентация PowerPoint готова!</b>\n<i>Тема: {clean_slide_title[:45]}</i>" if lang == "ru" else
             f"📽 <b>Your PowerPoint presentation is ready!</b>\n<i>Topic: {clean_slide_title[:45]}</i>")
        )
        pptx_fn = f"Prezentatsiya_{res_id}.pptx" if lang != "en" else f"Presentation_{res_id}.pptx"
        try:
            await message.answer_document(
                document=FSInputFile(pptx_path, filename=pptx_fn),
                caption=caption_pptx,
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
        status_msg = await message.reply(
            "📊 <i>Ma'lumotlar qayta ishlanmoqda va hujjatlar tayyorlanmoqda...</i>" if lang == "uz" else
            ("📊 <i>Обработка данных и подготовка документов...</i>" if lang == "ru" else
             "📊 <i>Processing data and preparing documents...</i>"),
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)

        clean_office_title = format_doc_title("", text[:35], lang=lang)
        docx_path = generate_docx(title=clean_office_title, content=text, user_name=user_name, lang=lang)
        pdf_path = generate_pdf(title=clean_office_title, content=text, user_name=user_name, lang=lang)
        table_text = await ai_service.format_for_excel(user_id, text, lang=lang)
        xlsx_path = generate_excel(title=clean_office_title, content=table_text, user_name=user_name, lang=lang)

        try:
            await status_msg.delete()
        except Exception:
            pass

        title_office = (
            clean_office_title if clean_office_title else
            ("Hujjatlar to'plami" if lang == "uz" else ("Пакет документов" if lang == "ru" else "Document package"))
        )
        res_id = store_result(title=title_office, content=text, user_name=user_name, user_id=user_id, category="office")
        caption_docx = (
            "📄 <b>Word formati (.docx)</b>" if lang == "uz" else
            ("📄 <b>Формат Word (.docx)</b>" if lang == "ru" else "📄 <b>Word format (.docx)</b>")
        )
        caption_pdf = (
            "📑 <b>PDF formati (.pdf)</b>" if lang == "uz" else
            ("📑 <b>Формат PDF (.pdf)</b>" if lang == "ru" else "📑 <b>PDF format (.pdf)</b>")
        )
        caption_xlsx = (
            "📊 <b>Excel jadvali (.xlsx)</b>" if lang == "uz" else
            ("📊 <b>Таблица Excel (.xlsx)</b>" if lang == "ru" else "📊 <b>Excel spreadsheet (.xlsx)</b>")
        )
        docx_fn = f"Hujjat_{res_id}.docx" if lang == "uz" else (f"Dokument_{res_id}.docx" if lang == "ru" else f"Document_{res_id}.docx")
        pdf_fn = f"Hujjat_{res_id}.pdf" if lang == "uz" else (f"Dokument_{res_id}.pdf" if lang == "ru" else f"Document_{res_id}.pdf")
        xlsx_fn = f"Jadval_{res_id}.xlsx" if lang == "uz" else (f"Tablitsa_{res_id}.xlsx" if lang == "ru" else f"Spreadsheet_{res_id}.xlsx")
        try:
            await message.answer_document(
                document=FSInputFile(docx_path, filename=docx_fn),
                caption=caption_docx,
                parse_mode="HTML"
            )
            await message.answer_document(
                document=FSInputFile(pdf_path, filename=pdf_fn),
                caption=caption_pdf,
                parse_mode="HTML"
            )
            await message.answer_document(
                document=FSInputFile(xlsx_path, filename=xlsx_fn),
                caption=caption_xlsx,
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

        status_msg = await message.reply(
            "📑 <i>Hujjat matni tahlil qilinmoqda...</i>" if lang == "uz" else
            ("📑 <i>Анализ текста документа...</i>" if lang == "ru" else
             "📑 <i>Analyzing document text...</i>"),
            parse_mode="HTML"
        )
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        analysis = await ai_service.analyze_document(user_id=user_id, doc_text=text, filename="Matnli Hujjat", lang=lang)
        try:
            await status_msg.delete()
        except Exception:
            pass

        if analysis.startswith("⚠️") or analysis.startswith("❌"):
            await message.answer(analysis, parse_mode="HTML")
            return

        title_doc = "Hujjat tahlili" if lang == "uz" else ("Анализ документа" if lang == "ru" else "Document Analysis")
        res_id = store_result(title=title_doc, content=analysis, user_name=user_name, user_id=user_id, category="doc")
        await send_ai_response(message, analysis, res_id=res_id, lang=lang)
        return

    # 6. Rejim: AI_CHAT yoki oddiy suhbat
    lang = get_user_lang(user_id)
    status_msg = await message.reply(
        "🤔 <i>O'ylamoqda...</i>" if lang == "uz" else
        ("🤔 <i>Думает...</i>" if lang == "ru" else
         "🤔 <i>Thinking...</i>"),
        parse_mode="HTML"
    )
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
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    wait_insta = (
        "⏳ <i>Instagram video yuklab olinmoqda, iltimos kuting...</i>" if lang == "uz" else
        ("⏳ <i>Скачивание видео из Instagram, пожалуйста подождите...</i>" if lang == "ru" else
         "⏳ <i>Downloading Instagram video, please wait...</i>")
    )
    status_msg = await message.reply(wait_insta, parse_mode="HTML")
    file_path = None
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)
        video_data = await download_instagram_video(url)
        file_path = video_data['file_path']

        bot_info = await message.bot.get_me()
        caption = (
            f"🎬 <b>{video_data['title']}</b>\n\n🤖 <i>@{bot_info.username} orqali yuklandi</i>" if lang == "uz" else
            (f"🎬 <b>{video_data['title']}</b>\n\n🤖 <i>Загружено через @{bot_info.username}</i>" if lang == "ru" else
             f"🎬 <b>{video_data['title']}</b>\n\n🤖 <i>Downloaded via @{bot_info.username}</i>")
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
            reply_markup=get_video_options_keyboard(file_path, lang=lang)
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except FileTooLargeError as e:
        err_large = (
            f"❌ <b>Hajm bo'yicha cheklov:</b>\n{str(e)}" if lang == "uz" else
            (f"❌ <b>Ограничение по размеру:</b>\n{str(e)}" if lang == "ru" else
             f"❌ <b>Size limit:</b>\n{str(e)}")
        )
        await status_msg.edit_text(err_large, parse_mode="HTML")
    except PrivateMediaError as e:
        err_priv = (
            f"🔒 <b>Yopiq hisob:</b>\n{str(e)}" if lang == "uz" else
            (f"🔒 <b>Закрытый аккаунт:</b>\n{str(e)}" if lang == "ru" else
             f"🔒 <b>Private account:</b>\n{str(e)}")
        )
        await status_msg.edit_text(err_priv, parse_mode="HTML")
    except DownloadException:
        err_down = (
            "❌ <b>Xatolik yuz berdi:</b>\nVideoni yuklab bo'lmadi." if lang == "uz" else
            ("❌ <b>Произошла ошибка:</b>\nНе удалось скачать видео." if lang == "ru" else
             "❌ <b>Error:</b>\nCould not download video.")
        )
        await status_msg.edit_text(err_down, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik: {e}")
        err_tech = (
            "❌ Texnik xatolik yuz berdi." if lang == "uz" else
            ("❌ Произошла техническая ошибка." if lang == "ru" else
             "❌ Technical error occurred.")
        )
        await status_msg.edit_text(err_tech, parse_mode="HTML")
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
                InlineKeyboardButton(text="👥 Foydalanuvchilar ro'yxati", callback_data="adm:list_users:0"),
                InlineKeyboardButton(text="🗑 Userni o'chirish", callback_data="adm:select_delete:0")
            ],
            [
                InlineKeyboardButton(text=toggle_text, callback_data="adm:toggle_mode")
            ],
            [
                InlineKeyboardButton(text="➕ User qo'shish yo'riqnomasi", callback_data="adm:help_add"),
                InlineKeyboardButton(text="➖ User o'chirish yo'riqnomasi", callback_data="adm:help_remove")
            ]
        ]
    )


def render_users_list(page: int = 0, select_delete: bool = False, admin_id: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Foydalanuvchilar ro'yxati matni va boshqaruv klaviaturasi (sahifalash bilan)"""
    all_users = access_guard.get_allowed_users()
    admin_ids = access_guard.get_admins()

    if not all_users:
        msg = "ℹ️ <b>Hozircha botda foydalanuvchilar mavjud emas.</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi user qo'shish", callback_data="adm:help_add")],
            [InlineKeyboardButton(text="🔙 Admin panelga qaytish", callback_data="adm:panel")]
        ])
        return msg, kb

    # Foydalanuvchilarni saralash: adminlar birinchi, so'ngra yangi qo'shilganlar
    def sort_key(item):
        uid_str, info = item
        uid = int(uid_str) if uid_str.isdigit() else 0
        is_adm = uid in admin_ids
        added = info.get("added_at", "")
        return (1 if is_adm else 0, added)

    sorted_users = sorted(all_users.items(), key=sort_key, reverse=True)

    per_page = 6
    total_users = len(sorted_users)
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_users = sorted_users[start_idx:end_idx]

    mode_title = (
        "🗑 <b>FOYDALANUVCHINI BOTDAN O'CHIRISH</b>\n"
        "<i>Quyidagi foydalanuvchilar ro'yxatidan o'chirmoqchi bo'lgan shaxsni tanlang:</i>"
        if select_delete else
        "👥 <b>BOTDAN FOYDALANUVCHILAR RO'YXATI</b>"
    )

    msg = (
        f"{mode_title}\n\n"
        f"📊 Jami foydalanuvchilar: <b>{total_users}</b> nafar | Sahifa: <b>{page + 1}/{total_pages}</b>\n\n"
    )

    kb_rows = []

    for idx, (uid_str, info) in enumerate(page_users, start=start_idx + 1):
        uid = int(uid_str) if uid_str.isdigit() else 0
        is_adm = uid in admin_ids
        badge = "👑 [Admin]" if is_adm else "👤 [User]"
        uname = f"@{info.get('username')}" if info.get('username') else "username yo'q"
        name = info.get('name', 'Nomsiz')
        added_at = info.get('added_at', '-')
        last_act = info.get('last_active') or added_at

        msg += (
            f"<b>{idx}. {badge} {name}</b> ({uname})\n"
            f"   🆔 ID: <code>{uid_str}</code>\n"
            f"   📅 Qo'shildi: {added_at}\n"
            f"   ⚡️ Faollik: {last_act}\n\n"
        )

        # O'chirish tugmasi: O'zini va ENV dagi super adminni o'chirish taqiqlanadi
        is_self = (uid == admin_id)
        is_super = (uid in ADMIN_IDS)
        if not is_self and not is_super:
            clean_btn_name = name[:16]
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"🗑 O'chirish: {clean_btn_name} ({uid_str[-4:]})",
                    callback_data=f"adm:del_prompt:{uid_str}"
                )
            ])

    # Sahifalash tugmalari
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            cb_prev = f"adm:select_delete:{page - 1}" if select_delete else f"adm:list_users:{page - 1}"
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=cb_prev))
        nav_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="adm:noop"))
        if page < total_pages - 1:
            cb_next = f"adm:select_delete:{page + 1}" if select_delete else f"adm:list_users:{page + 1}"
            nav_buttons.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=cb_next))
        kb_rows.append(nav_buttons)

    # Qo'shimcha amallar
    cb_refresh = f"adm:select_delete:{page}" if select_delete else f"adm:list_users:{page}"
    kb_rows.append([
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=cb_refresh),
        InlineKeyboardButton(text="➕ Yangi user qo'shish", callback_data="adm:help_add")
    ])
    kb_rows.append([
        InlineKeyboardButton(text="🔙 Admin panelga qaytish", callback_data="adm:panel")
    ])

    return msg, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.message(Command("admin"))
@router.message(Command("qoriqchi"))
@router.message(F.text.in_(get_all_button_variants("btn_guard")))
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


@router.callback_query(F.data == "adm:panel")
async def handle_adm_panel_callback(callback: CallbackQuery):
    """Admin paneliga qaytish callbacki"""
    if not access_guard.is_admin(callback.from_user.id):
        await callback.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            get_admin_panel_text(),
            parse_mode="HTML",
            reply_markup=get_admin_panel_keyboard()
        )
    except Exception:
        await callback.message.answer(
            get_admin_panel_text(),
            parse_mode="HTML",
            reply_markup=get_admin_panel_keyboard()
        )


@router.callback_query(F.data == "adm:noop")
async def handle_adm_noop(callback: CallbackQuery):
    """Sahifa ko'rsatkich tugmasi uchun noop"""
    await callback.answer()


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


@router.callback_query(F.data.startswith("adm:list_users"))
@router.callback_query(F.data.startswith("adm:select_delete"))
async def handle_list_users_callback(callback: CallbackQuery):
    """Foydalanuvchilar ro'yxati callbacki (sahifalash va o'chirish rejimi bilan)"""
    if not access_guard.is_admin(callback.from_user.id):
        await callback.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        return
    await callback.answer()

    parts = callback.data.split(":")
    is_delete_mode = (parts[1] == "select_delete")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    msg, kb = render_users_list(page=page, select_delete=is_delete_mode, admin_id=callback.from_user.id)
    try:
        await callback.message.edit_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(msg, parse_mode="HTML", reply_markup=kb)


@router.message(Command("users"))
async def handle_list_users_cmd(message: Message):
    """Ruxsat berilganlar ro'yxatini ko'rish buyrug'i: /users"""
    if not access_guard.is_admin(message.from_user.id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    msg, kb = render_users_list(page=0, select_delete=False, admin_id=message.from_user.id)
    await message.answer(msg, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:del_prompt:"))
async def handle_del_prompt(callback: CallbackQuery):
    """Foydalanuvchini o'chirishdan oldin tasdiqlash oynasi"""
    admin_id = callback.from_user.id
    if not access_guard.is_admin(admin_id):
        await callback.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        return

    target_id_str = callback.data.split(":")[2]
    target_id = int(target_id_str) if target_id_str.isdigit() else 0

    if target_id == admin_id:
        await callback.answer("⚠️ O'zingizni o'chira olmaysiz!", show_alert=True)
        return

    if target_id in ADMIN_IDS:
        await callback.answer("⚠️ Asosiy bosh administratorni o'chirib bo'lmaydi!", show_alert=True)
        return

    user_info = access_guard.get_user_info(target_id)
    if not user_info:
        await callback.answer("ℹ️ Foydalanuvchi allaqachon o'chirilgan yoki topilmadi.", show_alert=True)
        msg, kb = render_users_list(page=0, admin_id=admin_id)
        try:
            await callback.message.edit_text(msg, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        return

    await callback.answer()
    name = user_info.get("name", "Nomsiz")
    uname = f"@{user_info.get('username')}" if user_info.get("username") else "mavjud emas"
    added_at = user_info.get("added_at", "-")
    last_act = user_info.get("last_active") or added_at

    confirm_txt = (
        "⚠️ <b>FOYDALANUVCHINI BOTDAN O'CHIRISH</b>\n\n"
        f"👤 <b>Ismi:</b> {name}\n"
        f"🔗 <b>Username:</b> {uname}\n"
        f"🆔 <b>Telegram ID:</b> <code>{target_id}</code>\n"
        f"📅 <b>Qo'shilgan sana:</b> {added_at}\n"
        f"⚡️ <b>Oxirgi faolligi:</b> {last_act}\n\n"
        "❓ <b>Haqiqatdan ham ushbu foydalanuvchini botdan o'chirib, uning ruxsatini bekor qilmoqchimisiz?</b>\n\n"
        "<i>Eslatma: O'chirilgandan so'ng ushbu foydalanuvchi bot xizmatlaridan foydalana olmaydi.</i>"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Ha, botdan o'chirilsin", callback_data=f"adm:confirm_del:{target_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:list_users:0")
        ]
    ])

    try:
        await callback.message.edit_text(confirm_txt, parse_mode="HTML", reply_markup=confirm_kb)
    except Exception:
        await callback.message.answer(confirm_txt, parse_mode="HTML", reply_markup=confirm_kb)


@router.callback_query(F.data.startswith("adm:confirm_del:"))
async def handle_confirm_del(callback: CallbackQuery):
    """Foydalanuvchini botdan yakuniy o'chirish callbacki"""
    admin_id = callback.from_user.id
    if not access_guard.is_admin(admin_id):
        await callback.answer("🔒 Faqat adminlar uchun!", show_alert=True)
        return

    target_id_str = callback.data.split(":")[2]
    target_id = int(target_id_str) if target_id_str.isdigit() else 0

    if target_id == admin_id:
        await callback.answer("⚠️ O'zingizni o'chira olmaysiz!", show_alert=True)
        return

    if target_id in ADMIN_IDS:
        await callback.answer("⚠️ Asosiy bosh administratorni o'chirib bo'lmaydi!", show_alert=True)
        return

    user_info = access_guard.get_user_info(target_id)
    target_name = user_info.get("name", f"ID_{target_id}") if user_info else f"ID_{target_id}"

    success = access_guard.remove_user(target_id)
    if not success:
        await callback.answer("ℹ️ Foydalanuvchi topilmadi yoki allaqachon o'chirilgan.", show_alert=True)
    else:
        await callback.answer("✅ Foydalanuvchi muvaffaqiyatli o'chirildi!", show_alert=True)

    # Foydalanuvchiga bildirishnoma yuborish
    try:
        await callback.bot.send_message(
            chat_id=target_id,
            text=(
                "🚫 <b>DIQQAT!</b>\n\n"
                "Administrator tomonidan sizning ushbu botdan foydalanish huquqingiz bekor qilindi (botdan o'chirildingiz).\n\n"
                "Savollar bo'lsa, administrator bilan bog'lanishingiz mumkin."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.info(f"O'chirilgan foydalanuvchiga ({target_id}) xabar jo'natilmadi: {e}")

    result_txt = (
        "✅ <b>FOYDALANUVCHI BOTDAN O'CHIRILDI!</b>\n\n"
        f"👤 <b>Ismi:</b> {target_name}\n"
        f"🆔 <b>Telegram ID:</b> <code>{target_id}</code>\n"
        f"👑 <b>O'chirgan admin:</b> {callback.from_user.full_name}\n\n"
        "Ushbu foydalanuvchining botdan foydalanish ruxsati to'liq bekor qilindi."
    )

    result_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Foydalanuvchilar ro'yxati", callback_data="adm:list_users:0")],
        [InlineKeyboardButton(text="🛡 Admin panel", callback_data="adm:panel")]
    ])

    try:
        await callback.message.edit_text(result_txt, parse_mode="HTML", reply_markup=result_kb)
    except Exception:
        await callback.message.answer(result_txt, parse_mode="HTML", reply_markup=result_kb)


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
        "1️⃣ <b>Tugma orqali:</b> <code>/users</code> buyrug'ini yuboring va kerakli user tagidagi <b>«🗑 O'chirish»</b> tugmasini bosing.\n"
        "2️⃣ <b>Buyruq orqali:</b> <code>/delete_user &lt;TELEGRAM_ID&gt;</code> buyrug'ini yuboring.\n\n"
        "Misol: <code>/delete_user 123456789</code>",
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
@router.message(Command("delete_user"))
@router.message(Command("udalit_user"))
@router.message(Command("kick_user"))
async def handle_remove_user_cmd(message: Message):
    """Admin user ruxsatini bekor qilishi / botdan o'chirishi"""
    admin_id = message.from_user.id
    if not access_guard.is_admin(admin_id):
        await message.reply("🔒 Faqat adminlar uchun!")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        msg, kb = render_users_list(page=0, select_delete=True, admin_id=admin_id)
        instruction = (
            "⚠️ <b>Foydalanuvchini botdan o'chirish:</b>\n"
            "Quyidagi ro'yxatdan o'chirmoqchi bo'lgan foydalanuvchini tanlang yoki buyruq bilan ID raqam yuboring:\n"
            "<code>/delete_user &lt;TELEGRAM_ID&gt;</code>\n\n"
        )
        await message.reply(instruction + msg, parse_mode="HTML", reply_markup=kb)
        return

    target_id = int(parts[1].strip())
    if target_id == admin_id:
        await message.reply("⚠️ <b>O'zingizni o'chira olmaysiz!</b>", parse_mode="HTML")
        return

    if target_id in ADMIN_IDS:
        await message.reply("⚠️ <b>Asosiy bosh administratorni o'chirib bo'lmaydi!</b>", parse_mode="HTML")
        return

    user_info = access_guard.get_user_info(target_id)
    target_name = user_info.get("name", f"ID_{target_id}") if user_info else f"ID_{target_id}"

    if access_guard.remove_user(target_id):
        # Foydalanuvchiga bildirishnoma yuborish
        try:
            await message.bot.send_message(
                chat_id=target_id,
                text=(
                    "🚫 <b>DIQQAT!</b>\n\n"
                    "Administrator tomonidan sizning ushbu botdan foydalanish huquqingiz bekor qilindi (botdan o'chirildingiz).\n\n"
                    "Savollar bo'lsa, administrator bilan bog'lanishingiz mumkin."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.info(f"O'chirilgan foydalanuvchiga ({target_id}) xabar yetkazilmadi: {e}")

        await message.reply(
            f"✅ <b>Foydalanuvchi muvaffaqiyatli o'chirildi!</b>\n\n"
            f"👤 <b>Ism:</b> {target_name}\n"
            f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
            f"Ushbu foydalanuvchining botdan foydalanish huquqi to'liq bekor qilindi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 Foydalanuvchilar ro'yxati", callback_data="adm:list_users:0")]
            ])
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
