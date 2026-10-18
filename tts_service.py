import os
import re
import uuid
import logging
import asyncio
from pathlib import Path
from typing import Tuple

import edge_tts
from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# O'zbek ovozlari (Microsoft Neural)
DEFAULT_VOICE = "uz-UZ-MadinaNeural"  # Tabiiy, ravon va tiniq o'zbek ayol ovozi
MALE_VOICE = "uz-UZ-SardorNeural"    # Tabiiy o'zbek erkak ovozi

# Barcha Unicode emojilari va maxsus piktogrammalarni tozalash uchun regex
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff\u2600-\u27ff\u2b50-\u2b55\u2300-\u23ff\u200d\u200c\ufe0f]+",
    flags=re.UNICODE
)

# Krill alifbosini lotin alifbosiga o'girish jadvali (Edge-TTS faqat o'zbek lotin alifbosini to'g'ri o'qiydi)
CYRILLIC_TO_LATIN = {
    'А': 'A', 'а': 'a',
    'Б': 'B', 'б': 'b',
    'В': 'V', 'в': 'v',
    'Г': 'G', 'г': 'g',
    'Д': 'D', 'д': 'd',
    'Е': 'E', 'е': 'e',
    'Ё': 'Yo', 'ё': 'yo',
    'Ж': 'J', 'ж': 'j',
    'З': 'Z', 'з': 'z',
    'И': 'I', 'и': 'i',
    'Й': 'Y', 'й': 'y',
    'К': 'K', 'к': 'k',
    'Л': 'L', 'л': 'l',
    'М': 'M', 'м': 'm',
    'Н': 'N', 'н': 'n',
    'О': 'O', 'о': 'o',
    'П': 'P', 'п': 'p',
    'Р': 'R', 'р': 'r',
    'С': 'S', 'с': 's',
    'Т': 'T', 'т': 't',
    'У': 'U', 'у': 'u',
    'Ф': 'F', 'ф': 'f',
    'Х': 'X', 'х': 'x',
    'Ц': 'Ts', 'ц': 'ts',
    'Ч': 'Ch', 'ч': 'ch',
    'Ш': 'Sh', 'ш': 'sh',
    'Щ': 'Sh', 'щ': 'sh',
    'Ъ': "'", 'ъ': "'",
    'Ь': "", 'ь': "",
    'Э': 'E', 'э': 'e',
    'Ю': 'Yu', 'ю': 'yu',
    'Я': 'Ya', 'я': 'ya',
    'Ў': "O'", 'ў': "o'",
    'Қ': 'Q', 'қ': 'q',
    'Ғ': "G'", 'ғ': "g'",
    'Ҳ': 'H', 'ҳ': 'h'
}


def cyrillic_to_uzbek_latin(text: str) -> str:
    """Krill yozuvidagi matnni sof o'zbek lotin alifbosiga to'liq o'girish"""
    if not text:
        return ""
    # So'z boshida yoki unlilardan keyin kelgan 'Е/е' ni 'Ye/ye' qilish
    vowels = 'АЕЁИОУЭЮЯЎаеёиоуэюяў'
    text = re.sub(r'(^|[\s\(\[\"«—–-])Е', r'\1Ye', text)
    text = re.sub(r'(^|[\s\(\[\"«—–-])е', r'\1ye', text)
    text = re.sub(r'([' + vowels + r'])Е', r'\1Ye', text)
    text = re.sub(r'([' + vowels + r'])е', r'\1ye', text)

    result = []
    for ch in text:
        result.append(CYRILLIC_TO_LATIN.get(ch, ch))
    return ''.join(result)


