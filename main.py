# main.py — FastAPI backend + Telegram bot (PTB 21.x) lifecycle ichida
import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, field_validator

import database as db
from database import (
    save_registered_user,
    get_registered_user,
    get_coins,
    spend_coins,
)
# ───────────────────────────────────────────────────────────────
# Telegram bot lifecycle (FastAPI lifespan)
# ───────────────────────────────────────────────────────────────

_bot_app = None
_bot_polling_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app, _bot_polling_task

    token = os.getenv("BOT_TOKEN", "")
    if token:
        _bot_app = None
        await _bot_app.initialize()
        await _bot_app.start()

        async def _poll():
            # updater start_polling PTB 21.x
            await _bot_app.updater.start_polling(drop_pending_updates=True)

        _bot_polling_task = asyncio.create_task(_poll())
        print("🤖 Admin bot ishga tushdi")
    else:
        print("⚠️ BOT_TOKEN yo'q — bot ishlamaydi")

    yield

    if _bot_app:
        try:
            await _bot_app.updater.stop()
        except Exception:
            pass

        if _bot_polling_task:
            _bot_polling_task.cancel()

        await _bot_app.stop()
        await _bot_app.shutdown()


app = FastAPI(title="KFC Backend", lifespan=lifespan)

# ───────────────────────────────────────────────────────────────
# CORS (har qanday response'ga header qo'shadi)
# ───────────────────────────────────────────────────────────────

class CORSEverywhere(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return JSONResponse(
                content={"ok": True},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "*",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Max-Age": "86400",
                },
            )
        try:
            response = await call_next(request)
        except Exception as e:
            response = JSONResponse(status_code=500, content={"detail": str(e)})

        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response


app.add_middleware(CORSEverywhere)

# ───────────────────────────────────────────────────────────────
# Pydantic modellari
# ───────────────────────────────────────────────────────────────

def _norm_phone(p: str | None) -> str | None:
    if not p:
        return None
    p = p.strip()
    if not p:
        return None
    if not p.startswith("+"):
        p = "+" + p
    return p


class OrderItem(BaseModel):
    # front ba'zida faqat fullName yuboradi, shuning uchun name optional
    name: str | None = None
    fullName: str | None = None
    quantity: int
    price: int

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v):
        if v <= 0:
            raise ValueError("quantity musbat bo'lishi kerak")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v):
        if v < 0:
            raise ValueError("price manfiy bo'lmasin")
        return v


class OrderCreate(BaseModel):
    id: str | None = None  # e'tiborsiz qoldiriladi, db counter ishlaydi
    items: list[OrderItem]
    address: str
    total: int
    date: str | None = None
    tg_user_id: int | None = None
    phone: str | None = None
    customer_name: str | None = None
    coins_used: int | None = None
    payment: str | None = "naqt"
    extra_phone: str | None = None
    comment: str | None = None


    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v):
        if not v:
            raise ValueError("items bosh bo'lmasin")
        for it in v:
            has_name = (it.name and it.name.strip()) or (it.fullName and it.fullName.strip())
            if not has_name:
                raise ValueError("Har bir itemda name yoki fullName bo'lishi shart")
        return v

    @field_validator("total")
    @classmethod
    def min_total(cls, v):
        if v < 50000:
            raise ValueError("Minimal zakaz 50,000 UZS")
        return v


class OtpSendRequest(BaseModel):
    phone: str
    mode: str = "login"  # signup | login


class OtpVerifyRequest(BaseModel):
    phone: str
    code: str
    mode: str = "login"  # signup | login


class ProfileSaveRequest(BaseModel):
    phone: str
    firstName: str
    lastName: str


# ───────────────────────────────────────────────────────────────
# Helper: admin notify after cancel window
# ───────────────────────────────────────────────────────────────

async def notify_after_delay(order_id: str, delay: int = 65):
    """
    Cancel oynasi 55s. Admin 65s keyin ko'radi.
    """
    await asyncio.sleep(delay)
    order = db.get_by_id(order_id)
    if order and order.get("status") != "cancelled":
        await notify_new_order(order)


# ───────────────────────────────────────────────────────────────
# Endpointlar
# ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.get("/api/check-phone")
async def check_phone(phone: str):
    p = _norm_phone(phone)
    if not p:
        raise HTTPException(400, "phone required")
    return {"exists": get_registered_user(p) is not None}


