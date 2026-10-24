import re
from enum import Enum
from typing import Optional, Tuple


class SafetyViolation(Enum):
    NONE = "none"
    RELIGIOUS = "religious"
    TERRORISM_EXTREMISM = "terrorism_extremism"


# Terrorizm, ekstremizm, zo'ravonlik va qurol/portlovchi moddalarga oid andozalar
TERRORISM_PATTERNS = [
    # Bomba va portlovchi moddalar (lotin va kirill)
    r"\b(bomb[a-z]*|бомб[а-я]*)\b",
    r"\b(explosiv[a-z]*|portlovch[a-z]*)\b",
    r"\bportlat[a-z]*\b",
    r"\b(взрывчатк[а-я]*|взрывн[а-я]*|взрыв[а-я]*|взорвать)\b",
    r"\b(снаряд[а-я]*|мина|мины|мину)\b",
    r"\b(molotov|молотов)[a-zа-я\s]*(kokteyl|коктейл)?\b",
    
    # Terrorizm va ekstremizm
    r"\b(terror[a-z]*|террор[а-я]*)\b",
    r"\b(ekstremiz[a-z]*|ekstremist[a-z]*)\b",
    r"\b(экстремиз[а-я]*|экстремист[а-я]*)\b",
    r"\b(radikaliz[a-z]*|радикализ[а-я]*)\b",
    r"\b(radikal\s+(guruh|oqim|harakat)|радикальн[а-я]*)\b",
    
    # Terroristik guruhlar va atamalar
    r"\b(ishid|isis|daesh|daish|игил)\b",
    r"\b(al[- ]?qoida|al[- ]?qaeda|аль[- ]?каид[а-я]*)\b",
    r"\b(hizb[- ]?ut[- ]?tahrir|hizbut[- ]?tahrir|хизб[- ]?ут[- ]?тахрир)\b",
    r"\b(shahid[a-zа-я]*|xudkush[a-z]*|смертник[а-я]*)\b",
    r"\b(suicide\s+bomb[a-z]*)\b",
    r"\b(jihodch[a-z]*|джихадист[а-я]*)\b",
    
    # Qurol, diversiya va zo'ravonlik
    r"\b(qurol|pistolet|avtomat)\s+(yasash|tayyorlash|qo'lda\s+yasash)\b",
    r"\b(сделать|изготовить)\s+(оружие|пистолет|автомат|взрывчатку|яд)\b",
    r"\b(how\s+to\s+make\s+(a\s+)?(bomb|weapon|explosive))\b",
    r"\b(zaharlash|odam\s+o'ldirish)\s+(usullari|rejasini|yo'li)\b",
    r"\b(диверси[а-я]*|qo'poruvchilik[a-z]*)\b",
]

