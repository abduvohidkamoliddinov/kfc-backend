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


def stats_monthly() -> dict:
    """
    Joriy oy statistikasi — bot.py dagi handle_statistics_btn uchun.

    Qaytaradi:
      month_label : str   — "Fevral 2026"
      total       : int   — jami zakazlar
      done        : int   — yetkazildi
      cancelled   : int   — bekor qilindi
      revenue     : int   — daromad (UZS)
      users       : list  — har user uchun {name, phone, total, done, cancelled, revenue}
                            zakaz soniga qarab kamayish tartibida saralangan
    """
    MONTHS_UZ = {
        "01": "Yanvar",  "02": "Fevral",  "03": "Mart",
        "04": "Aprel",   "05": "May",     "06": "Iyun",
        "07": "Iyul",    "08": "Avgust",  "09": "Sentabr",
        "10": "Oktabr",  "11": "Noyabr",  "12": "Dekabr",
    }

    now           = datetime.utcnow()
    current_month = now.strftime("%Y-%m")
    year, mon_num = current_month.split("-")
    month_label   = f"{MONTHS_UZ.get(mon_num, mon_num)} {year}"

    with _lock:
        orders = _load()

    month_orders = [
        o for o in orders
        if o.get("created_at", "").startswith(current_month)
    ]

    # Har user uchun statistika
    user_map: dict[str, dict] = {}
    for o in month_orders:
        phone = o.get("phone", "unknown")
        if phone not in user_map:
            user_map[phone] = {
                "name":      o.get("customer_name", "").strip() or "—",
                "phone":     phone,
                "total":     0,
                "done":      0,
                "cancelled": 0,
                "revenue":   0,
            }
        u = user_map[phone]
        u["total"] += 1
        status = o.get("status", "")
        if status == "done":
            u["done"]    += 1
            u["revenue"] += o.get("total", 0)
        elif status == "cancelled":
            u["cancelled"] += 1

    users_sorted = sorted(user_map.values(), key=lambda x: x["total"], reverse=True)

    return {
        "month_label": month_label,
        "total":       len(month_orders),
        "done":        sum(1 for o in month_orders if o.get("status") == "done"),
        "cancelled":   sum(1 for o in month_orders if o.get("status") == "cancelled"),
        "revenue":     sum(
            o.get("total", 0) for o in month_orders
            if o.get("status") == "done"
        ),
        "users":       users_sorted,
    }


# ═══════════════════════════════════════════════════════════════
#  ORDER COUNTER — ketma-ket tartib raqam (#0001, #0002 ...)
# ═══════════════════════════════════════════════════════════════

_COUNTER_FILE = Path(__file__).parent / "order_counter.json"
_counter_lock = threading.Lock()


def _counter_load() -> int:
    if _COUNTER_FILE.exists():
        try:
            return int(json.loads(_COUNTER_FILE.read_text()).get("last", 0))
        except Exception:
            return 0
    return 0


def next_order_number() -> int:
    """Atomik ravishda keyingi tartib raqamni qaytaradi (1, 2, 3 ...)."""
    with _counter_lock:
        num = _counter_load() + 1
        _COUNTER_FILE.write_text(json.dumps({"last": num}))
    return num


def order_id_from_number(num: int) -> str:
    """Tartib raqamdan order ID hosil qiladi: 1 → '0001', 999 → '0999'."""
    return str(num).zfill(4)


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


def get_telegram_user_by_chat_id(chat_id) -> dict | None:
    """chat_id bo'yicha userni topadi (str yoki int bo'lishi mumkin)."""
    chat_id_str = str(chat_id)
    with _tg_lock:
        users = _tg_load()
    return next((u for u in users if str(u.get("chat_id", "")) == chat_id_str), None)


def save_telegram_user(
    phone: str,
    chat_id,
    username: str | None = None,
    full_name: str | None = None,
) -> dict:
    with _tg_lock:
        users = _tg_load()
        for u in users:
            if u.get("phone") == phone:
                u["chat_id"]   = str(chat_id)
                u["username"]  = username
                u["full_name"] = full_name
                _tg_save(users)
                return u
        user = {
            "phone":     phone,
            "chat_id":   str(chat_id),
            "username":  username,
            "full_name": full_name,
            "coins":     0,
        }
        users.append(user)
        _tg_save(users)
    return user


