# Qonunlar kutubxonasi (laws/)

Bu papkaga lex.uz dan olingan RASMIY qonun matnlarini qo'ying. Bot javob berayotganda
mos moddalarni shu matnlardan topib, javobni ularga asoslaydi va javobdagi modda
raqamlarini ular bilan solishtiradi (tasdiqlanmagan raqamlar belgilanadi).

## Qanday qo'shiladi
1. lex.uz da kerakli hujjatni oching (masalan, Mehnat kodeksi) va uni Word (.docx), PDF yoki matn (.txt) ko'rinishida saqlang.
2. Faylni shu papkaga qo'ying (masalan: `mehnat_kodeksi.docx`).
3. `index.json` fayliga shu fayl uchun yozuv qo'shing (namuna pastda).
4. O'zgarishlarni commit va push qiling. Render botni avtomatik qayta ishga tushiradi.

## index.json namunasi
```json
{
  "mehnat_kodeksi.docx": {
    "title": "O'zbekiston Respublikasi Mehnat kodeksi",
    "aliases": ["mehnat kodeksi", "mehnat kodeksiga"],
    "edition": "KK.OO.YYYY (lex.uz da ko'rsatilgan oxirgi tahrir sanasi)",
    "source": "lex.uz/uz/docs/-6257288"
  }
}
```
- `title` — javobda ko'rsatiladigan nom.
- `aliases` — foydalanuvchi savolida uchraydigan nomlar (kichik harflarda). Savolda shu nom bo'lsa, shu hujjat birinchi qidiriladi.
- `edition` — tahrir sanasi. Qonun o'zgarganda faylni yangilab, bu sanani ham yangilang.

## Muhim
- Faqat rasmiy manbadan (lex.uz) oling. Norma.uz kabi tijoriy portal materiallarini ko'chirmang: ularning saytida ruxsatsiz nusxalash taqiqlangani yozilgan.
- Hujjatda modda sarlavhalari "85-modda. Sarlavha" ko'rinishida bo'lsa, bot moddalarga to'g'ri ajratadi. Boshqa ko'rinishdagi hujjat (farmon, qaror) bo'laklarga bo'linadi.
- Qonun o'zgarganda faylni yangi tahrir bilan almashtirib, qayta commit va push qiling.
- Papka bo'sh bo'lsa, bot avvalgidek (kutubxonasiz) ishlaydi.
