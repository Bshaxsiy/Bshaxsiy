import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

import config
from config import DATA_DIR, ADMIN_IDS, ADMIN_PASSWORD

logger = logging.getLogger(__name__)

ACCESS_FILE = DATA_DIR / "access_control.json"


class AccessGuard:
    """
    Bot uchun qo'riqchi (Access Control / Gatekeeper) tizimi:
    - Cheklangan foydalanuvchilar ro'yxatini (whitelist) boshqarish
    - Administratorlarni boshqarish
    - Yangi foydalanuvchilarning ruxsat so'rovlarini qabul qilish / tasdiqlash
    - Ochiq yoki cheklangan rejimni boshqarish
    """

    def __init__(self):
        self.file_path = ACCESS_FILE
        self.data: Dict[str, Any] = {
            "restricted_mode": True,  # True = faqat ruxsat berilganlar foydalana oladi
            "admin_ids": [],
            "allowed_users": {},
            "pending_requests": {}
        }
        self.load()
        self._sync_env_admins()

    def _sync_env_admins(self):
        """ .env faylidagi ADMIN_IDS larni yuklash va saqlash """
        changed = False
        for admin_id in ADMIN_IDS:
            if admin_id not in self.data["admin_ids"]:
                self.data["admin_ids"].append(admin_id)
                changed = True
            str_id = str(admin_id)
            if str_id not in self.data["allowed_users"]:
                self.data["allowed_users"][str_id] = {
                    "name": "Super Admin",
                    "username": "",
                    "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "added_by": "ENV_CONFIG"
                }
                changed = True
        if changed:
            self.save()

    def load(self):
        """ Ma'lumotlarni JSON fayldan o'qish """
        try:
            if self.file_path.exists():
                with open(self.file_path, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)
                    self.data.update(saved_data)
                    logger.info("AccessGuard ma'lumotlari muvaffaqiyatli yuklandi.")
            else:
                self.save()
        except Exception as e:
            logger.error(f"AccessGuard ma'lumotlarini yuklashda xatolik: {e}")

    def save(self):
        """ Ma'lumotlarni JSON faylga saqlash """
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"AccessGuard ma'lumotlarini saqlashda xatolik: {e}")

    def is_admin(self, user_id: int) -> bool:
        """ Foydalanuvchi administrator ekanligini tekshirish """
        return user_id in self.data.get("admin_ids", [])

    def is_user_allowed(self, user_id: int) -> bool:
        """
        Foydalanuvchiga botdan foydalanishga ruxsat bormi?
        1. Administratorlarga har doim ruxsat berilgan.
        2. Agar cheklangan rejim (restricted_mode) o'chirilgan bo'lsa -> hamma foydalana oladi.
        3. Aks holda -> user_id allowed_users ro'yxatida bo'lishi shart.
        """
        if self.is_admin(user_id):
            return True

        if not self.data.get("restricted_mode", True):
            return True

        return str(user_id) in self.data.get("allowed_users", {})

    def add_user(self, user_id: int, name: str = "", username: str = "", added_by: str = "ADMIN") -> bool:
        """ Foydalanuvchini ruxsat berilganlar ro'yxatiga qo'shish """
        str_id = str(user_id)
        self.data["allowed_users"][str_id] = {
            "name": name or f"Foydalanuvchi_{user_id}",
            "username": username or "",
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "added_by": added_by
        }
        # Kutilayotgan so'rovlardan o'chirish
        if str_id in self.data.get("pending_requests", {}):
            del self.data["pending_requests"][str_id]

        self.save()
        logger.info(f"Foydalanuvchi qo'shildi: {user_id} ({name}) by {added_by}")
        return True

    def remove_user(self, user_id: int) -> bool:
        """ Foydalanuvchini ro'yxatdan o'chirish (ruxsatni bekor qilish) """
        str_id = str(user_id)
        if str_id in self.data["allowed_users"]:
            del self.data["allowed_users"][str_id]
            self.save()
            logger.info(f"Foydalanuvchi o'chirildi: {user_id}")
            return True
        return False

    def add_admin(self, user_id: int, name: str = "", username: str = "") -> bool:
        """ Yangi admin tayinlash """
        if user_id not in self.data["admin_ids"]:
            self.data["admin_ids"].append(user_id)
        self.add_user(user_id, name=name or "Admin", username=username, added_by="SUPER_ADMIN")
        self.save()
        logger.info(f"Yangi administrator tayinlandi: {user_id}")
        return True

    def remove_admin(self, user_id: int) -> bool:
        """ Administratorlik huquqini bekor qilish """
        if user_id in self.data["admin_ids"]:
            self.data["admin_ids"].remove(user_id)
            self.save()
            logger.info(f"Admin huquqi bekor qilindi: {user_id}")
            return True
        return False

    def register_access_request(self, user_id: int, name: str, username: str) -> bool:
        """ Foydalanuvchining kirish so'rovini qayd qilish """
        str_id = str(user_id)
        self.data.setdefault("pending_requests", {})[str_id] = {
            "name": name,
            "username": username,
            "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.save()
        return True

    def is_request_pending(self, user_id: int) -> bool:
        """ Foydalanuvchi so'rov yuborgan va kutilmoqdami? """
        return str(user_id) in self.data.get("pending_requests", {})

    def check_admin_password(self, password: str) -> bool:
        """ Admin parolini tekshirish """
        return bool(ADMIN_PASSWORD and password.strip() == ADMIN_PASSWORD.strip())

    def toggle_restricted_mode(self) -> bool:
        """ Cheklangan rejimni yoqish / o'chirish """
        current = self.data.get("restricted_mode", True)
        self.data["restricted_mode"] = not current
        self.save()
        return self.data["restricted_mode"]

    def is_restricted_mode(self) -> bool:
        """ Hozirgi cheklangan rejim holati """
        return self.data.get("restricted_mode", True)

    def get_allowed_users(self) -> Dict[str, Dict[str, Any]]:
        """ Ruxsat berilganlar ro'yxatini olish """
        return self.data.get("allowed_users", {})

    def get_admins(self) -> List[int]:
        """ Administratorlar ID larini olish """
        return self.data.get("admin_ids", [])


# Global instansiya
access_guard = AccessGuard()