# Diniy savollar, shariat ahkomlari, fatvolar, ibodatlar va e'tiqodiy mavzular
RELIGIOUS_PATTERNS = [
    # Shariat, fatvo, fiqh, aqida, mazhab
    r"\b(shariat[a-z]*|шариат[а-я]*|sharia|shariah)\b",
    r"\b(fatvo[a-z]*|фетв[а-я]*|fatwa[a-z]*)\b",
    r"\b(fiqh[a-z]*|фикх[а-я]*)\b",
    r"\b(aqid[a-z]*|акид[а-я]*)\b",
    r"\b(mazhab[a-z]*|мазхаб[а-я]*)\b",
    r"\b(hanafiy[a-z]*|shofeiy[a-z]*|molikiy[a-z]*|hanbaliy[a-z]*|ханафит[а-я]*|шафиит[а-я]*)\b",
    
    # Halol, harom, gunoh, savob
    r"\b(halol[a-z]*|harom[a-z]*|gunoh[a-z]*|savob[a-z]*|makruh[a-z]*|farz[a-z]*|vojib[a-z]*|sunnat[a-z]*)\b",
    r"\b(халяль|харам|грех[а-я]*|грешно[а-я]*|греховн[а-я]*)\b",
    r"\b(is\s+it\s+halal|is\s+it\s+haram|is\s+it\s+a\s+sin)\b",
    r"\b(murtad[a-z]*|kofir[a-z]*|dindan\s+chiqish|неверный|вероотступник)\b",
    
    # Namoz, tahorat, g'usl
    r"\b(namoz[a-z]*|намаз[а-я]*)\b",
    r"\b(bomdod|peshin|asr|shom|xufton|taroveh|tahajjud|janoza)\s+(namozi|vaqti|hukmi)?\b",
    r"\b(tahorat[a-z]*|g'usl[a-z]*|tayammum[a-z]*)\b",
    r"\b(омовение[а-я]*|тахарат[а-я]*|гусль[а-я]*)\b",
    r"\b(wudu|ghusl|ablution|salat|salah)\b",
    r"\b(azon\s+aytish|azon\s+duosi|sajda|ruku|rakat)\b",
    
    # Ro'za, zakot, haj
    r"\b(ro'za[a-z]*|roza[a-z]*)\b",
    r"\b(iftorlik[a-z]*|saharlik[a-z]*|iftor[a-z]*|sahur[a-z]*)\b",
    r"\b(ураза|ифтар|сухур|закят[а-я]*)\b",
    r"\b(fasting\s+in\s+ramadan|iftar|suhoor|zakat[a-z]*)\b",
    r"\b(zakot[a-z]*)\b",
    r"\b(haj\s+ziyorat|haj\s+amal|umra\s+amal|haji\s+qilish|паломничество\s+в\s+мекку|hajj|umrah)\b",
    r"\b(qurbonlik[a-z]*|qurbon\s+hayit)\b",
    
    # Qur'on, hadis, payg'ambarlar, diniy matnlar
    r"\b(qur'on[a-z]*|quron[a-z]*|коран[а-я]*|quran[a-z]*)\b",
    r"\b(hadis[a-z]*|хадис[а-я]*|hadith[a-z]*)\b",
    r"\b(payg'ambar[a-z]*|пророк[а-я]*)\b",
    r"\b(allohning\s+hukmi|tangrining\s+hukmi|xudo\s+bormi|yaratgan\s+egam|есть\s+ли\s+бог|does\s+god\s+exist)\b",
    r"\b(qiyomat[a-z]*|jannat\s+va\s+do'zax|do'zax\s+azobi|рай\s+и\s+ад)\b",
    r"\b(farishtalar|shayton\s+vasvasasi|jinlar|жинны?|шайтан)\b",
    
    # Dinlar, e'tiqodiy savollar
    r"\b(diniy[a-z]*|религиозн[а-я]*|religious)\b",
    r"\b(islom|islomiy|ислам[а-я]*|islam|islamic)\b",
    r"\b(musulmon[a-z]*|мусульман[а-я]*|muslim[a-z]*)\b",
    r"\b(xristian[a-z]*|nasroniy[a-z]*|христиан[а-я]*|christian[a-z]*)\b",
    r"\b(yahudiy[a-z]*|иудаизм[а-я]*|judaism)\b",
    r"\b(buddizm[a-z]*|buddiy[a-z]*|буддизм[а-я]*|buddhism)\b",
    r"\b(pravoslavi[a-zа-я]*|katolitsiz[a-zа-я]*)\b",
    r"\b(injil[a-z]*|tavrot[a-z]*|zabur[a-z]*|библия|библии|евангели[а-я]*|тора|torah|bible)\b",
]

# Yengil (kontekstga bog'liq) diniy so'zlar: «islomiy bank», «islomiy moliya standarti» kabi
# huquqiy/moliyaviy so'rovlarda bu so'zlar diniy savol hisoblanmaydi.
WEAK_RELIGIOUS_PATTERNS = {
    r"\b(islom|islomiy|ислам[а-я]*|islam|islamic)\b",
}

