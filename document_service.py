import os
import re
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

# Word
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# PowerPoint
from pptx import Presentation
from pptx.util import Inches as PptxInches, Pt as PptxPt
from pptx.dml.color import RGBColor as PptxRGBColor
from pptx.enum.text import PP_ALIGN

# Excel
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# PDF reader
import pypdf

from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Arial shriftlarini ro'yxatdan o'tkazish
FONT_INITIALIZED = False

def init_pdf_fonts():
    global FONT_INITIALIZED
    if FONT_INITIALIZED:
        return

    regular_font = "C:/Windows/Fonts/arial.ttf"
    bold_font = "C:/Windows/Fonts/arialbd.ttf"
    italic_font = "C:/Windows/Fonts/ariali.ttf"

    try:
        if os.path.exists(regular_font):
            pdfmetrics.registerFont(TTFont("Arial", regular_font))
        if os.path.exists(bold_font):
            pdfmetrics.registerFont(TTFont("Arial-Bold", bold_font))
        if os.path.exists(italic_font):
            pdfmetrics.registerFont(TTFont("Arial-Italic", italic_font))
        FONT_INITIALIZED = True
    except Exception as e:
        logger.warning(f"Shriftlarni ro'yxatdan o'tkazishda ogohlantirish: {e}")


def extract_text_from_file(file_path: str, filename: str) -> str:
    """
    Fayldan (PDF, Word, Excel, TXT, CSV) matnni ajratib olish
    """
    ext = Path(filename).suffix.lower()
    text = ""

    try:
        if ext == ".pdf":
            reader = pypdf.PdfReader(file_path)
            pages_text = []
            max_pages = min(len(reader.pages), 100)
            total_chars = 0
            for i in range(max_pages):
                page = reader.pages[i]
                t = page.extract_text()
                if t:
                    pages_text.append(f"--- Sahifa {i+1} ---\n{t.strip()}")
                    total_chars += len(t)
                    if total_chars > 120000:
                        break
            text = "\n\n".join(pages_text)

        elif ext in (".docx", ".doc"):
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        paragraphs.append(row_text)
            text = "\n".join(paragraphs)

        elif ext in (".xlsx", ".xls"):
            from openpyxl import load_workbook
            wb = load_workbook(file_path, data_only=True)
            sheet_texts = []
            for sheet in wb.worksheets[:10]:
                sheet_lines = [f"=== Varaq: {sheet.title} ==="]
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(v).strip() for v in row if v is not None]
                    if row_vals:
                        sheet_lines.append(" | ".join(row_vals))
                sheet_texts.append("\n".join(sheet_lines[:150]))
            text = "\n\n".join(sheet_texts)

        elif ext in (".txt", ".csv", ".json", ".md", ".log"):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(100000)

        else:
            text = f"Noma'lum format: {ext}"

    except Exception as e:
        logger.exception(f"Fayldan matn olishda xatolik ({filename}): {e}")
        text = f"Faylni o'qishda xatolik yuz berdi: {str(e)[:150]}"

    return text.strip()


