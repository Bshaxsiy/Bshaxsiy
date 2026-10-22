import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, InlineKeyboardMarkup, InlineKeyboardButton

from access_guard import access_guard

logger = logging.getLogger(__name__)


def get_request_access_keyboard() -> InlineKeyboardMarkup:
    """ Ruxsati yo'q foydalanuvchilar uchun klaviatura: FAQAT yagona ruxsat so'rovi tugmasi """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📩 Administratorga ruxsat so'rovi yuborish",
                callback_data="auth_request_access"
            )
        ]
    ])


class GuardMiddleware(BaseMiddleware):
    """
    Qo'riqchi (Access Guard) Middleware:
    Barcha xabarlar va callback so'rovlarni filtrlaydi.
    Ruxsati yo'q foydalanuvchilarni botga kiritmaydi va ularga xabar beradi.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        user_id = user.id

        # Foydalanuvchi faolligini har doim qayd qilish (admin monitoringi uchun)
        access_guard.touch_user(user_id, name=user.full_name, username=user.username or "")

        # 1. Foydalanuvchi BLOKLANGANMI (foydalanishi cheklanganmi)?
        if access_guard.is_user_blocked(user_id):
            b_info = access_guard.data.get("blocked_users", {}).get(str(user_id), {})
            reason = b_info.get("reason", "Administrator tomonidan belgilangan cheklov")
            blocked_date = b_info.get("blocked_at", "")

            blocked_msg = (
                "🚫 <b>SIZNING BOTDAN FOYDALANISHINGIZ CHEKLANGAN!</b>\n\n"
                "Administrator qaroriga binoan sizning ushbu bot xizmatlaridan foydalanish imkoniyatingiz to'xtatilgan (bloklangan).\n\n"
                f"👤 <b>Foydalanuvchi:</b> {user.full_name}\n"
                f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
                f"📝 <b>Sabab:</b> {reason}\n"
            )
            if blocked_date:
                blocked_msg += f"📅 <b>Cheklangan sana:</b> {blocked_date}\n"
            blocked_msg += "\n<i>Savollar bo'lsa, bot administratoriga murojaat qilishingiz mumkin.</i>"

            if isinstance(event, Message):
                await event.reply(blocked_msg, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Sizning botdan foydalanishingiz cheklangan!", show_alert=True)
            return

        # 2. Agar foydalanuvchiga ruxsat berilgan bo'lsa -> davom etish
        if access_guard.is_user_allowed(user_id):
            return await handler(event, data)

        # 2. Ruxsati yo'q bo'lsa:
        if isinstance(event, Message):
            text = (event.text or "").strip()

            # Maxfiy admin login buyrug'i tekshiruvi: /admin_login <parol>
            if text.startswith("/admin_login"):
                parts = text.split(maxsplit=1)
                if len(parts) > 1:
                    password = parts[1].strip()
                    if access_guard.check_admin_password(password):
                        access_guard.add_admin(
                            user_id=user_id,
                            name=user.full_name,
                            username=user.username or ""
                        )
                        await event.reply(
                            "🎉 <b>Tabriklaymiz!</b>\n\n"
                            "Parol to'g'ri! Siz muvaffaqiyatli <b>Administrator</b> etib tayinlandingiz.\n"
                            "Endi botdan to'liq foydalanishingiz va boshqa foydalanuvchilarni boshqarishingiz mumkin.\n\n"
                            "Boshlash uchun: /start\n"
                            "Qo'riqchi paneli: /admin",
                            parse_mode="HTML"
                        )
                        return
                    else:
                        await event.reply(
                            "❌ <b>Xato parol!</b>\nAdministrator paroli noto'g'ri kiritildi.",
                            parse_mode="HTML"
                        )
                        return
                else:
                    await event.reply(
                        "ℹ️ <b>Admin sifatida kirish formati:</b>\n"
                        "<code>/admin_login <parol></code>\n\n"
                        "Misol: <code>/admin_login admin777</code>",
                        parse_mode="HTML"
                    )
                    return

            # O'z ID sini ko'rish buyrug'i: /my_id yoki /id
            if text in ("/my_id", "/id"):
                await event.reply(
                    f"🆔 <b>Sizning Telegram ID raqamingiz:</b> <code>{user_id}</code>",
                    parse_mode="HTML"
                )
                return

            # Standart bloklash xabari
            username_display = f"@{user.username}" if user.username else "mavjud emas"

            if access_guard.is_request_pending(user_id):
                await event.reply(
                    "🛡 <b>KIRISH CHEKLANGAN! (QO'RIQCHI TIZIMI)</b>\n\n"
                    "Kechirasiz, ushbu bot faqat ruxsat berilgan cheklangan foydalanuvchilar uchun xizmat ko'rsatadi.\n\n"
                    f"👤 <b>Ismingiz:</b> {user.full_name}\n"
                    f"🔗 <b>Username:</b> {username_display}\n"
                    f"🆔 <b>Sizning Telegram ID:</b> <code>{user_id}</code>\n\n"
                    "⏳ <b>Ruxsat so'rovingiz administratorga yuborilgan.</b> Iltimos, administrator tasdiqlashini kuting.",
                    parse_mode="HTML"
                )
                return

            await event.reply(
                "🛡 <b>KIRISH CHEKLANGAN! (QO'RIQCHI TIZIMI)</b>\n\n"
                "Kechirasiz, ushbu bot faqat ruxsat berilgan cheklangan foydalanuvchilar uchun xizmat ko'rsatadi.\n\n"
                f"👤 <b>Ismingiz:</b> {user.full_name}\n"
                f"🔗 <b>Username:</b> {username_display}\n"
                f"🆔 <b>Sizning Telegram ID:</b> <code>{user_id}</code>\n\n"
                "Botdan foydalanish uchun administratorga ruxsat so'rovi yuborishingiz mumkin:",
                reply_markup=get_request_access_keyboard(),
                parse_mode="HTML"
            )
            return

        elif isinstance(event, CallbackQuery):
            # Ruxsat so'rash tugmasi bosilganda
            if event.data == "auth_request_access":
                if access_guard.is_request_pending(user_id):
                    await event.answer("⏳ So'rovingiz administratorga allaqachon yuborilgan. Iltimos, kuting.", show_alert=True)
                    return

                access_guard.register_access_request(
                    user_id=user_id,
                    name=user.full_name,
                    username=user.username or ""
                )
                await event.answer("✅ So'rovingiz administratorga yetkazildi!", show_alert=True)

                # Administratorlarga xabar yuborish
                admins = access_guard.get_admins()
                admin_markup = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Ruxsat berish",
                            callback_data=f"auth_approve:{user_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Rad etish",
                            callback_data=f"auth_deny:{user_id}"
                        )
                    ]
                ])

                username_text = f"@{user.username}" if user.username else "mavjud emas"
                request_text = (
                    "🔔 <b>YANGI KIRISH SO'ROVI! (Qo'riqchi)</b>\n\n"
                    f"👤 <b>Foydalanuvchi:</b> {user.full_name}\n"
                    f"🔗 <b>Username:</b> {username_text}\n"
                    f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n\n"
                    "Ushbu foydalanuvchiga botdan foydalanish uchun ruxsat berasizmi?"
                )

                for admin_id in admins:
                    try:
                        await event.bot.send_message(
                            chat_id=admin_id,
                            text=request_text,
                            reply_markup=admin_markup,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"Adminga ({admin_id}) xabar yuborishda xatolik: {e}")

                try:
                    await event.message.edit_text(
                        "⏳ <b>Ruxsat so'rovingiz administratorga muvaffaqiyatli yuborildi!</b>\n\n"
                        "Administrator so'rovingizni ko'rib chiqqach, sizga ushbu chat orqali xabar yetkaziladi.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                return

            elif event.data == "auth_admin_hint":
                await event.answer(
                    "Agar siz bot egasi bo'lsangiz, quyidagi formatda parol yuboring:\n/admin_login <parol>",
                    show_alert=True
                )
                return

            else:
                await event.answer("🔒 Ushbu botdan foydalanish uchun sizga ruxsat berilmagan.", show_alert=True)
                return
