import re
from typing import Optional, List, Dict, Tuple


# O'zbekiston Respublikasining asosiy kodekslari va qonunlari (havolalarsiz, faqat rasmiy nomlari)
KNOWN_LEGAL_ACTS = [
    {
        "patterns": [r"\bmehnat\s+kodeks[a-z]*\b", r"\bтрудов(ой|ого|ому|ом)\s+кодекс[а-я]*\b", r"\blabor\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Mehnat kodeksi",
        "title_ru": "Трудовой кодекс Республики Узбекистан",
        "title_en": "Labor Code of the Republic of Uzbekistan",
        "search_term": "Mehnat kodeksi"
    },
    {
        "patterns": [r"\bfuqarolik\s+kodeks[a-z]*\b", r"\bгражданск(ий|ого|ому|ом)\s+кодекс[а-я]*\b", r"\bcivil\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Fuqarolik kodeksi",
        "title_ru": "Гражданский кодекс Республики Узбекистан",
        "title_en": "Civil Code of the Republic of Uzbekistan",
        "search_term": "Fuqarolik kodeksi"
    },
    {
        "patterns": [
            r"\bma'muriy\s+javobgarlik[a-z\s]*kodeks[a-z]*\b",
            r"\bmjtk\b",
            r"\bкодекс[а-я\s]*об\s+административн(ой|ых)\s+ответственност[а-я]*\b",
            r"\bкоао\b",
            r"\badministrative\s+liability\s+code\b"
        ],
        "title_uz": "O'zbekiston Respublikasining Ma'muriy javobgarlik to'g'risidagi kodeksi",
        "title_ru": "Кодекс Республики Узбекистан об административной ответственности",
        "title_en": "Code of Administrative Responsibility of Uzbekistan",
        "search_term": "Ma'muriy javobgarlik to'g'risidagi kodeks"
    },
    {
        "patterns": [r"\bjinoyat\s+kodeks[a-z]*\b", r"\bуголовн(ый|ого|ому|ом)\s+кодекс[а-я]*\b", r"\bcriminal\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Jinoyat kodeksi",
        "title_ru": "Уголовный кодекс Республики Узбекистан",
        "title_en": "Criminal Code of the Republic of Uzbekistan",
        "search_term": "Jinoyat kodeksi"
    },
    {
        "patterns": [r"\bsoliq\s+kodeks[a-z]*\b", r"\bналогов(ый|ого|ому|ом)\s+кодекс[а-я]*\b", r"\btax\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Soliq kodeksi",
        "title_ru": "Налоговый кодекс Республики Узбекистан",
        "title_en": "Tax Code of the Republic of Uzbekistan",
        "search_term": "Soliq kodeksi"
    },
    {
        "patterns": [r"\boila\s+kodeks[a-z]*\b", r"\bсемейн(ый|ого|ому|ом)\s+кодекс[а-я]*\b", r"\bfamily\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Oila kodeksi",
        "title_ru": "Семейный кодекс Республики Узбекистан",
        "title_en": "Family Code of the Republic of Uzbekistan",
        "search_term": "Oila kodeksi"
    },
    {
        "patterns": [r"\buy[- ]?joy\s+kodeks[a-z]*\b", r"\bжилищн(ый|ого|ому|ом)\s+кодекс[а-я]*\b", r"\bhousing\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Uy-joy kodeksi",
        "title_ru": "Жилищный кодекс Республики Узбекистан",
        "title_en": "Housing Code of the Republic of Uzbekistan",
        "search_term": "Uy-joy kodeksi"
    },
    {
        "patterns": [r"\byer\s+kodeks[a-z]*\b", r"\bземельн(ый|ого|ому|ом)\s+кодекс[а-я]*\b", r"\bland\s+code\b"],
        "title_uz": "O'zbekiston Respublikasi Yer kodeksi",
        "title_ru": "Земельный кодекс Республики Узбекистан",
        "title_en": "Land Code of the Republic of Uzbekistan",
        "search_term": "Yer kodeksi"
    },
    {
        "patterns": [r"\bkonstitutsiy[a-z]*\b", r"\bконституци[а-я]*\b", r"\bconstitution\b"],
        "title_uz": "O'zbekiston Respublikasi Konstitutsiyasi",
        "title_ru": "Конституция Республики Узбекистан",
        "title_en": "Constitution of the Republic of Uzbekistan",
        "search_term": "O'zbekiston Respublikasi Konstitutsiyasi"
    },
    {
        "patterns": [r"\b3757[- ]?(son|qaror|buyruq)?[a-z]*\b", r"\bijro\s+xati\s+yo'riqnomasi\b"],
        "title_uz": "Notariuslar tomonidan ijro xatlarini yozish tartibi to'g'risidagi yo'riqnoma (Adliya vazirligi ro'yxat raqami: 3757)",
        "title_ru": "Инструкция о порядке совершения исполнительных надписей нотариусами (№ 3757)",
        "title_en": "Instruction on Notarial Execution Inscriptions (No. 3757)",
        "search_term": "3757-son ijro xati"
    },
]

# Qonunchilik va huquqiy mavzudagi so'rovlarni aniqlovchi kalit so'zlar
LEGAL_KEYWORDS = [
    # O'zbekcha
    r"\bqonun[a-z]*\b",
    r"\bkodeks[a-z]*\b",
    r"\bqaror[a-z]*\b",
    r"\bfarmon[a-z]*\b",
    r"\bnizom[a-z]*\b",
    r"\bmodda[a-z]*\b",
    r"\bband[a-z]*\b",
    r"\bhuquq[a-z]*\b",
    r"\bmajburiyat[a-z]*\b",
    r"\bjavobgarlik[a-z]*\b",
    r"\bjarima[a-z]*\b",
    r"\bsud[a-z]*\b",
    r"\bda'vo[a-z]*\b",
    r"\bariza[a-z]*\b",
    r"\bijro[a-z]*\b",
    r"\bnotarius[a-z]*\b",
    r"\badvokat[a-z]*\b",
    r"\bprokuror[a-z]*\b",
    r"\bprokuratura[a-z]*\b",
    r"\bmehnat\s+daftarchasi\b",
    r"\bishdan\s+bo'shatish\b",
    r"\bmehnat\s+ta'tili\b",
    r"\bish\s+haqi\b",
    r"\bkompensatsiya\b",
    r"\baliment[a-z]*\b",
    r"\bmeros[a-z]*\b",
    r"\bmulk\s+huquqi\b",
    r"\bijara\s+shartnomasi\b",
    r"\boldi[- ]sotdi\b",
    r"\bkafillik\b",
    r"\bshartnoma[a-z]*\b",
    r"\bqonunchilik[a-z]*\b",
    r"\bhuquqiy\b",
    r"\bqonuniy\b",
    r"\bqonunbuzarlik\b",
    r"\biste'molchi[a-z]*\s+huquq[a-z]*\b",
    r"\bdavlat\s+boji\b",
    r"\byo'l\s+harakati\s+qoidalari\b",
    r"\byhq\b",
    r"\bma'muriy\s+bayonnoma\b",
    r"\bshikoyat\s+qilish\b",

    # Ruscha
    r"\bзакон[а-я]*\b",
    r"\bкодекс[а-я]*\b",
    r"\bпостановлени[а-я]*\b",
    r"\bуказ[а-я]*\b",
    r"\bстать[а-я]*\b",
    r"\bпункт[а-я]*\b",
    r"\bправ[а-я]*\b",
    r"\bобязанност[а-я]*\b",
    r"\bответственност[а-я]*\b",
    r"\bштраф[а-я]*\b",
    r"\bсуд[а-я]*\b",
    r"\биск[а-я]*\b",
    r"\бисков[а-я]*\b",
    r"\bзаявлени[а-я]*\b",
    r"\bнотариус[а-я]*\b",
    r"\bадвокат[а-я]*\b",
    r"\bпрокуратур[а-я]*\b",
    r"\bтрудов(ой|ое|ые|ая)\b",
    r"\bувольнен[а-я]*\b",
    r"\bотпуск[а-я]*\b",
    r"\bзарплат[а-я]*\b",
    r"\bалимент[а-я]*\b",
    r"\bнаследств[а-я]*\b",
    r"\bаренд[а-я]*\b",
    r"\bдоговор[а-я]*\b",
    r"\bзаконодательств[а-я]*\b",
    r"\bправовы[а-я]*\b",
    r"\bзаконн[а-я]*\b",
    r"\bгоспошлин[а-я]*\b",
    r"\bпдд\b",

    # Inglizcha
    r"\blaw[s]?\b",
    r"\blegal\b",
    r"\bcode[s]?\b",
    r"\bdecree[s]?\b",
    r"\bresolution[s]?\b",
    r"\barticle[s]?\b",
    r"\bcontract[s]?\b",
    r"\bright[s]?\b",
    r"\bobligat[a-z]*\b",
    r"\bliabilit[a-z]*\b",
    r"\bpenalt[a-z]*\b",
    r"\bcourt[s]?\b",
    r"\blawsuit[s]?\b",
    r"\bnotar[a-z]*\b",
    r"\blawyer[s]?\b",
    r"\blabor\s+law\b",
    r"\balimon[a-z]*\b",
    r"\binheritan[a-z]*\b",
    r"\blegislation\b",
    r"\bconstitution\b",
]


def is_legal_query(text: str) -> bool:
    """
    Foydalanuvchi so'rovi yoki berilgan matn qonunchilik/huquqiy sohaga tegishli ekanligini aniqlash.
    """
    if not text or not text.strip():
        return False
    
    clean_text = text.lower()
    for pattern in LEGAL_KEYWORDS:
        if re.search(pattern, clean_text, re.IGNORECASE):
            return True
            
    return False


def detect_referenced_laws(text: str) -> List[Dict[str, str]]:
    """
    Matn ichida tilga olingan O'zbekiston Respublikasi kodekslari va qonunlarini aniqlash.
    Faqat rasmiy nomlarini qaytaradi.
    """
    if not text:
        return []
    
    clean_text = text.lower()
    found_acts = []
    seen_titles = set()

    for act in KNOWN_LEGAL_ACTS:
        for p in act["patterns"]:
            if re.search(p, clean_text, re.IGNORECASE):
                if act["title_uz"] not in seen_titles:
                    seen_titles.add(act["title_uz"])
                    found_acts.append(act)
                break

    return found_acts


def strip_legal_urls(text: str) -> str:
    """
    Matndan lex.uz va norma.uz havolalarini (URL yoki a teglari) tozalash.
    Faqat manba nomi va matnini qoldiradi.
    """
    if not text:
        return text

    # HTML <a> teglari ichidagi lex.uz va norma.uz havolalarini olib, faqat matnini qoldirish
    text = re.sub(
        r'<a\s+[^>]*href=["\'][^"\']*(?:lex\.uz|norma\.uz)[^"\']*["\'][^>]*>(.*?)</a>',
        r'\1',
        text,
        flags=re.IGNORECASE
    )

    # Markdown [Matn](https://lex.uz/...) havolalaridan linkni olib tashlash
    text = re.sub(
        r'\[([^\]]+)\]\((?:https?:\/\/)?(?:www\.)?(?:lex\.uz|norma\.uz)[^\)]*\)',
        r'\1',
        text,
        flags=re.IGNORECASE
    )

    # To'g'ridan-to'g'ri yozilgan URL havolalarni (https://lex.uz/... yoki https://www.norma.uz/...) tozalash
    text = re.sub(
        r'https?:\/\/(?:www\.)?(?:lex\.uz|norma\.uz)[^\s\)\>\]\,\"]*',
        '',
        text,
        flags=re.IGNORECASE
    )

    # "Lex.uz (Qonunchilik bazasi): [Hujjat]" yoki "Rasmiy portallar: lex.uz | norma.uz" kabi havolasimon qatorlarni tozalash
    text = re.sub(r'•\s*(?:🏛|📚|🔗)?\s*(?:Lex\.uz|Norma\.uz)[^\n]*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'•?\s*<i>(?:Rasmiy qonunchilik|Национальная база|Official Portals)[^\n]*<\/i>\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'🌐\s*<b>(?:Rasmiy qonunchilik portallari|Официальные правовые базы|Official Legislation Portals)[^\n]*<\/b>\n?', '', text, flags=re.IGNORECASE)

    # Ortiqcha bo'sh qatorlarni qisqartirish
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def build_legal_factcheck_block(
    query: str,
    response_text: str,
    lang: str = "uz"
) -> str:
    """
    Qonunchilik bo'yicha Fact-Checking: HECH QANDAY havola (URL, link) bermasdan,
    faqat qonunchilik manbasini (Qonun/Kodeks nomi) rasmiy ko'rsatish.
    """
    combined_text = f"{query}\n{response_text}"
    found_acts = detect_referenced_laws(combined_text)

    if lang == "ru":
        lines = [
            "⚖️ <b>Правовая основа (Источник нормы):</b>"
        ]
        if found_acts:
            for act in found_acts[:3]:
                title = act.get("title_ru", act["title_uz"])
                lines.append(f"📌 <b>Источник:</b> {title}")
        else:
            lines.append("📌 <b>Источник:</b> Действующее законодательство Республики Узбекистан")

    elif lang == "en":
        lines = [
            "⚖️ <b>Legal Basis (Source):</b>"
        ]
        if found_acts:
            for act in found_acts[:3]:
                title = act.get("title_en", act["title_uz"])
                lines.append(f"📌 <b>Source:</b> {title}")
        else:
            lines.append("📌 <b>Source:</b> Current Legislation of the Republic of Uzbekistan")

    else:
        # Standart O'zbek tili
        lines = [
            "⚖️ <b>Qonunchilik asosi (Huquqiy manba):</b>"
        ]
        if found_acts:
            for act in found_acts[:3]:
                title = act.get("title_uz", act["title_uz"])
                lines.append(f"📌 <b>Manba:</b> {title}")
        else:
            lines.append("📌 <b>Manba:</b> O'zbekiston Respublikasining amaldagi qonunchiligi")

    return "\n".join(lines)


def enrich_legal_response(
    response_text: str,
    query: str = "",
    lang: str = "uz"
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Agar so'rov yoki javob qonunchilikka oid bo'lsa:
    - Javobdan har qanday lex.uz va norma.uz havolalarini olib tashlaydi.
    - Faqat qonunchilik manbasini (Manba bloki) ko'rsatadi.
    - Hech qanday sayt havolasi yoki URL bermaydi (None, None).
    """
    if not response_text:
        return response_text, None, None

    # Avval har qanday lex.uz / norma.uz havolalarini olib tashlaymiz
    cleaned_text = strip_legal_urls(response_text)

    combined_for_check = f"{query} {cleaned_text}"
    if not is_legal_query(combined_for_check):
        return cleaned_text, None, None

    # Modda raqami keltirilgan bo'lsa, tekshiruv eslatmasini qo'shamiz (raqamlar modeldan keladi va avtomatik tasdiqlanmaydi)
    if re.search(r"\d+\s*-\s*(?:modda|band|qism)|\b(?:ст\.|статья|статьи|article)\s*\d+", cleaned_text, re.IGNORECASE):
        notes = {
            "uz": "⚠️ <i>Eslatma: modda raqamlari va muddatlar sun'iy intellekt tomonidan keltirilgan va avtomatik tasdiqlanmagan. Ularni lex.uz dagi amaldagi tahrir bilan tekshirib oling.</i>",
            "ru": "⚠️ <i>Примечание: номера статей и сроки приведены ИИ и не подтверждены автоматически. Сверьте их с действующей редакцией на lex.uz.</i>",
            "en": "⚠️ <i>Note: article numbers and deadlines are AI-generated and not automatically verified. Check them against the current edition on lex.uz.</i>",
        }
        cleaned_text = cleaned_text.rstrip() + "\n\n" + notes.get(lang, notes["uz"])

    # Agar matnda allaqachon huquqiy asos yoki manba keltirilgan bo'lsa, qayta manba blokini qo'shmaymiz
    has_source_heading = any(
        kw in cleaned_text.lower()
        for kw in [
            "qonunchilik asosi",
            "huquqiy asos",
            "правовая основа",
            "legal basis",
            "qonunchilik manbasi",
            "источник нормы",
            "huquqiy manba"
        ]
    )
    if has_source_heading:
        return cleaned_text, None, None

    block = build_legal_factcheck_block(query, cleaned_text, lang=lang)
    separator = "\n\n────────────────────\n"
    enriched_text = f"{cleaned_text.strip()}{separator}{block}"
    return enriched_text, None, None
