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
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

LAWS_DIR = os.getenv("LAWS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "laws")
SUPPORTED_EXT = (".txt", ".md", ".docx", ".pdf")
MAX_QUERY_STEMS = 14
# Qidiruvda ahamiyatsiz so'zlar (o'zak: 6 belgigacha, apostrofsiz)
_STOP = {
    "bilan", "necha", "mumkin", "qanday", "qancha", "uchun", "hamda", "boshqa", "kerak", "bolish", "bolad",
    "boladi", "qaysi", "qachon", "nima", "nimal", "tartib", "ularn", "ushbu", "yoki", "bolsa", "bolga",
    "eng", "kopi", "qilis", "qilin", "etilg", "etilad", "borasi", "haqida", "togri", "boyich", "boyic",
}

# "85-modda. Sarlavha" yoki "85-1-modda." (lotin va kirill)
ARTICLE_RE = re.compile(r"^\s*(\d{1,4}(?:-\d{1,2})?)-(?:modda|модда)\b[\.\s:]*(.*)$", re.IGNORECASE)
CITATION_RE = re.compile(r"(\d{1,4}(?:-\d{1,2})?)\s*-\s*(?:modda|moddasi|moddaga|moddada|moddani|moddalar)", re.IGNORECASE)

_APOS = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'", "´": "'"})
_WORD_RE = re.compile(r"[a-zа-яё0-9']+", re.IGNORECASE)


def _norm(text: str) -> str:
    return text.translate(_APOS).lower()


def _flat(text: str) -> str:
    """Qidiruv uchun: kichik harf, apostroflarsiz."""
    return _norm(text).replace("'", "")


def _stems(text: str) -> List[str]:
    seen, out = set(), []
    for w in _WORD_RE.findall(_flat(text)):
        if len(w) >= 4:
            s = w[:6]
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


class Article:
    __slots__ = ("act_key", "num", "title", "text", "flat", "flat_title")

    def __init__(self, act_key: str, num: Optional[str], title: str, text: str):
        self.act_key = act_key
        self.num = num
        self.title = title
        self.text = text
        self.flat = _flat(text)
        self.flat_title = _flat(title)


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

    for line in text.splitlines():
        m = ARTICLE_RE.match(line)
        if m:
            flush()
            current_num = m.group(1)
            current_title = m.group(2).strip()
            buf = []
        else:
            buf.append(line)
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

        if self._all:
            self._avg_len = sum(len(a.flat) for a in self._all) / len(self._all)
        if self.acts:
            logger.info(f"Qonunlar kutubxonasi yuklandi: {len(self.acts)} hujjat, {len(self._all)} modda/bo'lak.")

    def is_loaded(self) -> bool:
        return bool(self.acts)

    # ---- qidiruv ----
    def search(self, query: str, k: int = 6) -> List[Article]:
        if not self._all or not query:
            return []
        qn = _norm(query)
        stems = [s for s in _stems(query) if s not in _STOP][:MAX_QUERY_STEMS]
        explicit = {m.group(1) for m in CITATION_RE.finditer(query)}
        mentioned = {a.key for a in self.acts.values() if any(al and al in qn for al in a.aliases)}

        n = len(self._all)
        avg_len = self._avg_len or 1.0
        # Har bir o'zak qancha moddada uchrashi (idf uchun)
        df = {s: sum(1 for a in self._all if s in a.flat) for s in stems}
        idf = {s: math.log(1 + (n - d + 0.5) / (d + 0.5)) for s, d in df.items() if d > 0}
        if not idf and not explicit:
            return []

        k1, b = 1.2, 0.75
        scored: List[Tuple[float, Article]] = []
        for a in self._all:
            sc = 0.0
            norm = 1 - b + b * (len(a.flat) / avg_len)
            for s, w in idf.items():
                tf = a.flat.count(s)
                if tf:
                    sc += w * (tf * (k1 + 1)) / (tf + k1 * norm)
                if s in a.flat_title:
                    sc += 2.5 * w
            if sc and a.act_key in mentioned:
                sc *= 1.5
            if a.num and a.num in explicit and (not mentioned or a.act_key in mentioned):
                sc += 100
            if sc > 0:
                scored.append((sc, a))
        if not scored:
            return []
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0]
        return [a for sc, a in scored[:k] if sc >= top * 0.3]

    # ---- prompt va tekshiruv ----
    def build_context(self, query: str, k: int = 8, max_chars: int = 1800) -> Tuple[str, Set[str]]:
        """(kontekst matni, topilgan modda raqamlari) qaytaradi. Topilmasa ('', set())."""
        arts = self.search(query, k=k)
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

    def augment_prompt(self, query: str, prompt: str) -> Tuple[str, Set[str]]:
        ctx, nums = self.build_context(query)
        if not ctx:
            return prompt, set()
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
