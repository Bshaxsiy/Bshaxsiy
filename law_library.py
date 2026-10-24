"""
Qonunlar kutubxonasi: lex.uz dan olingan RASMIY matnlar (laws/ papkasi) asosida
modda bo'yicha qidiruv va javobni tekshirish.

Foydalanish:
  1. lex.uz dan hujjatni (Word/PDF/matn) yuklab, laws/ papkasiga qo'ying.
  2. laws/index.json da hujjat nomi, tahrir sanasi va manbasini yozing (laws/README.md ga qarang).
  3. Bot qayta ishga tushganda matnlar avtomatik yuklanadi.

Papka bo'sh bo'lsa, modul hech narsa o'zgartirmaydi (bot avvalgidek ishlaydi).
Xotirani tejash uchun indeks saqlanmaydi: qidiruv so'rov paytida matnlar ustida bajariladi.
"""
import json
import logging
import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

LAWS_DIR = os.getenv("LAWS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "laws")
SUPPORTED_EXT = (".txt", ".md", ".docx", ".pdf")
MAX_QUERY_STEMS = 14
# Qidiruvda ahamiyatsiz so'zlar (o'zak: 6 belgigacha, apostrofsiz)
_STOP = {
    "bilan", "necha", "mumkin", "qanday", "qancha", "uchun", "hamda", "boshqa", "kerak", "bolish", "bolad",
    "boladi", "qaysi", "qachon", "nima", "nimal", "tartib", "ularn", "ushbu", "yoki", "bolsa", "bolga",
    "eng", "kopi", "kerakm", "mumkinm", "bormi", "nimad", "qanaqa", "qayerd", "kimlar", "qayer", "qayerga", "qanchal", "qilis", "qilin", "etilg", "etilad", "borasi", "haqida", "togri", "boyich", "boyic",
}

TITLE_LEN_PENALTY = 0.04
TITLE_BOOST = 2.5       # so'z modda sarlavhasida bo'lsa qo'shimcha ball
SYN_WEIGHT = 1.0        # sinonim/atama kengaytmasining og'irligi
SHORT_QUERY = 300
MIN_COVERAGE = 0.5      # eng yaxshi moddada so'rov mazmunining kamida shu ulushi topilishi shart

# Hujjat nomida uchraydigan umumiy so'zlar (hujjat aytilganda mavzu so'zi hisoblanmaydi)
_ACT_WORDS = {"kodeks", "qonun", "ozbek", "respub", "tograd", "kodeks"}

# Qisqartmalar: hujjat nomida aytilsa ("MChJ to'g'risida"), mavzu so'zi hisoblanmaydi
_ACRONYMS = {"mchj", "iiv"}

# Og'zaki iboralar -> qonun matnidagi atamalar o'zaklari (o'zak: so'z qo'shimchasiz, 6 belgigacha)
_SYNONYMS = [
    (r"nikohn?i? bekor|ajrash|ajrim|ajralish|taloq", ["nikoh", "ajrali"]),
    (r"ishdan (bosha|haydal|haydas|hayda|chiqar|olin|ketish)|xodimni bosh|ishdan kets", ["bekor", "tashab"]),
    (r"ish haqi|oylik|maosh", ["mehnat", "haq"]),
    (r"sudga (berish|bermoq|murojaat)|da.vo qilish|da.vo bilan", ["ariza", "murojaat", "davo"]),
    (r"qarz(ni)? undir|qarzni qaytar", ["undiri", "majbur"]),
    (r"o.?g.?ri(lik|chilik)|talon|bosqinchi", ["talon", "ogrili"]),
    (r"ta.?til", ["tatil"]),
    (r"mulk huquq\w* .{0,25}(tugat|tugay|tugash|to.xta|yoqol)", ["bekor", "bolish"], ("tugat", "tugay", "tugas", "toxta", "yoqol")),
]

# "85-modda. Sarlavha" yoki "85-1-modda." (lotin va kirill)
ARTICLE_RE = re.compile(r"^\s*(\d{1,4}(?:-\d{1,2})?)-(?:modda|модда)\b[\.\s:]*(.*)$", re.IGNORECASE)
ANNEX_RE = re.compile(r"^\s*(?:\d+\s*-\s*)?(?:ilova|илова|приложение)\s*$", re.IGNORECASE)
ANNEX_CHUNK = 1500
CITATION_RE = re.compile(r"(\d{1,4}(?:-\d{1,2})?)\s*-\s*(?:modda|moddasi|moddaga|moddada|moddani|moddalar)", re.IGNORECASE)

_APOS = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'", "´": "'"})
_WORD_RE = re.compile(r"[a-zа-яё0-9']+", re.IGNORECASE)


def _norm(text: str) -> str:
    return text.translate(_APOS).lower()


def _flat(text: str) -> str:
    """Qidiruv uchun: kichik harf, apostroflarsiz."""
    return _norm(text).replace("'", "")


# So'z oxiridagi qo'shimchalar (uzunroqlari avval): so'rovdagi "xatiga" va matndagi "xati/xatini" mos kelishi uchun
_SUFFIXES = ("laridan", "larning", "larini", "lariga", "larida", "ining", "lardan", "larga", "larni", "ning",
             "ini", "iga", "ida", "idan", "lar", "ga", "da", "ni", "dan", "si", "ri", "i")


_POSS = ("sining", "sidan", "sini", "sida", "siga", "si")


def _strip_suffix(w: str) -> str:
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _stems(text: str) -> List[str]:
    seen, out = set(), []
    for w in _WORD_RE.findall(_flat(text)):
        if len(w) >= 4:
            s = _strip_suffix(w)[:6]
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _token_stems(flat_text: str) -> List[str]:
    """Matndagi so'zlarning o'zaklari (so'rovdagi o'zaklar bilan BIR XIL usulda): faqat so'z boshidan mos keladi."""
    out: List[str] = []
    for w in _WORD_RE.findall(flat_text):
        if len(w) < 4:
            continue
        out.append(_strip_suffix(w)[:6])
        # egalik qo'shimchali shakl ("arizasi", "arizasini"): "ariza" o'zagi ham indekslanadi, shunda so'rovdagi har ikki shakl mos keladi
        for suf in _POSS:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                out.append(w[: -len(suf)][:6])
                break
    return out


class Article:
    __slots__ = ("act_key", "num", "title", "text", "flat", "flat_title", "tf", "title_stems", "ntok")

    def __init__(self, act_key: str, num: Optional[str], title: str, text: str):
        self.act_key = act_key
        self.num = num
        self.title = title
        self.text = text
        self.flat = _flat(text)
        self.flat_title = _flat(title)
        toks = _token_stems(self.flat)
        self.tf = Counter(toks)
        self.title_stems = set(_token_stems(self.flat_title))
        self.ntok = max(len(toks), 1)


class Act:
    def __init__(self, key: str, title: str, aliases: List[str], edition: str, source: str):
        self.key = key
        self.title = title
        self.aliases = [_norm(a) for a in aliases if a]
        self.edition = edition
        self.source = source
        self.articles: List[Article] = []


def _read_docx(path: str) -> str:
    """Word hujjatni o'qiydi: paragraflar va jadvallar (davlat boji stavkalari kabi) tartib bilan."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(path)
    lines: List[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, d).text)
        elif tag == "tbl":
            for row in Table(child, d).rows:
                cells = []
                for c in row.cells:
                    t = c.text.strip().replace("\n", " ")
                    if t and (not cells or cells[-1] != t):
                        cells.append(t)
                if cells:
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _read_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in r.pages)
    return ""


def _split_articles(act: Act, text: str) -> None:
    current_num: Optional[str] = None
    current_title = ""
    buf: List[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if current_num is not None and (body or current_title):
            act.articles.append(Article(act.key, current_num, current_title.strip(), body))

    annex_n = 0
    in_annex = False
    annex_lines: List[str] = []

    def flush_annex():
        # Ilova (jadval, stavkalar) modda emas: raqamsiz bo'laklarga bo'lamiz
        nonlocal annex_n
        chunk, size = [], 0
        for para in [p.strip() for p in annex_lines if p.strip()]:
            chunk.append(para)
            size += len(para)
            if size >= ANNEX_CHUNK:
                annex_n += 1
                act.articles.append(Article(act.key, None, "Ilova — " + act.title + " (stavkalar, miqdorlar, jadval), " + str(annex_n) + "-bo'lak", "\n".join(chunk)))
                chunk, size = [], 0
        if chunk:
            annex_n += 1
            act.articles.append(Article(act.key, None, "Ilova — " + act.title + " (stavkalar, miqdorlar, jadval), " + str(annex_n) + "-bo'lak", "\n".join(chunk)))

    for line in text.splitlines():
        if ANNEX_RE.match(line):
            if in_annex:
                annex_lines.append(line)
                continue
            flush()
            current_num, current_title, buf = None, "", []
            in_annex = True
            annex_lines = []
            continue
        if in_annex:
            annex_lines.append(line)
            continue
        m = ARTICLE_RE.match(line)
        if m:
            flush()
            current_num = m.group(1)
            current_title = m.group(2).strip()
            buf = []
        else:
            buf.append(line)
    if in_annex:
        flush_annex()
    else:
        flush()

    if not act.articles:
        # Moddalarga bo'linmagan hujjat: bo'laklarga ajratamiz
        chunk, size = [], 0
        for para in [p.strip() for p in text.split("\n") if p.strip()]:
            chunk.append(para)
            size += len(para)
            if size >= 1200:
                act.articles.append(Article(act.key, None, "", "\n".join(chunk)))
                chunk, size = [], 0
        if chunk:
            act.articles.append(Article(act.key, None, "", "\n".join(chunk)))


class LawLibrary:
    def __init__(self, directory: str = LAWS_DIR):
        self.directory = directory
        self.acts: Dict[str, Act] = {}
        self._all: List[Article] = []
        self._avg_len = 1.0
        self.load()

    # ---- yuklash ----
    def load(self) -> None:
        self.acts.clear()
        self._all = []
        if not os.path.isdir(self.directory):
            return
        meta: Dict[str, dict] = {}
        idx = os.path.join(self.directory, "index.json")
        if os.path.isfile(idx):
            try:
                with open(idx, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                logger.warning(f"laws/index.json o'qilmadi: {e}")

        for name in sorted(os.listdir(self.directory)):
            if not name.lower().endswith(SUPPORTED_EXT) or name.lower() == "readme.md":
                continue
            path = os.path.join(self.directory, name)
            try:
                text = _read_file(path)
            except Exception as e:
                logger.warning(f"Qonun fayli o'qilmadi ({name}): {e}")
                continue
            if not text.strip():
                continue
            info = meta.get(name, {})
            stem = os.path.splitext(name)[0]
            title = info.get("title") or stem.replace("_", " ")
            act = Act(
                key=stem,
                title=title,
                aliases=info.get("aliases") or [title],
                edition=info.get("edition", "ko'rsatilmagan"),
                source=info.get("source", "lex.uz"),
            )
            _split_articles(act, text)
            if act.articles:
                self.acts[act.key] = act
                self._all.extend(act.articles)

        self._df = Counter()
        for a in self._all:
            self._df.update(set(a.tf) | a.title_stems)
        if self._all:
            self._avg_len = sum(a.ntok for a in self._all) / len(self._all)
        if self.acts:
            logger.info(f"Qonunlar kutubxonasi yuklandi: {len(self.acts)} hujjat, {len(self._all)} modda/bo'lak.")

    def is_loaded(self) -> bool:
        return bool(self.acts)

    # ---- qidiruv ----
    def search(self, query: str, k: int = 6, min_cov: Optional[float] = None) -> List[Article]:
        if not self._all or not query:
            return []
        qn = _norm(query)
        qflat = _flat(query)
        stems = [s for s in _stems(query) if s not in _STOP][:MAX_QUERY_STEMS]
        explicit = {m.group(1) for m in CITATION_RE.finditer(query)}
        mentioned = {a.key for a in self.acts.values() if any(al and al in qn for al in a.aliases)}

        n = len(self._all)
        avg_len = self._avg_len or 1.0

        def idf_of(s: str) -> float:
            d = self._df.get(s, 0)
            return math.log(1 + (n - d + 0.5) / (d + 0.5))

        if mentioned:
            stems = [s for s in stems if s not in _ACRONYMS]
        matched = [s for s in stems if self._df.get(s, 0) > 0]
        unmatched = {s for s in stems if self._df.get(s, 0) == 0}

        # Hujjat nomida aytilgan, lekin matnlarda uchramaydigan qisqartma ("MChJ") mavzu so'zi emas
        for a in self.acts.values():
            if a.key in mentioned:
                for al in a.aliases:
                    if al and al in qn:
                        unmatched -= set(_stems(al))

        # Og'zaki iboralar -> qonun atamalari. Matnda uchramaydigan so'rov so'zini shu atamalar "qoplaydi"
        rules = []   # (terms, info, floor_w)
        for entry in _SYNONYMS:
            pattern, terms = entry[0], entry[1]
            replace = entry[2] if len(entry) > 2 else ()
            m = re.search(pattern, qflat)
            if not m:
                continue
            mt = m.group(0)
            consumed = {s for s in stems
                        if (s[:4] in mt and self._df.get(s, 0) <= 2) or (replace and s.startswith(replace))}
            terms = [t for t in terms if self._df.get(t, 0) > 0]
            if not terms:
                continue
            info = max((idf_of(s) ** 2 for s in consumed), default=0.0)
            floor_w = 0.6 * max((idf_of(s) for s in consumed), default=0.0)
            unmatched -= consumed
            matched = [s for s in matched if s not in consumed]
            rules.append((terms, info, floor_w))

        if not matched and not rules and not explicit:
            return []

        base = {s: idf_of(s) for s in matched}
        info = {s: w * w for s, w in base.items()}
        total_info = (sum(info.values()) + sum(idf_of(s) ** 2 for s in unmatched)
                      + sum(r[1] for r in rules)) or 1.0
        extra: Dict[str, float] = {}
        for terms, _, floor_w in rules:
            for t in terms:
                if t not in base:
                    extra[t] = max(extra.get(t, 0.0), idf_of(t) * SYN_WEIGHT, floor_w)

        k1, b = 1.2, 0.75
        scored: List[Tuple[float, float, Article]] = []
        for a in self._all:
            sc = 0.0
            norm = 1 - b + b * (a.ntok / avg_len)
            hit_info = 0.0
            for s, w in base.items():
                tf = a.tf.get(s, 0)
                in_title = s in a.title_stems
                if tf or in_title:
                    hit_info += info[s]
                if tf:
                    sc += w * (tf * (k1 + 1)) / (tf + k1 * norm)
                if in_title:
                    sc += TITLE_BOOST * w
            for s, w in extra.items():
                tf = a.tf.get(s, 0)
                if tf:
                    sc += w * (tf * (k1 + 1)) / (tf + k1 * norm)
                if s in a.title_stems:
                    sc += TITLE_BOOST * w
            for terms, rinfo, _fw in rules:
                if rinfo and any((t in a.tf or t in a.title_stems) for t in terms):
                    hit_info += rinfo
            if sc <= 0 and not (a.num and a.num in explicit):
                continue
            cov = hit_info / total_info
            sc *= (0.3 + 0.7 * cov)          # so'rov so'zlarining ko'prog'ini qamrab olgan modda yuqoriga chiqadi
            # Maxsus toifalarga (o'rindosh, mavsumiy, uy ishchisi...) oid uzun sarlavhali moddalar umumiy qoidadan pastroq turadi
            extra_t = len(a.title_stems - set(base) - set(extra))
            sc /= 1 + TITLE_LEN_PENALTY * extra_t
            if a.act_key in mentioned:
                sc *= 1.5
            if a.num and a.num in explicit and (not mentioned or a.act_key in mentioned):
                sc += 100
                cov = 1.0
            scored.append((sc, cov, a))
        if not scored:
            return []
        scored.sort(key=lambda x: x[0], reverse=True)
        top_sc, top_cov, _ = scored[0]
        # Eng yaxshi moddada ham so'rovning mazmunli qismi topilmagan bo'lsa — to'qima javob bermaslik uchun bo'sh qaytaramiz
        # (uzun so'rovlarda — hujjat tayyorlash matnida — ism-familiya, manzil kabi so'zlar ko'p bo'lgani uchun bu qoida qo'llanmaydi)
        floor = MIN_COVERAGE if min_cov is None else min_cov
        if top_cov < floor and len(query) <= SHORT_QUERY:
            return []
        out: List[Article] = []
        annex_per_act: Dict[str, int] = {}
        for sc, cov, a in scored:
            if sc < top_sc * 0.3 or len(out) >= k:
                break
            if a.num is None and a.title.startswith("Ilova"):
                # ilova (jadval) bo'laklari natijani to'ldirib yubormasligi uchun: bir hujjatdan ko'pi bilan 2 ta
                if annex_per_act.get(a.act_key, 0) >= 2:
                    continue
                annex_per_act[a.act_key] = annex_per_act.get(a.act_key, 0) + 1
            out.append(a)
        return out

    # ---- prompt va tekshiruv ----
    def build_context(self, query: str, k: int = 8, max_chars: int = 1800, min_cov: Optional[float] = None) -> Tuple[str, Set[str]]:
        """(kontekst matni, topilgan modda raqamlari) qaytaradi. Topilmasa ('', set())."""
        arts = self.search(query, k=k, min_cov=min_cov)
        if not arts:
            return "", set()
        lines = [
            "=== RASMIY MATN PARCHALARI (O'zbekiston qonunchilik bazasi, lex.uz) ===",
            "Qoida: javobni birinchi navbatda shu parchalarga asoslang. Modda raqami, muddat va miqdorni FAQAT shu parchalardan keltiring.",
            "Parchalarda savolga javob bo'lmasa, buni ochiq ayting va taxmin qilmang. Hujjat nomini modda raqami bilan birga yozing.",
            "",
        ]
        nums: Set[str] = set()
        for art in arts:
            act = self.acts[art.act_key]
            head = f"[{act.title}"
            if art.num:
                head += f", {art.num}-modda"
                nums.add(art.num)
            if art.title:
                head += f". {art.title}"
            head += f" | tahrir: {act.edition}]"
            body = art.text.strip()
            if len(body) > max_chars:
                body = body[:max_chars].rstrip() + " ..."
            lines += [head, body, ""]
        lines.append("=== PARCHALAR OXIRI ===\n")
        return "\n".join(lines), nums

    def augment_prompt(self, query: str, prompt: str, note_if_empty: bool = True) -> Tuple[str, Set[str]]:
        # Huquqiy kalit so'zsiz (oddiy) so'rovlarda kontekst faqat so'rov to'liq qamralganda qo'shiladi
        ctx, nums = self.build_context(query, min_cov=None if note_if_empty else 0.95)
        if not ctx:
            if not note_if_empty or not self.acts or len(query) > SHORT_QUERY:
                return prompt, set()
            # Kutubxonada mos parcha topilmadi: model modda raqami, muddat va miqdorni xotiradan to'qimasligi uchun ogohlantiramiz
            note = (
                "=== DIQQAT: rasmiy matnlar kutubxonasidan bu savolga mos parcha TOPILMADI ===\n"
                "Qoida: modda raqami, band, muddat, jarima yoki boj miqdorini xotiradan keltirmang va taxmin qilmang. "
                "Faqat aniq bilgan umumiy huquqiy tamoyilni tushuntiring, aniq normani qaysi hujjatdan izlash kerakligini ayting "
                "va javob oxirida normani lex.uz da tekshirish zarurligini qayd eting.\n"
                "=== OGOHLANTIRISH OXIRI ===\n"
            )
            return f"{note}\nSAVOL / TOPSHIRIQ:\n{prompt}", set()
        return f"{ctx}\nSAVOL / TOPSHIRIQ:\n{prompt}", nums

    def annotate(self, response: str, retrieved_nums: Set[str], lang: str = "uz") -> str:
        """Javobdagi modda raqamlarini topilgan parchalar bilan solishtirib, izoh qo'shadi."""
        if not response or not self.acts:
            return response
        cited = {m.group(1) for m in CITATION_RE.finditer(response)}
        if not cited:
            return response
        unverified = sorted(c for c in cited if c not in retrieved_nums)
        if unverified:
            lst = ", ".join(f"{c}-modda" for c in unverified)
            notes = {
                "uz": f"⚠️ <i>Quyidagi modda raqamlari rasmiy matn kutubxonasidan olingan parchalar bilan tasdiqlanmadi: {lst}. lex.uz da tekshiring.</i>",
                "ru": f"⚠️ <i>Следующие номера статей не подтверждены фрагментами из библиотеки официальных текстов: {lst}. Проверьте на lex.uz.</i>",
                "en": f"⚠️ <i>These article numbers were not confirmed by excerpts from the official text library: {lst}. Check lex.uz.</i>",
            }
            return response.rstrip() + "\n\n" + notes.get(lang, notes["uz"])
        return response


library = LawLibrary()
