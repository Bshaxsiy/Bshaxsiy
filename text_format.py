"""
Model javobini Telegram HTML formatiga xavfsiz o'tkazish.
Model ba'zan **qalin**, *qiya*, `kod`, # sarlavha kabi Markdown, ba'zan <b>..</b> kabi HTML yozadi,
bot esa qo'shimcha bloklarni (<i>⚠️ ...</i>) HTML bilan qo'shadi. Hammasini bitta formatga keltiramiz.
"""
import re
from typing import List

_ALLOWED = ("b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote")
_TAG_RE = re.compile(r"</?(?:%s)(?:\s+[^<>]*)?>" % "|".join(_ALLOWED), re.IGNORECASE)
_NORMALIZE = {"strong": "b", "em": "i", "ins": "u", "strike": "s", "del": "s"}


def _esc(seg: str) -> str:
    seg = re.sub(r"&(?!(?:amp|lt|gt|quot|#\d+);)", "&amp;", seg)
    return seg.replace("<", "&lt;").replace(">", "&gt;")


def _escape_outside_tags(text: str) -> str:
    out, pos = [], 0
    for m in _TAG_RE.finditer(text):
        out.append(_esc(text[pos:m.start()]))
        tag = m.group(0)
        name = re.match(r"</?([a-zA-Z]+)", tag).group(1).lower()
        if name in _NORMALIZE:
            tag = re.sub(r"^(</?)[a-zA-Z]+", lambda mm: mm.group(1) + _NORMALIZE[name], tag)
        out.append(tag)
        pos = m.end()
    out.append(_esc(text[pos:]))
    return "".join(out)


def to_telegram_html(text: str) -> str:
    if not text:
        return text
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # kod bloklari va satr ichidagi kod: ichiga tegmaymiz (faqat belgilarni ekranlaymiz)
    holders: List[str] = []

    def hold(m):
        holders.append("<code>%s</code>" % _esc(m.group(1)))
        return "\x00%d\x00" % (len(holders) - 1)

    t = re.sub(r"```[a-zA-Z]*\n?(.*?)```", hold, t, flags=re.S)
    t = re.sub(r"`([^`\n]+)`", hold, t)
    t = _escape_outside_tags(t)
    # sarlavhalar
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", r"<b>\1</b>", t)
    # ro'yxat belgilari
    t = re.sub(r"(?m)^(\s*)[\*\-•]\s+", r"\1• ", t)
    # qalin va qiya
    t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])__(?=\S)(.+?)(?<=\S)__(?![\w*])", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])", r"<i>\1</i>", t)
    for i, h in enumerate(holders):
        t = t.replace("\x00%d\x00" % i, h)
    return t


def split_html(text: str, limit: int = 4000) -> List[str]:
    """Matnni satr chegarasida bo'laklarga ajratadi; ochiq qolgan teglarni yopib, keyingi bo'lakda qayta ochadi."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur); cur = ""
            parts.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur); cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        parts.append(cur)
    # teglarni muvozanatlash
    out, carry = [], []
    for p in parts:
        p = "".join("<%s>" % n for n in carry) + p
        stack = []
        for m in re.finditer(r"<(/?)([a-z]+)(?:\s[^>]*)?>", p):
            close, name = m.group(1), m.group(2)
            if not close:
                stack.append(name)
            elif stack and stack[-1] == name:
                stack.pop()
        carry = [n for n in stack if n != "a"]
        out.append(p + "".join("</%s>" % n for n in reversed(stack)))
    return out


def strip_tags(text: str) -> str:
    t = re.sub(r"<[^>]+>", "", text)
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
