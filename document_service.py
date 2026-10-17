import os
import re
import uuid
import logging
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Arial shriftlarini ro'yxatdan o'tkazish (O'zbekcha harflar va kiritillarni xatosiz chiqarish uchun)
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


def clean_markdown_tags(text: str) -> str:
    """Belgilarni tozalash yoki moslashtirish"""
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", ""), text)
    return text


def generate_docx(title: str, content: str, user_name: str = "") -> str:
    """
    Tahlil natijalaridan chiroyli formatlangan Word (.docx) hujjati yaratish.
    Fayl yo'lini qaytaradi.
    """
    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Audio_Tahlil_{unique_id}.docx"

    doc = Document()

    # Sahifa chetlarining o'lchamlari
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    # 1. Asosiy Sarlavha
    heading = doc.add_heading(level=1)
    run_heading = heading.add_run("🎙 " + (title or "Audio Xabar Tahlili"))
    run_heading.font.size = Pt(20)
    run_heading.font.bold = True
    run_heading.font.color.rgb = RGBColor(31, 78, 121)  # To'q ko'k
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # 2. Hujjat metama'lumotlari
    meta_p = doc.add_paragraph()
    time_str = datetime.now().strftime("%d.%m.%Y, %H:%M")
    user_str = f" | Foydalanuvchi: {user_name}" if user_name else ""
    meta_run = meta_p.add_run(f"📅 Sana: {time_str}{user_str} | 🤖 Tayyorlovchi: Gemini AI Bot")
    meta_run.font.size = Pt(9.5)
    meta_run.font.italic = True
    meta_run.font.color.rgb = RGBColor(128, 128, 128)

    doc.add_paragraph("—" * 45)

    # 3. Asosiy matnni qatorlar bo'yicha tahlil qilish va formatlash
    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Sarlavhalar (### yoki ##)
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
        # Ro'yxat elementlari (* yoki - yoki •)
        elif re.match(r"^[\*\-•]\s+", line):
            clean_item = re.sub(r"^[\*\-•]\s+", "", line)
            p = doc.add_paragraph(style='List Bullet')
            # Qalin matnlarni aniqlash (**matn**)
            _add_formatted_runs(p, clean_item)
        # Raqamlangan ro'yxat (1. yoki 2.)
        elif re.match(r"^\d+\.\s+", line):
            p = doc.add_paragraph(style='List Number')
            clean_item = re.sub(r"^\d+\.\s+", "", line)
            _add_formatted_runs(p, clean_item)
        else:
            p = doc.add_paragraph()
            _add_formatted_runs(p, line)

    # 4. Yakuniy izoh
    doc.add_paragraph()
    footer_p = doc.add_paragraph()
    footer_run = footer_p.add_run("Ushbu hisobot Telegram @Bshaxsiy_bot tomonidan avtomatik yaratildi.")
    footer_run.font.size = Pt(8.5)
    footer_run.font.italic = True
    footer_run.font.color.rgb = RGBColor(160, 160, 160)

    doc.save(str(filename))
    logger.info(f"Word hujjati yaratildi: {filename}")
    return str(filename)


def _add_formatted_runs(paragraph, text: str):
    """Matndagi **qalin** qismlarni Word paragraphiga chiroyli qo'shish"""
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
    Tahlil natijalaridan chiroyli formatlangan PDF hujjati yaratish.
    Fayl yo'lini qaytaradi.
    """
    init_pdf_fonts()

    unique_id = uuid.uuid4().hex[:8]
    filename = DOWNLOADS_DIR / f"Audio_Tahlil_{unique_id}.pdf"

    doc = SimpleDocTemplate(
        str(filename),
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()

    # Maxsus stillar
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

    # Sarlavha
    story.append(Paragraph(f"🎙 {title or 'Audio Xabar Tahlili'}", title_style))

    # Sana va muallif
    time_str = datetime.now().strftime("%d.%m.%Y, %H:%M")
    user_str = f" | Foydalanuvchi: {user_name}" if user_name else ""
    meta_text = f"📅 Sana: {time_str}{user_str} | 🤖 Tayyorlovchi: Gemini AI Bot"
    story.append(Paragraph(meta_text, meta_style))

    # Chiziq
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#D3D3D3"), spaceAfter=12))

    # Qatorlarni qo'shish
    lines = content.split("\n")
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue

        # HTML-safe qilish
        escaped_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Markdown **bold** ni <b>bold</b> ga o'tkazish
        formatted_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", escaped_line)

        if line.startswith("#"):
            clean_h = re.sub(r"^#+\s*", "", formatted_line)
            story.append(Paragraph(clean_h, h2_style))
        elif re.match(r"^[\*\-•]\s+", line):
            clean_b = re.sub(r"^[\*\-•]\s*", "• ", formatted_line)
            story.append(Paragraph(clean_b, bullet_style))
        else:
            story.append(Paragraph(formatted_line, body_style))

    # Footer
    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E0E0E0"), spaceAfter=6))
    footer_text = "<i>Ushbu hisobot Telegram @Bshaxsiy_bot tomonidan avtomatik tarzda shakllantirildi.</i>"
    story.append(Paragraph(footer_text, meta_style))

    doc.build(story)
    logger.info(f"PDF hujjati yaratildi: {filename}")
    return str(filename)
