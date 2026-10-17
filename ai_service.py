import io
import os
import logging
from typing import Optional, Dict

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Siz Telegram bot ichida ishlovchi o'ta aqlli, foydali va xushmuomala sun'iy intellekt yordamchisisiz. "
    "Foydalanuvchi qaysi tilda yozsa (o'zbek, rus, ingliz va h.k.), xuddi shu tilda ravon va aniq javob bering. "
    "Javoblarni chiroyli, tushunarli, tuzilmaviy formatda (kerak bo'lsa punktlar, kod bloklari bilan) taqdim eting. "
    "Agar savol berilsa, to'liq va konstruktiv yordam bering."
)

class AIService:
    def __init__(self):
        self._client: Optional[genai.Client] = None
        self._user_chats: Dict[int, types.GenerateContentResponse] = {}
        self._api_key = config.GEMINI_API_KEY
        self._model = config.GEMINI_MODEL or "gemini-3.8-flash"
        self._init_client()

    def _init_client(self):
        if self._api_key and self._api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                self._client = genai.Client(api_key=self._api_key)
                logger.info(f"Gemini AI mijozi muvaffaqiyatli ishga tushirildi (model: {self._model})")
            except Exception as e:
                logger.error(f"Gemini AI mijozini yaratishda xatolik: {e}")
                self._client = None
        else:
            self._client = None

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip() and self._api_key != "YOUR_GEMINI_API_KEY_HERE")

    def update_api_key(self, new_key: str):
        """Yangi API kalitni o'rnatish va clientni yangilash"""
        self._api_key = new_key.strip()
        self._user_chats.clear()
        self._init_client()

    def reset_chat(self, user_id: int):
        """Foydalanuvchi chat tarixini tozalash"""
        if user_id in self._user_chats:
            del self._user_chats[user_id]
            logger.info(f"Foydalanuvchi {user_id} uchun AI chat tarixi tozalandi.")

    def _get_or_create_chat(self, user_id: int):
        if not self._client:
            self._init_client()
        if not self._client:
            return None

        if user_id not in self._user_chats:
            chat_config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            )
            chat = self._client.aio.chats.create(
                model=self._model,
                config=chat_config
            )
            self._user_chats[user_id] = chat

        return self._user_chats[user_id]

    async def ask_gemini(
        self,
        user_id: int,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg"
    ) -> str:
        """
        Gemini AI ga savol/xabar yuborish va javob olish
        """
        if not self.is_configured():
            return (
                "⚠️ <b>Gemini API kaliti kiritilmagan!</b>\n\n"
                "AI xizmatidan foydalanish uchun <code>.env</code> faylida <code>GEMINI_API_KEY</code> ni sozlang.\n\n"
                "🔑 Kalitni bepul olish: https://aistudio.google.com/app/apikey"
            )

        chat = self._get_or_create_chat(user_id)
        if not chat:
            return "❌ AI mijozi bilan bog'lanishda xatolik yuz berdi. API kalitni tekshiring."

        try:
            if image_bytes:
                # Agar rasm bo'lsa
                image_part = types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type
                )
                text_content = prompt.strip() if prompt else "Ushbu rasmni ko'rib chiqib, nima aks etganini tushuntiring."
                response = await chat.send_message(message=[image_part, text_content])
            else:
                response = await chat.send_message(message=prompt)

            if response and response.text:
                return response.text
            return "Javob olinmadi. Iltimos, savolingizni boshqacharoq shaklda berib ko'ring."

        except Exception as e:
            err_msg = str(e)
            logger.exception(f"Gemini API so'rovida xatolik: {err_msg}")

            if "API_KEY_INVALID" in err_msg or "400" in err_msg and "API key" in err_msg:
                return "❌ <b>Gemini API kaliti noto'g'ri yoki yaroqsiz!</b> Iltimos, to'g'ri kalit kiriting."
            elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
                return "⏳ <b>Gemini API so'rovlar limiti to'ldi.</b> Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
            elif "SAFETY" in err_msg:
                return "🛡 Ushbu so'rov xavfsizlik filtrlari tomonidan to'xtatildi."

            return f"❌ <b>AI xatoligi:</b>\n{err_msg[:200]}"

    async def ask_gemini_audio(
        self,
        user_id: int,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        prompt: Optional[str] = None
    ) -> str:
        """
        Audio xabarni Gemini AI ga yuborish va tahlil/javob olish
        """
        if not self.is_configured():
            return (
                "⚠️ <b>Gemini API kaliti kiritilmagan!</b>\n\n"
                "AI xizmatidan foydalanish uchun <code>.env</code> faylida <code>GEMINI_API_KEY</code> ni sozlang.\n\n"
                "🔑 Kalitni bepul olish: https://aistudio.google.com/app/apikey"
            )

        chat = self._get_or_create_chat(user_id)
        if not chat:
            return "❌ AI mijozi bilan bog'lanishda xatolik yuz berdi. API kalitni tekshiring."

        try:
            audio_part = types.Part.from_bytes(
                data=audio_bytes,
                mime_type=mime_type
            )
            instruction = (
                prompt.strip() if prompt else
                "Foydalanuvchi ushbu audio xabarni yubordi. "
                "Audio xabarni diqqat bilan eshiting va undagi savol yoki topshiriqqa to'liq, ravon va tushunarli javob bering. "
                "Agar bu fikr yoki murojaat bo'lsa, xabarning mazmunini bayon qilib, tahliliy fikringizni bildiring."
            )
            response = await chat.send_message(message=[audio_part, instruction])
            if response and response.text:
                return response.text
            return "Audio xabarni tahlil qilishda javob matni olinmadi."

        except Exception as e:
            err_msg = str(e)
            logger.exception(f"Gemini audio tahlilida xatolik: {err_msg}")

            if "API_KEY_INVALID" in err_msg or "400" in err_msg and "API key" in err_msg:
                return "❌ <b>Gemini API kaliti noto'g'ri yoki yaroqsiz!</b>"
            elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
                return "⏳ <b>Gemini so'rovlar limiti to'ldi.</b> Birozdan so'ng qayta urinib ko'ring."

            return f"❌ <b>Audio tahlilida xatolik:</b>\n{err_msg[:200]}"


ai_service = AIService()

