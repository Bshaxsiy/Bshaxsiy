import io
import os
import re
import logging
import asyncio
from typing import Optional, Dict, List, Tuple

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

# Model tanqisligi yoki 503 bo'lganda avtomatik ulanadigan zaxira modellar ro'yxati
FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
]

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
        self._user_chats: Dict[int, Tuple[any, str]] = {}
        self._api_key = config.GEMINI_API_KEY
        self._model = config.GEMINI_MODEL or "gemini-3.5-flash"
        self._init_client()

    def _init_client(self):
        if self._api_key and self._api_key != "YOUR_GEMINI_API_KEY_HERE":
            try:
                self._client = genai.Client(api_key=self._api_key)
                logger.info(f"Gemini AI mijozi muvaffaqiyatli ishga tushirildi (bosh model: {self._model})")
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

    def _get_candidate_models(self) -> List[str]:
        """Sinab ko'riladigan modellar navbati (bosh model + zaxiralar)"""
        candidates = [self._model]
        for m in FALLBACK_MODELS:
            if m not in candidates:
                candidates.append(m)
        return candidates

    def _get_or_create_chat(self, user_id: int, model: Optional[str] = None):
        if not self._client:
            self._init_client()
        if not self._client:
            return None

        target_model = model or self._model

        if user_id in self._user_chats:
            chat, current_model = self._user_chats[user_id]
            if model and current_model != model:
                try:
                    chat_config = types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.7,
                    )
                    chat = self._client.aio.chats.create(
                        model=model,
                        config=chat_config
                    )
                    self._user_chats[user_id] = (chat, model)
                except Exception as e:
                    logger.warning(f"Chat yaratishda xatolik ({model}): {e}")
                    return None
            return chat

        try:
            chat_config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
            )
            chat = self._client.aio.chats.create(
                model=target_model,
                config=chat_config
            )
            self._user_chats[user_id] = (chat, target_model)
            return chat
        except Exception as e:
            logger.warning(f"Yangi chat yaratishda xatolik ({target_model}): {e}")
            return None

    async def generate_content_with_fallback(
        self,
        contents,
        config: Optional[types.GenerateContentConfig] = None,
        timeout: float = 55.0,
        operation_name: str = "AI so'rovi"
    ) -> str:
        """
        Gemini modeliga murojaat qilish.
        Agar tanlangan model 503 (high demand), 429 (quota), server xatosi yoki timeout bersa,
        avtomatik ravishda zaxira (fallback) modellarga ulanadi.
        """
        if not self.is_configured():
            return "⚠️ Gemini API kaliti kiritilmagan!"

        if not self._client:
            self._init_client()
        if not self._client:
            return "❌ AI mijozi bilan bog'lanishda xatolik."

        candidates = self._get_candidate_models()
        last_error = ""

        for model_name in candidates:
            try:
                task = self._client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                response = await asyncio.wait_for(task, timeout=timeout)
                if response and response.text:
                    if model_name != self._model:
                        logger.info(f"[{operation_name}] Zaxira model '{model_name}' muvaffaqiyatli ishladi!")
                    return response.text
            except asyncio.TimeoutError:
                last_error = f"{model_name} timeout (> {timeout}s)"
                logger.warning(f"[{operation_name}] Model '{model_name}' timeout bo'ldi. Zaxira modelga o'tilmoqda...")
            except Exception as e:
                err_str = str(e)
                last_error = err_str
                logger.warning(f"[{operation_name}] Model '{model_name}' da xatolik: {err_str[:120]}. Zaxira modelga o'tilmoqda...")
                if "API_KEY_INVALID" in err_str or ("400" in err_str and "API key" in err_str):
                    return "❌ <b>Gemini API kaliti noto'g'ri yoki yaroqsiz!</b> Iltimos, to'g'ri kalit kiriting."

        logger.error(f"[{operation_name}] Barcha Gemini modellari band yoki xato qaytardi: {last_error}")
        if "503" in last_error or "UNAVAILABLE" in last_error or "high demand" in last_error:
            return (
                "⚠️ <b>Sun'iy intellekt serverida vaqtinchalik yuqori yuklanish (Google 503).</b>\n\n"
                "Google Gemini tizimida ayni daqiqalarda qisqa muddatli yuqori talab kuzatilmoqda. "
                "Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring yoki boshqa bo'limni tanlang."
            )
        elif "RESOURCE_EXHAUSTED" in last_error or "429" in last_error:
            return "⏳ <b>Gemini API so'rovlar limiti to'ldi.</b> Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."

        return (
            "❌ <b>AI xizmati bilan bog'lanishda vaqtinchalik uzilish yuz berdi.</b>\n\n"
            "Iltimos, birozdan so'ng qayta urinib ko'ring."
        )

    async def ask_gemini(
        self,
        user_id: int,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg"
    ) -> str:
        """
        Gemini AI ga savol/xabar yuborish va javob olish (zaxira modellar bilan)
        """
        if not self.is_configured():
            return (
                "⚠️ <b>Gemini API kaliti kiritilmagan!</b>\n\n"
                "AI xizmatidan foydalanish uchun <code>.env</code> faylida <code>GEMINI_API_KEY</code> ni sozlang.\n\n"
                "🔑 Kalitni bepul olish: https://aistudio.google.com/app/apikey"
            )

        candidates = self._get_candidate_models()
        last_error = ""

        for model_name in candidates:
            chat = self._get_or_create_chat(user_id, model=model_name)
            if not chat:
                continue

            try:
                if image_bytes:
                    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                    text_content = prompt.strip() if prompt else "Ushbu rasmni ko'rib chiqib, nima aks etganini tushuntiring."
                    msg_payload = [image_part, text_content]
                else:
                    msg_payload = prompt

                task = chat.send_message(message=msg_payload)
                response = await asyncio.wait_for(task, timeout=45.0)

                if response and response.text:
                    return response.text
                return "Javob olinmadi. Iltimos, savolingizni boshqacharoq shaklda berib ko'ring."
            except asyncio.TimeoutError:
                last_error = f"{model_name} timeout"
                logger.warning(f"ask_gemini: {model_name} timeout bo'ldi. Zaxira modelga o'tilmoqda...")
                self.reset_chat(user_id)
            except Exception as e:
                err_msg = str(e)
                last_error = err_msg
                logger.warning(f"ask_gemini: {model_name} da xatolik: {err_msg[:120]}. Zaxira modelga o'tilmoqda...")
                self.reset_chat(user_id)
                if "API_KEY_INVALID" in err_msg or ("400" in err_msg and "API key" in err_msg):
                    return "❌ <b>Gemini API kaliti noto'g'ri yoki yaroqsiz!</b> Iltimos, to'g'ri kalit kiriting."
                if "SAFETY" in err_msg:
                    return "🛡 Ushbu so'rov xavfsizlik filtrlari tomonidan to'xtatildi."

        if "503" in last_error or "UNAVAILABLE" in last_error or "high demand" in last_error:
            return (
                "⚠️ <b>Sun'iy intellekt serverida vaqtinchalik yuqori yuklanish (503).</b>\n\n"
                "Google Gemini tizimida global talab oshganligi sababli so'rovga vaqtinchalik javob berilmadi. "
                "Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
            )
        elif "RESOURCE_EXHAUSTED" in last_error or "429" in last_error:
            return "⏳ <b>Gemini API so'rovlar limiti to'ldi.</b> Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."

        return "❌ <b>AI xatoligi:</b> Bog'lanishda uzilish yuz berdi. Iltimos, birozdan so'ng qayta urinib ko'ring."

    async def ask_gemini_audio(
        self,
        user_id: int,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        prompt: Optional[str] = None
    ) -> str:
        """
        Audio xabarni Gemini AI ga yuborish va tahlil/javob olish (zaxira modellar bilan)
        """
        if not self.is_configured():
            return (
                "⚠️ <b>Gemini API kaliti kiritilmagan!</b>\n\n"
                "AI xizmatidan foydalanish uchun <code>.env</code> faylida <code>GEMINI_API_KEY</code> ni sozlang.\n\n"
                "🔑 Kalitni bepul olish: https://aistudio.google.com/app/apikey"
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

        candidates = self._get_candidate_models()
        last_error = ""

        for model_name in candidates:
            chat = self._get_or_create_chat(user_id, model=model_name)
            if not chat:
                continue

            try:
                audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
                task = chat.send_message(message=[audio_part, instruction])
                response = await asyncio.wait_for(task, timeout=50.0)

                if response and response.text:
                    return response.text
                return "Audio xabarni tahlil qilishda javob matni olinmadi."
            except asyncio.TimeoutError:
                last_error = f"{model_name} timeout"
                logger.warning(f"ask_gemini_audio: {model_name} timeout bo'ldi. Zaxira modelga o'tilmoqda...")
                self.reset_chat(user_id)
            except Exception as e:
                err_msg = str(e)
                last_error = err_msg
                logger.warning(f"ask_gemini_audio: {model_name} da xatolik: {err_msg[:120]}. Zaxira modelga o'tilmoqda...")
                self.reset_chat(user_id)
                if "API_KEY_INVALID" in err_msg or ("400" in err_msg and "API key" in err_msg):
                    return "❌ <b>Gemini API kaliti noto'g'ri yoki yaroqsiz!</b>"

        if "503" in last_error or "UNAVAILABLE" in last_error or "high demand" in last_error:
            return (
                "⚠️ <b>Sun'iy intellekt serverida vaqtinchalik yuqori yuklanish (503).</b>\n\n"
                "Google Gemini tizimida global talab oshganligi sababli audio tahlili vaqtinchalik bajarilmadi. "
                "Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
            )
        elif "RESOURCE_EXHAUSTED" in last_error or "429" in last_error:
            return "⏳ <b>Gemini so'rovlar limiti to'ldi.</b> Birozdan so'ng qayta urinib ko'ring."

        return "❌ <b>Audio tahlilida xatolik yuz berdi.</b> Iltimos, birozdan so'ng qayta urinib ko'ring."

    async def translate_text(
        self,
        text: str,
        target_lang: str = "uz",
        source_lang: Optional[str] = "auto"
    ) -> str:
        """
        Matnni tanlangan tilga (O'zbek, Rus, Ingliz) professional, aniq va grammatik xatolarsiz tarjima qilish
        """
        lang_names = {
            "uz": "SOF ADABIY O'ZBEK TILI",
            "ru": "ЛИТЕРАТУРНЫЙ РУССКИЙ ЯЗЫК",
            "en": "PROFESSIONAL ENGLISH"
        }
        target_name = lang_names.get(target_lang, "O'ZBEK TILI")

        prompt = (
            f"Siz jahon darajasidagi oliy toifali professional tarjimonsiz.\n"
            f"Topshiriq: Berilgan matnni diqqat bilan o'rganib, uni {target_name}ga to'liq, aniq, ravon va professional tarzda tarjima qiling.\n\n"
            "🔴 TARJIMA QOIDALARI:\n"
            "1. Asl matnning barcha ma'no nozikliklari, yuridik, moliyaviy va texnik atamalarini 100% to'g'ri yetkazing.\n"
            "2. O'zbek tiliga tarjimada barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) aslo tushirib qoldirmasdan, to'liq va imlo xatolarisiz yozing.\n"
            "3. Rus yoki ingliz tiliga tarjimada tegishli tilning qat'iy adabiy va uslubiy qoidalariga rioya qiling.\n"
            "4. Asl matnning barcha sarlavhalari, ro'yxat punktlari, paragraflari va Markdown formatini aynan saqlang.\n"
            "5. Faqat va faqat tarjima natijasini qaytaring. Hech qanday keraksiz kirish yoki xulosa izohlari qo'shmang.\n\n"
            f"--- TARJIMA QILINADIGAN MATN ---\n{text}"
        )

        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=50.0,
            operation_name=f"Tarjima ({target_lang})"
        )

    async def deep_research(self, user_id: int, query: str, lang: str = "uz") -> str:
        """
        Mavzu bo'yicha chuqur tahlil (Deep Research) o'tkazish
        """
        lang_instruction = (
            "Hisobotni 100% SOF ADABIY O'ZBEK TILIDA, imlo va grammatika qoidalariga rioya qilib tayyorlang."
            if lang == "uz" else
            ("Подготовьте отчет на БЕЗУПРЕЧНОМ РУССКОМ ЯЗЫКЕ, соблюдая все грамматические и стилистические нормы."
             if lang == "ru" else
             "Prepare the complete report in FLAWLESS, PROFESSIONAL ENGLISH.")
        )
        cfg = types.GenerateContentConfig(
            system_instruction=f"{DEEP_RESEARCH_PROMPT}\n\n{lang_instruction}",
            temperature=0.4
        )
        prompt = f"Mavzu: {query}\n\nIltimos, ushbu mavzu bo'yicha har tomonlama to'liq va professional Deep Research hisoboti tayyorlang."
        return await self.generate_content_with_fallback(
            contents=prompt,
            config=cfg,
            timeout=60.0,
            operation_name=f"Deep Research ({query[:30]})"
        )

    async def detect_document_sections(self, doc_text: str, filename: str, lang: str = "uz") -> List[str]:
        """
        Hujjatning asosiy bo'limlari, boblari, moddalari yoki mavzularini aniqlash (tanlangan tilda)
        """
        default_sections = {
            "uz": [
                "1. Umumiy qoidalar va asosiy tushunchalar",
                "2. Huquqlar, majburiyatlar va tartib-taomillar",
                "3. Moliyaviy shartlar, muddatlar va javobgarlik",
                "4. Nizolar, bekor qilish va yakuniy qoidalar"
            ],
            "ru": [
                "1. Общие положения и основные понятия",
                "2. Права, обязанности и процедуры",
                "3. Финансовые условия, сроки и ответственность",
                "4. Разрешение споров и заключительные положения"
            ],
            "en": [
                "1. General Provisions & Key Concepts",
                "2. Rights, Obligations & Procedures",
                "3. Financial Terms, Deadlines & Liability",
                "4. Dispute Resolution & Final Clauses"
            ]
        }

        lang_request = (
            "Bo'lim nomlarini o'zbek tilida chiqaring."
            if lang == "uz" else
            ("Выведите названия разделов на русском языке." if lang == "ru" else "Output section titles in English.")
        )

        prompt = (
            f"Foydalanuvchi '{filename}' nomli hujjatni yubordi.\n"
            f"Ushbu hujjat matnining asosiy bo'limlari, boblari, moddalar guruhlari yoki asosiy qismlarini aniqlang.\n"
            f"{lang_request}\n"
            "Maksimal 3-5 ta eng muhim va tushunarli bo'lim nomini chiqaring (har bir bo'lim nomi 15-40 belgidan oshmasin).\n"
            "Faqat har bir bo'lim nomini alohida qatorda yozing. Hech qanday qo'shimcha so'z, izoh yoki kirish qismi yozmang.\n\n"
            f"--- HUJJAT BOSHI ---\n{doc_text[:18000]}"
        )

        try:
            res_text = await self.generate_content_with_fallback(
                contents=prompt,
                timeout=25.0,
                operation_name=f"Bo'limlarni aniqlash ({filename})"
            )
            if res_text and not res_text.startswith("⚠️") and not res_text.startswith("❌"):
                lines = [l.strip() for l in res_text.split("\n") if l.strip()]
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

        return default_sections.get(lang, default_sections["uz"])

    async def analyze_document_section(
        self,
        user_id: int,
        doc_text: str,
        filename: str,
        section_name: str,
        custom_prompt: Optional[str] = None,
        lang: str = "uz"
    ) -> str:
        """
        Hujjatning tanlangan aniq bo'limi bo'yicha chuqur, amaliy va to'liq tahlil.
        Agar hujjat Rus yoki Ingliz tilida bo'lsa, tanlangan tilga (masalan, O'zbek)
        to'g'ri, aniq, ravon va grammatik xatolarsiz tarjima qilib beradi.
        """
        if lang == "ru":
            lang_directive = (
                "🔴 ВАЖНОЕ ТРЕБОВАНИЕ К ЯЗЫКУ И ПЕРЕВОДУ:\n"
                "- Предоставьте анализ на 100% ЧИСТОМ, ЛИТЕРАТУРНОМ И ГРАММАТИЧЕСКИ БЕЗУПРЕЧНОМ РУССКОМ ЯЗЫКЕ.\n"
                "- Независимо от того, на каком языке составлен исходный документ (узбекский, английский и др.), "
                "точно и качественно переведите все положения на русский язык.\n"
                "- Структура анализа:\n"
                "1. 📌 Основная цель и практическая суть раздела\n"
                "2. 🔑 Четкие правила, механизмы и пошаговые действия\n"
                "3. ⏱ Сроки, даты, суммы, лимиты и санкции\n"
                "4. ⚖️ Права, обязанности сторон и скрытые риски\n"
                "5. 💡 Практические рекомендации и выводы"
            )
        elif lang == "en":
            lang_directive = (
                "🔴 CRITICAL LANGUAGE & TRANSLATION REQUIREMENT:\n"
                "- Provide the entire analysis in FLAWLESS, PROFESSIONAL, AND NATURAL ENGLISH.\n"
                "- Regardless of the source language (Uzbek, Russian, etc.), accurately translate and interpret all provisions into English.\n"
                "- Analysis structure:\n"
                "1. 📌 Core objective and practical meaning of the section\n"
                "2. 🔑 Concrete rules, mechanisms, and step-by-step actions required\n"
                "3. ⏱ Dates, strict deadlines, monetary amounts, and penalties\n"
                "4. ⚖️ Rights, obligations, and hidden liabilities\n"
                "5. 💡 Actionable advice, safeguards, and conclusions"
            )
        else:
            lang_directive = (
                "🔴 O'TA MUHIM TIL VA TARJIMA TALABI:\n"
                "- Tahlilni 100% SOF, ADABIY VA GRAMMATIK JIHATDAN MUKAMMAL O'ZBEK TILIDA bayon qiling.\n"
                "- Agar hujjat RUS, INGLIZ yoki boshqa tilda bo'lsa, undagi barcha muhim bandlarni, shartlarni, "
                "qoidalarni va xulosalarni O'ZBEK TILIGA TO'G'RI, ANIQ, RAVON VA GRAMMATIK XATOLARSIZ TARJIMA QILING va amaliy ma'nosini tushuntiring.\n"
                "- Barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) "
                "aslo tushirib qoldirmasdan, to'liq va xatosiz yozing.\n"
                "- Tahlilda quyidagi bloklarni qat'iy yoriting:\n"
                "1. 📌 Bo'limning asosiy maqsadi va hayotiy mohiyati (tushunarli va ravon tilda)\n"
                "2. 🔑 Aniq qoidalar, mexanizmlar va qadamma-qadam harakatlar (amaliyotda kim nima qilishi shart)\n"
                "3. ⏱ Sanalar, qat'iy muddatlar, summalar, chegaralar va sanktsiyalar (aniq raqamlar bilan)\n"
                "4. ⚖️ Taraflarning huquqlari, majburiyatlari va yashirin xavflar (rad etish, jarima yoki xatoliklar)\n"
                "5. 💡 Amaliy xulosa va tavsiyalar (foydalanuvchi o'z manfaatini himoya qilish uchun nimalarga e'tibor berishi shart)"
            )

        doc_instruction = (
            f"Siz yuqori malakali bosh yurist, tahlilchi va professional tarjimonsiz.\n"
            f"Foydalanuvchi '{filename}' nomli rasmiy hujjatni taqdim etdi va aynan quyidagi bo'lim / yo'nalish bo'yicha tahlil so'radi:\n"
            f"🎯 TANLANGAN BO'LIM / YO'NALISH: «{section_name}»\n\n"
            f"{lang_directive}\n\n"
            "Quruq nazariy ro'yxat emas, balki amaliyotda to'liq qo'llanma tarzida yozing."
        )
        if custom_prompt:
            doc_instruction += f"\n\nFoydalanuvchining qo'shimcha aniq savoli/topshirig'i: {custom_prompt}\n"

        prompt = f"{doc_instruction}\n\n--- HUJJAT MATNI ---\n{doc_text[:85000]}"

        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=55.0,
            operation_name=f"Hujjat bo'lim tahlili ({filename})"
        )

    async def analyze_document(
        self,
        user_id: int,
        doc_text: str,
        filename: str,
        custom_prompt: Optional[str] = None,
        lang: str = "uz"
    ) -> str:
        """
        Hujjat (PDF, Word, Excel, TXT) matnini to'liq tahlil qilish
        """
        sec_title = (
            "Butun hujjat bo'yicha to'liq va umumiy amaliy tahlil"
            if lang == "uz" else
            ("Полный практический анализ всего документа" if lang == "ru" else "Full practical analysis of the entire document")
        )
        return await self.analyze_document_section(
            user_id=user_id,
            doc_text=doc_text,
            filename=filename,
            section_name=sec_title,
            custom_prompt=custom_prompt,
            lang=lang
        )

    async def refine_content(self, user_id: int, original_text: str, instruction: str, lang: str = "uz") -> str:
        """
        Mavjud tahlil yoki matnni tahrirlash (kengaytirish, qisqartirish, formatlash)
        """
        prompt = (
            f"Siz professional matn muharriri va tahlilchisiz.\n"
            f"Topshiriq: '{instruction}'.\n"
            f"Til: {lang}.\n\n"
            f"--- ASL MATN ---\n{original_text}\n\n"
            f"Faqat yangilangan, to'liq va sayqallangan natijani bering."
        )
        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=40.0,
            operation_name="Matnni tahrirlash"
        )

    async def format_for_presentation(self, user_id: int, text: str) -> str:
        """
        Matnni PowerPoint slaydlariga mos struktura (# Slayd N:) ga aylantirish
        """
        prompt = (
            "Ushbu matn asosida PowerPoint taqdimoti uchun 5-7 ta slayd ssenariysini tuzing.\n"
            "Har bir slaydni '## Slayd N: Sarlavha' shaklida boshlang va tagidan 3-4 ta muhim punkt ('* Punkt') yozing.\n\n"
            f"--- MATN ---\n{text[:10000]}"
        )
        res = await self.generate_content_with_fallback(
            contents=prompt,
            timeout=35.0,
            operation_name="PowerPoint formatlash"
        )
        if res.startswith("⚠️") or res.startswith("❌"):
            return text
        return res

    async def format_for_excel(self, user_id: int, text: str) -> str:
        """
        Matndagi ma'lumotlarni Excel jadvaliga mos Markdown table (| col1 | col2 | col3 |) ga aylantirish
        """
        prompt = (
            "Ushbu ma'lumotlarni tartibli jadvalga aylantiring.\n"
            "Jadvalni Markdown formatida tuzing (| № | Bo'lim / Parametr | Tavsif / Ma'lumot | Izoh |):\n\n"
            f"--- MATN ---\n{text[:10000]}"
        )
        res = await self.generate_content_with_fallback(
            contents=prompt,
            timeout=35.0,
            operation_name="Excel formatlash"
        )
        if res.startswith("⚠️") or res.startswith("❌"):
            return text
        return res


ai_service = AIService()