# Huquqiy-moliyaviy kontekst belgilari (faqat yengil diniy so'zlarni istisno qilish uchun)
LEGAL_FINANCE_CONTEXT = (
    r"\b(bank\w*|moliya\w*|moliyalashtir\w*|murobaha|markaziy\s+bank|standart\w*|shartnoma\w*|"
    r"kodeks\w*|qaror\w*|loyiha\w*|lizing\w*|kredit\w*|ustama\w*|nasiya\w*|komissiya\w*|"
    r"sud\w*|da'vo\w*|talabnoma\w*|банк\w*|финанс\w*|договор\w*|кредит\w*|"
    r"financ\w*|contract\w*|regulation\w*)\b"
)

# Istisnolar ro'yxati (False Positive larni oldini olish uchun)
WHITELIST_EXCEPTIONS = [
    r"\bislom\s+karimov\b",
    r"\bислам\s+каримов\b",
    r"\bislam\s+karimov\b",
    r"\bislomobod\b",
    r"\bисламабад\b",
    r"\bislamabad\b",
    r"\bdinamo\b",
    r"\bдинамо\b",
]


def _normalize_text(text: str) -> str:
    """Matnni apostroflar va bo'shliqlardan tozalash"""
    if not text:
        return ""
    # Har xil ko'rinishdagi tutuq belgilari va apostroflarni standart "'" ga o'girish
    t = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("ʻ", "'").replace("ʼ", "'")
    return t.lower().strip()


def check_content_safety(text: str) -> Tuple[SafetyViolation, Optional[str]]:
    """
    Foydalanuvchi matnini xavfsizlik va cheklovlar bo'yicha tekshirish.
    Diniy yoki terrorizm/ekstremizm mavzularini aniqlaydi.
    
    Qaytaradi:
        (SafetyViolation.NONE, None) -> Agar xavfsiz bo'lsa
        (SafetyViolation.TERRORISM_EXTREMISM, sabab) -> Agar terrorizm/ekstremizm bo'lsa
        (SafetyViolation.RELIGIOUS, sabab) -> Agar diniy savol bo'lsa
    """
    if not text or not text.strip():
        return SafetyViolation.NONE, None

    normalized_text = _normalize_text(text)

    # 1. Istisnolarni tekshirish (False positive larni oldini olish, masalan Islom Karimov)
    for exc in WHITELIST_EXCEPTIONS:
        if re.search(exc, normalized_text, re.IGNORECASE):
            # Agar istisno bo'lsa, va unda boshqa terrorizm/bomba so'zlari bo'lmasa, diniy cheklov qo'llanilmaydi
            has_terror = any(re.search(p, normalized_text, re.IGNORECASE) for p in TERRORISM_PATTERNS)
            if not has_terror:
                return SafetyViolation.NONE, None

    # 2. Terrorizm, ekstremizm va qurol/portlovchi moddalar tekshiruvi (eng yuqori xavfsizlik darajasi)
    for pattern in TERRORISM_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match:
            return SafetyViolation.TERRORISM_EXTREMISM, f"Taqiqlangan mavzu (terrorizm/ekstremizm/xavfsizlik): {match.group(0)}"

    # 3. Diniy mavzular, fatvolar, ibodatlar va e'tiqodiy savollar tekshiruvi
    for pattern in RELIGIOUS_PATTERNS:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match:
            if pattern in WEAK_RELIGIOUS_PATTERNS and re.search(LEGAL_FINANCE_CONTEXT, normalized_text, re.IGNORECASE):
                continue
            return SafetyViolation.RELIGIOUS, f"Diniy mavzudagi so'rov: {match.group(0)}"

    return SafetyViolation.NONE, None


