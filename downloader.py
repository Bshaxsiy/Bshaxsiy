import os
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
from config import DOWNLOADS_DIR

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB (Telegram Bot API cheklovi)

class DownloadException(Exception):
    """Barcha yuklab olish xatoliklari uchun asosiy klass"""
    pass

class FileTooLargeError(DownloadException):
    """Video 50MB dan katta bo'lsa"""
    pass

class PrivateMediaError(DownloadException):
    """Profil yopiq (private) bo'lsa yoki autentifikatsiya talab qilinsa"""
    pass


import shutil

def _get_ffmpeg_path() -> Optional[str]:
    """Tizimda yoki WinGet papkasida ffmpeg mavjudligini aniqlaydi"""
    # 1. PATH muhit o'zgaruvchisidan qidirish
    in_path = shutil.which("ffmpeg")
    if in_path:
        return in_path

    # 2. WinGet o'rnatgan paketlar papkasidan qidirish
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.exists():
            for exe in winget_packages.glob("**/ffmpeg.exe"):
                return str(exe)

    return None


def _sync_download(url: str) -> Dict[str, Any]:
    """
    yt-dlp orqali videoni sinxron yuklab olish funksiyasi.
    Alohida thread ichida chaqiriladi.
    """
    unique_id = uuid.uuid4().hex[:8]
    output_template = str(DOWNLOADS_DIR / f"{unique_id}_%(id)s.%(ext)s")

    ffmpeg_path = _get_ffmpeg_path()

    ydl_opts = {
        # Agar ffmpeg bo'lsa yuqori sifatli video+audio ni birlashtiradi, bo'lmasa tayyor mp4 oladi
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best' if ffmpeg_path else 'best[ext=mp4]/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'max_filesize': MAX_FILE_SIZE,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        }
    }

    if ffmpeg_path:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Video ma'lumotlarini olish
            info = ydl.extract_info(url, download=True)
            
            # Agar karusel bo'lsa (bir nechta media)
            if 'entries' in info:
                # Birinchi videoni olamiz
                for entry in info['entries']:
                    if entry and entry.get('ext') == 'mp4':
                        info = entry
                        break
                else:
                    if info['entries']:
                        info = info['entries'][0]

            filename = ydl.prepare_filename(info)
            # Agar format mp4 ga aylantirilgan bo'lsa
            mp4_filename = Path(filename).with_suffix('.mp4')
            final_file = mp4_filename if mp4_filename.exists() else Path(filename)

            if not final_file.exists():
                # Ba'zan fayl nomi boshqacha kengaytmada saqlanadi, downloads papkasini tekshiramiz
                candidates = list(DOWNLOADS_DIR.glob(f"{unique_id}_*"))
                if candidates:
                    final_file = candidates[0]
                else:
                    raise DownloadException("Fayl topilmadi yoki yuklab bo'lmadi.")

            # Fayl hajmini tekshirish
            file_size = final_file.stat().st_size
            if file_size > MAX_FILE_SIZE:
                if final_file.exists():
                    final_file.unlink()
                raise FileTooLargeError("Video hajmi 50MB dan katta bo'lgani sababli Telegram orqali yuborib bo'lmaydi.")

            title = info.get('description') or info.get('title') or "Instagram Video"
            # Caption juda uzun bo'lsa (Telegram caption limit 1024 belgi)
            if len(title) > 500:
                title = title[:497] + "..."

            return {
                'file_path': str(final_file),
                'title': title,
                'duration': info.get('duration'),
                'width': info.get('width'),
                'height': info.get('height')
            }

    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e).lower()
        if "private" in err_msg or "login" in err_msg:
            raise PrivateMediaError("Ushbu post yopiq (private) hisobga tegishli yoki ruxsat talab qiladi.")
        elif "max-filesize" in err_msg or "file is larger than" in err_msg:
            raise FileTooLargeError("Video hajmi 50MB dan katta bo'lgani sababli yuklab olinmadi.")
        else:
            raise DownloadException(f"Yuklab olishda xatolik yuz berdi: {e}")


async def download_instagram_video(url: str) -> Dict[str, Any]:
    """
    Instagram videosini yuklab olish (asinxron wrapper).
    Botning boshqa foydalanuvchilar bilan ishlashini to'xtatib qo'ymaslik uchun thread pool da bajariladi.
    """
    return await asyncio.to_thread(_sync_download, url)


def _sync_extract_audio(video_path: str) -> str:
    """Videodan MP3 audioni ajratib olish (FFmpeg orqali)"""
    unique_id = uuid.uuid4().hex[:8]
    mp3_path = DOWNLOADS_DIR / f"audio_{unique_id}.mp3"
    ffmpeg_path = _get_ffmpeg_path() or "ffmpeg"

    import subprocess
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", video_path,
        "-vn",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        str(mp3_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise DownloadException("Videodan audio ajratib olishda xatolik yuz berdi.")
    return str(mp3_path)


async def extract_audio_from_video(video_path: str) -> str:
    """Videodan MP3 formatida audioni ajratib olish (asinxron)"""
    return await asyncio.to_thread(_sync_extract_audio, video_path)

