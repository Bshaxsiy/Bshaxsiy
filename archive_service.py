import sqlite3
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "archive.db"


class ArchiveService:
    """
    Foydalanuvchilarning botdagi barcha so'rovlari va natijalarini doimiy (SQLite) arxivda saqlash,
    ko'rish va keraksizlarini o'chirish xizmati.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Ma'lumotlar bazasi va jadvallarni yaratish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_archive (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    category TEXT DEFAULT 'chat',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_archive_user ON user_archive(user_id, created_at DESC)")
                conn.commit()
                logger.info("Arxiv ma'lumotlar bazasi muvaffaqiyatli ishga tushirildi.")
        except Exception as e:
            logger.exception(f"Arxiv ma'lumotlar bazasini yaratishda xatolik: {e}")

    def save_item(
        self,
        res_id: Optional[str] = None,
        user_id: int = 0,
        user_name: str = "Foydalanuvchi",
        title: str = "Tahlil natijasi",
        content: str = "",
        category: str = "chat"
    ) -> str:
        """
        Yangi so'rov va natijani arxivga saqlash
        """
        item_id = res_id or uuid.uuid4().hex[:8]
        summary = content[:200].replace("\n", " ").strip()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO user_archive (id, user_id, user_name, category, title, content, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (item_id, user_id, user_name, category, title, content, summary, now_str))
                conn.commit()
            return item_id
        except Exception as e:
            logger.exception(f"Arxivga yozishda xatolik ({item_id}): {e}")
            return item_id

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Aynan bitta arxiv yozuvini ID bo'yicha to'liq olish
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM user_archive WHERE id = ?", (item_id,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.exception(f"Arxivdan yozuvni olishda xatolik ({item_id}): {e}")
            return None

    def get_user_items(self, user_id: int, limit: int = 5, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Foydalanuvchining arxivlangan so'rovlarini sahifalab olish
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT id, user_id, user_name, category, title, summary, created_at
                FROM user_archive
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """, (user_id, limit, offset))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.exception(f"Foydalanuvchi ({user_id}) arxivini olishda xatolik: {e}")
            return []

    def get_user_count(self, user_id: int) -> int:
        """
        Foydalanuvchining jami arxivlangan yozuvlari soni
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM user_archive WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.exception(f"Foydalanuvchi ({user_id}) arxiv sonini olishda xatolik: {e}")
            return 0

    def delete_item(self, item_id: str, user_id: Optional[int] = None) -> bool:
        """
        Bitta arxiv yozuvini o'chirish (agar user_id berilsa, faqat o'ziga tegishlisini)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if user_id is not None:
                    cursor.execute("DELETE FROM user_archive WHERE id = ? AND user_id = ?", (item_id, user_id))
                else:
                    cursor.execute("DELETE FROM user_archive WHERE id = ?", (item_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception(f"Arxivdan o'chirishda xatolik ({item_id}): {e}")
            return False

    def clear_user_archive(self, user_id: int) -> int:
        """
        Foydalanuvchining butun arxivini tozalash
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_archive WHERE user_id = ?", (user_id,))
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.exception(f"Foydalanuvchi ({user_id}) arxivini tozalashda xatolik: {e}")
            return 0


# Yagona global xizmat obyekti
archive_service = ArchiveService()
