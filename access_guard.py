import json
import logging
import os
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
    - Barcha foydalanuvchilarni to'liq monitoring qilish va ro'yxatga olish
    - Foydalanuvchilarni botdan foydalanishini cheklash (bloklash / ban)
    - Cheklovni bekor qilish va ruxsat berish
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
            "blocked_users": {},      # Cheklangan (bloklangan) foydalanuvchilar
            "pending_requests": {},   # Ruxsat kutayotganlar
            "all_users": {}           # Botdan foydalangan barcha foydalanuvchilar registri
        }
        self.load()
        self._sync_env_admins()

    def _sync_env_admins(self):
        """ .env faylidagi ADMIN_IDS larni yuklash va saqlash """
        changed = False
        self.data.setdefault("admin_ids", [])
        self.data.setdefault("allowed_users", {})
        self.data.setdefault("blocked_users", {})
        self.data.setdefault("pending_requests", {})
        self.data.setdefault("all_users", {})

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
        # Render kabi vaqtinchalik diskda ruxsat ro'yxati yo'qolmasligi uchun:
        # ALLOWED_USER_IDS=id1,id2,... muhit o'zgaruvchisidagi foydalanuvchilar har ishga tushganda qayta qo'shiladi
        for raw in os.getenv("ALLOWED_USER_IDS", "").split(","):
            raw = raw.strip()
            if (raw.isdigit()
                    and raw not in self.data["allowed_users"]
                    and raw not in self.data["blocked_users"]):
                self.data["allowed_users"][raw] = {
                    "name": f"Foydalanuvchi_{raw}",
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
                    self.data.setdefault("blocked_users", {})
                    self.data.setdefault("all_users", {})
                    self.data.setdefault("pending_requests", {})
                    self.data.setdefault("allowed_users", {})
                    self.data.setdefault("admin_ids", [])
                    logger.info("AccessGuard ma'lumotlari muvaffaqiyatli yuklandi.")
            else:
                self.save()
        except Exception as e:
            logger.error(f"AccessGuard ma'lumotlarini yuklashda xatolik: {e}")

    def save(self):
        """ Ma'lumotlarni JSON faylga saqlash """
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"AccessGuard ma'lumotlarini saqlashda xatolik: {e}")

    def is_admin(self, user_id: int) -> bool:
        """ Foydalanuvchi administrator ekanligini tekshirish """
        return user_id in self.data.get("admin_ids", [])

    def is_user_blocked(self, user_id: int) -> bool:
        """ Foydalanuvchi cheklanganmi (bloklanganmi)? """
        return str(user_id) in self.data.get("blocked_users", {})

    def is_user_allowed(self, user_id: int) -> bool:
        """
        Foydalanuvchiga botdan foydalanishga ruxsat bormi?
        1. Agar foydalanuvchi bloklangan bo'lsa -> QAT'IYAN TAQIQLANADI (False).
        2. Administratorlarga har doim ruxsat berilgan (True).
        3. Agar cheklangan rejim (restricted_mode) o'chirilgan bo'lsa -> hamma foydalana oladi (True).
        4. Aks holda -> user_id allowed_users ro'yxatida bo'lishi shart.
        """
        if self.is_user_blocked(user_id):
            return False

        if self.is_admin(user_id):
            return True

        if not self.data.get("restricted_mode", True):
            return True

        return str(user_id) in self.data.get("allowed_users", {})

    def touch_user(self, user_id: int, name: str = "", username: str = ""):
        """
        Foydalanuvchining faolligi va profil ma'lumotlarini qayd qilish.
        Barcha foydalanuvchilar admin panelida to'liq ko'rinib turishi uchun saqlanadi.
        """
        str_id = str(user_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. all_users registrida saqlash
        all_u = self.data.setdefault("all_users", {})
        if str_id not in all_u:
            all_u[str_id] = {
                "name": name or f"Foydalanuvchi_{user_id}",
                "username": username or "",
                "first_seen": now_str,
                "last_active": now_str,
                "messages_count": 1
            }
        else:
            entry = all_u[str_id]
            entry["last_active"] = now_str
            entry["messages_count"] = entry.get("messages_count", 0) + 1
            if name and name != entry.get("name"):
                entry["name"] = name
            if username and username != entry.get("username"):
                entry["username"] = username

        # 2. allowed_users ro'yxatida bo'lsa yangilash
        if str_id in self.data.get("allowed_users", {}):
            u_entry = self.data["allowed_users"][str_id]
            u_entry["last_active"] = now_str
            if name:
                u_entry["name"] = name
            if username:
                u_entry["username"] = username

        # 3. blocked_users da bo'lsa oxirgi urinish vaqtini yangilash
        if str_id in self.data.get("blocked_users", {}):
            b_entry = self.data["blocked_users"][str_id]
            b_entry["last_active"] = now_str

        self.save()

    def block_user(self, user_id: int, reason: str = "", blocked_by: str = "ADMIN") -> bool:
        """
        Foydalanuvchini botdan foydalanish imkoniyatini cheklash (bloklash).
        Foydalanuvchi darhol qora ro'yxatga olinadi va botdan foydalana olmaydi.
        """
        if user_id in ADMIN_IDS:
            logger.warning(f"Asosiy administratorni cheklash mumkin emas: {user_id}")
            return False

        str_id = str(user_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Mavjud ma'lumotlarni aniqlash
        name = f"Foydalanuvchi_{user_id}"
        username = ""
        if str_id in self.data.get("allowed_users", {}):
            info = self.data["allowed_users"].pop(str_id)
            name = info.get("name", name)
            username = info.get("username", username)
        elif str_id in self.data.get("all_users", {}):
            info = self.data["all_users"][str_id]
            name = info.get("name", name)
            username = info.get("username", username)

        # Agar kutilayotgan so'rovlarda bo'lsa, o'chiramiz
        if str_id in self.data.get("pending_requests", {}):
            del self.data["pending_requests"][str_id]

        # Cheklanganlar ro'yxatiga kiritish
        self.data.setdefault("blocked_users", {})[str_id] = {
            "name": name,
            "username": username,
            "blocked_at": now_str,
            "blocked_by": blocked_by,
            "reason": reason or "Administrator tomonidan cheklandi",
            "last_active": now_str
        }

        self.save()
        logger.info(f"Foydalanuvchi cheklandi/bloklandi: {user_id} ({name}) by {blocked_by}")
        return True

    def unblock_user(self, user_id: int, unblocked_by: str = "ADMIN") -> bool:
        """
        Cheklangan foydalanuvchining cheklovini bekor qilish va yana botdan foydalanishiga ruxsat berish.
        """
        str_id = str(user_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        name = f"Foydalanuvchi_{user_id}"
        username = ""

        if str_id in self.data.get("blocked_users", {}):
            info = self.data["blocked_users"].pop(str_id)
            name = info.get("name", name)
            username = info.get("username", username)
        elif str_id in self.data.get("all_users", {}):
            info = self.data["all_users"][str_id]
            name = info.get("name", name)
            username = info.get("username", username)

        # Ruxsat berilganlar ro'yxatiga qo'shamiz
        self.data.setdefault("allowed_users", {})[str_id] = {
            "name": name,
            "username": username,
            "added_at": now_str,
            "added_by": unblocked_by,
            "last_active": now_str
        }

        self.save()
        logger.info(f"Foydalanuvchi cheklovi bekor qilindi: {user_id} ({name}) by {unblocked_by}")
        return True

    def add_user(self, user_id: int, name: str = "", username: str = "", added_by: str = "ADMIN") -> bool:
        """ Foydalanuvchini ruxsat berilganlar ro'yxatiga qo'shish """
        str_id = str(user_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Agar bloklangan bo'lsa, blokdan chiqaramiz
        if str_id in self.data.get("blocked_users", {}):
            del self.data["blocked_users"][str_id]

        self.data.setdefault("allowed_users", {})[str_id] = {
            "name": name or f"Foydalanuvchi_{user_id}",
            "username": username or "",
            "added_at": now_str,
            "added_by": added_by,
            "last_active": now_str
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
        changed = False
        if str_id in self.data.get("allowed_users", {}):
            del self.data["allowed_users"][str_id]
            changed = True
        if str_id in self.data.get("pending_requests", {}):
            del self.data["pending_requests"][str_id]
            changed = True
        if str_id in self.data.get("blocked_users", {}):
            del self.data["blocked_users"][str_id]
            changed = True
        # Agar admin bo'lsa (lekin ENV dagi asosiy admin bo'lmasa), admin_ids dan ham chiqarish
        if user_id in self.data.get("admin_ids", []) and user_id not in ADMIN_IDS:
            self.data["admin_ids"].remove(user_id)
            changed = True
        if changed:
            self.save()
            logger.info(f"Foydalanuvchi o'chirildi: {user_id}")
            return True
        return False

    def get_user_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """ Foydalanuvchi haqida to'liq ma'lumot olish """
        str_id = str(user_id)
        info = None
        status = "guest"

        if str_id in self.data.get("blocked_users", {}):
            info = dict(self.data["blocked_users"][str_id])
            status = "blocked"
        elif str_id in self.data.get("allowed_users", {}):
            info = dict(self.data["allowed_users"][str_id])
            status = "allowed"
        elif str_id in self.data.get("all_users", {}):
            info = dict(self.data["all_users"][str_id])
            status = "guest"

        if info:
            info["user_id"] = user_id
            info["is_admin"] = self.is_admin(user_id)
            info["is_blocked"] = self.is_user_blocked(user_id)
            info["status"] = "admin" if info["is_admin"] else status
            return info
        return None

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
        if user_id in self.data["admin_ids"] and user_id not in ADMIN_IDS:
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

    def get_blocked_users(self) -> Dict[str, Dict[str, Any]]:
        """ Cheklangan (bloklangan) foydalanuvchilar ro'yxatini olish """
        return self.data.get("blocked_users", {})

    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """ Barcha bot foydalanuvchilari birlashtirilgan to'liq ro'yxati """
        unified = {}

        # 1. all_users (har qanday murojaat qilganlar)
        for uid_str, info in self.data.get("all_users", {}).items():
            unified[uid_str] = dict(info)
            unified[uid_str]["status"] = "guest"

        # 2. allowed_users
        for uid_str, info in self.data.get("allowed_users", {}).items():
            if uid_str not in unified:
                unified[uid_str] = dict(info)
            else:
                unified[uid_str].update(info)
            unified[uid_str]["status"] = "allowed"

        # 3. blocked_users
        for uid_str, info in self.data.get("blocked_users", {}).items():
            if uid_str not in unified:
                unified[uid_str] = dict(info)
            else:
                unified[uid_str].update(info)
            unified[uid_str]["status"] = "blocked"

        # 4. adminlar
        for admin_id in self.data.get("admin_ids", []):
            str_id = str(admin_id)
            if str_id in unified:
                unified[str_id]["status"] = "admin"
                unified[str_id]["is_admin"] = True

        return unified

    def get_admins(self) -> List[int]:
        """ Administratorlar ID larini olish """
        return self.data.get("admin_ids", [])


# Global instansiya
access_guard = AccessGuard()
