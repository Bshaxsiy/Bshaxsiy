import os
import re
import uuid
import logging
import asyncio
from pathlib import Path
from typing import Tuple

import edge_tts
from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# O'zbek va xalqaro ovozlar
DEFAULT_VOICE = "uz-UZ-MadinaNeural"  # Tabiiy o'zbek ayol ovozi
MALE_VOICE = "uz-UZ-SardorNeural"    # Tabiiy o'zbek erkak ovozi


def clean_text_for_speech(text: str) -> str:
    """
    Matndan markdown belgilari, havolalar va ortiqcha teglarni olib tashlash,
    nutq ravon va tabiiy eshitilishi uchun tozalash.
    """
    if not text:
        return ""

    # Kod bloklarini qisqartirish
    text = re.sub(r"```[\s\S]*?```", " Kod bloki keltirilgan. ", text)
    # Inline kod belgilarini tozalash
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Markdown sarlavhalari, qalin va qiya belgilarni tozalash
    text = re.sub(r"[*_~#>-]", " ", text)
    # Havolalarni tozalash [matn](havola) -> matn
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # URL havolalarini olib tashlash
    text = re.sub(r"https?:\/\/\S+", "", text)
    # HTML teglarni olib tashlash
    text = re.sub(r"<[^>]+>", "", text)
    # Ortiqcha bo'shliqlarni bitta bo'shliqqa aylantirish
    text = re.sub(r"\s+", " ", text).strip()

    return text


async def convert_text_to_voice_and_audio(text: str, voice: str = DEFAULT_VOICE) -> Tuple[str, str]:
    """
    Matnni audio xabarga aylantirish.
    (ogg_path, mp3_path) qaytaradi:
    - ogg_path: Telegram Voice Note uchun
    - mp3_path: Telegram Audio File uchun (agar ovozli xabarlar cheklangan bo'lsa)
    """
    cleaned_text = clean_text_for_speech(text)
    if not cleaned_text:
        cleaned_text = "Javob matni mavjud emas."

    # Agar matn juda uzun bo'lsa, nutq uchun dastlabki 2000 belgini olish
    if len(cleaned_text) > 2000:
        cleaned_text = cleaned_text[:1990] + "... Davomi matnda keltirilgan."

    unique_id = uuid.uuid4().hex[:8]
    mp3_path = DOWNLOADS_DIR / f"tts_{unique_id}.mp3"
    ogg_path = DOWNLOADS_DIR / f"tts_{unique_id}.ogg"

    try:
        # 1. Edge TTS orqali MP3 yaratish
        communicate = edge_tts.Communicate(cleaned_text, voice)
        await communicate.save(str(mp3_path))

        # 2. FFmpeg orqali Telegram Voice formati (OGG Opus) ga o'tkazish
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(mp3_path),
            "-c:a", "libopus",
            "-b:a", "32k",
            str(ogg_path)
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        # Agar OGG yaratilmagan bo'lsa, MP3 ni ogg_path o'rniga ham ishlatish
        actual_ogg = str(ogg_path) if ogg_path.exists() else str(mp3_path)
        actual_mp3 = str(mp3_path) if mp3_path.exists() else actual_ogg

        return actual_ogg, actual_mp3

    except Exception as e:
        logger.exception(f"TTS audio yaratishda xatolik: {e}")
        raise
