import os
import uuid
import asyncio
import subprocess
import logging
from pathlib import Path
from typing import Tuple
from config import DOWNLOADS_DIR
from downloader import _get_ffmpeg_path

logger = logging.getLogger(__name__)


def _get_video_duration(ffprobe_bin: str, file_path: str) -> int:
    """Video davomiyligini aniqlash"""
    try:
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return int(float(res.stdout.strip()))
    except Exception:
        return 0


async def convert_to_video_note(input_path: str, max_duration: int = 60) -> Tuple[str, int]:
    """
    Har qanday to'rtburchak videoni Telegram Video Note (dumaloq video / krujok) formatiga o'tkazish.
    
    Telegram Video Note qat'iy standartlari:
    - O'lcham: 384x384 (1:1 kvadrat)
    - Video kodek: H.264 (libx264, yuv420p)
    - Audio kodek: AAC (audio stream bo'lishi shart!)
    - Maksimal davomiylik: 60 soniya
    """
    ffmpeg_bin = _get_ffmpeg_path() or "ffmpeg"
    ffprobe_bin = str(Path(ffmpeg_bin).parent / "ffprobe.exe")
    if not os.path.exists(ffprobe_bin):
        ffprobe_bin = "ffprobe"

    unique_id = uuid.uuid4().hex[:8]
    output_path = str(DOWNLOADS_DIR / f"circle_{unique_id}.mp4")

    # Audio oqim borligini tekshirish
    has_audio = False
    try:
        probe_cmd = [ffprobe_bin, "-i", input_path, "-show_streams", "-select_streams", "a", "-loglevel", "error"]
        probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        has_audio = len(probe_res.stdout.strip()) > 0
    except Exception:
        has_audio = True

    # Dumaloq format (markazdan qirqish, 384x384 va to'liq doira shaklida qirqish)
    circle_filter = (
        "crop=min(iw\\,ih):min(iw\\,ih),"
        "scale=384:384,"
        "geq=lum='if(lte(hypot(X-W/2,Y-H/2),W/2),lum(X,Y),0)':"
        "cb='if(lte(hypot(X-W/2,Y-H/2),W/2),cb(X,Y),128)':"
        "cr='if(lte(hypot(X-W/2,Y-H/2),W/2),cr(X,Y),128)'"
    )

    if has_audio:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", input_path,
            "-t", str(max_duration),
            "-vf", circle_filter,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-movflags", "+faststart",
            output_path
        ]
    else:
        # Video ovozsiz bo'lsa, Telegram Video Note talabiga mos bo'lishi uchun soxta toza audio oqim qo'shamiz
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", input_path,
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(max_duration),
            "-vf", circle_filter,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]

    def _run() -> Tuple[str, int]:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if process.returncode != 0:
            error_text = process.stderr.decode('utf-8', errors='ignore')
            logger.error(f"FFmpeg xatoligi: {error_text}")
            raise RuntimeError("Videoni dumaloq formatga o'tkazishda xatolik yuz berdi.")
        
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Dumaloq video fayli yaratilmadi.")

        duration = _get_video_duration(ffprobe_bin, output_path)
        return output_path, duration

    return await asyncio.to_thread(_run)
