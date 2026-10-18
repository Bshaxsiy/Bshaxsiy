import io
import os
import re
import logging
from typing import Optional, Dict, List

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Siz Telegram bot ichida ishlovchi o'ta aqlli, foydali va xushmuomala sun'iy intellekt yordamchisisiz. "
    "Foydalanuvchi qaysi tilda yozsa (o'zbek, rus, ingliz va h.k.), xuddi shu tilda ravon va aniq javob bering. "
    "O'zbek tilidagi javoblarda qat'iy ravishda sof adabiy o'zbek tilidan foydalaning. "
    "Imlo va grammatika qoidalariga 100% rioya qiling: barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) "
    "aslo tushirib qoldirmasdan, to'liq yozing. Hech qanday keraksiz inglizcha so'zlar, atamalar yoki jargonlarni ishlatmang. "
    "Javoblarni chiroyli, tushunarli, tuzilmaviy formatda taqdim eting. "
    "Hech qanday ma'lumotni oxirida chala qoldirmang, xulosalarni to'liq yakunlang."
)

DEEP_RESEARCH_PROMPT = (
    "Siz xalqaro darajadagi bosh ilmiy va tahliliy izlanuvchisiz. "
    "Mavzu yoki savol bo'yicha eng yuqori darajada chuqur, mukammal, faktlarga asoslangan va tizimli hisobot tayyorlang.\n"
    "Qat'iy ravishda sof adabiy o'zbek tilida, imlo va grammatika qoidalariga rioya qilib, har bir so'z va qo'shimchani to'liq bayon qiling.\n"
    "Hisobot tuzilishi:\n"
    "1. 📌 Kirish va Mavzuning Dolzarbligi\n"
    "2. 🔍 Chuqur Tahlil va Asosiy Mexanizmlar\n"
    "3. 📊 Asosiy Ko'rsatkichlar, Faktlar va Statistika\n"
    "4. ⚠️ Xatarlar, Muammolar va To'siqlar\n"
    "5. 💡 Strategik Yechimlar, Amaliy Qadamlar va Xulosa\n\n"
    "Javobni professional, tushunarli va chiroyli sarlavhalar (#, ##), punktlar bilan bayon qiling."
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
                "Foydalanuvchi ushbu audio xabarni yubordi.\n"
                "Topshiriq:\n"
                "1. Audio xabarni boshidan oxirigacha diqqat bilan eshiting va undagi barcha fikrlar, savollar hamda topshiriqlarni to'liq inobatga oling.\n"
                "2. Audio xabarning oxiridagi gaplar yoki so'zlarni aslo tushirib qoldirmang, xulosani to'liq chiqaring.\n"
                "3. Javobni sof, adabiy o'zbek tilida, imlo va grammatika qoidalariga 100% rioya qilib bering.\n"
                "4. So'z oxiridagi barcha qo'shimchalar (-da, -dan, -ga, -ni, -ning, -lar, -dagi) to'liq va xatosiz yozilsin, harflar tushib qolmasin.\n"
                "5. Javobda hech qanday keraksiz inglizcha so'z yoki jargon ishlatmang.\n"
                "6. Agar foydalanuvchi murojaat yoki fikr bildirgan bo'lsa, xabar mazmunini to'liq ifodalab, ravon tahliliy xulosa bering."
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

    async def deep_research(self, user_id: int, query: str) -> str:
        """
        Mavzu bo'yicha chuqur tahlil (Deep Research) o'tkazish
        """
        if not self.is_configured():
            return "⚠️ Gemini API kaliti kiritilmagan!"

        if not self._client:
            self._init_client()
        if not self._client:
            return "❌ AI mijozi bilan bog'lanishda xatolik."

        try:
            cfg = types.GenerateContentConfig(
                system_instruction=DEEP_RESEARCH_PROMPT,
                temperature=0.4
            )
            prompt = f"Mavzu: {query}\n\nIltimos, ushbu mavzu bo'yicha har tomonlama to'liq va professional Deep Research hisoboti tayyorlang."
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=cfg
            )
            if response and response.text:
                return response.text
            return "Tadqiqot natijasi olinmadi."
        except Exception as e:
            logger.exception(f"Deep Research xatoligi: {e}")
            return f"❌ <b>Deep Research xatoligi:</b> {e}"

    async def detect_document_sections(self, doc_text: str, filename: str) -> List[str]:
        """
        Hujjatning asosiy bo'limlari, boblari, moddalari yoki mavzularini aniqlash
        """
        if not self.is_configured():
            return [
                "1. Umumiy qoidalar va asosiy tushunchalar",
                "2. Huquqlar, majburiyatlar va tartib-taomillar",
                "3. Moliyaviy shartlar, muddatlar va javobgarlik",
                "4. Nizolar, bekor qilish va yakuniy qoidalar"
            ]

        if not self._client:
            self._init_client()
        if not self._client:
            return [
                "1. Umumiy qoidalar va asosiy tushunchalar",
                "2. Huquqlar, majburiyatlar va tartib-taomillar",
                "3. Moliyaviy shartlar, muddatlar va javobgarlik",
                "4. Nizolar, bekor qilish va yakuniy qoidalar"
            ]

        prompt = (
            f"Foydalanuvchi '{filename}' nomli hujjatni yubordi.\n"
            "Ushbu hujjat matnining asosiy bo'limlari, boblari, moddalar guruhlari yoki asosiy qismlarini aniqlang.\n"
            "Maksimal 3-5 ta eng muhim va tushunarli bo'lim nomini chiqaring (har bir bo'lim nomi 15-40 belgidan oshmasin).\n"
            "Misol: '1–21-moddalar: Asosiy qoidalar' yoki '2. Moliyaviy shartlar va hisob-kitob'.\n"
            "Faqat har bir bo'lim nomini alohida qatorda yozing. Hech qanday qo'shimcha so'z, izoh yoki kirish qismi yozmang.\n\n"
            f"--- HUJJAT BOSHI ---\n{doc_text[:18000]}"
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt
            )
            if response and response.text:
                lines = [l.strip() for l in response.text.split("\n") if l.strip()]
                cleaned = []
                for line in lines:
                    c = re.sub(r"^[\*\-•\d\.\)]+\s*", "", line).strip()
                    c = c.replace("**", "").replace("`", "").strip()
                    if 4 <= len(c) <= 60:
                        cleaned.append(c)
                if len(cleaned) >= 2:
                    return cleaned[:5]
        except Exception as e:
            logger.warning(f"Bo'limlarni aniqlashda ogohlantirish: {e}")

        return [
            "1. Umumiy qoidalar va asosiy tushunchalar",
            "2. Huquqlar, majburiyatlar va tartib-taomillar",
            "3. Moliyaviy shartlar, muddatlar va javobgarlik",
            "4. Nizolar, bekor qilish va yakuniy qoidalar"
        ]

    async def analyze_document_section(
        self,
        user_id: int,
        doc_text: str,
        filename: str,
        section_name: str,
        custom_prompt: Optional[str] = None
    ) -> str:
        """
        Hujjatning tanlangan aniq bo'limi yoki yo'nalishi bo'yicha chuqur, amaliy va to'liq tahlil
        """
        if not self.is_configured():
            return "⚠️ Gemini API kaliti kiritilmagan!"

        if not self._client:
            self._init_client()
        if not self._client:
            return "❌ AI mijozi bilan bog'lanishda xatolik."

        doc_instruction = (
            f"Siz yuqori malakali bosh yurist, tahlilchi va amaliy maslahatchisiz.\n"
            f"Foydalanuvchi '{filename}' nomli rasmiy hujjatni taqdim etdi va aynan quyidagi bo'lim / yo'nalish bo'yicha tahlil so'radi:\n"
            f"🎯 TANLANGAN BO'LIM / YO'NALISH: «{section_name}»\n\n"
            "Ushbu bo'lim bo'yicha tahlilni o'ta aniq, to'liq, ravon va amaliy ahamiyatini ko'rsatgan holda bayon qiling.\n"
            "Tahlilda quyidagi bloklarni qat'iy yoriting:\n"
            "1. 📌 Bo'limning asosiy maqsadi va hayotiy mohiyati (tushunarli va ravon tilda)\n"
            "2. 🔑 Aniq qoidalar, mexanizmlar va qadamma-qadam harakatlar (amaliyotda kim nima qilishi shart)\n"
            "3. ⏱ Sanalar, qat'iy muddatlar, summalar, chegaralar va sanktsiyalar (aniq raqamlar bilan)\n"
            "4. ⚖️ Taraflarning huquqlari, majburiyatlari va yashirin xavflar (rad etish, jarima yoki xatoliklar)\n"
            "5. 💡 Amaliy xulosa va tavsiyalar (foydalanuvchi o'z manfaatini himoya qilish uchun nimalarga e'tibor berishi shart)\n\n"
            "Quruq nazariy ro'yxat emas, balki hayotda qo'llash mumkin bo'lgan aniq va to'liq qo'llanma tarzida yozing."
        )
        if custom_prompt:
            doc_instruction += f"\n\nFoydalanuvchining qo'shimcha aniq savoli/topshirig'i: {custom_prompt}\n"

        prompt = f"{doc_instruction}\n\n--- HUJJAT MATNI ---\n{doc_text[:85000]}"

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt
            )
            if response and response.text:
                return response.text
            return "Bo'lim bo'yicha tahlil natijasi olinmadi."
        except Exception as e:
            logger.exception(f"Bo'lim tahlili xatoligi: {e}")
            return f"❌ <b>Bo'lim tahlilida xatolik:</b> {e}"

    async def analyze_document(self, user_id: int, doc_text: str, filename: str, custom_prompt: Optional[str] = None) -> str:
        """
        Hujjat (PDF, Word, Excel, TXT) matnini to'liq tahlil qilish
        """
        return await self.analyze_document_section(
            user_id=user_id,
            doc_text=doc_text,
            filename=filename,
            section_name="Butun hujjat bo'yicha to'liq va umumiy amaliy tahlil",
            custom_prompt=custom_prompt
        )

    async def refine_content(self, user_id: int, original_text: str, instruction: str) -> str:
        """
        Mavjud tahlil yoki matnni tahrirlash (kengaytirish, qisqartirish, formatlash)
        """
        if not self.is_configured():
            return "⚠️ Gemini API kaliti kiritilmagan!"

        if not self._client:
            self._init_client()
        if not self._client:
            return "❌ AI mijozi bilan bog'lanishda xatolik."

        prompt = (
            f"Siz professional matn muharririsiz.\n"
            f"Quyidagi matnga ushbu topshiriqni bajaring: '{instruction}'.\n\n"
            f"--- ASL MATN ---\n{original_text}\n\n"
            f"Faqat yangilangan, to'liq va sayqallangan natijani bering."
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt
            )
            if response and response.text:
                return response.text
            return "Tahrirlash natijasi olinmadi."
        except Exception as e:
            logger.exception(f"Matnni tahrirlashda xatolik: {e}")
            return f"❌ <b>Tahrirlashda xatolik:</b> {e}"

    async def format_for_presentation(self, user_id: int, text: str) -> str:
        """
        Matnni PowerPoint slaydlariga mos struktura (# Slayd N:) ga aylantirish
        """
        if not self.is_configured():
            return text

        if not self._client:
            self._init_client()
        if not self._client:
            return text

        prompt = (
            "Ushbu matn asosida PowerPoint taqdimoti uchun 5-7 ta slayd ssenariysini tuzing.\n"
            "Har bir slaydni '## Slayd N: Sarlavha' shaklida boshlang va tagidan 3-4 ta muhim punkt ('* Punkt') yozing.\n\n"
            f"--- MATN ---\n{text[:10000]}"
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt
            )
            if response and response.text:
                return response.text
            return text
        except Exception:
            return text

    async def format_for_excel(self, user_id: int, text: str) -> str:
        """
        Matndagi ma'lumotlarni Excel jadvaliga mos Markdown table (| col1 | col2 | col3 |) ga aylantirish
        """
        if not self.is_configured():
            return text

        if not self._client:
            self._init_client()
        if not self._client:
            return text

        prompt = (
            "Ushbu ma'lumotlarni tartibli jadvalga aylantiring.\n"
            "Jadvalni Markdown formatida tuzing (| № | Bo'lim / Parametr | Tavsif / Ma'lumot | Izoh |):\n\n"
            f"--- MATN ---\n{text[:10000]}"
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt
            )
            if response and response.text:
                return response.text
            return text
        except Exception:
            return text


ai_service = AIService()