# Rim raqamlarini o'zbekcha so'zga aylantirish (inglizcha harf bo'lib o'qilmasligi uchun)
ROMAN_NUMERALS_MAP = [
    (r"\bXXI\b\s*[-–—.]?\s*", "Yigirma birinchi "),
    (r"\bXX\b\s*[-–—.]?\s*", "Yigirmanchi "),
    (r"\bXIX\b\s*[-–—.]?\s*", "O'n to'qqizinchi "),
    (r"\bXVIII\b\s*[-–—.]?\s*", "O'n sakkizinchi "),
    (r"\bXVII\b\s*[-–—.]?\s*", "O'n yettinchi "),
    (r"\bXVI\b\s*[-–—.]?\s*", "O'n oltinchi "),
    (r"\bXV\b\s*[-–—.]?\s*", "O'n beshinchi "),
    (r"\bXIV\b\s*[-–—.]?\s*", "O'n to'rtinchi "),
    (r"\bXIII\b\s*[-–—.]?\s*", "O'n uchinchi "),
    (r"\bXII\b\s*[-–—.]?\s*", "O'n ikkinchi "),
    (r"\bXI\b\s*[-–—.]?\s*", "O'n birinchi "),
    (r"\bX\b\s*[-–—.]?\s*", "O'ninchi "),
    (r"\bIX\b\s*[-–—.]?\s*", "To'qqizinchi "),
    (r"\bVIII\b\s*[-–—.]?\s*", "Sakkizinchi "),
    (r"\bVII\b\s*[-–—.]?\s*", "Yettinchi "),
    (r"\bVI\b\s*[-–—.]?\s*", "Oltinchi "),
    (r"\bIV\b\s*[-–—.]?\s*", "To'rtinchi "),
    (r"\bV\b\s*[-–—.]?\s*", "Beshinchi "),
    (r"\bIII\b\s*[-–—.]?\s*", "Uchinchi "),
    (r"\bII\b\s*[-–—.]?\s*", "Ikkinchi "),
    (r"\bI\b\s*[-–—.]?\s*", "Birinchi "),
]

# Inglizcha atamalar va AI so'zlarini o'zbekcha muqobiliga aylantirish
ENGLISH_TERMS_MAP = [
    (r"\bDeep\s+Research\b", "Chuqur izlanish"),
    (r"\bChief\s+Research\s+Analyst\b", "Bosh tahlilchi"),
    (r"\bChecklist\b", "Tekshiruv ro'yxati"),
    (r"\bCheck-?in\b", "Tekshiruv"),
    (r"\bCheck-?up\b", "To'liq tekshiruv"),
    (r"\bResearch\b", "Tadqiqot"),
    (r"\bAnalyst\b", "Tahlilchi"),
    (r"\bAnalysis\b", "Tahlil"),
    (r"\bSummary\b", "Qisqacha mazmun"),
    (r"\bConclusion\b", "Yakuniy xulosa"),
    (r"\bIntroduction\b", "Kirish"),
    (r"\bOverview\b", "Umumiy sharh"),
    (r"\bHighlights\b", "Asosiy jihatlar"),
    (r"\bKey\s+Points\b", "Muhim fikrlar"),
    (r"\bAction\s+Plan\b", "Harakatlar rejasi"),
    (r"\bRoadmap\b", "Bosqichma-bosqich reja"),
    (r"\bSlides?\b", "Slaydlar"),
    (r"\bPresentation\b", "Prezentatsiya"),
    (r"\bPowerPoint\b", "Taqdimot"),
    (r"\bExcel\b", "Eksel jadvali"),
    (r"\bWord\b", "Vord hujjati"),
    (r"\bPDF\b", "P D F hujjati"),
    (r"\bAI\b", "Sun'iy intellekt"),
    (r"\bOnline\b", "Onlayn"),
    (r"\bOffline\b", "Oflayn"),
    (r"\bReels?\b", "Rils"),
    (r"\bVideo\s+Note\b", "Dumaloq video"),
    (r"\bVideo\b", "Video"),
    (r"\bAudio\b", "Audio"),
    (r"\bVoice\b", "Ovoz"),
    (r"\bChat\b", "Chat"),
    (r"\bBot\b", "Bot"),
    (r"\bStatus\b", "Holat"),
    (r"\bOption\b", "Variant"),
    (r"\bStep\b", "Qadam"),
    (r"\bMenu\b", "Menyu"),
    (r"\bFeedback\b", "Fikr-mulohaza"),
    (r"\bInsight\b", "Tavsiya"),
    (r"\bUpdate\b", "Yangilanish"),
    (r"\bDownload\b", "Yuklab olish"),
    (r"\bUpload\b", "Yuklash"),
    (r"\bFile\b", "Fayl"),
    (r"\bNote\b", "Eslatma"),
    (r"\bImportant\b", "Muhim"),
    (r"\bWarning\b", "Ogohlantirish"),
    (r"\bError\b", "Xatolik"),
    (r"\bLink\b", "Havola"),
    (r"\bID\b", "Raqam"),
    (r"\bOK\b", "Tushunarli"),
]


