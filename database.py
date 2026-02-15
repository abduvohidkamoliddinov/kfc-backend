"""
database.py — JSON fayl orqali zakazlarni saqlash
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

DB_FILE = Path(os.getenv("DB_FILE", "orders.json"))

_lock = threading.Lock()  # bir vaqtda yozishdan himoya


def _load() -> list[dict]:
    if not DB_FILE.exists():
        return []
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save(orders: list[dict]) -> None:
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


# ── Public API ───────────────────────────────────────────────

def get_all(status: str | None = None, phone: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    with _lock:
        orders = _load()
    if status:
        orders = [o for o in orders if o["status"] == status]
    if phone:
        orders = [o for o in orders if o.get("phone") == phone]
    orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orders[offset: offset + limit]


def get_by_id(order_id: str) -> dict | None:
    with _lock:
        orders = _load()
    return next((o for o in orders if o["id"] == order_id), None)


def create(order: dict) -> dict:
    with _lock:
        orders = _load()
        if any(o["id"] == order["id"] for o in orders):
            raise ValueError("DUPLICATE_ID")
        order["created_at"] = order.get("created_at") or datetime.utcnow().isoformat()
        order["status"] = "pending"
        order["tg_msg_id"] = None
        order.setdefault("tg_user_id", None)
        orders.append(order)
        _save(orders)
    return order


def update_status(order_id: str, status: str) -> dict | None:
    with _lock:
        orders = _load()
        for o in orders:
            if o["id"] == order_id:
                o["status"] = status
                _save(orders)
                return o
    return None


def update_tg_msg_id(order_id: str, msg_id: int) -> None:
    with _lock:
        orders = _load()
        for o in orders:
            if o["id"] == order_id:
                o["tg_msg_id"] = msg_id
                _save(orders)
                return


def count(status: str | None = None, phone: str | None = None) -> int:
    with _lock:
        orders = _load()
    if status:
        orders = [o for o in orders if o["status"] == status]
    if phone:
        orders = [o for o in orders if o.get("phone") == phone]
    return len(orders)


def stats_today() -> dict:
    today = datetime.utcnow().date().isoformat()
    with _lock:
        orders = _load()
    today_orders = [o for o in orders if o.get("created_at", "").startswith(today)]
    return {
        "total":     len(today_orders),
        "done":      sum(1 for o in today_orders if o["status"] == "done"),
        "pending":   sum(1 for o in today_orders if o["status"] == "pending"),
        "cancelled": sum(1 for o in today_orders if o["status"] == "cancelled"),
        "revenue":   sum(
            o.get("total", 0)
            for o in today_orders
            if o["status"] not in ("cancelled",)
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM USERS — botga /start bosgan foydalanuvchilar
# ═══════════════════════════════════════════════════════════════

_TG_FILE = Path(__file__).parent / "telegram_users.json"
_tg_lock = threading.Lock()

def _tg_load() -> list[dict]:
    if _TG_FILE.exists():
        try:
            return json.loads(_TG_FILE.read_text())
        except Exception:
            return []
    return []

def _tg_save(users: list[dict]) -> None:
    _TG_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2))

def get_telegram_user(phone: str) -> dict | None:
    with _tg_lock:
        users = _tg_load()
    return next((u for u in users if u.get("phone") == phone), None)

def save_telegram_user(phone: str, chat_id, username: str | None = None, full_name: str | None = None) -> dict:
    with _tg_lock:
        users = _tg_load()
        for u in users:
            if u.get("phone") == phone:
                u["chat_id"] = str(chat_id)
                u["username"] = username
                u["full_name"] = full_name
                _tg_save(users)
                return u
        user = {"phone": phone, "chat_id": str(chat_id), "username": username, "full_name": full_name}
        users.append(user)
        _tg_save(users)
    return user

def get_telegram_user_by_chat_id(chat_id: int) -> dict | None:
    with _tg_lock:
        users = _tg_load()
    return next((u for u in users if u.get("chat_id") == chat_id), None)


# ═══════════════════════════════════════════════════════════════
#  OTP CODES — tasdiqlash kodlari
# ═══════════════════════════════════════════════════════════════

_OTP_FILE = Path(__file__).parent / "otp_codes.json"
_otp_lock = threading.Lock()

def _otp_load() -> list[dict]:
    if _OTP_FILE.exists():
        try:
            return json.loads(_OTP_FILE.read_text())
        except Exception:
            return []
    return []

def _otp_save(codes: list[dict]) -> None:
    _OTP_FILE.write_text(json.dumps(codes, ensure_ascii=False, indent=2))

def get_otp(phone: str) -> dict | None:
    with _otp_lock:
        codes = _otp_load()
    return next((c for c in codes if c.get("phone") == phone), None)

def save_otp(phone: str, code: str, expires_at: float) -> dict:
    with _otp_lock:
        codes = _otp_load()
        codes = [c for c in codes if c.get("phone") != phone]
        record = {"phone": phone, "code": code, "expires_at": expires_at, "attempts": 0}
        codes.append(record)
        _otp_save(codes)
    return record

def delete_otp(phone: str) -> None:
    with _otp_lock:
        codes = _otp_load()
        codes = [c for c in codes if c.get("phone") != phone]
        _otp_save(codes)

def increment_otp_attempts(phone: str) -> int:
    with _otp_lock:
        codes = _otp_load()
        for c in codes:
            if c.get("phone") == phone:
                c["attempts"] = c.get("attempts", 0) + 1
                _otp_save(codes)
                return c["attempts"]
    return 0
