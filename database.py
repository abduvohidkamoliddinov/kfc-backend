"""
database.py — JSON fayllar orqali data saqlash (Railway Volume ready)

✅ DATA_DIR qo‘llab-quvvatlaydi:
- Agar DATA_DIR=/data bo‘lsa → hamma jsonlar /data ga yoziladi (Persistent Volume)
- Agar DATA_DIR berilmasa → loyiha papkasida ishlaydi (local/dev)

✅ Order counter reset bo‘lmaydi:
- order_counter.json + orders.json dagi eng katta ID bilan sync qiladi
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
#  DATA DIR (Railway volume)
# ═══════════════════════════════════════════════════════════════

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = Path(os.getenv("DB_FILE", str(DATA_DIR / "orders.json"))).resolve()


# ═══════════════════════════════════════════════════════════════
#  ATOMIC WRITE helper
# ═══════════════════════════════════════════════════════════════

def _atomic_write(path: Path, content: str) -> None:
    """
    Atomik yozish (yarim yozilib qolishdan saqlaydi).
    Windows/Linux mos.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ═══════════════════════════════════════════════════════════════
#  ORDERS (orders.json)
# ═══════════════════════════════════════════════════════════════

_lock = threading.Lock()  # bir vaqtda yozishdan himoya


def _load() -> list[dict]:
    if not DB_FILE.exists():
        return []
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(orders: list[dict]) -> None:
    _atomic_write(DB_FILE, json.dumps(orders, ensure_ascii=False, indent=2))


def get_all(
    status: str | None = None,
    phone: str | None = None,
    limit: int = 50,
    offset: int = 0
) -> list[dict]:
    with _lock:
        orders = _load()

    if status:
        orders = [o for o in orders if o.get("status") == status]
    if phone:
        orders = [o for o in orders if o.get("phone") == phone]

    orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return orders[offset: offset + limit]


def get_by_id(order_id: str) -> dict | None:
    with _lock:
        orders = _load()
    return next((o for o in orders if o.get("id") == order_id), None)


def create(order: dict) -> dict:
    with _lock:
        orders = _load()
        if any(o.get("id") == order.get("id") for o in orders):
            raise ValueError("DUPLICATE_ID")

        order["created_at"] = order.get("created_at") or datetime.utcnow().isoformat()
        order["status"] = order.get("status") or "pending"
        order.setdefault("tg_msg_id", None)
        order.setdefault("tg_user_id", None)

        orders.append(order)
        _save(orders)

    return order


def update_status(order_id: str, status: str) -> dict | None:
    with _lock:
        orders = _load()
        for o in orders:
            if o.get("id") == order_id:
                o["status"] = status
                _save(orders)
                return o
    return None


def update_tg_msg_id(order_id: str, msg_id: int) -> None:
    with _lock:
        orders = _load()
        for o in orders:
            if o.get("id") == order_id:
                o["tg_msg_id"] = msg_id
                _save(orders)
                return


def count(status: str | None = None, phone: str | None = None) -> int:
    with _lock:
        orders = _load()

    if status:
        orders = [o for o in orders if o.get("status") == status]
    if phone:
        orders = [o for o in orders if o.get("phone") == phone]
    return len(orders)


def stats_today() -> dict:
    today = datetime.utcnow().date().isoformat()
    with _lock:
        orders = _load()

    today_orders = [o for o in orders if str(o.get("created_at", "")).startswith(today)]
    return {
        "total":     len(today_orders),
        "done":      sum(1 for o in today_orders if o.get("status") == "done"),
        "pending":   sum(1 for o in today_orders if o.get("status") == "pending"),
        "cancelled": sum(1 for o in today_orders if o.get("status") == "cancelled"),
        "revenue":   sum(
            int(o.get("total", 0) or 0)
            for o in today_orders
            if o.get("status") not in ("cancelled",)
        ),
    }