def clean_doc_text(text: str) -> str:
    """Hujjatlar (Word, PDF, PPTX, Excel) uchun matnni tozalash: bot manzillari, <_>, <...>, va markdown belgilarini yo'qotish"""
    if not text:
        return ""
    # Bot manzillari va imzolarini olib tashlash
    text = re.sub(r"@Bshaxsiy_bot", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Telegram\s*@\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Tayyorlovchi:.*", "", text, flags=re.IGNORECASE)
    # <_>, <br>, <hr> kabi xom teglarni olib tashlash
    text = re.sub(r"<[^>]*>", "", text)
    # Markdown italics _so'z_ ni tozalash va alohida tagchiziqlarni bo'shliqqa aylantirish
    text = re.sub(r"(^|\s)_(.*?)_($|\s)", r"\1\2\3", text)
    text = text.replace("_", " ").replace("`", "")
    # Ortiqcha bo'shliqlarni tozalash
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def generate_docx(title: str, content: str, user_name: str = "", lang: str = "uz") -> str:
    """
    Word (.docx) hujjati yaratish (tanlangan tilda).
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Hujjat_{unique_id}.docx"

    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    # 1. Asosiy Sarlavha
    default_title = (
        "Hisobot va Tahlil" if lang == "uz" else
        ("Аналитический отчет" if lang == "ru" else "Analytical Report")
    )
    clean_title = clean_doc_text(title) or default_title
    heading = doc.add_heading(level=1)
    run_heading = heading.add_run(clean_title)
    run_heading.font.size = Pt(20)
    run_heading.font.bold = True
    run_heading.font.color.rgb = RGBColor(31, 78, 121)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # 2. Asosiy matn
    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Divider chiziqlarni sahifa ajratgich (page break) sifatida ishlatish (masalan Ilova xati va Ma'lumotnoma o'rtasida)
        if re.match(r"^[\-_=—]{3,}\s*$", line):
            doc.add_page_break()
            continue

        # <_> va teglarni tozalash
        line = re.sub(r"<[^>]*>", "", line).strip()
        if not line:
            continue

        # Yuridik hujjatlar maxsus sarlavhalari (Da'vo arizasi, Talabnoma, Ma'lumotnoma, Ilova xati, Notarius ijro xati, So'rayman)
        clean_upper = re.sub(r"[\*#`_]", "", line).strip().upper()
        if any(clean_upper.startswith(k) for k in [
            "DA'VO ARIZASI", "TALABNOMA", "PRETENZIYA", "ИСКОВОЕ ЗАЯВЛЕНИЕ", "ДОСУДЕБНАЯ ПРЕТЕНЗИЯ", "STATEMENT OF CLAIM",
            "QARZDORLIK VA PENYA TO'G'RISIDA MA'LUMOTNOMA", "QARZDORLIK TO'G'RISIDA MA'LUMOTNOMA", "MA'LUMOTNOMA",
            "ILOVA XATI", "KUZATUV XATI", "СПРАВКА О ЗАДОЛЖЕННОСТИ", "СОПРОВОДИТЕЛЬНОЕ ПИСЬМО",
            "CERTIFICATE OF INDEBTEDNESS", "COVER LETTER", "STATEMENT OF DEBT",
            "NOTARIUSGA ARIZA", "IJRO XATI YOZISH TO'G'RISIDA ARIZA", "IJRO XATI", "NOTARIUSNING IJRO XATI",
            "OGOHLANTIRISH XATI", "BILDIRISHNOMA", "NOTARIAL OGOHLANTIRISH",
            "ЗАЯВЛЕНИЕ НОТАРИУСУ", "ЗАЯВЛЕНИЕ О СОВЕРШЕНИИ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ", "ИСПОЛНИТЕЛЬНАЯ НАДПИСЬ", "ПРЕДУПРЕЖДЕНИЕ ДОЛЖНИКУ",
            "APPLICATION TO NOTARY", "APPLICATION FOR EXECUTION INSCRIPTION", "EXECUTION INSCRIPTION", "NOTARIAL NOTICE",
            "1-QISM:", "2-QISM:", "3-QISM:", "ЧАСТЬ 1:", "ЧАСТЬ 2:", "ЧАСТЬ 3:", "PART 1:", "PART 2:", "PART 3:"
        ]):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _add_formatted_docx_runs(p, line)
            if p.runs:
                p.runs[0].font.size = Pt(16)
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = RGBColor(31, 78, 121)
            continue
        elif any(clean_upper.startswith(k) for k in ["SO'RAYMAN:", "TALAB QILAMAN:", "ПРОШУ СУД:", "ПРОШУ:", "PRAYER FOR RELIEF:"]):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _add_formatted_docx_runs(p, line)
            if p.runs:
                p.runs[0].font.size = Pt(13)
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = RGBColor(31, 78, 121)
            continue
        elif any(clean_upper.startswith(k) for k in ["ILOVALAR:", "ILOVA:", "ПРИЛОЖЕНИЯ:", "ПРИЛОЖЕНИЕ:", "EXHIBITS:"]):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _add_formatted_docx_runs(p, line)
            if p.runs:
                p.runs[0].font.size = Pt(12)
                p.runs[0].font.bold = True
                p.runs[0].font.color.rgb = RGBColor(31, 78, 121)
            continue

        if line.startswith("### "):
            h = doc.add_heading(level=3)
            clean_h = clean_doc_text(line.replace("### ", ""))
            r = h.add_run(clean_h)
            r.font.size = Pt(13)
            r.font.color.rgb = RGBColor(46, 117, 182)
        elif line.startswith("## "):
            h = doc.add_heading(level=2)
            clean_h = clean_doc_text(line.replace("## ", ""))
            r = h.add_run(clean_h)
            r.font.size = Pt(15)
            r.font.color.rgb = RGBColor(31, 78, 121)
        elif line.startswith("# "):
            h = doc.add_heading(level=1)
            clean_h = clean_doc_text(line.replace("# ", ""))
            r = h.add_run(clean_h)
            r.font.size = Pt(16)
            r.font.color.rgb = RGBColor(31, 78, 121)
        elif re.match(r"^[\*\-•]\s+", line):
            clean_item = re.sub(r"^[\*\-•]\s+", "", line)
            p = doc.add_paragraph(style='List Bullet')
            _add_formatted_docx_runs(p, clean_item)
        elif re.match(r"^\d+\.\s+", line):
            p = doc.add_paragraph(style='List Number')
            clean_item = re.sub(r"^\d+\.\s+", "", line)
            _add_formatted_docx_runs(p, clean_item)
        else:
            p = doc.add_paragraph()
            _add_formatted_docx_runs(p, line)

    doc.save(str(filename))
    logger.info(f"Word hujjati yaratildi ({lang}): {filename}")
    return str(filename)


def _add_formatted_docx_runs(paragraph, text: str):
    text = re.sub(r"<[^>]*>", "", text)
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            clean_inner = part[2:-2].replace("*", "").replace("`", "")
            clean_inner = re.sub(r"(^|\s)_(.*?)_($|\s)", r"\1\2\3", clean_inner).replace("_", " ")
            if clean_inner:
                run = paragraph.add_run(clean_inner)
                run.bold = True
                run.font.size = Pt(11)
        else:
            clean_part = part.replace("*", "").replace("`", "")
            clean_part = re.sub(r"(^|\s)_(.*?)_($|\s)", r"\1\2\3", clean_part).replace("_", " ")
            if clean_part:
                run = paragraph.add_run(clean_part)
                run.font.size = Pt(11)


def generate_pdf(title: str, content: str, user_name: str = "", lang: str = "uz") -> str:
    """
    PDF (.pdf) hujjati yaratish (tanlangan tilda).
    """
    init_pdf_fonts()

    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Hujjat_{unique_id}.pdf"

    doc = SimpleDocTemplate(
        str(filename),
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()

    font_family = "Arial" if FONT_INITIALIZED else "Helvetica"
    font_bold = "Arial-Bold" if FONT_INITIALIZED else "Helvetica-Bold"

    title_style = ParagraphStyle(
        "ReportTitle",
        fontName=font_bold,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1F4E79"),
        spaceAfter=14
    )

    h2_style = ParagraphStyle(
        "ReportH2",
        fontName=font_bold,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#2E75B6"),
        spaceBefore=10,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        "ReportBody",
        fontName=font_family,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#262626"),
        spaceAfter=6
    )

    bullet_style = ParagraphStyle(
        "ReportBullet",
        fontName=font_family,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#262626"),
        leftIndent=15,
        spaceAfter=4
    )

    story = []
    default_title = (
        "Hisobot va Tahlil" if lang == "uz" else
        ("Аналитический отчет" if lang == "ru" else "Analytical Report")
    )
    clean_title = clean_doc_text(title) or default_title
    story.append(Paragraph(clean_title, title_style))

    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue

        # Divider chiziqlarni sahifa ajratgich sifatida ishlatish (masalan Ilova xati va Ma'lumotnoma o'rtasida)
        if re.match(r"^[\-_=—]{3,}\s*$", line):
            story.append(PageBreak())
            continue

        # <_> va teglarni tozalash
        line = re.sub(r"<[^>]*>", "", line).strip()
        if not line:
            continue

        escaped_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        formatted_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", escaped_line)
        formatted_line = re.sub(r"(^|\s)_(.*?)_($|\s)", r"\1\2\3", formatted_line)
        formatted_line = formatted_line.replace("_", " ").replace("`", "")

        clean_upper = re.sub(r"[\*#`_]", "", line).strip().upper()
        if any(clean_upper.startswith(k) for k in [
            "DA'VO ARIZASI", "TALABNOMA", "PRETENZIYA", "ИСКОВОЕ ЗАЯВЛЕНИЕ", "ДОСУДЕБНАЯ ПРЕТЕНЗИЯ", "STATEMENT OF CLAIM",
            "QARZDORLIK VA PENYA TO'G'RISIDA MA'LUMOTNOMA", "QARZDORLIK TO'G'RISIDA MA'LUMOTNOMA", "MA'LUMOTNOMA",
            "ILOVA XATI", "KUZATUV XATI", "СПРАВКА О ЗАДОЛЖЕННОСТИ", "СОПРОВОДИТЕЛЬНОЕ ПИСЬМО",
            "CERTIFICATE OF INDEBTEDNESS", "COVER LETTER",
            "NOTARIUSGA ARIZA", "IJRO XATI YOZISH TO'G'RISIDA ARIZA", "IJRO XATI", "NOTARIUSNING IJRO XATI",
            "OGOHLANTIRISH XATI", "BILDIRISHNOMA", "NOTARIAL OGOHLANTIRISH",
            "ЗАЯВЛЕНИЕ НОТАРИУСУ", "ЗАЯВЛЕНИЕ О СОВЕРШЕНИИ ИСПОЛНИТЕЛЬНОЙ НАДПИСИ", "ИСПОЛНИТЕЛЬНАЯ НАДПИСЬ", "ПРЕДУПРЕЖДЕНИЕ ДОЛЖНИКУ",
            "APPLICATION TO NOTARY", "APPLICATION FOR EXECUTION INSCRIPTION", "EXECUTION INSCRIPTION", "NOTARIAL NOTICE",
            "1-QISM:", "2-QISM:", "3-QISM:", "ЧАСТЬ 1:", "ЧАСТЬ 2:", "ЧАСТЬ 3:", "PART 1:", "PART 2:", "PART 3:"
        ]):
            center_heading_style = ParagraphStyle(
                f"CenterLegalHead_{uuid.uuid4().hex[:6]}",
                parent=h2_style,
                alignment=1,  # Center
                fontSize=14,
                leading=18,
                spaceBefore=14,
                spaceAfter=8
            )
            story.append(Paragraph(f"<b>{line}</b>", center_heading_style))
        elif line.startswith("#"):
            clean_h = re.sub(r"^#+\s*", "", formatted_line)
            story.append(Paragraph(clean_h, h2_style))
        elif re.match(r"^[\*\-•]\s+", line):
            clean_b = re.sub(r"^[\*\-•]\s*", "• ", formatted_line)
            story.append(Paragraph(clean_b, bullet_style))
        else:
            story.append(Paragraph(formatted_line, body_style))

    doc.build(story)
    logger.info(f"PDF hujjati yaratildi ({lang}): {filename}")
    return str(filename)


def generate_presentation(title: str, content: str, user_name: str = "", lang: str = "uz") -> str:
    """
    Tahlil yoki mavzu asosida PowerPoint (.pptx) taqdimotini yaratish (tanlangan tilda).
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Prezentatsiya_{unique_id}.pptx"

    prs = Presentation()

    default_title = (
        "Taqdimot" if lang == "uz" else
        ("Презентация" if lang == "ru" else "Presentation")
    )
    clean_title = clean_doc_text(re.sub(r"^#+\s*", "", title)) or default_title

    # 1. Titul slaydi (Title Slide)
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    slide_title = slide.shapes.title
    slide_subtitle = slide.placeholders[1]

    slide_title.text = clean_title
    slide_subtitle.text = (
        "Tahliliy hisobot va taqdimot" if lang == "uz" else
        ("Аналитический отчет и презентация" if lang == "ru" else "Analytical Report & Presentation")
    )

    # 2. Matnni slaydlar bo'yicha ajratish
    sections: List[Dict[str, List[str]]] = []
    default_plan = (
        "Reja va Tahlil" if lang == "uz" else
        ("План и тезисы" if lang == "ru" else "Plan & Analysis")
    )
    current_title = (
        "Umumiy Ma'lumot" if lang == "uz" else
        ("Общие сведения" if lang == "ru" else "Overview")
    )
    current_bullets: List[str] = []

    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Divider chiziqlarni o'tkazib yuborish
        if re.match(r"^[\-_=—]{3,}\s*$", line):
            continue

        # <_> va teglarni tozalash
        line = re.sub(r"<[^>]*>", "", line).strip()
        if not line:
            continue

        if line.startswith("#") or re.match(r"^(Slayd|Slide|Слайд)\s*\d+[:\.]?", line, re.IGNORECASE):
            if current_bullets:
                sections.append({"title": current_title, "bullets": current_bullets})
                current_bullets = []
            clean_sec_title = re.sub(r"^(#+|Slayd\s*\d+[:\.]?|Slide\s*\d+[:\.]?|Слайд\s*\d+[:\.]?)\s*", "", line, flags=re.IGNORECASE)
            clean_sec_title = clean_doc_text(clean_sec_title)
            current_title = clean_sec_title or default_plan
        else:
            clean_bullet = re.sub(r"^[\*\-•\d\.]+\s*", "", line)
            clean_bullet = clean_doc_text(clean_bullet)
            if clean_bullet:
                current_bullets.append(clean_bullet)

    if current_bullets:
        sections.append({"title": current_title, "bullets": current_bullets})

    # Agar bo'limlar 0 bo'lsa yoki bittagina bo'lsa, xatboshilarni 3-4 tadan slaydlarga taqsimlaymiz
    if len(sections) <= 1:
        all_items = current_bullets if current_bullets else [clean_doc_text(l) for l in lines if l.strip()]
        all_items = [x for x in all_items if x]
        sections = []
        chunk_size = 4
        part_word = "Qism" if lang == "uz" else ("Часть" if lang == "ru" else "Part")
        topic_word = "Mavzu" if lang == "uz" else ("Тема" if lang == "ru" else "Topic")
        for i in range(0, len(all_items), chunk_size):
            chunk = all_items[i:i + chunk_size]
            part_num = (i // chunk_size) + 1
            sections.append({
                "title": f"{topic_word}: {clean_title} ({part_word} {part_num})",
                "bullets": chunk
            })

    # Maksimal 8 ta slayd yaratish
    bullet_slide_layout = prs.slide_layouts[1]
    for sec in sections[:8]:
        slide = prs.slides.add_slide(bullet_slide_layout)
        shapes = slide.shapes
        title_shape = shapes.title
        body_shape = shapes.placeholders[1]

        title_shape.text = sec["title"][:50]
        tf = body_shape.text_frame
        tf.word_wrap = True

        for idx, bullet_text in enumerate(sec["bullets"][:5]):
            if idx == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = bullet_text
            p.level = 0
            p.font.size = PptxPt(16)

    # 3. Yakuniy slayd (Conclusion)
    end_slide = prs.slides.add_slide(title_slide_layout)
    end_title = end_slide.shapes.title
    end_sub = end_slide.placeholders[1]
    end_title.text = (
        "E'tiboringiz uchun rahmat!" if lang == "uz" else
        ("Спасибо за внимание!" if lang == "ru" else "Thank you for your attention!")
    )
    end_sub.text = (
        "Savollar va muhokama" if lang == "uz" else
        ("Вопросы и обсуждение" if lang == "ru" else "Questions & Discussion")
    )

    prs.save(str(filename))
    logger.info(f"Prezentatsiya yaratildi ({lang}): {filename}")
    return str(filename)


def generate_excel(title: str, content: str, user_name: str = "", lang: str = "uz") -> str:
    """
    Tahlil ma'lumotlarini professional formatlangan Excel (.xlsx) jadvaliga aylantirish (tanlangan tilda).
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Jadval_{unique_id}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = (
        "Tahlil Jadvali" if lang == "uz" else
        ("Таблица анализа" if lang == "ru" else "Analysis Table")
    )

    # Stillar
    navy_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    alt_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Arial", size=10)
    bold_font = Font(name="Arial", size=10, bold=True)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)

    thin_border_side = Side(border_style="thin", color="D9D9D9")
    thin_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    # Sarlavha qatori (Metama'lumot va bot manzili olib tashlangan)
    ws.merge_cells("A1:D1")
    title_cell = ws["A1"]
    default_title = (
        "Hisobot va Maʼlumotlar Jadvali" if lang == "uz" else
        ("Таблица данных и анализа" if lang == "ru" else "Data & Analysis Table")
    )
    clean_title = clean_doc_text(title) or default_title
    title_cell.value = clean_title
    title_cell.font = Font(name="Arial", size=14, bold=True, color="1F4E79")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    # Jadval sarlavhasi (Headers) - 3-qatordan boshlanadi
    if lang == "uz":
        headers = ["№", "Bo'lim / Mavzu", "Batafsil Tavsif / Ma'lumot", "Holat / Izoh"]
        default_status = "Mavjud"
        analyzed_status = "Tahlil qilingan"
        current_category = "Asosiy tahlil"
    elif lang == "ru":
        headers = ["№", "Раздел / Тема", "Подробное описание / Данные", "Статус / Примечание"]
        default_status = "В наличии"
        analyzed_status = "Проанализировано"
        current_category = "Основной анализ"
    else:
        headers = ["№", "Section / Topic", "Detailed Description / Data", "Status / Note"]
        default_status = "Available"
        analyzed_status = "Analyzed"
        current_category = "Key Analysis"

    for col_idx, h_text in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_idx, value=h_text)
        cell.fill = navy_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border
    ws.row_dimensions[3].height = 24

    # Matnni qatorlarga ajratish
    lines = content.split("\n")
    row_num = 4
    item_idx = 1

    # Agar matnda Markdown jadval (| col1 | col2 |) bo'lsa
    table_lines = [l.strip() for l in lines if "|" in l and not l.strip().startswith("|---")]
    if len(table_lines) >= 2:
        for t_line in table_lines:
            cols = [clean_doc_text(c) for c in t_line.strip("|").split("|")]
            if cols and any(cols):
                c1 = ws.cell(row=row_num, column=1, value=item_idx)
                c2 = ws.cell(row=row_num, column=2, value=cols[0] if len(cols) > 0 else "")
                c3 = ws.cell(row=row_num, column=3, value=cols[1] if len(cols) > 1 else "")
                c4 = ws.cell(row=row_num, column=4, value=cols[2] if len(cols) > 2 else default_status)

                for c in (c1, c2, c3, c4):
                    c.font = data_font
                    c.border = thin_border
                    if row_num % 2 == 0:
                        c.fill = alt_fill
                c1.alignment = center_align
                c2.alignment = left_align
                c3.alignment = left_align
                c4.alignment = center_align
                ws.row_dimensions[row_num].height = 22
                row_num += 1
                item_idx += 1
    else:
        for raw_line in lines:
            line = raw_line.strip()
            if not line or re.match(r"^[\-_=—]{3,}\s*$", line):
                continue

            if line.startswith("#"):
                current_category = clean_doc_text(re.sub(r"^#+\s*", "", line))[:40]
            else:
                clean_text = clean_doc_text(re.sub(r"^[\*\-•\d\.]+\s*", "", line))
                if len(clean_text) > 3:
                    c1 = ws.cell(row=row_num, column=1, value=item_idx)
                    c2 = ws.cell(row=row_num, column=2, value=current_category)
                    c3 = ws.cell(row=row_num, column=3, value=clean_text)
                    c4 = ws.cell(row=row_num, column=4, value=analyzed_status)

                    for c in (c1, c2, c3, c4):
                        c.font = data_font
                        c.border = thin_border
                        if row_num % 2 == 0:
                            c.fill = alt_fill
                    c1.alignment = center_align
                    c2.font = bold_font
                    c2.alignment = left_align
                    c3.alignment = left_align
                    c4.alignment = center_align
                    ws.row_dimensions[row_num].height = 24
                    row_num += 1
                    item_idx += 1
                    if item_idx > 60:
                        break

    # Ustun kengliklarini avtomatik to'g'irlash
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 25
    ws.column_dimensions["C"].width = 55
    ws.column_dimensions["D"].width = 18

    wb.save(str(filename))
    logger.info(f"Excel fayli yaratildi ({lang}): {filename}")
    return str(filename)