@app.post("/api/otp/send")
async def otp_send(body: OtpSendRequest):
    phone = _norm_phone(body.phone)
    if not phone:
        raise HTTPException(400, "phone required")

    mode = (body.mode or "login").strip().lower()
    if mode not in ("login", "signup"):
        raise HTTPException(400, detail={"error": "bad_mode", "message": "mode faqat login/signup bo'lishi kerak"})

    # Telegram botda borligini tekshiramiz
    tg_user = db.get_telegram_user(phone)
    if not tg_user:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_registered",
                "message": "Avval Telegram botga /start yuboring va raqamingizni tasdiqlang"
            }
        )

    is_registered = get_registered_user(phone) is not None

    if mode == "signup" and is_registered:
        raise HTTPException(
            status_code=400,
            detail={"error": "user_already_exists", "message": "Bu raqam allaqachon ro'yxatdan o'tgan. Kirishdan foydalaning."}
        )

    if mode == "login" and not is_registered:
        raise HTTPException(
            status_code=404,
            detail={"error": "user_not_found", "message": "Bu raqam topilmadi. Ro'yxatdan o'tishdan foydalaning."}
        )

    # OTP cooldown (db.save_otp created_at qo'shgan)
    existing = db.get_otp(phone)
    if existing:
        sent_ago = time.time() - float(existing.get("created_at", 0) or 0)
        if sent_ago < 60:
            raise HTTPException(
                status_code=429,
                detail={"error": "too_soon", "message": "1 daqiqa kuting va qayta urining"}
            )

    code = str(random.randint(100000, 999999))
    expires_at = time.time() + 5 * 60
    db.save_otp(phone=phone, code=code, expires_at=expires_at, mode=mode)

    try:
        await send_otp(chat_id=int(tg_user["chat_id"]), code=code)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Telegram ga yuborishda xato: {str(e)}"},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    return {"success": True, "message": "Telegram ga kod yuborildi"}


@app.post("/api/otp/verify")
def otp_verify(body: OtpVerifyRequest):
    phone = _norm_phone(body.phone)
    if not phone:
        raise HTTPException(400, "phone required")

    record = db.get_otp(phone)
    if not record:
        raise HTTPException(400, detail={"error": "not_found", "message": "Kod topilmadi. Qayta yuboring."})

    # expired
    if time.time() > float(record.get("expires_at", 0) or 0):
        db.delete_otp(phone)
        raise HTTPException(400, detail={"error": "expired", "message": "Kod muddati o'tdi. Qayta yuboring."})

    # attempts
    if int(record.get("attempts", 0) or 0) >= 5:
        db.delete_otp(phone)
        raise HTTPException(400, detail={"error": "too_many_attempts", "message": "Ko'p noto'g'ri urinish. Qayta yuboring."})

    # mode mismatch (xavfsizlik)
    body_mode = (body.mode or "login").strip().lower()
    rec_mode = (record.get("mode") or "login").strip().lower()
    if body_mode != rec_mode:
        raise HTTPException(400, detail={"error": "mode_mismatch", "message": "Kod boshqa rejim uchun yuborilgan. Qayta yuboring."})

    # code check
    if str(record.get("code", "")).strip() != str(body.code).strip():
        attempts = db.increment_otp_attempts(phone)
        left = 5 - attempts
        raise HTTPException(400, detail={"error": "wrong_code", "message": f"Noto'g'ri kod. {left} ta urinish qoldi."})

    # success → delete otp
    db.delete_otp(phone)

    reg_user = get_registered_user(phone)
    tg_user = db.get_telegram_user(phone)

    if rec_mode == "signup":
        if reg_user:
            raise HTTPException(400, detail={"error": "user_already_exists", "message": "Bu raqam allaqachon ro'yxatdan o'tgan."})
        # telegram full_name bo'lsa, shu bilan prefill
        first, last = "", ""
        if tg_user and tg_user.get("full_name"):
            parts = tg_user["full_name"].split(" ", 1)
            first = parts[0] if len(parts) > 0 else ""
            last = parts[1] if len(parts) > 1 else ""
        user_data = {"firstName": first, "lastName": last, "phone": phone}
        return {"success": True, "phone": phone, "user": user_data, "mode": "signup"}

    # login
    if not reg_user:
        raise HTTPException(404, detail={"error": "user_not_found", "message": "Foydalanuvchi topilmadi."})

    user_data = {
        "firstName": reg_user.get("firstName", ""),
        "lastName": reg_user.get("lastName", ""),
        "phone": phone,
    }
    return {"success": True, "phone": phone, "user": user_data, "mode": "login"}


@app.post("/api/users/profile")
def save_profile(body: ProfileSaveRequest):
    phone = _norm_phone(body.phone)
    if not phone:
        raise HTTPException(400, "phone required")
    if not (body.firstName or "").strip():
        raise HTTPException(400, detail="firstName bo'sh bo'lmasin")

    user = save_registered_user(
        phone=phone,
        first_name=body.firstName.strip(),
        last_name=(body.lastName or "").strip(),
    )
    return {"success": True, "user": user}


