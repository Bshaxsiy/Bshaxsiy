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
    "Siz Telegram bot ichida ishlovchi o'ta aqlli, foydali va xushmuomala sun'iy intellekt yordamchisisiz.\n"
    "QAT'IY TIL VA IMLO TALABLARI (LANGUAGE PURITY & GRAMMAR):\n"
    "1. Foydalanuvchi yoki vazifa qaysi tilni talab qilsa (o'zbek, rus, ingliz), butun javobni 100% FAQAT VA FAQAT O'SHA TILDA bering.\n"
    "2. TILLARNI ARALASHTIRISH QAT'IYAN MAN ETILADI: Agar so'rov rus tilida bo'lsa, birorta ham o'zbekcha so'z yoki qo'shimcha ishlatmang, barcha terminlarni to'liq rus tiliga tarjima qiling. Agar o'zbek tilida bo'lsa, ruscha yoki inglizcha so'zlarni aralashtirmang.\n"
    "3. Imlo va grammatika qoidalariga 100% rioya qiling: barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) aslo tushirib qoldirmasdan, to'liq yozing.\n"
    "4. Rus tilida: соблюдайте строгие правила русской орфографии, пунктуации и падежных окончаний, избегайте опечаток и калек.\n"
    "5. Javoblarni chiroyli, tushunarli, tuzilmaviy formatda taqdim eting. Hech qanday ma'lumotni oxirida chala qoldirmang, xulosalarni to'liq yakunlang."
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
                "2. Права, обязанности и процедуры исполнения",
                "3. Сроки, финансовые условия и ответственность",
                "4. Разрешение споров и заключительные положения"
            ],
            "en": [
                "1. General Provisions & Key Concepts",
                "2. Rights, Obligations & Enforcement Procedures",
                "3. Deadlines, Financial Terms & Liability",
                "4. Dispute Resolution & Final Clauses"
            ]
        }

        if lang == "ru":
            prompt = (
                f"Пользователь предоставил документ «{filename}».\n"
                "Определите 3-5 ключевых логических разделов, глав или групп статей этого документа.\n"
                "🔴 КАТЕГОРИЧЕСКОЕ ТРЕБОВАНИЕ:\n"
                "- Выведите ВСЕ названия разделов СТРОГО НА ЧИСТОМ РУССКОМ ЯЗЫКЕ (например: «Статьи 1–10: Общие положения и органы исполнения»).\n"
                "- Категорически ЗАПРЕЩЕНО оставлять узбекские слова (например: «moddalar», «ijro», «bob» и т.д. — переводите как «статьи», «исполнение», «глава»).\n"
                "- Каждое название раздела напишите с новой строки (длиной от 15 до 50 символов).\n"
                "- Не добавляйте никаких вводных слов, пояснений или знаков нумерации.\n\n"
                f"--- НАЧАЛО ДОКУМЕНТА ---\n{doc_text[:20000]}"
            )
        elif lang == "en":
            prompt = (
                f"The user provided the document '{filename}'.\n"
                "Identify 3-5 key logical sections, chapters, or article groups in this document.\n"
                "🔴 REQUIREMENT:\n"
                "- Output all section titles STRICTLY IN PROFESSIONAL ENGLISH (e.g., «Articles 1–10: General Provisions & Enforcement Bodies»).\n"
                "- Do not leave untranslated foreign words.\n"
                "- Write each section title on a new line (15 to 50 characters).\n"
                "- Do not add conversational filler or notes.\n\n"
                f"--- DOCUMENT START ---\n{doc_text[:20000]}"
            )
        else:
            prompt = (
                f"Foydalanuvchi '{filename}' nomli hujjatni taqdim etdi.\n"
                "Ushbu hujjat matnining 3-5 ta eng muhim mantiqiy bo'limlari, boblari yoki moddalar guruhlarini aniqlang.\n"
                "🔴 TALAB:\n"
                "- Barcha bo'lim nomlarini 100% SOF ADABIY O'ZBEK TILIDA, imlo xatolarisiz yozing.\n"
                "- Har bir bo'lim nomini alohida qatorda yozing (15 dan 50 belgigacha).\n"
                "- Hech qanday ortiqcha kirish yoki izoh yozmang.\n\n"
                f"--- HUJJAT BOSHI ---\n{doc_text[:20000]}"
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
        Agar hujjat boshqa tilda bo'lsa, tanlangan tilga 100% to'liq, ravon va xatosiz tarjima qiladi.
        Hech qanday tillar aralashuvi yoki imlo xatolariga yo'l qo'yilmaydi.
        """
        if lang == "ru":
            doc_instruction = (
                "Вы — ведущий юрисконсульт, главный аналитик и профессиональный переводчик высшей квалификации.\n"
                f"Пользователь предоставил официальный документ «{filename}» и запросил исчерпывающий практический анализ раздела:\n"
                f"🎯 ВЫБРАННЫЙ РАЗДЕЛ / НАПРАВЛЕНИЕ: «{section_name}»\n\n"
                "🔴 КАТЕГОРИЧЕСКИЕ ТРЕБОВАНИЯ К ЯЗЫКУ И ОРФОГРАФИИ:\n"
                "1. ВЕСЬ ТЕКСТ АНАЛИЗА ДОЛЖЕН БЫТЬ СТРОГО НА 100% ЧИСТОМ, БЕЗУПРЕЧНОМ ЛИТЕРАТУРНОМ РУССКОМ ЯЗЫКЕ.\n"
                "2. Если документ составлен на узбекском или ином языке, ПОЛНОСТЬЮ ПЕРЕВЕДИТЕ ВСЕ термины, названия статей и понятий на русский язык.\n"
                "   (Например: «moddalar» ➡️ «статьи», «Ijro etish organlari» ➡️ «Органы принудительного исполнения», «sud ijrochisi» ➡️ «судебный исполнитель», «undiruvchi» ➡️ «взыскатель», «qarzdor» ➡️ «должник»).\n"
                "3. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО смешивать языки, оставлять узбекские слова или окончания в русском тексте.\n"
                "4. Орфография, падежи и пунктуация должны быть абсолютно безошибочными.\n"
                "5. Структура анализа (с четкими заголовками и эмодзи):\n"
                "   📌 1. Основная цель, суть и сфера применения раздела\n"
                "   🔑 2. Четкие правила, пошаговые действия и практические механизмы исполнения\n"
                "   ⏱ 3. Сроки, критические даты, суммы, финансовые лимиты и ответственность\n"
                "   ⚖️ 4. Права, обязанности сторон и скрытые юридические риски\n"
                "   💡 5. Практические рекомендации и выводы для надежной защиты интересов\n\n"
                "Пишите профессионально, логично, исчерпывающе и доступным языком, как готовое практическое руководство."
            )
            if custom_prompt:
                doc_instruction += f"\n\nТочный вопрос/запрос пользователя: {custom_prompt}\n"
        elif lang == "en":
            doc_instruction = (
                "You are a senior legal counsel, principal analyst, and certified translator.\n"
                f"The user provided official document '{filename}' and requested a comprehensive practical analysis on:\n"
                f"🎯 TARGET SECTION / TOPIC: «{section_name}»\n\n"
                "🔴 STRICT LANGUAGE & GRAMMAR REQUIREMENTS:\n"
                "1. THE ENTIRE RESPONSE MUST BE 100% IN FLAWLESS, PROFESSIONAL, AND NATURAL ENGLISH.\n"
                "2. If the document is in Uzbek, Russian, or another language, FULLY TRANSLATE all terms, article names, and concepts into standard legal/business English.\n"
                "3. DO NOT mix languages or leave untranslated foreign words.\n"
                "4. Ensure perfect spelling, grammar, and consistent terminology.\n"
                "5. Analysis structure:\n"
                "   📌 1. Core Purpose & Practical Meaning\n"
                "   🔑 2. Concrete Rules, Step-by-Step Mechanisms & Procedures\n"
                "   ⏱ 3. Deadlines, Strict Dates, Monetary Amounts & Penalties\n"
                "   ⚖️ 4. Rights, Obligations & Hidden Legal Risks\n"
                "   💡 5. Actionable Advice & Strategic Safeguards\n\n"
                "Provide an exhaustive, high-value, and perfectly structured guide."
            )
            if custom_prompt:
                doc_instruction += f"\n\nUser specific question/instruction: {custom_prompt}\n"
        else:
            doc_instruction = (
                "Siz yuqori malakali bosh yurist, tahlilchi va professional tarjimonsiz.\n"
                f"Foydalanuvchi '{filename}' nomli rasmiy hujjatni taqdim etdi va aynan quyidagi bo'lim / yo'nalish bo'yicha tahlil so'radi:\n"
                f"🎯 TANLANGAN BO'LIM / YO'NALISH: «{section_name}»\n\n"
                "🔴 QAT'IY TIL VA IMLO TALABLARI:\n"
                "1. Tahlilni 100% SOF, ADABIY VA GRAMMATIK JIHATDAN MUKAMMAL O'ZBEK TILIDA bayon qiling.\n"
                "2. Agar hujjat RUS, INGLIZ yoki boshqa tilda bo'lsa, undagi barcha moddalar, bandlar, huquqiy va moliyaviy atamalarni O'ZBEK TILIGA TO'LIQ VA ANIQ TARJIMA QILING. Hech qanday so'zni ruscha yoki chala qoldirmang.\n"
                "3. Barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) aslo tushirib qoldirmasdan, to'liq va imlo xatolarisiz yozing.\n"
                "4. Tahlil tuzilishi (aniq sarlavhalar va belgilar bilan):\n"
                "   📌 1. Bo'limning asosiy maqsadi va hayotiy mohiyati (tushunarli va ravon tilda)\n"
                "   🔑 2. Aniq qoidalar, mexanizmlar va qadamma-qadam harakatlar (amaliyotda kim nima qilishi shart)\n"
                "   ⏱ 3. Sanalar, qat'iy muddatlar, summalar, chegaralar va javobgarlik (aniq raqamlar bilan)\n"
                "   ⚖️ 4. Taraflarning huquqlari, majburiyatlari va yashirin xavflar (rad etish, jarima yoki xatoliklar)\n"
                "   💡 5. Amaliy xulosa va tavsiyalar (foydalanuvchi o'z manfaatini himoya qilish uchun nimalarga e'tibor berishi shart)\n\n"
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
        if lang == "ru":
            prompt = (
                "Вы — профессиональный редактор текстов и аналитик высшей категории.\n"
                f"Задача: {instruction}\n"
                "🔴 ТРЕБОВАНИЕ: Результат должен быть на 100% чистом, грамотном русском языке без смешивания с другими языками.\n\n"
                f"--- ИСХОДНЫЙ ТЕКСТ ---\n{original_text}\n\n"
                "Предоставьте только отредактированный, чистый и отшлифованный результат."
            )
        elif lang == "en":
            prompt = (
                "You are an expert editor and senior analyst.\n"
                f"Task: {instruction}\n"
                "🔴 REQUIREMENT: Output strictly in 100% flawless English without mixing languages.\n\n"
                f"--- ORIGINAL TEXT ---\n{original_text}\n\n"
                "Return only the refined, polished text."
            )
        else:
            prompt = (
                "Siz professional matn muharriri va oliy toifali tahlilchisiz.\n"
                f"Topshiriq: '{instruction}'.\n"
                "🔴 TALAB: Natijani 100% sof adabiy o'zbek tilida, imlo xatolarisiz yozing.\n\n"
                f"--- ASL MATN ---\n{original_text}\n\n"
                "Faqat yangilangan, to'liq va sayqallangan natijani bering."
            )
        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=40.0,
            operation_name="Matnni tahrirlash"
        )

    async def format_for_presentation(self, user_id: int, text: str, lang: str = "uz") -> str:
        """
        Matnni PowerPoint slaydlariga mos struktura (# Slayd N:) ga aylantirish (tanlangan tilda)
        """
        if lang == "ru":
            prompt = (
                "Вы — эксперт по созданию презентаций и визуальных докладов.\n"
                "На основе следующего текста составьте профессиональный сценарий презентации PowerPoint из 5-7 слайдов.\n"
                "🔴 КАТЕГОРИЧЕСКИЕ ТРЕБОВАНИЯ К ЯЗЫКУ:\n"
                "- Весь текст должен быть на 100% ЧИСТОМ РУССКОМ ЯЗЫКЕ, без грамматических и орфографических ошибок.\n"
                "- Категорически ЗАПРЕЩЕНО использовать узбекские или иностранные слова.\n"
                "- Формат каждого слайда строго:\n"
                "  ## Слайд N: Название слайда\n"
                "  * Ключевой тезис или факт\n"
                "  * Важное правило, срок или действие\n"
                "  * Вывод или рекомендация\n\n"
                f"--- МАТЕРИАЛ ДЛЯ ПРЕЗЕНТАЦИИ ---\n{text[:12000]}"
            )
        elif lang == "en":
            prompt = (
                "You are an expert presentation designer.\n"
                "Based on the following text, create a professional 5-7 slide PowerPoint presentation structure.\n"
                "🔴 LANGUAGE REQUIREMENT: 100% FLAWLESS ENGLISH.\n"
                "- Format each slide strictly as:\n"
                "  ## Slide N: Slide Title\n"
                "  * Key takeaway or fact\n"
                "  * Procedure, rule, or action\n"
                "  * Practical outcome or recommendation\n\n"
                f"--- SOURCE MATERIAL ---\n{text[:12000]}"
            )
        else:
            prompt = (
                "Ushbu matn asosida PowerPoint taqdimoti uchun 5-7 ta professional slayd ssenariysini tuzing.\n"
                "🔴 TIL TALABI: 100% SOF ADABIY O'ZBEK TILI, imloviy xatolarsiz, hech qanday ruscha/inglizcha so'zlarsiz.\n"
                "Har bir slaydni '## Slayd N: Sarlavha' shaklida boshlang va tagidan 3-4 ta muhim punkt ('* Punkt') yozing.\n\n"
                f"--- MATN ---\n{text[:12000]}"
            )
        res = await self.generate_content_with_fallback(
            contents=prompt,
            timeout=35.0,
            operation_name=f"PowerPoint formatlash ({lang})"
        )
        if res.startswith("⚠️") or res.startswith("❌"):
            return text
        return res

    async def format_for_excel(self, user_id: int, text: str, lang: str = "uz") -> str:
        """
        Matndagi ma'lumotlarni Excel jadvaliga mos Markdown table (| col1 | col2 | col3 |) ga aylantirish (tanlangan tilda)
        """
        if lang == "ru":
            prompt = (
                "Преобразуйте предоставленные данные в аккуратную, структурированную таблицу Markdown.\n"
                "🔴 ТРЕБОВАНИЯ К ЯЗЫКУ:\n"
                "- 100% ЧИСТЫЙ РУССКИЙ ЯЗЫК, без орфографических ошибок и без смешивания с узбекским или иными языками.\n"
                "- Заголовки таблицы строго в следующем формате:\n"
                "| № | Раздел / Тема | Подробное описание / Данные | Статус / Примечание |\n"
                "|---|---|---|---|\n"
                "Заполните таблицу информативными строками (10-25 строк) на основе текста.\n\n"
                f"--- ДАННЫЕ ---\n{text[:12000]}"
            )
        elif lang == "en":
            prompt = (
                "Convert the provided data into a neat, structured Markdown table.\n"
                "🔴 LANGUAGE REQUIREMENT: 100% FLAWLESS ENGLISH.\n"
                "Headers:\n"
                "| № | Section / Topic | Detailed Description / Data | Status / Note |\n"
                "|---|---|---|---|\n"
                "Fill 10-25 rows based on the text.\n\n"
                f"--- DATA ---\n{text[:12000]}"
            )
        else:
            prompt = (
                "Ushbu ma'lumotlarni tartibli va chiroyli jadvalga aylantiring.\n"
                "🔴 TIL TALABI: 100% SOF ADABIY O'ZBEK TILI, imlo xatolarisiz.\n"
                "Jadvalni Markdown formatida tuzing:\n"
                "| № | Bo'lim / Parametr | Batafsil Tavsif / Ma'lumot | Holat / Izoh |\n"
                "|---|---|---|---|\n"
                "Matn asosida 10-25 qatordan iborat mazmunli jadval qiling.\n\n"
                f"--- MATN ---\n{text[:12000]}"
            )
        res = await self.generate_content_with_fallback(
            contents=prompt,
            timeout=35.0,
            operation_name=f"Excel formatlash ({lang})"
        )
        if res.startswith("⚠️") or res.startswith("❌"):
            return text
        return res

    async def generate_legal_document(
        self,
        user_id: int,
        doc_type: str,
        details: str,
        lang: str = "uz"
    ) -> str:
        """
        Sudga rasmiy Da'vo arizasi (lawsuit) yoki Talabnoma/Pretenziya (demand letter) yaratish.
        Amaldagi qonunchilik (Fuqarolik, Iqtisodiy, Mehnat, Oila kodekslari va h.k.) moddalariga asoslangan,
        davlat boji va ilovalar bilan to'liq rasmiy hujjat tayyorlaydi.
        """
        type_titles = {
            "lawsuit": "Sudga Da'vo arizasi",
            "demand": "Sudgacha Talabnoma (Pretenziya)",
            "debt": "Qarz va penya undirish to'g'risida Da'vo arizasi",
            "contract": "Shartnoma majburiyatlarini buzganlik bo'yicha Da'vo arizasi",
            "alimony": "Aliment undirish to'g'risida Da'vo arizasi",
            "divorce": "Nikohni bekor qilish to'g'risida Da'vo arizasi",
            "labor": "Ishga tiklash va oylik maosh undirish to'g'risida Da'vo arizasi",
            "damage": "Yetkazilgan moddiy va ma'naviy zararni qoplash to'g'risida Da'vo arizasi",
            "demand_debt": "Qarzdorlikni to'lash to'g'risida Talabnoma (Pretenziya)",
            "demand_quality": "Sifatsiz mahsulot yoki xizmat bo'yicha Talabnoma (Pretenziya)",
            "free": "Rasmiy Yuridik Hujjat / Da'vo arizasi"
        }
        type_name = type_titles.get(doc_type, "Sudga Da'vo arizasi / Talabnoma")

        if lang == "ru":
            prompt = (
                "Вы — ведущий адвокат высшей квалификации, старший юрисконсульт и признанный эксперт по судебным документам.\n\n"
                f"ЗАДАЧА: На основе предоставленных пользователем данных составьте официальный, безупречный с юридической точки зрения ДОКУМЕНТ: «{type_name}».\n"
                f"ИСХОДНЫЕ ДАННЫЕ И СИТУАЦИЯ:\n{details}\n\n"
                "🔴 КАТЕГОРИЧЕСКИЕ ТРЕБОВАНИЯ К ЯЗЫКУ И ОФОРМЛЕНИЮ:\n"
                "1. ВЕСЬ ТЕКСТ ДОЛЖЕН БЫТЬ НА 100% ЧИСТОМ, ЛИТЕРАТУРНОМ И СТРОГОМ ЮРИДИЧЕСКОМ РУССКОМ ЯЗЫКЕ. Никаких орфографических ошибок и смешивания языков!\n"
                "2. СТРУКТУРА ДОКУМЕНТА (Официальный судебный / претензионный стандарт):\n"
                "   • ШАПКА (Справа или единым судебным блоком):\n"
                "     В [Наименование суда, например: Межрайонный суд по гражданским делам г. Ташкента / Экономический суд]\n"
                "     Истец / Заявитель: [Ф.И.О. или Наименование организации, адрес, ПИНФЛ/ИНН, телефон]\n"
                "     Ответчик / Должник: [Ф.И.О. или Наименование организации, адрес, телефон, ИНН]\n"
                "     Третьи лица: [при наличии]\n"
                "     Цена иска: [Сумма с расчетом: основной долг + пеня/неустойка]\n"
                "     Госпошлина: [Размер госпошлины по ставкам закона]\n\n"
                "   • ЗАГОЛОВОК:\n"
                "     ИСКОВОЕ ЗАЯВЛЕНИЕ (или ДОСУДЕБНАЯ ПРЕТЕНЗИЯ)\n"
                "     [о взыскании задолженности, расторжении договора, защите трудовых прав и т.д.]\n\n"
                "   • ОБСТОЯТЕЛЬСТВА ДЕЛА (Фактическая часть):\n"
                "     Хронологическое, четкое изложение: когда был заключен договор/расписка, обязательства сторон, конкретные нарушения ответчика, период просрочки, сумма неисполненных обязательств.\n\n"
                "   • ПРАВОВОЕ ОБОСНОВАНИЕ (Нормы законодательства):\n"
                "     Точные статьи Гражданского кодекса (ГК), Гражданского процессуального кодекса (ГПК) или Экономического процессуального кодекса (ЭПК) (об исполнении обязательств, ответственности, взыскании неустойки, подсудности).\n\n"
                "   • СОБЛЮДЕНИЕ ДОСУДЕБНОГО ПОРЯДКА:\n"
                "     Указание на направление претензии и отсутствие ответа или отказ.\n\n"
                "   • ПРОШУ СУД (Просительная часть / Требования) — строго по пунктам:\n"
                "     1. Взыскать с Ответчика в пользу Истца сумму основного долга в размере ...;\n"
                "     2. Взыскать с Ответчика неустойку/пеню в размере ...;\n"
                "     3. Взыскать с Ответчика сумму государственной пошлины и почтовые расходы.\n\n"
                "   • ПРИЛОЖЕНИЯ (Перечень доказательств):\n"
                "     1. Документ об уплате государственной пошлины;\n"
                "     2. Копия договора / расписки / актов / накладных;\n"
                "     3. Копия направленной претензии и почтовой квитанции;\n"
                "     4. Расчет исковых требований;\n"
                "     5. Копия паспорта / свидетельства о регистрации Истца;\n"
                "     6. Доказательства направления копии иска Ответчику.\n\n"
                "   • ДАТА И ПОДПИСЬ:\n"
                "     «___» _________ 202__ года        Истец: _______________ / Подпись / Ф.И.О.\n\n"
                "3. Если пользователь не указал конкретные персональные данные (паспорт, номер договора), укажите понятные маркеры в скобках [Ф.И.О. истца: ...], но ВСЮ правовую аргументацию, расчеты и статьи напишите полно, профессионально и готово к подаче.\n"
                "4. Выдайте сразу готовый официальный документ без вводных фраз."
            )
        elif lang == "en":
            prompt = (
                "You are a senior litigation attorney and expert in civil, commercial, and procedural litigation.\n\n"
                f"TASK: Draft a professional, court-admissible legal document: «{type_name}» based on user facts.\n"
                f"USER DETAILS & FACTS:\n{details}\n\n"
                "🔴 REQUIREMENTS:\n"
                "1. Flawless, professional legal English.\n"
                "2. Standard litigation layout: Court Caption, Plaintiff and Defendant info, Claim value, State fees, Title, Factual background, Statutory grounds, Pre-trial compliance, Prayer for relief (WHEREFORE / PRAYER FOR RELIEF), List of exhibits, Date and signature block.\n"
                "3. Provide the complete ready-to-file document immediately without conversational filler."
            )
        else:
            prompt = (
                "Siz O'zbekiston Respublikasining oliy toifali advokati, amaliyotchi bosh yurist va sud hujjatlari tuzish bo'yicha yetakchi ekspertsiz.\n\n"
                f"VAZIFA: Foydalanuvchi taqdim etgan ma'lumotlar asosida rasmiy va yuridik jihatdan benuqson, sudga yoki qarzdorga topshirishga to'liq tayyor bo'lgan HUJJAT: «{type_name}» tuzib bering.\n"
                f"FOYDALANUVCHI VAZIYATI VA TAFSILOTLARI:\n{details}\n\n"
                "🔴 QAT'IY YURIDIK VA FORMAT TALABLARI:\n"
                "1. TIL VA IMLO: 100% sof, ravon va adabiy o'zbek tilida (lotin alifbosida), mukammal yuridik atamalar bilan, birorta ham imlo xatolarisiz yozilsin.\n"
                "2. HUJJAT TUZILISHI (Rasmiy sud va da'vo talablari bo'yicha):\n"
                "   • SHAPKA (Sud nomi va taraflar rekvizitlari):\n"
                "     Fuqarolik ishlari bo'yicha [Tuman/Shahar] tumanlararo sudiga\n"
                "     (yoki [Viloyat/Shahar] tumanlararo iqtisodiy sudiga)\n"
                "     Da'vogar: [F.I.Sh. yoki Tashkilot nomi, manzili, JShShIR/STIR, telefon raqami]\n"
                "     Javobgar: [F.I.Sh. yoki Tashkilot nomi, manzili, JShShIR/STIR, telefon raqami]\n"
                "     Uchinchi shaxslar: [mavjud bo'lsa]\n"
                "     Da'vo bahosi: [Aniq summa: asosiy qarz + penya/foizlar summasi]\n"
                "     Davlat boji: [O'zbekiston Respublikasi «Davlat boji to'g'risida»gi Qonuniga muvofiq hisoblangan davlat boji summasi]\n\n"
                "   • KATTA SARLAVHA:\n"
                "     DA'VO ARIZASI (yoki TALABNOMA / PRETENZIYA)\n"
                "     ([Qarzdorlikni va penya summalarini undirish to'g'risida] yoki tegishli mavzu)\n\n"
                "   • HOLATNING BAYONI (Faktlar va xronologiya):\n"
                "     Kelishuv, qarz tilxati yoki shartnoma qachon tuzilgan, kim qanday majburiyat olgan, javobgar qaysi majburiyatini buzgan, qancha to'lov qilinmagan, qancha muddat o'tgan.\n\n"
                "   • HUQUQIY VA QONUNIY ASOSLAR (Kodeks moddalari):\n"
                "     O'zbekiston Respublikasi Fuqarolik kodeksining (FK) tegishli moddalari (masalan, 236, 237, 324, 325, 327, 333, 382, 732-739-moddalar), Fuqarolik protsessual kodeksining (FPK) 188, 189, 190, 191-moddalari (yoki Iqtisodiy protsessual kodeksining tegishli moddalari).\n\n"
                "   • SUDGACHA HAL QILISH TARTIBIGA RIOYA QILINGANLIGI:\n"
                "     Javobgarga talabnoma (pretenziya) yuborilganligi va unga javob berilmagani yoki rad etilgani haqida yozish.\n\n"
                "   • TALAB VA ILTIMOS (SO'RAYMAN: yoki TALAB QILAMAN: — bandma-band):\n"
                "     1. Javobgardan da'vogar foydasiga ... so'm asosiy qarz summasi undirilsin;\n"
                "     2. Javobgardan da'vogar foydasiga ... so'm penya / jarima summasi undirilsin;\n"
                "     3. Da'vogar tomonidan to'langan ... so'm davlat boji va pochta xarajatlari javobgardan undirilsin.\n\n"
                "   • ILOVALAR (Ilova qilinadigan hujjatlar ro'yxati):\n"
                "     1. Davlat boji to'langanligi kvitansiyasi;\n"
                "     2. Shartnoma / qarz tilxati / hisob-faktura / solishtirma dalolatnoma nusxasi;\n"
                "     3. Javobgarga talabnoma yuborilganligi va pochta kvitansiyasi nusxasi;\n"
                "     4. Da'vo summasi hisob-kitob varaqasi;\n"
                "     5. Da'vogarning pasport / guvohnoma nusxasi;\n"
                "     6. Da'vo arizasining javobgarga topshirilgani (yuborilgani) tasdig'i.\n\n"
                "   • SANA VA IMZO:\n"
                "     202__ yil «___» ____________        Da'vogar: _________________ (F.I.Sh.)\n\n"
                "3. Agar foydalanuvchi ma'lum shaxsiy rekvizitlarni (masalan pasport raqami, aniq uy manzili) aytmagan bo'lsa, ularni [Da'vogarning manzili: ...] kabi aniq qavslarda qoldiring, lekin yuridik matn, moddalar, qarz hisob-kitobi va talablarni 100% to'liq va professional darajada shakllantiring.\n"
                "4. To'g'ridan-to'g'ri rasmiy hujjat matnini chiqaring, ortiqcha salom-alik yoki kirish so'zlarini qo'shmang."
            )

        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=55.0,
            operation_name=f"Yuridik hujjat generatsiyasi ({doc_type})"
        )


ai_service = AIService()