def clean_text_for_speech(text: str) -> str:
    """
    Matnni ovozli xabar uchun maxsus tozalash va fonetik jihatdan to'liq moslashtirish:
    - Krill yozuvidagi so'zlarni avtomatik o'zbek lotiniga o'giradi (Edge-TTS xatolik berishi va harflarni tushirib qoldirishining oldini oladi);
    - Barcha emojilarni yo'qotadi;
    - Markdown va qo'shtirnoq belgilarini so'zga bo'shliq qo'shmasdan tozalaydi, shunda qo'shimchalar (-gi, -da, -ning) uzilib ketmaydi;
    - Rim raqamlari va sonlar oralig'ini ravon so'zlarga aylantiradi;
    - Qisqartmalarni (MIB, BHM, O'zR) to'g'ri o'zbekcha talaffuzga keltiradi;
    - Hech qanday ma'lumot oxiri qirqilmasdan to'liq ovozlashtiriladi.
    """
    if not text:
        return ""

    # 1. Krill harflari bo'lsa, to'liq o'zbek lotiniga aylantirish
    text = cyrillic_to_uzbek_latin(text)

    # 2. Tizim va Telegram sarlavhalari / metama'lumotlarini tozalash
    text = re.sub(r"The current local time is:.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Active Document:.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Cursor is on line:.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\d{2}\.\d{2}\.\d{4}[^\]]*\]\s*[^:\n]*:", "", text)

    # 3. Bot manzillari va havolalari
    text = re.sub(r"@Bshaxsiy_bot", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Telegram\s*@\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?:\/\/\S+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # 4. Kod bloklari va HTML teglari
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"<[^>]*>", "", text)

    # 5. Markdown gorizontal chiziqlari (---, ===, ___) ni butunlay yo'qotish
    text = re.sub(r"^\s*[-=_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # 6. Markdown sarlavha belgilari (###, ##, #) ni yo'qotish
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)

    # 7. Ro'yxat punktlari (*, -, •) ni qator boshidan olib tashlash
    text = re.sub(r"^\s*[\*\-•]\s+", "", text, flags=re.MULTILINE)

    # 8. Barcha emojilarni butunlay olib tashlash
    text = EMOJI_PATTERN.sub(" ", text)

    # 9. Formatlash teglari (*, _, ~, `) ni BO'SHLIQSIZ tozalash (qo'shimchalar uzilib ketmasligi uchun!)
    # Masalan: **«Sud to'g'risida»**gi -> to'g'risidagi
    text = re.sub(r"[*_~`]", "", text)

    # 10. Qo'shtirnoqlarni BO'SHLIQSIZ tozalash (shunda »gi, »da, »ning uzilib inglizcha talaffuz qilinmaydi)
    text = re.sub(r'[«»“”"„‟]', '', text)

    # 11. Qavslarni nutqda tabiiy pauzaga (vergul) aylantirish
    text = text.replace("(", ", ").replace(")", ", ")

    # 12. Rim raqamlarini o'zbekcha so'zlarga aylantirish
    for pattern, repl in ROMAN_NUMERALS_MAP:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # 13. Inglizcha atamalarni o'zbekchaga o'girish
    for pattern, repl in ENGLISH_TERMS_MAP:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # 14. Indekslar va daraja belgilari (masalan, 8²-modda -> 8 prim 2-modda)
    text = text.replace("¹", " prim bir ").replace("²", " prim ikki ").replace("³", " prim uch ")
    text = text.replace("⁴", " prim to'rt ").replace("⁵", " prim besh ")

    # 15. Sonlar oralig'i (1–21-moddalarini -> 1 dan 21 gacha bo'lgan moddalarini)
    text = re.sub(r"(\d+)\s*[–—-]\s*(\d+)\s*-\s*([a-zA-Z'ʼʻ]+)", r"\1 dan \2 gacha bo'lgan \3", text)
    text = re.sub(r"(\d+)\s*[–—-]\s*(\d+)", r"\1 dan \2 gacha bo'lgan", text)

    # 16. Katta sonlar bo'shlig'i (50 000 -> 50000, 100 000 -> 100000)
    text = re.sub(r"(\d+)\s+(\d{3})\b", r"\1\2", text)

    # 17. Maxsus belgilar
    text = text.replace("%", " foiz ")
    text = text.replace("№", " raqamli ")
    text = text.replace("&", " va ")
    text = text.replace("$", " dollar ")
    text = text.replace("€", " yevro ")
    text = text.replace("|", " ")

    # 18. Qisqartmalarni to'liq va fonetik to'g'ri shaklga keltirish
    # MIB -> Mib yoki Majburiy ijro byurosi (bunda hech qanday 'Em-Eye-Bee' inglizcha tovush chiqmaydi)
    text = re.sub(r"\bMIBning\b", "Majburiy ijro byurosining", text)
    text = re.sub(r"\bMIBga\b", "Majburiy ijro byurosiga", text)
    text = re.sub(r"\bMIBdan\b", "Majburiy ijro byurosidan", text)
    text = re.sub(r"\bMIBda\b", "Majburiy ijro byurosida", text)
    text = re.sub(r"\bMIB\b", "Mib", text)

    # BHM -> bazaviy hisoblash miqdori (hech qanday 'Bee-Aitch-Em' inglizcha harflarsiz)
    text = re.sub(r"\bBHMning\b", "bazaviy hisoblash miqdorining", text)
    text = re.sub(r"\bBHMga\b", "bazaviy hisoblash miqdoriga", text)
    text = re.sub(r"\bBHMdan\b", "bazaviy hisoblash miqdoridan", text)
    text = re.sub(r"\bBHMda\b", "bazaviy hisoblash miqdorida", text)
    text = re.sub(r"\bBHM\b", "bazaviy hisoblash miqdori", text)

    text = re.sub(r"\bO[ʻ‘ʼ']zRning\b", "O'zbekiston Respublikasining", text)
    text = re.sub(r"\bO[ʻ‘ʼ']zRga\b", "O'zbekiston Respublikasiga", text)
    text = re.sub(r"\bO[ʻ‘ʼ']zR\b", "O'zbekiston Respublikasi", text)
    text = re.sub(r"\bO[ʻ‘ʼ']RQ\b", "O'zbekiston Respublikasi Qonuni", text)
    text = re.sub(r"\bJShShIR\b", "jismoniy shaxsning shaxsiy identifikatsiya raqami", text)
    text = re.sub(r"\bSTIR\b", "soliq to'lovchining identifikatsiya raqami", text)
    text = re.sub(r"\bBMT\b", "Birlashgan Millatlar Tashkiloti", text)
    text = re.sub(r"\bMDH\b", "Mustaqil Davlatlar Hamdo'stligi", text)
    text = re.sub(r"\bIIB\b", "Ichki ishlar bo'limi", text)
    text = re.sub(r"\bIIV\b", "Ichki ishlar vazirligi", text)

    # 19. O'zbekcha apostroflarni (oʻ, gʻ, ma'lumot) standart bitta apostrofga keltirish
    text = re.sub(r"[‘’ʻʼ`]", "'", text)

    # 20. Bo'shliqlar va tinish belgilari takrorlanishini tozalash
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r",\s*\.", ".", text)

    # 21. Katta hajmli matnlarni faqat o'ta zarur bo'lgandagina (12 000 belgidan oshsa) to'liq gap oxiridan tartiblash
    if len(text) > 12000:
        cut_idx = -1
        for punct in (". ", "? ", "! "):
            idx = text.rfind(punct, 9000, 11800)
            if idx > cut_idx:
                cut_idx = idx
        if cut_idx != -1:
            text = text[:cut_idx + 1]

    # 22. Matn oxiri to'liq va aniq talaffuz qilinishi uchun nuqta bilan tugashini ta'minlash
    if text and not text.endswith((".", "!", "?")):
        text += "."

    return text