@app.get("/api/users/profile")
def get_profile(phone: str):
    p = _norm_phone(phone)
    if not p:
        raise HTTPException(400, "phone required")
    user = get_registered_user(p)
    if not user:
        raise HTTPException(404, detail="Foydalanuvchi topilmadi")
    return user


@app.post("/api/orders", status_code=201)
async def place_order(body: OrderCreate):
    # DB counter orqali ID
    num = db.next_order_number()
    order_id = db.order_id_from_number(num)

    phone = _norm_phone(body.phone)

    order_dict = {
        "id": order_id,
        "created_at": body.date or datetime.utcnow().isoformat(),
        "address": body.address,
        "items": [i.model_dump() for i in body.items],
        "total": int(body.total),
        "status": "pending",
        "tg_user_id": body.tg_user_id,
        "phone": phone,
        "customer_name": body.customer_name,
        "coins_used": int(body.coins_used or 0),
        "payment": body.payment or "naqt",
        "extra_phone": body.extra_phone,
        "comment": body.comment,
    }

    try:
        order = db.create(order_dict)
    except ValueError as e:
        if "DUPLICATE_ID" in str(e):
            raise HTTPException(409, "Bu ID bilan zakaz allaqachon bor")
        raise HTTPException(400, str(e))

    # coin sarflash (agar ishlatilgan bo'lsa)
    if phone and body.coins_used and int(body.coins_used) > 0:
        try:
            spend_coins(phone=phone, amount=int(body.coins_used), order_id=order_id)
        except ValueError:
            # yetarli coin bo'lmasa — discount bermaymiz (frontda ham tekshirgan yaxshi)
            pass

    # admin notify (cancel oynasidan keyin)
    asyncio.create_task(notify_after_delay(order_id))
    return {"success": True, "orderId": order["id"], "status": "pending"}


@app.get("/api/orders")
def list_orders(status: str | None = None, phone: str | None = None, limit: int = 50, offset: int = 0):
    p = _norm_phone(phone) if phone else None
    orders = db.get_all(status=status, phone=p, limit=limit, offset=offset)
    total = db.count(status=status, phone=p)
    return {"orders": orders, "total": total}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    order = db.get_by_id(order_id)
    if not order:
        raise HTTPException(404, "Zakaz topilmadi")
    return order


@app.patch("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str):
    order = db.get_by_id(order_id)
    if not order:
        raise HTTPException(404, "Zakaz topilmadi")

    if order.get("status") != "pending":
        raise HTTPException(400, "Faqat kutilayotgan zakazni bekor qilish mumkin")

    created_raw = str(order.get("created_at") or "").replace("Z", "+00:00")
    try:
        created_dt = datetime.fromisoformat(created_raw)
        # tz-aware bo'lsa, naive ga
        if created_dt.tzinfo is not None:
            created_dt = created_dt.replace(tzinfo=None)
    except Exception:
        created_dt = datetime.utcnow()

    elapsed = (datetime.utcnow() - created_dt).total_seconds()
    if elapsed > 55:
        raise HTTPException(400, "Bekor qilish vaqti o'tdi (55 sekund)")

    updated = db.update_status(order_id, "cancelled") or order
    asyncio.create_task(notify_cancelled(updated))
    return {"success": True, "status": "cancelled"}


@app.get("/api/coins")
def get_user_coins(phone: str):
    p = _norm_phone(phone)
    if not p:
        raise HTTPException(400, "phone required")
    balance = get_coins(p)
    return {"phone": p, "balance": balance, "sum_value": balance * 1000}

@app.get("/api/menu")
def get_menu():
    return load_data()

@app.post("/api/menu")
def save_menu_endpoint(payload: dict):
    data = load_data()
    if "categories" in payload:
        data["categories"] = payload["categories"]
    if "items" in payload:
        data["items"] = payload["items"]
    save_data(data)
    return {"ok": True, "data": data}

# ===== MENU SIMPLE STORAGE =====
import json
import os

MENU_FILE = "menu_data.json"

def load_menu():
    if not os.path.exists(MENU_FILE):
        return {"categories": [], "items": []}
    with open(MENU_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_menu(data):
    with open(MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/menu")
def get_menu():
    return load_menu()


@app.post("/api/menu")
def update_menu(payload: dict):
    save_menu(payload)
    return {"ok": True}

# ===== MENU STORAGE =====
import json, os

MENU_FILE = "menu_data.json"

def load_data():
    if not os.path.exists(MENU_FILE):
        return {"categories": [], "items": []}
    with open(MENU_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/menu")
def get_menu():
    return load_data()


@app.post("/api/menu")
def set_menu(payload: dict):
    save_data(payload)
    return {"ok": True}


# ───────────────────────────────────────────────────────────────
# Run local
# ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"\n🚀 Server: http://localhost:{port}")
    print(f"📋 API docs: http://localhost:{port}/docs\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
