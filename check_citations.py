"""
Kodda (promptlar, matnlar) yozilgan "N-modda" havolalarini laws/ kutubxonasi bilan solishtiradi.

Ishlatish:  python check_citations.py [fayl ...]      (standart: ai_service.py legal_factcheck.py document_service.py)
Har bir havola uchun: hujjat aniqlandimi, shunday modda mavjudmi va uning sarlavhasi nima ekanini chiqaradi.
Sarlavhani odam o'qib, mazmunga mosligini baholaydi (skript faqat mavjudligini va nomini ko'rsatadi).
Chiqish kodi: 1 — mavjud bo'lmagan modda topilsa, aks holda 0.
"""
import re
import sys

from law_library import LawLibrary, _norm

# (qidiruv namunasi hujjat nomi uchun, laws/ dagi fayl kaliti)
ACTS = [
    (r"fuqarolik protsessual|fpk", "fuqarolik_protsessual_kodeksi"),
    (r"iqtisodiy protsessual|ipk", "iqtisodiy_protsessual_kodeksi"),
    (r"jinoyat-protsessual|jinoyat protsessual", "jinoyat_protsessual_kodeksi"),
    (r"jinoyat kodeks", "jinoyat_kodeksi"),
    (r"mehnat kodeks", "mehnat_kodeksi"),
    (r"soliq kodeks", "soliq_kodeksi"),
    (r"oila kodeks", "oila_kodeksi"),
    (r"yer kodeks", "yer_kodeksi"),
    (r"byudjet kodeks", "byudjet_kodeksi"),
    (r"notariat", "notariat_qonuni"),
    (r"ijro etish to'g'risida|ijro etish to'g'risida", "sud_hujjatlarini_ijro_etish_qonuni"),
    (r"buxgalteriya", "buxgalteriya_qonuni"),
    (r"davlat boji", "davlat_boji_qonuni"),
    (r"advokatura", "advokatura_qonuni"),
    (r"prokuratura", "prokuratura_qonuni"),
    (r"mas'uliyati cheklangan|mchj", "mchj_qonuni"),
    (r"to'lovga qobiliyatsizlik", "tolovga_qobiliyatsizlik_qonuni"),
    (r"banklar", "banklar_qonuni"),
    (r"raqobat", "raqobat_qonuni"),
    (r"murojaatlar", "murojaatlar_qonuni"),
    (r"sudlar to'g'risida", "sudlar_qonuni"),
    (r"\bfk\b|fuqarolik kodeks", "FK"),  # 1-qism / 2-qism: moddaga qarab aniqlanadi
]
# "236, 237, 327, 333-moddalari", "76-80-moddalari", "188, 189, 190, 191-moddalari"
LIST_RE = re.compile(r"((?:\d{1,4}(?:\s*[-–]\s*\d{1,4})?\s*(?:,|va|hamda|и)?\s*)+)[-–]?\s*(?:modda|moddasi|moddalari|moddalar)", re.I)
NUM_RE = re.compile(r"(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?")


def expand(spec: str):
    out = []
    for m in NUM_RE.finditer(spec):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b >= a and b - a <= 60:
            out.extend(range(a, b + 1))
        else:
            out.extend([a, b])
    return out


def main(files):
    lib = LawLibrary()
    if not lib.is_loaded():
        print("laws/ bo'sh yoki topilmadi")
        return 2
    by_key = {}
    for a in lib._all:
        if a.num:
            by_key.setdefault(a.act_key, {}).setdefault(a.num, a)
    bad = 0
    seen = set()
    for fn in files:
        try:
            lines = open(fn, encoding="utf-8").read().splitlines()
        except OSError as e:
            print(f"{fn}: o'qilmadi ({e})")
            continue
        for ln, line in enumerate(lines, 1):
            low = _norm(line)
            for m in LIST_RE.finditer(low):
                before = low[max(0, m.start() - 140):m.start()]
                # eng yaqin (oxirgi) hujjat nomini olamiz
                best, pos = None, -1
                for pat, key in ACTS:
                    for mm in re.finditer(pat, before):
                        if mm.start() > pos:
                            best, pos = key, mm.start()
                if not best:
                    continue
                for n in expand(m.group(1)):
                    keys = ["fuqarolik_kodeksi_1_qism", "fuqarolik_kodeksi_2_qism"] if best == "FK" else [best]
                    art = next((by_key.get(k, {}).get(str(n)) for k in keys if by_key.get(k, {}).get(str(n))), None)
                    tag = (fn, ln, best, n)
                    if tag in seen:
                        continue
                    seen.add(tag)
                    if art:
                        print(f"OK    {fn}:{ln}  {best} {n}-modda — {art.title[:80]}")
                    else:
                        bad += 1
                        print(f"YO'Q  {fn}:{ln}  {best} {n}-modda — kutubxonada topilmadi")
    print(f"\nTekshirildi: {len(seen)} havola, topilmagan: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["ai_service.py", "legal_factcheck.py", "document_service.py"]))