async def convert_text_to_voice_and_audio(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "-6%"
) -> Tuple[str, str]:
    """
    Matnni tiniq va aniq talaffuzli audio xabarga aylantirish.
    - rate='-6%' tezligi nutqni shoshilmasdan, dona-dona va har bir harfni aniq eshitilishini ta'minlaydi;
    - apad=pad_dur=1.8 filtri ham OGG Opus ham MP3 oxiriga 1.8 soniya jimlik qo'shadi,
      bu Telegram pleeri yoki telefon apparati audio oxirgi harflar va so'zlarni yutib yuborishining oldini oladi.
    """
    cleaned_text = clean_text_for_speech(text)
    if not cleaned_text:
        cleaned_text = "Javob matni mavjud emas."

    unique_id = uuid.uuid4().hex[:8]
    raw_mp3_path = DOWNLOADS_DIR / f"raw_tts_{unique_id}.mp3"
    final_mp3_path = DOWNLOADS_DIR / f"tts_{unique_id}.mp3"
    ogg_path = DOWNLOADS_DIR / f"tts_{unique_id}.ogg"

    try:
        # 1. Edge TTS orqali raw MP3 yaratish
        communicate = edge_tts.Communicate(cleaned_text, voice, rate=rate)
        await communicate.save(str(raw_mp3_path))

        # 2. FFmpeg orqali Telegram Voice formati (OGG Opus) ga 1.8 soniya jimlik bilan aylantirish
        cmd_ogg = [
            "ffmpeg",
            "-y",
            "-i", str(raw_mp3_path),
            "-af", "apad=pad_dur=1.8",
            "-c:a", "libopus",
            "-b:a", "48k",
            str(ogg_path)
        ]
        proc_ogg = await asyncio.create_subprocess_exec(
            *cmd_ogg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc_ogg.communicate()

        # 3. Shuningdek MP3 formatiga ham 1.8 soniya jimlik qo'shish (agar audio formatda jo'natilsa)
        cmd_mp3 = [
            "ffmpeg",
            "-y",
            "-i", str(raw_mp3_path),
            "-af", "apad=pad_dur=1.8",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            str(final_mp3_path)
        ]
        proc_mp3 = await asyncio.create_subprocess_exec(
            *cmd_mp3,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc_mp3.communicate()

        actual_ogg = str(ogg_path) if ogg_path.exists() else str(raw_mp3_path)
        actual_mp3 = str(final_mp3_path) if final_mp3_path.exists() else (str(raw_mp3_path) if raw_mp3_path.exists() else actual_ogg)

        return actual_ogg, actual_mp3

    except Exception as e:
        logger.exception(f"TTS audio yaratishda xatolik: {e}")
        raise
    finally:
        # Xom MP3 faylini tozalash
        if raw_mp3_path.exists():
            try:
                raw_mp3_path.unlink()
            except Exception:
                pass
