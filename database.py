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