def stats_monthly() -> dict:
    MONTHS_UZ = {
        "01": "Yanvar",  "02": "Fevral",  "03": "Mart",
        "04": "Aprel",   "05": "May",     "06": "Iyun",
        "07": "Iyul",    "08": "Avgust",  "09": "Sentabr",
        "10": "Oktabr",  "11": "Noyabr",  "12": "Dekabr",
    }

    now = datetime.utcnow()
    current_month = now.strftime("%Y-%m")
    year, mon_num = current_month.split("-")
    month_label = f"{MONTHS_UZ.get(mon_num, mon_num)} {year}"

    with _lock:
        orders = _load()

    month_orders = [o for o in orders if str(o.get("created_at", "")).startswith(current_month)]

    user_map: dict[str, dict] = {}
    for o in month_orders:
        phone = o.get("phone", "unknown")
        if phone not in user_map:
            user_map[phone] = {
                "name":      (o.get("customer_name", "") or "").strip() or "—",
                "phone":     phone,
                "total":     0,
                "done":      0,
                "cancelled": 0,
                "revenue":   0,
            }

        u = user_map[phone]
        u["total"] += 1

        st = o.get("status", "")
        if st == "done":
            u["done"] += 1
            u["revenue"] += int(o.get("total", 0) or 0)
        elif st == "cancelled":
            u["cancelled"] += 1

    users_sorted = sorted(user_map.values(), key=lambda x: x["total"], reverse=True)

    return {
        "month_label": month_label,
        "total":       len(month_orders),
        "done":        sum(1 for o in month_orders if o.get("status") == "done"),
        "cancelled":   sum(1 for o in month_orders if o.get("status") == "cancelled"),
        "revenue":     sum(int(o.get("total", 0) or 0) for o in month_orders if o.get("status") == "done"),
        "users":       users_sorted,
    }


# ═══════════════════════════════════════════════════════════════
#  ORDER COUNTER (order_counter.json) — reset bo‘lmasin
# ═══════════════════════════════════════════════════════════════

_COUNTER_FILE = DATA_DIR / "order_counter.json"
_counter_lock = threading.Lock()


def _counter_load() -> int:
    if _COUNTER_FILE.exists():
        try:
            return int(json.loads(_COUNTER_FILE.read_text(encoding="utf-8")).get("last", 0))
        except Exception:
            return 0
    return 0


def _max_order_number_from_orders() -> int:
    """
    orders.json ichidan eng katta raqamli id ni topadi.
    ID raqam bo‘lsa (masalan '0001', '0123') ishlaydi.
    """
    try:
        with _lock:
            orders = _load()
        mx = 0
        for o in orders:
            oid = str(o.get("id", "")).strip()
            if oid.isdigit():
                mx = max(mx, int(oid))
        return mx
    except Exception:
        return 0


def next_order_number() -> int:
    """
    Deploy/restart bo‘lsa ham 0001ga qaytmasin:
    - order_counter.json
    - orders.json dagi max id
    ikkalasidan kattasini olib +1 qiladi.
    """
    with _counter_lock:
        last_file = _counter_load()
        last_db = _max_order_number_from_orders()
        last = max(last_file, last_db)
        num = last + 1
        _atomic_write(_COUNTER_FILE, json.dumps({"last": num}, ensure_ascii=False))
        return num


def order_id_from_number(num: int) -> str:
    return str(num).zfill(4)


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM USERS (telegram_users.json)
# ═══════════════════════════════════════════════════════════════

_TG_FILE = DATA_DIR / "telegram_users.json"
_tg_lock = threading.Lock()


