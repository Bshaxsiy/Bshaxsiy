import io
import os
import re
import logging
import asyncio
from typing import Optional, Dict, List, Tuple

from google import genai
from google.genai import types

import config
from content_safety import check_content_safety, get_safety_refusal_message, SafetyViolation
from legal_factcheck import enrich_legal_response, is_legal_query
from law_library import library as law_library

logger = logging.getLogger(__name__)

# Model tanqisligi yoki 503 bo'lganda avtomatik ulanadigan zaxira modellar ro'yxati
FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
]

# Standart xavfsizlik sozlamalari (xavfli kontent, nafrat va tahqirlashlarni bloklash)
DEFAULT_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
]

SYSTEM_PROMPT = (
    "Siz Telegram bot ichida ishlovchi o'ta aqlli, foydali va xushmuomala sun'iy intellekt yordamchisisiz.\n\n"
    "QAT'IY XAVFSIZLIK VA MAVZU TAQIQLARI (SAFETY & STRICT PROHIBITIONS):\n"
    "1. DINIY MAVZULAR VA SAVOLLAR QAT'IYAN TAQIQLANGAN:\n"
    "   - Hech qanday diniy masalalar, shariat ahkomlari, fatvolar, aqidaviy talqinlar, ibodat tartiblari (namoz, ro'za, tahorat, haj, zakot va b.), halol-harom, gunoh-savob, mazhabiy bahslar yoki e'tiqodiy savollarga javob bermang.\n"
    "   - Agar foydalanuvchi diniy savol bersa, muloyimlik bilan bot diniy masalalarda vakolatli emasligini va bunday savollarga javob berish qat'iyan taqiqlanganini bildiring hamda rasmiy vakolatli manbalarga (O'zbekiston musulmonlari idorasi: fatvo.uz yoki muslim.uz) murojaat qilishni tavsiya qiling.\n"
    "2. TERRORIZM, EKSTREMIZM VA ZO'RAVONLIK QAT'IYAN TAQIQLANGAN:\n"
    "   - Terrorizm, ekstremizm, radikalizm, qurol yoki portlovchi moddalar tayyorlash, terrorchilik tashkilotlari (ISHID, Al-Qoida va h.k.), zo'ravonlik, odam o'ldirish yoki noqonuniy qo'poruvchilik harakatlariga oid har qanday so'rovlarga javob berish QAT'IYAN MAN ETILADI.\n"
    "   - Bunday so'rovlarga zudlik bilan qat'iy rad javobini bering.\n\n"
    "QAT'IY TIL VA IMLO TALABLARI (LANGUAGE PURITY & GRAMMAR):\n"
    "1. Foydalanuvchi yoki vazifa qaysi tilni talab qilsa (o'zbek, rus, ingliz), butun javobni 100% FAQAT VA FAQAT O'SHA TILDA bering.\n"
    "2. TILLARNI ARALASHTIRISH QAT'IYAN MAN ETILADI: Agar so'rov rus tilida bo'lsa, birorta ham o'zbekcha so'z yoki qo'shimcha ishlatmang, barcha terminlarni to'liq rus tiliga tarjima qiling. Agar o'zbek tilida bo'lsa, ruscha yoki inglizcha so'zlarni aralashtirmang.\n"
    "3. Imlo va grammatika qoidalariga 100% rioya qiling: barcha so'zlar va ularning grammatik qo'shimchalarini (-da, -dan, -ga, -ni, -ning, -lar, -dagi) aslo tushirib qoldirmasdan, to'liq yozing.\n"
    "4. Rus tilida: соблюдайте строгие правила русской орфографии, пунктуации и падежных окончаний, избегайте опечаток и калек.\n"
    "5. Javoblarni chiroyli, tushunarli, tuzilmaviy formatda taqdim eting. Hech qanday ma'lumotni oxirida chala qoldirmang, xulosalarni to'liq yakunlang.\n\n"
    "QONUNCHILIK VA HUQUQIY FACT-CHECKING TALABI (LEGAL FACT-CHECKING & OFFICIAL SOURCES):\n"
    "1. Agar foydalanuvchi so'rovi qonunchilik, huquqiy masalalar, kodekslar, qonunlar, qarorlar, farmonlar, moddalar, sud, jarimalar, mehnat/fuqarolik/soliq/oila huquqi yoki yuridik normalarga oid bo'lsa:\n"
    "   - ASOS: Javobni O'zbekiston Respublikasining amaldagi qonunchiligiga (tegishli Kodeks, Qonun, Qaror, Farmon) asoslang.\n"
    "   - ANIQLIK QOIDASI (ENG MUHIM): Modda, qism yoki band raqamini FAQAT unga to'liq ishonchingiz komil bo'lganda yozing. Ishonchingiz komil bo'lmasa, raqamni UYDIRMANG va TAXMIN QILMANG: faqat hujjat nomini yozing va 'modda raqamini lex.uz dagi amaldagi tahrirdan tekshiring' deb ayting.\n"
    "   - MUDDAT, MIQDOR VA TOIFALARNI UYDIRMANG: kunlar, oylar, foizlar, summalar, istisno toifalar (masalan, 'rahbarlar uchun alohida muddat') faqat aniq bilsangiz yozilsin. Bilmasangiz, 'aniq muddat qonunchilikda belgilangan, uni amaldagi tahrirdan tekshiring' deb yozing.\n"
    "   - TAHRIR: Mehnat kodeksi — 2022-yil 28-oktabrdagi O'RQ-798-son Qonun bilan tasdiqlangan, 2023-yil 30-aprelda kuchga kirgan yangi kodeks. 1995-yil 21-dekabrdagi eski kodeks amaldan chiqqan: eski moddalar raqami va qoidalarini ishlatmang. Boshqa hujjatlarning ham faqat amaldagi tahririga tayaning.\n"
    "   - MANBANI KO'RSATISH: Qaysi hujjatdan olinganini yozing (Kodeks yoki Qonun nomi; raqam faqat ishonchli bo'lsa). Masalan: 'O'zbekiston Respublikasi Mehnat kodeksiga muvofiq...'. Raqamli ma'lumotlar uchun 'amaldagi tahrirni lex.uz da tekshiring' eslatmasini qo'shing.\n"
    "   - Agar savolga qonunchilikda aniq javob topilmasa yoki sizda ishonchli ma'lumot bo'lmasa, buni ochiq ayting va taxminni qonun deb ko'rsatmang.\n"
    "   - HAVOLALAR (URL/LINKLAR) TAQIQLANADI: Javobda hech qanday sayt havolasi, URL yoki web-link (jumladan lex.uz yoki norma.uz havolalari) ko'rsatilmasin! Faqatgina rasmiy manbaning o'zini matn shaklida ko'rsating.\n"
)

