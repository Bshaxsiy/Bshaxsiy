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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
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
            for i, page in enumerate(reader.pages[:25]):  # Dastlabki 25 sahifa
                t = page.extract_text()
                if t:
                    pages_text.append(f"--- Sahifa {i+1} ---\n{t.strip()}")
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
            for sheet in wb.worksheets[:5]:
                sheet_lines = [f"=== Varaq: {sheet.title} ==="]
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(v).strip() for v in row if v is not None]
                    if row_vals:
                        sheet_lines.append(" | ".join(row_vals))
                sheet_texts.append("\n".join(sheet_lines[:100]))
            text = "\n\n".join(sheet_texts)

        elif ext in (".txt", ".csv", ".json", ".md", ".log"):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(50000)

        else:
            text = f"Noma'lum format: {ext}"

    except Exception as e:
        logger.exception(f"Fayldan matn olishda xatolik ({filename}): {e}")
        text = f"Faylni o'qishda xatolik yuz berdi: {str(e)[:150]}"

    return text.strip()


def generate_docx(title: str, content: str, user_name: str = "") -> str:
    """
    Word (.docx) hujjati yaratish.
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
    heading = doc.add_heading(level=1)
    run_heading = heading.add_run(title or "Hisobot va Tahlil")
    run_heading.font.size = Pt(20)
    run_heading.font.bold = True
    run_heading.font.color.rgb = RGBColor(31, 78, 121)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # 2. Hujjat metama'lumotlari
    meta_p = doc.add_paragraph()
    time_str = datetime.now().strftime("%d.%m.%Y, %H:%M")
    user_str = f" | Foydalanuvchi: {user_name}" if user_name else ""
    meta_run = meta_p.add_run(f"📅 Sana: {time_str}{user_str} | 🤖 Tayyorlovchi: Telegram @Bshaxsiy_bot")
    meta_run.font.size = Pt(9.5)
    meta_run.font.italic = True
    meta_run.font.color.rgb = RGBColor(128, 128, 128)

    doc.add_paragraph("—" * 45)

    # 3. Asosiy matn
    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("### "):
            h = doc.add_heading(level=3)
            r = h.add_run(line.replace("### ", "").strip())
            r.font.size = Pt(13)
            r.font.color.rgb = RGBColor(46, 117, 182)
        elif line.startswith("## "):
            h = doc.add_heading(level=2)
            r = h.add_run(line.replace("## ", "").strip())
            r.font.size = Pt(15)
            r.font.color.rgb = RGBColor(31, 78, 121)
        elif line.startswith("# "):
            h = doc.add_heading(level=1)
            r = h.add_run(line.replace("# ", "").strip())
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

    # 4. Footer
    doc.add_paragraph()
    footer_p = doc.add_paragraph()
    footer_run = footer_p.add_run("Ushbu hisobot Telegram @Bshaxsiy_bot tomonidan avtomatik yaratildi.")
    footer_run.font.size = Pt(8.5)
    footer_run.font.italic = True
    footer_run.font.color.rgb = RGBColor(160, 160, 160)

    doc.save(str(filename))
    logger.info(f"Word hujjati yaratildi: {filename}")
    return str(filename)


def _add_formatted_docx_runs(paragraph, text: str):
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
            run.font.size = Pt(11)
        else:
            clean_part = part.replace("*", "").replace("`", "")
            run = paragraph.add_run(clean_part)
            run.font.size = Pt(11)


def generate_pdf(title: str, content: str, user_name: str = "") -> str:
    """
    PDF (.pdf) hujjati yaratish.
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
    font_italic = "Arial-Italic" if FONT_INITIALIZED else "Helvetica-Oblique"

    title_style = ParagraphStyle(
        "ReportTitle",
        fontName=font_bold,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1F4E79"),
        spaceAfter=6
    )

    meta_style = ParagraphStyle(
        "ReportMeta",
        fontName=font_italic,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#7F7F7F"),
        spaceAfter=10
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
    story.append(Paragraph(title or "Hisobot va Tahlil", title_style))

    time_str = datetime.now().strftime("%d.%m.%Y, %H:%M")
    user_str = f" | Foydalanuvchi: {user_name}" if user_name else ""
    meta_text = f"📅 Sana: {time_str}{user_str} | 🤖 Tayyorlovchi: Telegram @Bshaxsiy_bot"
    story.append(Paragraph(meta_text, meta_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#D3D3D3"), spaceAfter=12))

    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue

        escaped_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        formatted_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", escaped_line)

        if line.startswith("#"):
            clean_h = re.sub(r"^#+\s*", "", formatted_line)
            story.append(Paragraph(clean_h, h2_style))
        elif re.match(r"^[\*\-•]\s+", line):
            clean_b = re.sub(r"^[\*\-•]\s*", "• ", formatted_line)
            story.append(Paragraph(clean_b, bullet_style))
        else:
            story.append(Paragraph(formatted_line, body_style))

    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E0E0E0"), spaceAfter=6))
    story.append(Paragraph("<i>Ushbu hisobot Telegram @Bshaxsiy_bot tomonidan avtomatik yaratildi.</i>", meta_style))

    doc.build(story)
    logger.info(f"PDF hujjati yaratildi: {filename}")
    return str(filename)


def generate_presentation(title: str, content: str, user_name: str = "") -> str:
    """
    Tahlil yoki mavzu asosida PowerPoint (.pptx) taqdimotini yaratish.
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Prezentatsiya_{unique_id}.pptx"

    prs = Presentation()

    # 1. Titul slaydi (Title Slide)
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    slide_title = slide.shapes.title
    slide_subtitle = slide.placeholders[1]

    clean_title = re.sub(r"^#+\s*", "", title).replace("*", "").strip() or "Prezentatsiya"
    slide_title.text = clean_title
    time_str = datetime.now().strftime("%d.%m.%Y")
    slide_subtitle.text = f"{user_name + ' | ' if user_name else ''}{time_str}\nTayyorlovchi: @Bshaxsiy_bot"

    # 2. Matnni slaydlar bo'yicha ajratish
    # Matnda # yoki ## yoki Slayd so'zi bormi tekshiramiz
    sections: List[Dict[str, List[str]]] = []
    current_title = "Umumiy Ma'lumot"
    current_bullets: List[str] = []

    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#") or re.match(r"^(Slayd|Slide)\s*\d+[:\.]?", line, re.IGNORECASE):
            if current_bullets:
                sections.append({"title": current_title, "bullets": current_bullets})
                current_bullets = []
            clean_sec_title = re.sub(r"^(#+|Slayd\s*\d+[:\.]?|Slide\s*\d+[:\.]?)\s*", "", line, flags=re.IGNORECASE)
            clean_sec_title = re.sub(r"[\*`]", "", clean_sec_title).strip()
            current_title = clean_sec_title or "Reja va Tahlil"
        else:
            clean_bullet = re.sub(r"^[\*\-•\d\.]+\s*", "", line).replace("*", "").replace("`", "").strip()
            if clean_bullet:
                current_bullets.append(clean_bullet)

    if current_bullets:
        sections.append({"title": current_title, "bullets": current_bullets})

    # Agar bo'limlar 0 bo'lsa yoki bittagina bo'lsa, xatboshilarni 3-4 tadan slaydlarga taqsimlaymiz
    if len(sections) <= 1:
        all_items = current_bullets if current_bullets else [l.strip() for l in lines if l.strip()]
        sections = []
        chunk_size = 4
        for i in range(0, len(all_items), chunk_size):
            chunk = all_items[i:i + chunk_size]
            part_num = (i // chunk_size) + 1
            sections.append({
                "title": f"Mavzu: {clean_title} (Qism {part_num})",
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
    end_title.text = "E'tiboringiz uchun rahmat!"
    end_sub.text = "Savollar va muhokama\n@Bshaxsiy_bot orqali yaratildi"

    prs.save(str(filename))
    logger.info(f"Prezentatsiya yaratildi: {filename}")
    return str(filename)


def generate_excel(title: str, content: str, user_name: str = "") -> str:
    """
    Tahlil ma'lumotlarini professional formatlangan Excel (.xlsx) jadvaliga aylantirish.
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Jadval_{unique_id}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Tahlil Jadvali"

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

    # Sarlavha qatori
    ws.merge_cells("A1:D1")
    title_cell = ws["A1"]
    title_cell.value = f"📊 {title or 'Hisobot va Maʼlumotlar Jadvali'}"
    title_cell.font = Font(name="Arial", size=14, bold=True, color="1F4E79")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    # Metama'lumot
    ws.merge_cells("A2:D2")
    meta_cell = ws["A2"]
    time_str = datetime.now().strftime("%d.%m.%Y, %H:%M")
    meta_cell.value = f"Sana: {time_str} | Foydalanuvchi: {user_name or 'Hurmatli foydalanuvchi'} | Bot: @Bshaxsiy_bot"
    meta_cell.font = Font(name="Arial", size=9, italic=True, color="7F7F7F")
    ws.row_dimensions[2].height = 18

    # Jadval sarlavhasi (Headers)
    headers = ["№", "Bo'lim / Mavzu", "Batafsil Tavsif / Ma'lumot", "Status / Izoh"]
    for col_idx, h_text in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_idx, value=h_text)
        cell.fill = navy_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border
    ws.row_dimensions[4].height = 24

    # Matnni qatorlarga ajratish
    lines = content.split("\n")
    row_num = 5
    item_idx = 1
    current_category = "Asosiy tahlil"

    # Agar matnda Markdown jadval (| col1 | col2 |) bo'lsa
    table_lines = [l.strip() for l in lines if "|" in l and not l.strip().startswith("|---")]
    if len(table_lines) >= 2:
        for t_line in table_lines:
            cols = [c.strip() for c in t_line.strip("|").split("|")]
            if cols:
                c1 = ws.cell(row=row_num, column=1, value=item_idx)
                c2 = ws.cell(row=row_num, column=2, value=cols[0] if len(cols) > 0 else "")
                c3 = ws.cell(row=row_num, column=3, value=cols[1] if len(cols) > 1 else "")
                c4 = ws.cell(row=row_num, column=4, value=cols[2] if len(cols) > 2 else "Mavjud")
                
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
            if not line:
                continue

            if line.startswith("#"):
                current_category = re.sub(r"^#+\s*", "", line).replace("*", "").strip()[:40]
            else:
                clean_text = re.sub(r"^[\*\-•\d\.]+\s*", "", line).replace("*", "").replace("`", "").strip()
                if len(clean_text) > 3:
                    c1 = ws.cell(row=row_num, column=1, value=item_idx)
                    c2 = ws.cell(row=row_num, column=2, value=current_category)
                    c3 = ws.cell(row=row_num, column=3, value=clean_text)
                    c4 = ws.cell(row=row_num, column=4, value="Tahlil qilingan")

                    for c in (c1, c2, c3, c4):
                        c.font = data_font
                        c.border = thin_border
                        if row_num % 2 == 0:
                            c.fill = alt_fill
                    c1.alignment = center_align
                    c2.alignment = bold_font
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
    logger.info(f"Excel fayli yaratildi: {filename}")
    return str(filename)