def get_safety_refusal_message(violation: SafetyViolation, lang: str = "uz") -> str:
    """
    Taqiqlangan so'rovlar uchun foydalanuvchi tiliga mos rasmiy rad javobini shakllantirish.
    """
    if violation == SafetyViolation.TERRORISM_EXTREMISM:
        if lang == "ru":
            return (
                "🚫 <b>Отклонено: Запрос нарушает политику безопасности!</b>\n\n"
                "Данный бот категорически не отвечает на запросы, связанные с <b>терроризмом, экстремизмом, "
                "радикализмом, насилием, изготовлением оружия или взрывчатых веществ</b>, а также любыми "
                "другими противоправными действиями.\n\n"
                "⚠️ <i>Пожалуйста, соблюдайте нормы законодательства и правила безопасного использования бота.</i>"
            )
        elif lang == "en":
            return (
                "🚫 <b>Declined: Request violates safety policies!</b>\n\n"
                "This bot strictly does not answer queries related to <b>terrorism, extremism, "
                "violence, illegal armed groups, or the manufacture of weapons/explosives</b>.\n\n"
                "⚠️ <i>Please adhere to applicable laws and safe usage guidelines.</i>"
            )
        else:
            return (
                "🚫 <b>Rad etildi: Xavfsizlik talablariga zid so'rov!</b>\n\n"
                "Ushbu bot <b>terrorizm, ekstremizm, radikalizm, qurol yoki portlovchi moddalar tayyorlash, "
                "zo'ravonlik</b> hamda qonunga xilof boshqa harakatlarga oid so'rovlarga javob bermaydi va ularni qat'iyan qoralaydi.\n\n"
                "⚠️ <i>Iltimos, amaldagi qonunchilik talablariga va botdan foydalanish xavfsizlik qoidalariga rioya qiling.</i>"
            )

    elif violation == SafetyViolation.RELIGIOUS:
        if lang == "ru":
            return (
                "⚠️ <b>Ограничение: Вопросы религиозной тематики не рассматриваются!</b>\n\n"
                "Данный бот не уполномочен давать ответы, разъяснения или толкования по <b>религиозным вопросам, "
                "нормам шариата, фетвам, обрядам и вероубеждению</b>. Ответы на религиозные темы правилами бота запрещены.\n\n"
                "ℹ️ <i>По религиозным вопросам и фетвам рекомендуем обращаться к официальным духовным управлениям и профильным источникам:</i>\n"
                "• Официальный портал Управления мусульман Узбекистана: <a href=\"https://muslim.uz\">muslim.uz</a>\n"
                "• Центр фетв: <a href=\"https://fatvo.uz\">fatvo.uz</a>"
            )
        elif lang == "en":
            return (
                "⚠️ <b>Restriction: Religious inquiries are not supported!</b>\n\n"
                "This bot is not authorized to provide guidance, answers, fatwas, or interpretations on "
                "<b>religious matters, sharia rulings, theological questions, or faith practices</b>. "
                "Answering religious questions is strictly restricted.\n\n"
                "ℹ️ <i>For religious inquiries and certified fatwas, please consult recognized official religious authorities and scholars.</i>"
            )
        else:
            return (
                "⚠️ <b>Cheklov: Diniy mavzulardagi savollarga javob berilmaydi!</b>\n\n"
                "Ushbu bot <b>diniy masalalar, shariat ahkomlari, fatvolar, ibodat qoidalari (namoz, ro'za va h.k.), "
                "halol-harom masalalari hamda e'tiqodiy talqinlar</b> bo'yicha javob berish vakolatiga ega emas va bu kabi savollarga javob berish qat'iyan taqiqlangan.\n\n"
                "ℹ️ <i>Diniy savollar va rasmiy fatvolar bo'yicha vakolatli rasmiy manbalarga murojaat qilishingizni tavsiya etamiz:</i>\n"
                "• O'zbekiston musulmonlari idorasi portali: <a href=\"https://muslim.uz\">muslim.uz</a>\n"
                "• O'zbekiston musulmonlari idorasi Fatvo markazi: <a href=\"https://fatvo.uz\">fatvo.uz</a>"
            )

    return ""