def _tg_load() -> list[dict]:
    if _TG_FILE.exists():
        try:
            return json.loads(_TG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _tg_save(users: list[dict]) -> None:
    _atomic_write(_TG_FILE, json.dumps(users, ensure_ascii=False, indent=2))


def get_telegram_user(phone: str) -> dict | None:
    with _tg_lock:
        users = _tg_load()
    return next((u for u in users if u.get("phone") == phone), None)


def get_telegram_user_by_chat_id(chat_id) -> dict | None:
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
                u["chat_id"] = str(chat_id)
                u["username"] = username
                u["full_name"] = full_name
                u.setdefault("coins", 0)
                _tg_save(users)
                return u

        user = {
            "phone": phone,
            "chat_id": str(chat_id),
            "username": username,
            "full_name": full_name,
            "coins": 0,
        }
        users.append(user)
        _tg_save(users)
        return user


def update_telegram_user_coins(phone: str, coins: int) -> None:
    with _tg_lock:
        users = _tg_load()
        for u in users:
            if u.get("phone") == phone:
                u["coins"] = coins
                _tg_save(users)
                return


# ═══════════════════════════════════════════════════════════════
#  OTP CODES (otp_codes.json)
# ═══════════════════════════════════════════════════════════════

_OTP_FILE = DATA_DIR / "otp_codes.json"
_otp_lock = threading.Lock()


def _otp_load() -> list[dict]:
    if _OTP_FILE.exists():
        try:
            return json.loads(_OTP_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _otp_save(codes: list[dict]) -> None:
    _atomic_write(_OTP_FILE, json.dumps(codes, ensure_ascii=False, indent=2))


def get_otp(phone: str) -> dict | None:
    with _otp_lock:
        codes = _otp_load()
    return next((c for c in codes if c.get("phone") == phone), None)


def save_otp(phone: str, code: str, expires_at: float, mode: str = "login") -> dict:
    with _otp_lock:
        codes = _otp_load()
        codes = [c for c in codes if c.get("phone") != phone]
        record = {
            "phone": phone,
            "code": code,
            "expires_at": expires_at,
            "attempts": 0,
            "mode": mode,
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
                c["attempts"] = int(c.get("attempts", 0) or 0) + 1
                _otp_save(codes)
                return c["attempts"]
    return 0


# ═══════════════════════════════════════════════════════════════
#  REGISTERED USERS (registered_users.json)
# ═══════════════════════════════════════════════════════════════

_USERS_FILE = DATA_DIR / "registered_users.json"
_users_lock = threading.Lock()


def _users_load() -> list[dict]:
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _users_save(users: list[dict]) -> None:
    _atomic_write(_USERS_FILE, json.dumps(users, ensure_ascii=False, indent=2))


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
                u["lastName"] = last_name
                _users_save(users)
                return u

        user = {"phone": phone, "firstName": first_name, "lastName": last_name}
        users.append(user)
        _users_save(users)
        return user


# ═══════════════════════════════════════════════════════════════
#  COINS (coins.json)
# ═══════════════════════════════════════════════════════════════

_COINS_FILE = DATA_DIR / "coins.json"
_coins_lock = threading.Lock()


def _coins_load() -> list[dict]:
    if _COINS_FILE.exists():
        try:
            return json.loads(_COINS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _coins_save(data: list[dict]) -> None:
    _atomic_write(_COINS_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def get_coins(phone: str) -> int:
    with _coins_lock:
        data = _coins_load()
    rec = next((r for r in data if r.get("phone") == phone), None)
    return int(rec.get("balance", 0) or 0) if rec else 0


def add_coins(phone: str, amount: int, order_id: str) -> int:
    now_str = datetime.utcnow().isoformat()
    with _coins_lock:
        data = _coins_load()
        rec = next((r for r in data if r.get("phone") == phone), None)
        if rec:
            rec["balance"] = int(rec.get("balance", 0) or 0) + int(amount)
            rec.setdefault("history", []).append({
                "type": "earn",
                "amount": int(amount),
                "order_id": order_id,
                "at": now_str,
            })
        else:
            rec = {
                "phone": phone,
                "balance": int(amount),
                "history": [{
                    "type": "earn",
                    "amount": int(amount),
                    "order_id": order_id,
                    "at": now_str,
                }],
            }
            data.append(rec)

        _coins_save(data)
        return int(rec["balance"])


def spend_coins(phone: str, amount: int, order_id: str) -> int:
    now_str = datetime.utcnow().isoformat()
    with _coins_lock:
        data = _coins_load()
        rec = next((r for r in data if r.get("phone") == phone), None)
        if not rec or int(rec.get("balance", 0) or 0) < int(amount):
            raise ValueError("Yetarli coin yo'q")

        rec["balance"] = int(rec.get("balance", 0) or 0) - int(amount)
        rec.setdefault("history", []).append({
            "type": "spend",
            "amount": int(amount),
            "order_id": order_id,
            "at": now_str,
        })

        _coins_save(data)
        return int(rec["balance"])


# ═══════════════════════════════════════════════════════════════
#  MENU CATEGORIES (menu_categories.json)
# ═══════════════════════════════════════════════════════════════

MENU_CATEGORIES_FILE = DATA_DIR / "menu_categories.json"
_menu_cat_lock = threading.Lock()


def _menu_categories_load() -> list[dict]:
    if MENU_CATEGORIES_FILE.exists():
        try:
            return json.loads(MENU_CATEGORIES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _menu_categories_save(cats: list[dict]) -> None:
    _atomic_write(MENU_CATEGORIES_FILE, json.dumps(cats, ensure_ascii=False, indent=2))


def menu_next_category_id() -> int:
    cats = _menu_categories_load()
    if not cats:
        return 1
    return max(int(c.get("id", 0)) for c in cats) + 1


def menu_get_categories(active_only: bool = False) -> list[dict]:
    with _menu_cat_lock:
        cats = _menu_categories_load()
    if active_only:
        cats = [c for c in cats if c.get("is_active", True)]
    cats.sort(key=lambda c: c.get("sort_order", 0))
    return cats


def menu_create_category(cat: dict) -> dict:
    with _menu_cat_lock:
        cats = _menu_categories_load()
        cat["id"] = menu_next_category_id()
        cats.append(cat)
        _menu_categories_save(cats)
    return cat


def menu_update_category(cat_id: int, patch: dict) -> dict | None:
    with _menu_cat_lock:
        cats = _menu_categories_load()
        for c in cats:
            if int(c.get("id", 0)) == cat_id:
                for k, v in patch.items():
                    if k != "id":
                        c[k] = v
                _menu_categories_save(cats)
                return c
    return None


def menu_delete_category(cat_id: int) -> bool:
    """Delete category. Raises ValueError if foods reference it."""
    with _menu_cat_lock:
        cats = _menu_categories_load()
        target = next((c for c in cats if int(c.get("id", 0)) == cat_id), None)
        if not target:
            return False
        # Check if any food uses this category
        foods = _menu_foods_load()
        key = target.get("key", "")
        if any(f.get("category") == key for f in foods):
            raise ValueError("CATEGORY_HAS_FOODS")
        cats = [c for c in cats if int(c.get("id", 0)) != cat_id]
        _menu_categories_save(cats)
    return True


# ═══════════════════════════════════════════════════════════════
#  MENU FOODS (menu_foods.json)
# ═══════════════════════════════════════════════════════════════

MENU_FOODS_FILE = DATA_DIR / "menu_foods.json"
_menu_food_lock = threading.Lock()


def _menu_foods_load() -> list[dict]:
    if MENU_FOODS_FILE.exists():
        try:
            return json.loads(MENU_FOODS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _menu_foods_save(foods: list[dict]) -> None:
    _atomic_write(MENU_FOODS_FILE, json.dumps(foods, ensure_ascii=False, indent=2))


def menu_next_food_id() -> int:
    foods = _menu_foods_load()
    if not foods:
        return 1
    return max(int(f.get("id", 0)) for f in foods) + 1


def menu_get_foods(
    category: str | None = None,
    search: str | None = None,
    active_only: bool = False,
) -> list[dict]:
    with _menu_food_lock:
        foods = _menu_foods_load()
    if active_only:
        foods = [f for f in foods if f.get("is_active", True)]
    if category:
        foods = [f for f in foods if f.get("category") == category]
    if search:
        q = search.lower()
        foods = [f for f in foods if q in (f.get("name", "") or "").lower()]
    return foods


def menu_create_food(food: dict) -> dict:
    with _menu_food_lock:
        foods = _menu_foods_load()
        food["id"] = menu_next_food_id()
        food["created_at"] = datetime.utcnow().isoformat()
        foods.append(food)
        _menu_foods_save(foods)
    return food


def menu_update_food(food_id: int, patch: dict) -> dict | None:
    with _menu_food_lock:
        foods = _menu_foods_load()
        for f in foods:
            if int(f.get("id", 0)) == food_id:
                for k, v in patch.items():
                    if k != "id":
                        f[k] = v
                _menu_foods_save(foods)
                return f
    return None


def menu_delete_food(food_id: int) -> bool:
    with _menu_food_lock:
        foods = _menu_foods_load()
        orig_len = len(foods)
        foods = [f for f in foods if int(f.get("id", 0)) != food_id]
        if len(foods) == orig_len:
            return False
        _menu_foods_save(foods)
    return True