DEEP_RESEARCH_PROMPT = (
    "Siz xalqaro darajadagi bosh ilmiy va tahliliy izlanuvchisiz. "
    "Mavzu yoki savol bo'yicha eng yuqori darajada chuqur, mukammal, faktlarga asoslangan va tizimli hisobot tayyorlang.\n"
    "QAT'IY TAQIQ: Diniy masalalar, shariat hukmlari, fatvolar hamda terrorizm, ekstremizm, zo'ravonlik yoki qurol/portlovchi moddalarga oid tadqiqotlar o'tkazish QAT'IYAN TAQIQLANADI! Bunday mavzularda hisobot tayyorlamang va qat'iy rad javobini bering.\n"
    "HUQUQIY FACT-CHECKING: Agar tadqiqot mavzusi qonunchilik yoki huquqiy sohalarga tegishli bo'lsa, O'zbekiston Respublikasining amaldagi qonunchilik hujjatlari bo'yicha aniq qonun nomi, modda va me'yorlar bilan fact-checking qiling. Hech qanday sayt havolalari (lex.uz, norma.uz va b. URL linklar) kiritilmasin, faqat rasmiy manba nomi matn ko'rinishida ko'rsatilsin.\n"
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
                        temperature=0.2,
                        safety_settings=DEFAULT_SAFETY_SETTINGS,
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
                temperature=0.2,
                safety_settings=DEFAULT_SAFETY_SETTINGS,
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

        # Xavfsizlik sozlamalarini ta'minlash
        if config and not getattr(config, "safety_settings", None):
            config.safety_settings = DEFAULT_SAFETY_SETTINGS
        elif not config:
            config = types.GenerateContentConfig(safety_settings=DEFAULT_SAFETY_SETTINGS)

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
        mime_type: str = "image/jpeg",
        lang: str = "uz"
    ) -> str:
        """
        Gemini AI ga savol/xabar yuborish va javob olish (zaxira modellar bilan)
        """
        # Kontent xavfsizligi tekshiruvi (diniy va terrorizm/ekstremizm taqiqlari)
        violation, reason = check_content_safety(prompt)
        if violation != SafetyViolation.NONE:
            logger.warning(f"ask_gemini: xavfsizlik filtri ishga tushdi ({reason}) - user_id={user_id}")
            return get_safety_refusal_message(violation, lang=lang)

        if not self.is_configured():
            return (
                "⚠️ <b>Gemini API kaliti kiritilmagan!</b>\n\n"
                "AI xizmatidan foydalanish uchun <code>.env</code> faylida <code>GEMINI_API_KEY</code> ni sozlang.\n\n"
                "🔑 Kalitni bepul olish: https://aistudio.google.com/app/apikey"
            )

        candidates = self._get_candidate_models()
        last_error = ""

        # Rasmiy matnlar kutubxonasi (laws/): mos modda parchalarini kontekst sifatida qo'shish
        lib_prompt, lib_nums = prompt, set()
        if prompt and not image_bytes and is_legal_query(prompt):
            lib_prompt, lib_nums = law_library.augment_prompt(prompt, prompt)

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
                    msg_payload = lib_prompt

                task = chat.send_message(message=msg_payload)
                response = await asyncio.wait_for(task, timeout=45.0)

                if response and response.text:
                    checked_text = law_library.annotate(response.text, lib_nums, lang)
                    enriched, _, _ = enrich_legal_response(checked_text, query=prompt, lang=lang)
                    return enriched
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
        prompt: Optional[str] = None,
        lang: str = "uz"
    ) -> str:
        """
        Audio xabarni Gemini AI ga yuborish va tahlil/javob olish (zaxira modellar bilan)
        """
        if prompt:
            violation, reason = check_content_safety(prompt)
            if violation != SafetyViolation.NONE:
                logger.warning(f"ask_gemini_audio: xavfsizlik filtri ishga tushdi ({reason}) - user_id={user_id}")
                return get_safety_refusal_message(violation, lang=lang)

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
            "6. Agar foydalanuvchi murojaat yoki fikr bildirgan bo'lsa, xabar mazmunini to'liq ifodalab, ravon tahliliy xulosa bering.\n"
            "7. QAT'IY XAVFSIZLIK VA MAVZU CHEKLOVI: Agar audio xabarda diniy masalalar, shariat ahkomlari, fatvolar, aqidaviy talqinlar yoki terrorizm, ekstremizm, zo'ravonlik, qurol/portlovchi moddalar yasashga oid savol yoki topshiriq bo'lsa, ularga javob berish QAT'IYAN TAQIQLANADI. Bunday hollarda qat'iy rad javobini bering."
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
                    resp_violation, _ = check_content_safety(response.text)
                    if resp_violation != SafetyViolation.NONE:
                        return get_safety_refusal_message(resp_violation, lang=lang)
                    enriched, _, _ = enrich_legal_response(response.text, query=prompt or "", lang=lang)
                    return enriched
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
        # Kontent xavfsizligi tekshiruvi (diniy va terrorizm/ekstremizm taqiqlari)
        violation, reason = check_content_safety(query)
        if violation != SafetyViolation.NONE:
            logger.warning(f"deep_research: xavfsizlik filtri ishga tushdi ({reason}) - user_id={user_id}")
            return get_safety_refusal_message(violation, lang=lang)

        lang_instruction = (
            "Hisobotni 100% SOF ADABIY O'ZBEK TILIDA, imlo va grammatika qoidalariga rioya qilib tayyorlang."
            if lang == "uz" else
            ("Подготовьте отчет на БЕЗУПРЕЧНОМ РУССКОМ ЯЗЫКЕ, соблюдая все грамматические и стилистические нормы."
             if lang == "ru" else
             "Prepare the complete report in FLAWLESS, PROFESSIONAL ENGLISH.")
        )
        cfg = types.GenerateContentConfig(
            system_instruction=f"{DEEP_RESEARCH_PROMPT}\n\n{lang_instruction}",
            temperature=0.4,
            safety_settings=DEFAULT_SAFETY_SETTINGS
        )
        prompt = f"Mavzu: {query}\n\nIltimos, ushbu mavzu bo'yicha har tomonlama to'liq va professional Deep Research hisoboti tayyorlang."
        deep_nums = set()
        if is_legal_query(query):
            prompt, deep_nums = law_library.augment_prompt(query, prompt)
        report = await self.generate_content_with_fallback(
            contents=prompt,
            config=cfg,
            timeout=60.0,
            operation_name=f"Deep Research ({query[:30]})"
        )
        report = law_library.annotate(report, deep_nums, lang)
        enriched, _, _ = enrich_legal_response(report, query=query, lang=lang)
        return enriched

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
            "both": "Sudgacha Talabnoma (Pretenziya) va Sudga Da'vo arizasi (To'liq to'plam)",
            "debt_cert": "Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati (Rasmiy to'plam)",
            "debt_certificate": "Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati (Rasmiy to'plam)",
            "malumotnoma": "Qarzdorlik to'g'risida Ma'lumotnoma va Ilova xati (Rasmiy to'plam)",
            "notary_exec": "Notariusdan Ijro xati olish to'g'risida Ariza va Ogohlantirish xati",
            "notary_app": "Notariusga Ijro xati yozish to'g'risida Ariza",
            "notary_notice": "Qarzdorga notarial ogohlantirish xati (Bildirishnoma)",
            "notary_both": "Notariusga Ijro xati arizasi va Ogohlantirish to'plami",
            "upload": "Shartnoma/Hujjat asosida Sudga Da'vo arizasi",
            "contract_lawsuit": "Shartnoma bo'yicha Sudga Da'vo arizasi",
            "contract_demand": "Shartnoma bo'yicha Sudgacha Talabnoma (Pretenziya)",
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

        if doc_type.startswith("notary") or doc_type in ("notary_exec", "notary_app", "notary_notice", "notary_both", "notary", "ijro_xati"):
            if lang == "ru":
                prompt = (
                    "Вы — ведущий юрисконсульт по нотариальному праву, эксперт по бесспорному взысканию задолженности и принудительному исполнению в Республике Узбекистан.\n\n"
                    "ЗАДАЧА: На основе предоставленных пользователем данных (договор, расписка, акт сверки взаиморасчетов, счет-фактуры, задолженность) "
                    "составьте официальный комплект документов для получения ИСПОЛНИТЕЛЬНОЙ НАДПИСИ НОТАРИУСА (Notarius ijro xati) "
                    "в строгом соответствии с Законом Республики Узбекистан «О нотариате» (статьи 76–80), Положением, утверждённым приказом министра юстиции Республики Узбекистан № 2-мх (рег. № 3757) "
                    "и Законом РУз «Об исполнении судебных актов и актов других органов» (статья 8).\n\n"
                    f"ИСХОДНЫЕ ДАННЫЕ И МАТЕРИАЛЫ:\n{details}\n\n"
                    "🔴 ОБЯЗАТЕЛЬНАЯ СТРУКТУРА ДОКУМЕНТА (3 ЧАСТИ ЧЕРЕЗ РАЗДЕЛИТЕЛЬ '---'):\n\n"
                    "ЧАСТЬ 1: ЗАЯВЛЕНИЕ НОТАРИУСУ О СОВЕРШЕНИИ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ\n"
                    "• Адресат: Частнопрактикующему / государственному нотариусу нотариального округа [Город / Район, Ф.И.О. нотариуса при наличии];\n"
                    "• Взыскатель: [Наименование организации / Ф.И.О. гражданина, ИНН/ПИНФЛ, юридический адрес, телефон, банковские реквизиты: р/счет, банк, МФО];\n"
                    "• Должник: [Наименование организации / Ф.И.О. гражданина, ИНН/ПИНФЛ, адрес места жительства/нахождения, телефон, банковские реквизиты];\n"
                    "• Заголовок по центру: ЗАЯВЛЕНИЕ О СОВЕРШЕНИИ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ\n"
                    "  (в порядке статей 76-80 Закона РУз «О нотариате» и Положения, утверждённого приказом министра юстиции № 2-мх, рег. № 3757)\n"
                    "• Обстоятельства дела: Подробное описание договора/расписки (дата, номер), обязательства по поставке/оплате, возникшая задолженность, подтверждение признания долга (акт сверки, счета-фактуры), отсутствие спора о праве;\n"
                    "• Соблюдение порядка уведомления: Указание на направление должнику письменного требования-предупреждения и истечение срока для добровольного погашения;\n"
                    "• Расчет требований: Основной долг, начисленные штрафные санкции (если предусмотрены договором/законом), государственная пошлина и плата за правовые услуги нотариуса;\n"
                    "• Правовое основание: Статьи 76, 77, 78, 79, 80 Закона РУз «О нотариате», Положение, утверждённое приказом министра юстиции № 2-мх (рег. № 3757), ст. 7 Закона «Об исполнении судебных актов и актов других органов», ст. 236, 237, 327 ГК РУз;\n"
                    "• ПРОШУ (по пунктам):\n"
                    "  1. Совершить исполнительную надпись о бесспорном взыскании с Должника в пользу Взыскателя задолженности в размере ... (...) сум;\n"
                    "  2. Взыскать с Должника уплаченную государственную пошлину и расходы за услуги нотариуса в размере ... сум;\n"
                    "• Перечень приложений (копия договора, акт сверки взаиморасчетов, копия уведомления должнику и почтовой квитанции, квитанция об оплате госпошлины);\n"
                    "• Подпись: Взыскатель / Руководитель: _______________ (Ф.И.О.) / М.П., Дата.\n\n"
                    "---\n\n"
                    "ЧАСТЬ 2: ОФИЦИАЛЬНОЕ УВЕДОМЛЕНИЕ-ПРЕДУПРЕЖДЕНИЕ ДОЛЖНИКУ\n"
                    "• Исходящий номер и дата;\n"
                    "• Кому (Должник): Руководителю [Наименование организации / Ф.И.О.], адрес;\n"
                    "• От кого (Взыскатель): [Наименование, реквизиты, контакты];\n"
                    "• Заголовок по центру: ПРЕДУПРЕЖДЕНИЕ О СОВЕРШЕНИИ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ НОТАРИУСА\n"
                    "• Текст: В соответствии со статьями 76–80 Закона РУз «О нотариате» и Положением, утверждённым приказом министра юстиции № 2-мх (рег. № 3757) уведомляем, что в случае непогашения задолженности по Договору № ___ в сумме ... сум в 10-дневный срок с момента получения настоящего уведомления, мы обратимся к нотариусу за совершением ИСПОЛНИТЕЛЬНОЙ НАДПИСИ.\n"
                    "• Разъяснение правовых последствий: Исполнительная надпись нотариуса имеет силу исполнительного листа (ст. 7 Закона «Об исполнении судебных актов») и будет немедленно предъявлена в Бюро принудительного исполнения (БПИ) при Генеральной прокуратуре РУз для наложения ареста на расчетные счета и принудительного обращения взыскания на имущество с возложением всех расходов на должника;\n"
                    "• Реквизиты для оплаты;\n"
                    "• Подпись: Взыскатель _______________ (Ф.И.О.).\n\n"
                    "---\n\n"
                    "ЧАСТЬ 3: ПРОЕКТ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ НОТАРИУСА\n"
                    "• Заголовок по центру: ИСПОЛНИТЕЛЬНАЯ НАДПИСЬ\n"
                    "• Дата совершения и место совершения;\n"
                    "• Сведения о нотариусе, взыскателе и должнике;\n"
                    "• Распоряжение нотариуса о взыскании с должника в пользу взыскателя денежных средств в бесспорном порядке;\n"
                    "• Указание о немедленной передаче в органы Бюро принудительного исполнения (БПИ);\n"
                    "• Взысканная государственная пошлина и гербовый сбор, номер в реестре, подпись и гербовая печать нотариуса.\n\n"
                    "100% чистый юридический русский язык, строгие формулировки законов, без лишних вводных фраз."
                )
            elif lang == "en":
                prompt = (
                    "You are a senior legal expert in notarial enforcement and debt recovery under the laws of the Republic of Uzbekistan.\n\n"
                    "TASK: Based on the provided contract, debt records, or reconciliation statements, draft a complete official document package "
                    "for obtaining a NOTARIAL EXECUTION INSCRIPTION (Notarius ijro xati) pursuant to the Law of the Republic of Uzbekistan 'On Notaries' (Articles 76–80), "
                    "Regulation approved by Minister of Justice Order No. 2-mh (reg. No. 3757) (Rules on Notarial Execution Inscriptions), and the Law 'On Enforcement of Judicial and Other Acts' (Article 7).\n\n"
                    f"SOURCE DATA:\n{details}\n\n"
                    "🔴 MANDATORY STRUCTURE (3 DISTINCT PARTS SEPARATED BY '---'):\n\n"
                    "PART 1: FORMAL APPLICATION TO NOTARY PUBLIC FOR EXECUTION INSCRIPTION\n"
                    "• Addressed to Notary Public of relevant district;\n"
                    "• Creditor and Debtor details (Tax ID, address, banking credentials);\n"
                    "• Centered Title: APPLICATION FOR ISSUANCE OF NOTARIAL EXECUTION INSCRIPTION\n"
                    "• Factual background regarding undisputed contractual default, reconciliation statement, and lack of bona fide dispute;\n"
                    "• Compliance with formal pre-notification to debtor;\n"
                    "• Exact calculation of principal debt, statutory interests, and notary service charges;\n"
                    "• Statutory references: Law 'On Notaries' Arts 76-80, MoJ Regulation (Order 2-mh, reg. 3757), Law 'On Enforcement' Art 7, Civil Code Arts 236, 237, 327;\n"
                    "• Prayer for Relief requesting notarial execution inscription;\n"
                    "• List of exhibits, signature, and date.\n\n"
                    "---\n\n"
                    "PART 2: FORMAL NOTICE AND FINAL WARNING TO DEBTOR\n"
                    "• Outgoing number and date;\n"
                    "• Addressed to Debtor executive management;\n"
                    "• Centered Title: FORMAL NOTICE OF FORTHCOMING NOTARIAL EXECUTION INSCRIPTION\n"
                    "• Demand to settle outstanding debt within 10 days;\n"
                    "• Warning that failure will result in immediate Notarial Execution Inscription enforceable by the Compulsory Enforcement Bureau (MIB) with asset freeze and bank debiting;\n"
                    "• Payment account details and creditor signature.\n\n"
                    "---\n\n"
                    "PART 3: DRAFT NOTARIAL EXECUTION INSCRIPTION\n"
                    "• Centered Title: NOTARIAL EXECUTION INSCRIPTION\n"
                    "• Formal order commanding enforcement by the Compulsory Enforcement Bureau (MIB);\n"
                    "• Registry number, fee notation, Notary seal and signature block.\n\n"
                    "Professional legal English, complete factual drafting without placeholders, no conversational filler."
                )
            else:
                prompt = (
                    "Siz O'zbekiston Respublikasining notariat sohasi bo'yicha bosh yuristkonsulti, notarius va majburiy ijro bo'yicha yetakchi ekspertsiz.\n\n"
                    "VAZIFA: Foydalanuvchi taqdim etgan shartnoma, qarz tilxati, hisob-kitob solishtiruvi dalolatnomasi (akt sverki), "
                    "hisob-fakturalar yoki qarzdorlik ma'lumotlari asosida NOTARIUSDAN IJRO XATI OLISH UCHUN rasmiy va qonuniy to'liq hujjatlar to'plamini tuzib bering.\n\n"
                    "HUQUQIY ASOSLAR:\n"
                    "• O'zbekiston Respublikasining «Notariat to'g'risida»gi Qonuni 76, 77, 78, 79, 80-moddalari (Ijro xatlarini yozish tartibi, shartlari va mazmuni);\n"
                    "• O'zbekiston Respublikasi Adliya vazirining 2026-yil 13-yanvardagi 2-mh-sonli buyrug'i bilan tasdiqlangan «Notariuslar tomonidan ijro xatlarini yozish tartibi to'g'risida»gi Nizom (ro'yxat raqami 3757, 19.01.2026; 2026-yil 1-martdan kuchga kirgan; 26-mh-son buyruq bilan o'zgartirilgan). Qarz so'zsiz undiriladigan hujjatlar ro'yxati esa Vazirlar Mahkamasi tomonidan belgilanadi (Notariat qonuni 77-modda);\n"
                    "• O'zbekiston Respublikasi «Sud hujjatlari va boshqa organlar hujjatlarini ijro etish to'g'risida»gi Qonunining 7-moddasi birinchi qismi 6-bandi (Notariuslarning ijro xatlari to'g'ridan-to'g'ri MIB uchun ijro hujjati hisoblanishi);\n"
                    "• O'zbekiston Respublikasi Fuqarolik kodeksining 236, 237, 327, 333-moddalari.\n\n"
                    f"FOYDALANUVCHI TAQDIM ETGAN HUJJAT VA TAFSILOTLAR:\n{details}\n\n"
                    "🔴 QAT'IY TALABLAR (HUJJATLAR TO'PLAMI KETMA-KET 3 TA QISMDAN IBORAT BO'LIB, O'RTASIDA '---' AJRATKICHI BO'LISHI SHART):\n\n"
                    "1-QISM: NOTARIUSGA IJRO XATI YOZISH TO'G'RISIDA ARIZA (ILTIMOSNOMA)\n"
                    "• Kimga: [Tuman/Shahar] davlat notarial idorasi (yoki xususiy amaliyot bilan shug'ullanuvchi) notariusi [Notarius F.I.Sh.]ga;\n"
                    "• Undiruvchi (Kreditor): Tashkilot nomi yoki F.I.Sh., STIR/JShShIR, yuridik manzili, hisob-raqami, bank nomi, MFO, telefon;\n"
                    "• Qarzdor: Tashkilot nomi yoki F.I.Sh., STIR/JShShIR, yuridik/yashash manzili, telefon raqami, bank rekvizitlari;\n"
                    "• Katta sarlavha: NOTARIUSGA IJRO XATI YOZISH TO'G'RISIDA ARIZA\n"
                    "  (O'zR «Notariat to'g'risida»gi Qonuni 76-80-moddalari va Adliya vazirining 2-mh-sonli buyrug'i bilan tasdiqlangan Nizom (ro'yxat raqami 3757) asosida)\n"
                    "• Holat bayoni: [Sana] dagi [№] -sonli Shartnoma, o'zaro hisob-kitob solishtiruvi dalolatnomasi, yetkazib berilgan tovar/xizmatlar, qarzdorlik qachon vujudga kelgani, to'lov muddati qachon tugagani va nizo mavjud emasligi;\n"
                    "• Ogohlantirish xati yuborilganligi: Qarzdorga yozma ravishda ogohlantirish xati yuborilganligi va berilgan ixtiyoriy muddatda qarz to'lanmaganligi;\n"
                    "• Undiriladigan summa hisob-kitobi: Asosiy qarz summasi + shartnomaviy penya + to'langan davlat boji va notarial xizmat haqi;\n"
                    "• Qonuniy asos: «Notariat to'g'risida»gi Qonun 76-80-moddalari, Adliya vazirining 2-mh-sonli buyrug'i bilan tasdiqlangan Nizom (ro'yxat raqami 3757), «Sud hujjatlari va boshqa organlar hujjatlarini ijro etish to'g'risida»gi Qonunning 7-moddasi birinchi qismining 6-bandi, FK 236, 237, 327-moddalari;\n"
                    "• SO'RAYMAN (bandma-band):\n"
                    "  1. Qarzdor [Qarzdor F.I.Sh./Nomi]dan undiruvchi [Undiruvchi F.I.Sh./Nomi] foydasiga ... (...) so'm asosiy qarz summasini so'zsiz tartibda undirish to'g'risida IJRO XATI YOZIB BERISHINGIZNI;\n"
                    "  2. Undiruvchi tomonidan to'langan ... so'm davlat boji va notarial xizmat haqini qarzdor hisobidan undirib berishingizni.\n"
                    "• Ilovalar ro'yxati (Shartnoma nusxasi, o'zaro hisob-kitob solishtiruvi dalolatnomasi, schyot-fakturalar, qarzdorga yuborilgan ogohlantirish xati va pochta kvitansiyasi, davlat boji to'langanligi kvitansiyasi);\n"
                    "• Undiruvchi (Rahbar): _______________ (F.I.Sh.)  M.O'.  Sana: 202_ yil «___» ___________\n\n"
                    "---\n\n"
                    "2-QISM: QARZDORGA RASMIY OGOHLANTIRISH XATI (NOTARIAL BILDIRISHNOMA)\n"
                    "• Chiqish raqami va sana: Chiqish № _____ 202_ yil «___» ___________\n"
                    "• Kimga: [Qarzdor korxona rahbari F.I.Sh. yoki fuqaro ismi], manzili;\n"
                    "• Kimdan: [Undiruvchi tashkilot/fuqaro], rekvizitlari;\n"
                    "• Katta sarlavha: OGOHLANTIRISH XATI (BILDIRISHNOMA)\n"
                    "  (Notarius tomonidan ijro xati yozilishi va MIBga yuborilishi to'g'risida)\n"
                    "• Matn: O'zbekiston Respublikasining «Notariat to'g'risida»gi Qonuni 76–80-moddalari hamda Adliya vazirining 2-mh-sonli buyrug'i bilan tasdiqlangan Nizom (ro'yxat raqami 3757) talablariga muvofiq, [Sana] dagi [№] -sonli Shartnoma bo'yicha to'lanmagan ... (...) so'm qarzdorlikni ushbu xat olingan kundan boshlab 10 (o'n) kun ichida ixtiyoriy ravishda quyidagi bank hisob-raqamiga to'lashingizni yoki yozma asoslantirilgan e'tirozingizni bildirishingizni talab qilamiz;\n"
                    "• Huquqiy oqibatlar haqida qat'iy ogohlantirish: Agar ko'rsatilgan 10 kunlik muddatda qarz to'lanmasa, sud muhokamalarisiz NOTARIUS TOMONIDAN IJRO XATI YOZILADI. Notariusning ijro xati ijro hujjati bo'lib («Sud hujjatlari va boshqa organlar hujjatlarini ijro etish to'g'risida»gi Qonun 7-moddasi birinchi qismining 6-bandi), darhol O'zR Bosh prokuraturasi huzuridagi Majburiy ijro byurosi (MIB)ga topshiriladi. MIB tomonidan barcha bank hisoblaringizga band solinadi va mol-mulkingiz xatlanib, majburiy undiruvga qaratiladi. Barcha ijro xarajatlari hamda davlat boji qarzdor hisobidan undiriladi;\n"
                    "• To'lov uchun bank rekvizitlari;\n"
                    "• Imzo: Undiruvchi rahbar _______________ (F.I.Sh.) M.O'.\n\n"
                    "---\n\n"
                    "3-QISM: NOTARIUSNING IJRO XATI RASMIY LOYIHASI\n"
                    "• Katta sarlavha: IJRO XATI\n"
                    "• Yozilgan sana va joy (shahar/tuman);\n"
                    "• Notarius rekvizitlari (notarial okrug, notarius F.I.Sh.);\n"
                    "• Undiruvchi va qarzdor rekvizitlari;\n"
                    "• Qarzdorlik undiriladigan davr va asos hujjatlar;\n"
                    "• Farmoyish matni: «O'zbekiston Respublikasining 'Notariat to'g'risida'gi Qonunining 76-80-moddalari va Adliya vazirining 2-mh-sonli buyrug'i bilan tasdiqlangan Nizom (ro'yxat raqami 3757) talablariga asosan, Qarzdor [Nomi/F.I.Sh.]dan Undiruvchi [Nomi/F.I.Sh.] foydasiga ... so'm qarz summasi va ... so'm davlat boji undirilsin. Ushbu ijro xati O'zbekiston Respublikasi 'Sud hujjatlari va boshqa organlar hujjatlarini ijro etish to'g'risida'gi Qonunning 7-moddasi birinchi qismining 6-bandiga asosan ijro hujjati kuchiga ega bo'lib, MIB organlari tomonidan majburiy ijro etilishi lozim.»;\n"
                    "• Reyestrda qayd qilingan raqam va undirilgan davlat boji/to'lov summasi;\n"
                    "• Notarius imzosi va gerbli muhr joyi (M.O'.).\n\n"
                    "1. 100% sof va rasmiy o'zbek tilida (lotin alifbosida), mukammal yuridik atamalar bilan, O'zbekiston qonunchiligiga to'liq mos ravishda yozilsin.\n"
                    "2. To'g'ridan-to'g'ri tayyor hujjatlar to'plamini chiqaring, ortiqcha salom-alik yoki kirish so'zlarisiz."
                )
        elif doc_type in ("debt_cert", "debt_certificate", "malumotnoma"):
            if lang == "ru":
                prompt = (
                    "Вы — главный юрисконсульт и главный бухгалтер-аудитор высшей квалификации.\n\n"
                    "ЗАДАЧА: На основе предоставленных пользователем документов (договор, акт сверки взаиморасчетов, счета-фактуры, претензия или иск) "
                    "составьте официальный комплект документов: «СПРАВКА О ЗАДОЛЖЕННОСТИ И НЕУСТОЙКЕ + СОПРОВОДИТЕЛЬНОЕ ПИСЬМО».\n\n"
                    f"ИСХОДНЫЕ ДАННЫЕ И МАТЕРИАЛЫ:\n{details}\n\n"
                    "🔴 СТРОГИЕ ТРЕБОВАНИЯ К ОФОРМЛЕНИЮ И СТРУКТУРЕ (ОБЯЗАТЕЛЬНО 2 ЧАСТИ ЧЕРЕЗ РАЗДЕЛИТЕЛЬ '---'):\n\n"
                    "ЧАСТЬ 1: СОПРОВОДИТЕЛЬНОЕ ПИСЬМО (KUZATUV / ILOWA XATI)\n"
                    "• Фирменный бланк организации-кредитора (Наименование, ИНН, адрес, р/счет, банк, МФО, телефон, e-mail);\n"
                    "• Исходящий номер и дата: Исх. № ____ от «___» ___________ 202_ г.;\n"
                    "• Адресат: В [Наименование суда] или Руководителю [Наименование организации должника];\n"
                    "• Заголовок по центру: СОПРОВОДИТЕЛЬНОЕ ПИСЬМО\n"
                    "• Текст письма: Направляем Вам Справку о состоянии взаимных расчетов и задолженности по Договору № ___ от «___» ________ 202_ г. и Акту сверки взаиморасчетов для рассмотрения и приобщения к материалам дела / удовлетворения требований;\n"
                    "• Перечень приложений (Приложение: 1. Справка о задолженности на __ л. в 1 экз.; 2. Копия акта сверки; 3. Копии счетов-фактур и накладных);\n"
                    "• Подпись: Руководитель (Директор): _______________ / Ф.И.О. / М.П.\n\n"
                    "---\n\n"
                    "ЧАСТЬ 2: СПРАВКА О ЗАДОЛЖЕННОСТИ И РАСЧЕТЕ НЕУСТОЙКИ\n"
                    "• Шапка с реквизитами кредитора и адресата (В суд / Должнику);\n"
                    "• Заголовок по центру: СПРАВКА О ЗАДОЛЖЕННОСТИ\n"
                    "  (по Договору № ___ от «___» ________ 202_ г. и Акту сверки взаиморасчетов)\n"
                    "• Вводная часть: Настоящая справка выдана [Наименование кредитора] в том, что по состоянию на «___» ________ 202_ г. задолженность [Наименование должника, ИНН] составляет следующую сумму:\n"
                    "• ТАБЛИЦА РАСЧЕТА (Обязательно в формате Markdown таблицы: | № | Документ (Счет-фактура / Накладная / Акт) | Дата | Сумма поставки / оказанных услуг (сум) | Оплачено (сум) | Остаток долга (сум) | Срок оплаты по договору | Дней просрочки | Начисленная пеня (сум) |);\n"
                    "• Строка «ИТОГО:» с подсчетом суммы основного долга и суммы неустойки/пени;\n"
                    "• СУММА ПРОПИСЬЮ И ЦИФРАМИ:\n"
                    "  - Сумма основного долга: ... (...) сум;\n"
                    "  - Сумма пени/неустойки: ... (...) сум;\n"
                    "  - ВСЕГО К ВЗЫСКАНИЮ: ... (...) сум;\n"
                    "• Правовое и бухгалтерское обоснование: Закон РУз «О бухгалтерском учете» (статьи 14, 19), Гражданский кодекс РУз (статьи 236, 237, 327, 333), условия договора и акт сверки взаиморасчетов;\n"
                    "• Подтверждение достоверности: «Справка составлена на основании первичных бухгалтерских документов, выписок банка и акта сверки взаиморасчетов. Правильность расчетов подтверждаем:»;\n"
                    "• ДВЕ ПОДПИСИ:\n"
                    "  Руководитель организации: _______________ (Ф.И.О.)\n"
                    "  Главный бухгалтер: _______________ (Ф.И.О.)\n"
                    "  М.П. (Место печати)\n\n"
                    "100% чистый русский язык, точные цифры из предоставленных данных, без лишних вводных реплик."
                )
            elif lang == "en":
                prompt = (
                    "You are a senior legal counsel and certified public accountant (CPA).\n\n"
                    "TASK: Based on the provided documents (contract, reconciliation statement, invoices, lawsuit or demand letter), "
                    "draft a complete official document set: «STATEMENT / CERTIFICATE OF INDEBTEDNESS + COVER LETTER».\n\n"
                    f"SOURCE DATA:\n{details}\n\n"
                    "🔴 STRICT FORMAT REQUIREMENTS (MUST HAVE 2 PARTS SEPARATED BY '---'):\n\n"
                    "PART 1: FORMAL TRANSMITTAL / COVER LETTER\n"
                    "• Corporate letterhead of Creditor (Company name, Tax ID, address, bank details, contact info);\n"
                    "• Reference No. and Date: Ref: _____ Date: ____________;\n"
                    "• Addressee: To the [Court Name] or Managing Director of [Debtor Company];\n"
                    "• Centered Title: FORMAL TRANSMITTAL / COVER LETTER\n"
                    "• Statement enclosing the official Certificate of Indebtedness and reconciliation records;\n"
                    "• Enclosures list (1. Certificate of Indebtedness; 2. Reconciliation Act; 3. Invoices/Receipts);\n"
                    "• Signature: Managing Director / CEO: _______________ (Full Name) [Seal / Stamp]\n\n"
                    "---\n\n"
                    "PART 2: CERTIFICATE OF INDEBTEDNESS AND PENALTY BREAKDOWN\n"
                    "• Header and Addressee;\n"
                    "• Centered Title: CERTIFICATE OF INDEBTEDNESS\n"
                    "• Factual background regarding contract reference, delivery dates, and payment defaults;\n"
                    "• ITEMIZED BREAKDOWN TABLE in Markdown (| No. | Document Ref / Invoice | Date | Amount Incurred | Amount Paid | Outstanding Balance | Due Date | Overdue Days | Accrued Penalty |);\n"
                    "• TOTAL ROW summarizing principal debt and penalty;\n"
                    "• SUM IN WORDS AND FIGURES (Principal debt, contractual penalties, total claim amount);\n"
                    "• Legal and accounting grounds (Law on Accounting, Civil Code performance and breach articles, contractual terms);\n"
                    "• Certification statement of accuracy based on primary ledger records;\n"
                    "• DUAL SIGNATURE BLOCK:\n"
                    "  Managing Director: _______________ (Full Name)\n"
                    "  Chief Accountant: _______________ (Full Name)\n"
                    "  Corporate Seal (L.S.)\n\n"
                    "Flawless professional English, exact financial calculations based on input, no conversational filler."
                )
            else:
                prompt = (
                    "Siz O'zbekiston Respublikasining bosh yuristkonsulti va oliy toifali bosh buxgalter-auditorisiz.\n\n"
                    "VAZIFA: Foydalanuvchi taqdim etgan shartnoma, o'zaro hisob-kitob solishtiruvi dalolatnomasi (akt sverki), "
                    "hisob-fakturalar, da'vo arizasi yoki talabnoma ma'lumotlari asosida sudga yoki debitorga taqdim etiladigan "
                    "rasmiy va qonuniy to'liq hujjatlar to'plami: «QARZDORLIK VA PENYA TO'G'RISIDA MA'LUMOTNOMA + ILOVA XATI»ni tuzib bering.\n\n"
                    f"FOYDALANUVCHI TAQDIM ETGAN HUJJAT VA MA'LUMOTLAR:\n{details}\n\n"
                    "🔴 QAT'IY TALABLAR (HUJJAT KETMA-KET 2 TA QISMDAN IBORAT BO'LIB, O'RTASIDA '---' AJRATKICHI BO'LISHI SHART):\n\n"
                    "1-QISM: RASMIY ILOVA XATI (KUZATUV XATI)\n"
                    "• Tashkilot rasmiy blankasi / rekvizitlari (Kreditor tashkilot nomi, STIR/INN, yuridik manzili, hisob-raqami, bank nomi, MFO, telefon, elektron pochta);\n"
                    "• Chiqish raqami va sana: Chiqish № _____ 202_ yil «___» ___________\n"
                    "• Kimga: [Tegishli tumanlararo iqtisodiy / fuqarolik sudiga yoki Qarzdor tashkilot rahbariga];\n"
                    "• Katta sarlavha: ILOVA XATI\n"
                    "• Matn: [Kreditor tashkilot nomi] va [Qarzdor tashkilot nomi] o'rtasida tuzilgan [Sana] dagi [№] -sonli Shartnoma hamda o'zaro hisob-kitob solishtiruvi dalolatnomasi bo'yicha vujudga kelgan qarzdorlik yuzasidan tayyorlangan rasmiy «Qarzdorlik va hisoblangan penya to'g'risida Ma'lumotnoma»ni ko'rib chiqish / da'vo arizasiga ilova qilish uchun taqdim etamiz;\n"
                    "• Ilovalar ro'yxati:\n"
                    "  1. Qarzdorlik va penya to'g'risida Ma'lumotnoma — 1 nusxada, ___ varaqda;\n"
                    "  2. O'zaro hisob-kitob solishtiruvi dalolatnomasi (Akt sverki) nusxasi — 1 nusxada;\n"
                    "  3. Shartnoma va hisob-fakturalar (yuk xatlari) nusxalari — 1 nusxada.\n"
                    "• Imzo:\n"
                    "  Bosh direktor (Rahbar): _______________ (F.I.Sh.)\n"
                    "  M.O'. (Muhr o'rni)\n\n"
                    "---\n\n"
                    "2-QISM: QARZDORLIK VA PENYA TO'G'RISIDA MA'LUMOTNOMA\n"
                    "• Tashkilot nomi va rekvizitlari;\n"
                    "• Kimga: [Sudga yoki Qarzdor korxonaga];\n"
                    "• Katta sarlavha:\n"
                    "  QARZDORLIK VA PENYA TO'G'RISIDA MA'LUMOTNOMA\n"
                    "  ([Sana] dagi [№] -sonli Shartnoma va hisob-kitoblarni solishtirish dalolatnomasi bo'yicha)\n"
                    "• Kirish qismi: Ushbu ma'lumotnoma [Kreditor tashkilot] tomonidan berildiki, [Sana] dagi [№] -sonli Shartnomaga muvofiq [Qarzdor tashkilot, STIR] ning 202_ yil «___» ___________ holatiga to'lanmagan qarzdorligi quyidagicha shakllangan:\n"
                    "• ANIQ JADVAL (Markdown jadval formatida):\n"
                    "  | № | Hujjat nomi va raqami (schyot-faktura / yuk xati) | Sana | Yetkazilgan tovar/xizmat qiymati (so'm) | To'langan summa (so'm) | Qolgan asosiy qarz (so'm) | To'lov muddati | Kechiktirilgan kunlar | Hisoblangan penya (so'm) |\n"
                    "  (Taqdim etilgan hujjatdagi ma'lumotlar asosida to'ldiring, agar ayrim detallar yetishmasa mantiqiy to'g'ri hisob-kitob qiling);\n"
                    "  | | JAMI: | | ... | ... | ... | | | ... |\n\n"
                    "• YAKUNIY SUMMALAR (RAQAMDA VA SO'Z BILAN):\n"
                    "  - Asosiy qarz summasi: ... (...) so'm;\n"
                    "  - Hisoblangan penya summasi: ... (...) so'm;\n"
                    "  - JAMI TALAB QILINAYOTGAN QARZDORLIK: ... (...) so'm.\n\n"
                    "• HUQUQIY VA BUXGALTERIYA ASOSLARI:\n"
                    "  - O'zbekiston Respublikasi «Buxgalteriya hisobi to'g'risida»gi Qonunining 14-moddasi (boshlang'ich hisob hujjatlari);\n"
                    "  - O'zbekiston Respublikasi Fuqarolik kodeksining 236, 237, 327, 333-moddalari;\n"
                    "  - Taraflar o'rtasida imzolangan Shartnoma bandlari hamda hisob-kitob solishtiruvi dalolatnomasi.\n\n"
                    "• TASDIQ SO'ZI:\n"
                    "  «Ushbu ma'lumotnoma buxgalteriya hisobining birlamchi hujjatlari, bank ko'chirmalari va o'zaro hisob-kitoblarni solishtirish dalolatnomasi asosida tuzildi hamda ko'rsatilgan raqamlarning to'g'riligi va haqqoniyligi tasdiqlanadi.»\n\n"
                    "• IKKITA MAS'UL SHAXS IMZOSI VA MUHR:\n"
                    "  Bosh direktor (Rahbar): _______________ (F.I.Sh.)\n"
                    "  Bosh buxgalter: _______________ (F.I.Sh.)\n"
                    "  M.O'. (Muhr o'rni)\n\n"
                    "1. 100% sof va rasmiy o'zbek tilida (lotin alifbosida), professional yuridik va moliyaviy terminlar bilan yozilsin.\n"
                    "2. Hech qanday kirish so'zlarisiz darhol tayyor rasmiy hujjat matnini chiqaring."
                )
        elif lang == "ru":
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
                + ("\n\nВАЖНО: Поскольку запрошен полный комплект (doc_type='both'), составьте документ из двух последовательных частей:\n"
                   "ЧАСТЬ 1: ДОСУДЕБНАЯ ПРЕТЕНЗИЯ (с 10-дневным сроком погашения и реквизитами);\n"
                   "---\n"
                   "ЧАСТЬ 2: ИСКОВОЕ ЗАЯВЛЕНИЕ В СУД (со всеми статьями ГК/ГПК, расчетом госпошлины и приложениями)." if doc_type == "both" else "")
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
                + ("\n\nNOTE: Since both documents are requested (doc_type='both'), provide two distinct parts:\n"
                   "PART 1: FORMAL DEMAND LETTER (NOTICE OF CLAIM)\n"
                   "---\n"
                   "PART 2: COURT LAWSUIT / STATEMENT OF CLAIM" if doc_type == "both" else "")
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
                "     O'zbekiston Respublikasi Fuqarolik kodeksining (FK) tegishli moddalari (masalan, 236, 237, 324, 325, 327, 333, 382, 732-739-moddalar), Fuqarolik protsessual kodeksining (FPK) 188, 189, 190, 191-moddalari (iqtisodiy sudga murojaat qilinsa — Iqtisodiy protsessual kodeksining 149, 150, 151-moddalari).\n\n"
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
                + ("\n\nMUHIM QOIDA: To'liq to'plam so'ralganligi sababli (doc_type='both'), hujjatni ketma-ket 2 ta qismdan iborat qiling:\n"
                   "1-QISM: SUDGACHA TALABNOMA (PRETENZIYA) (ixtiyoriy to'lash uchun 10 kunlik muddat va bank rekvizitlari bilan);\n"
                   "---\n"
                   "2-QISM: SUDGA DA'VO ARIZASI (Fuqarolik/Iqtisodiy sudga barcha moddalar, davlat boji hisob-kitobi va ilovalar ro'yxati bilan)." if doc_type == "both" else "")
            )

        prompt, _ = law_library.augment_prompt(f"{type_name} {str(details)[:6000]}", prompt)
        return await self.generate_content_with_fallback(
            contents=prompt,
            timeout=55.0,
            operation_name=f"Yuridik hujjat generatsiyasi ({doc_type})"
        )


ai_service = AIService()