def update_telegram_user_coins(phone: str, coins: int) -> None:
    """telegram_users.json dagi coin balansini yangilaydi."""
    with _tg_lock:
        users = _tg_load()
        for u in users:
            if u.get("phone") == phone:
                u["coins"] = coins
                _tg_save(users)
                return


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


def save_otp(
    phone: str,
    code: str,
    expires_at: float,
    mode: str = "login",
) -> dict:
    with _otp_lock:
        codes = _otp_load()
        codes = [c for c in codes if c.get("phone") != phone]
        record = {
            "phone":      phone,
            "code":       code,
            "expires_at": expires_at,
            "attempts":   0,
            "mode":       mode,
        }
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


# ═══════════════════════════════════════════════════════════════
#  REGISTERED USERS — signup da kiritilgan ism/familya
# ═══════════════════════════════════════════════════════════════

_USERS_FILE = Path(__file__).parent / "registered_users.json"
_users_lock = threading.Lock()


def _users_load() -> list[dict]:
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text())
        except Exception:
            return []
    return []


def _users_save(users: list[dict]) -> None:
    _USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2))


def get_registered_user(phone: str) -> dict | None:
    with _users_lock:
        users = _users_load()
    return next((u for u in users if u.get("phone") == phone), None)


def save_registered_user(phone: str, first_name: str, last_name: str) -> dict:
    with _users_lock:
        users = _users_load()
        for u in users:
            if u.get("phone") == phone:
                u["firstName"] = first_name
                u["lastName"]  = last_name
                _users_save(users)
                return u
        user = {"phone": phone, "firstName": first_name, "lastName": last_name}
        users.append(user)
        _users_save(users)
    return user


# ═══════════════════════════════════════════════════════════════
#  COINS — foydalanuvchi coinlari
# ═══════════════════════════════════════════════════════════════

_COINS_FILE = Path(__file__).parent / "coins.json"
_coins_lock = threading.Lock()


def _coins_load() -> list[dict]:
    if _COINS_FILE.exists():
        try:
            return json.loads(_COINS_FILE.read_text())
        except Exception:
            return []
    return []


def _coins_save(data: list[dict]) -> None:
    _COINS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def get_coins(phone: str) -> int:
    """Foydalanuvchining joriy coin balansini qaytaradi."""
    with _coins_lock:
        data = _coins_load()
    rec = next((r for r in data if r.get("phone") == phone), None)
    return rec.get("balance", 0) if rec else 0


def add_coins(phone: str, amount: int, order_id: str) -> int:
    """Coin qo'shadi va yangi balansni qaytaradi."""
    now_str = datetime.utcnow().isoformat()
    with _coins_lock:
        data = _coins_load()
        rec  = next((r for r in data if r.get("phone") == phone), None)
        if rec:
            rec["balance"] = rec.get("balance", 0) + amount
            rec.setdefault("history", []).append({
                "type": "earn", "amount": amount,
                "order_id": order_id, "at": now_str,
            })
        else:
            rec = {
                "phone":   phone,
                "balance": amount,
                "history": [{"type": "earn", "amount": amount,
                             "order_id": order_id, "at": now_str}],
            }
            data.append(rec)
        _coins_save(data)
    return rec["balance"]


def spend_coins(phone: str, amount: int, order_id: str) -> int:
    """Coin sarflaydi va yangi balansni qaytaradi."""
    now_str = datetime.utcnow().isoformat()
    with _coins_lock:
        data = _coins_load()
        rec  = next((r for r in data if r.get("phone") == phone), None)
        if not rec or rec.get("balance", 0) < amount:
            raise ValueError("Yetarli coin yo'q")
        rec["balance"] -= amount
        rec.setdefault("history", []).append({
            "type": "spend", "amount": amount,
            "order_id": order_id, "at": now_str,
        })
        _coins_save(data)
    return rec["balance"]
