"""
Qonunlar kutubxonasi: lex.uz dan olingan RASMIY matnlar (laws/ papkasi) asosida
modda bo'yicha qidiruv va javobni tekshirish.

Foydalanish:
  1. lex.uz dan hujjatni (Word/PDF/matn) yuklab, laws/ papkasiga qo'ying.
  2. laws/index.json da hujjat nomi, tahrir sanasi va manbasini yozing (laws/README.md ga qarang).
  3. Bot qayta ishga tushganda matnlar avtomatik yuklanadi.

Papka bo'sh bo'lsa, modul hech narsa o'zgartirmaydi (bot avvalgidek ishlaydi).
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

# "85-modda. Sarlavha" yoki "85-1-modda." (lotin va kirill)
ARTICLE_RE = re.compile(r"^\s*(\d{1,4}(?:-\d{1,2})?)-(?:modda|модда)\b[\.\s:]*(.*)$", re.IGNORECASE)
CITATION_RE = re.compile(r"(\d{1,4}(?:-\d{1,2})?)\s*-\s*(?:modda|moddasi|moddaga|moddada|moddani|moddalar)", re.IGNORECASE)

_APOS = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "‘": "'", "`": "'", "´": "'"})
_WORD_RE = re.compile(r"[a-zа-яё0-9']+", re.IGNORECASE)


def _norm(text: str) -> str:
    return text.translate(_APOS).lower()


def _stems(text: str) -> List[str]:
    out = []
    for w in _WORD_RE.findall(_norm(text)):
        w = w.replace("'", "")
        if len(w) >= 4:
            out.append(w[:6])
    return out


class Article:
    __slots__ = ("act_key", "num", "title", "text", "stems", "title_stems")

    def __init__(self, act_key: str, num: Optional[str], title: str, text: str):
        self.act_key = act_key
        self.num = num
        self.title = title
        self.text = text
        self.stems = Counter(_stems(f"{title} {text}"))
        self.title_stems = set(_stems(title))


class Act:
    def __init__(self, key: str, title: str, aliases: List[str], edition: str, source: str):
        self.key = key
        self.title = title
        self.aliases = [_norm(a) for a in aliases if a]
        self.edition = edition
        self.source = source
        self.articles: List[Article] = []
        self.by_num: Dict[str, Article] = {}


def _read_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".docx":
        import docx  # python-docx
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
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
            art = Article(act.key, current_num, current_title.strip(), body)
            act.articles.append(art)
            act.by_num.setdefault(current_num, art)

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
        # Moddalarga bo'linmagan hujjat (farmon, qaror): bo'laklarga ajratamiz
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
        self._df: Counter = Counter()
        self._n = 0
        self.load()

    # ---- yuklash ----
    def load(self) -> None:
        self.acts.clear()
        self._df = Counter()
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
            act = Act(
                key=stem,
                title=info.get("title") or stem.replace("_", " "),
                aliases=info.get("aliases") or [info.get("title") or stem.replace("_", " ")],
                edition=info.get("edition", "ko'rsatilmagan"),
                source=info.get("source", "lex.uz"),
            )
            _split_articles(act, text)
            if act.articles:
                self.acts[act.key] = act

        for act in self.acts.values():
            for art in act.articles:
                for s in art.stems:
                    self._df[s] += 1
                self._n += 1
        total = sum(len(a.articles) for a in self.acts.values())
        if self.acts:
            logger.info(f"Qonunlar kutubxonasi yuklandi: {len(self.acts)} hujjat, {total} modda/bo'lak.")

    def is_loaded(self) -> bool:
        return bool(self.acts)

    # ---- qidiruv ----
    def _score(self, art: Article, q_stems: List[str], act_boost: float) -> float:
        score = 0.0
        n = max(self._n, 1)
        for s in set(q_stems):
            df = self._df.get(s, 0)
            if df == 0:
                continue
            idf = math.log(1 + n / df)
            if s in art.title_stems:
                score += 3 * idf
            tf = art.stems.get(s, 0)
            if tf:
                score += min(tf, 3) * idf
        return score * act_boost

    def search(self, query: str, k: int = 6) -> List[Article]:
        if not self.acts or not query:
            return []
        qn = _norm(query)
        q_stems = _stems(query)
        results: List[Tuple[float, Article]] = []

        explicit = {m.group(1) for m in CITATION_RE.finditer(query)}
        mentioned_acts = [a for a in self.acts.values() if any(al and al in qn for al in a.aliases)]

        for act in self.acts.values():
            boost = 1.5 if act in mentioned_acts else 1.0
            for art in act.articles:
                sc = self._score(art, q_stems, boost)
                if art.num and art.num in explicit and (not mentioned_acts or act in mentioned_acts):
                    sc += 100
                if sc > 0:
                    results.append((sc, art))
        results.sort(key=lambda x: x[0], reverse=True)
        # Juda past ball bo'yicha tasodifiy natijalarni olib tashlaymiz
        if not results:
            return []
        top = results[0][0]
        return [a for sc, a in results[:k] if sc >= top * 0.25]

    # ---- prompt va tekshiruv ----
    def build_context(self, query: str, k: int = 6, max_chars: int = 1800) -> Tuple[str, Set[str]]:
        """(kontekst matni, topilgan modda raqamlari) qaytaradi. Kutubxona bo'sh bo'lsa ('', set())."""
        arts = self.search(query, k=k)
        if not arts:
            return "", set()
        lines = [
            "=== RASMIY MATN PARCHALARI (O'zbekiston qonunchilik bazasi, lex.uz) ===",
            "Qoida: javobni birinchi navbatda shu parchalarga asoslang. Modda raqami, muddat va miqdorni FAQAT shu parchalardan keltiring.",
            "Parchalarda savolga javob bo'lmasa, buni ochiq ayting va taxmin qilmang.",
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
            lines.append(head)
            lines.append(body)
            lines.append("")
        lines.append("=== PARCHALAR OXIRI ===\n")
        return "\n".join(lines), nums

    def augment_prompt(self, query: str, prompt: str) -> Tuple[str, Set[str]]:
        ctx, nums = self.build_context(query)
        if not ctx:
            return prompt, set()
        return f"{ctx}\nSAVOL / TOPSHIRIQ:\n{prompt}", nums

    def annotate(self, response: str, retrieved_nums: Set[str], lang: str = "uz") -> str:
        """Javobdagi modda raqamlarini kutubxonadan olingan parchalar bilan solishtirib, izoh qo'shadi."""
        if not response or not self.acts:
            return response
        cited = {m.group(1) for m in CITATION_RE.finditer(response)}
        if not cited:
            return response
        unverified = sorted(c for c in cited if c not in retrieved_nums)
        if unverified:
            lst = ", ".join(f"{c}-modda" for c in unverified)
            notes = {
                "uz": f"⚠️ <i>Quyidagi modda raqamlari rasmiy matn kutubxonasi bilan tasdiqlanmadi: {lst}. lex.uz da tekshiring.</i>",
                "ru": f"⚠️ <i>Следующие номера статей не подтверждены библиотекой официальных текстов: {lst}. Проверьте на lex.uz.</i>",
                "en": f"⚠️ <i>These article numbers were not confirmed by the official text library: {lst}. Check lex.uz.</i>",
            }
            return response.rstrip() + "\n\n" + notes.get(lang, notes["uz"])
        return response


library = LawLibrary()
