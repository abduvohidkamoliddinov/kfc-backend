import asyncio
import random
import time
import os
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, field_validator

import database as db
from database import save_registered_user, get_registered_user
from bot import create_app, notify_new_order, notify_cancelled, send_otp

_bot_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app
    token = os.getenv("BOT_TOKEN", "")
    if token:
        _bot_app = create_app()
        await _bot_app.initialize()
        await _bot_app.start()
        await _bot_app.updater.start_polling(drop_pending_updates=True)
        print("🤖 Admin bot ishga tushdi")
    else:
        print("BOT_TOKEN yoq - bot ishlamaydi")
    yield
    if _bot_app:
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()

app = FastAPI(title="KFC Backend", lifespan=lifespan)

# ── CORS: har qanday javobga (500 ham) header qo'shadi ──────
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
            response = JSONResponse(
                status_code=500,
                content={"detail": str(e)},
            )
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response

app.add_middleware(CORSEverywhere)

# ── Pydantic modellari ───────────────────────────────────────

class OrderItem(BaseModel):
    name: str
    fullName: str | None = None
    quantity: int
    price: int

class OrderCreate(BaseModel):
    id: str | None = None
    items: list[OrderItem]
    address: str
    total: int
    date: str | None = None
    tg_user_id: int | None = None
    phone: str | None = None
    customer_name: str | None = None

    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v):
        if not v:
            raise ValueError("items bosh bolmasin")
        return v

    @field_validator("total")
    @classmethod
    def min_total(cls, v):
        if v < 50000:
            raise ValueError("Minimal zakaz 50,000 UZS")
        return v

class OtpSendRequest(BaseModel):
    phone: str

class OtpVerifyRequest(BaseModel):
    phone: str
    code: str

# ── Yordamchi funksiya ───────────────────────────────────────

async def notify_after_delay(order_id: str, delay: int = 60):
    await asyncio.sleep(delay)
    order = db.get_by_id(order_id)
    if order and order["status"] != "cancelled":
        await notify_new_order(order)

# ── Endpointlar ──────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

# ────────────────────────────────────────────────────────────
#  POST /api/otp/send — Telegram orqali OTP yuborish
# ────────────────────────────────────────────────────────────
@app.post("/api/otp/send")
async def otp_send(body: OtpSendRequest):
    phone = body.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    tg_user = db.get_telegram_user(phone)
    if not tg_user:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_registered",
                "message": "Avval Telegram botga /start yuboring va raqamingizni tasdiqlang"
            }
        )

    existing = db.get_otp(phone)
    if existing:
        remaining = existing["expires_at"] - time.time()
        if remaining > 4 * 60:
            raise HTTPException(
                status_code=429,
                detail={"error": "too_soon", "message": "Biroz kuting va qayta urining"}
            )

    code = str(random.randint(100000, 999999))
    expires_at = time.time() + 5 * 60

    db.save_otp(phone=phone, code=code, expires_at=expires_at)

    try:
        await send_otp(chat_id=int(tg_user["chat_id"]), code=code)
    except Exception as e:
        # OTP saqlangan, lekin Telegram yuborishda xato
        # CORS header bilan qaytarish uchun JSONResponse ishlatamiz
        return JSONResponse(
            status_code=500,
            content={"detail": f"Telegram ga yuborishda xato: {str(e)}. BOT_TOKEN Railway Variables da to'g'ri kiritilganmi?"},
            headers={"Access-Control-Allow-Origin": "*"}
        )

    # Foydalanuvchi allaqachon ro'yxatdan o'tganmi tekshirish
    is_registered = get_registered_user(phone) is not None

    return {"success": True, "message": "Telegram ga kod yuborildi", "user_exists": is_registered}

# ────────────────────────────────────────────────────────────
#  POST /api/otp/verify — OTP tekshirish
# ────────────────────────────────────────────────────────────
@app.post("/api/otp/verify")
def otp_verify(body: OtpVerifyRequest):
    phone = body.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    record = db.get_otp(phone)
    if not record:
        raise HTTPException(
            status_code=400,
            detail={"error": "not_found", "message": "Kod topilmadi. Qayta yuborish kerak."}
        )

    if time.time() > record["expires_at"]:
        db.delete_otp(phone)
        raise HTTPException(
            status_code=400,
            detail={"error": "expired", "message": "Kod muddati o'tdi. Qayta yuborish kerak."}
        )

    if record.get("attempts", 0) >= 5:
        db.delete_otp(phone)
        raise HTTPException(
            status_code=400,
            detail={"error": "too_many_attempts", "message": "Ko'p noto'g'ri urinish. Qayta yuborish kerak."}
        )

    if record["code"] != str(body.code).strip():
        attempts = db.increment_otp_attempts(phone)
        left = 5 - attempts
        raise HTTPException(
            status_code=400,
            detail={"error": "wrong_code", "message": f"Noto'g'ri kod. {left} ta urinish qoldi."}
        )

    db.delete_otp(phone)
    
    # 1. Avval signup da saqlangan profil tekshiriladi
    reg_user = get_registered_user(phone)
    user_data = None
    if reg_user:
        user_data = {
            "firstName": reg_user.get("firstName", ""),
            "lastName": reg_user.get("lastName", ""),
            "phone": phone
        }
    else:
        # 2. Agar yo'q bo'lsa, Telegram full_name ishlatiladi
        tg_user = db.get_telegram_user(phone)
        if tg_user and tg_user.get("full_name"):
            parts = tg_user["full_name"].split(" ", 1)
            user_data = {
                "firstName": parts[0] if len(parts) > 0 else "",
                "lastName": parts[1] if len(parts) > 1 else "",
                "phone": phone
            }
    
    return {"success": True, "phone": phone, "user": user_data}

# ────────────────────────────────────────────────────────────
#  POST /api/orders — Yangi zakaz
# ────────────────────────────────────────────────────────────
@app.post("/api/orders", status_code=201)
async def place_order(body: OrderCreate):
    order_id = body.id or f"kfc_{int(datetime.utcnow().timestamp() * 1000)}"
    order_dict = {
        "id": order_id,
        "created_at": body.date or datetime.utcnow().isoformat(),
        "address": body.address,
        "items": [i.model_dump() for i in body.items],
        "total": body.total,
        "status": "pending",
        "tg_user_id": body.tg_user_id,
        "phone": body.phone,
        "customer_name": body.customer_name,
    }
    try:
        order = db.create(order_dict)
    except ValueError as e:
        if "DUPLICATE_ID" in str(e):
            raise HTTPException(409, "Bu ID bilan zakaz allaqachon bor")
        raise HTTPException(400, str(e))

    asyncio.create_task(notify_after_delay(order_id))
    return {"success": True, "orderId": order["id"], "status": "pending"}

# ────────────────────────────────────────────────────────────
#  GET /api/orders — Barcha zakazlar
# ────────────────────────────────────────────────────────────
@app.get("/api/orders")
def list_orders(status: str | None = None, phone: str | None = None, limit: int = 50, offset: int = 0):
    orders = db.get_all(status=status, phone=phone, limit=limit, offset=offset)
    total = db.count(status=status, phone=phone)
    return {"orders": orders, "total": total}

# ────────────────────────────────────────────────────────────
#  GET /api/orders/{order_id} — Bitta zakaz
# ────────────────────────────────────────────────────────────
@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    order = db.get_by_id(order_id)
    if not order:
        raise HTTPException(404, "Zakaz topilmadi")
    return order

# ────────────────────────────────────────────────────────────
#  PATCH /api/orders/{order_id}/cancel — Bekor qilish
# ────────────────────────────────────────────────────────────
@app.patch("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: str):
    order = db.get_by_id(order_id)
    if not order:
        raise HTTPException(404, "Zakaz topilmadi")
    if order["status"] != "pending":
        raise HTTPException(400, "Faqat kutilayotgan zakazni bekor qilish mumkin")
    created = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
    elapsed = (datetime.utcnow() - created.replace(tzinfo=None)).total_seconds()
    if elapsed > 60:
        raise HTTPException(400, "Bekor qilish vaqti otdi (1 daqiqa)")
    updated = db.update_status(order_id, "cancelled")
    asyncio.create_task(notify_cancelled(updated))
    return {"success": True, "status": "cancelled"}


# ────────────────────────────────────────────────────────────
#  POST /api/users/profile — Ism/familya saqlash (signup)
# ────────────────────────────────────────────────────────────
class ProfileSaveRequest(BaseModel):
    phone: str
    firstName: str
    lastName: str

@app.post("/api/users/profile")
def save_profile(body: ProfileSaveRequest):
    phone = body.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    if not body.firstName.strip():
        raise HTTPException(status_code=400, detail="firstName bo'sh bo'lmasin")
    user = save_registered_user(
        phone=phone,
        first_name=body.firstName.strip(),
        last_name=body.lastName.strip()
    )
    return {"success": True, "user": user}

# ────────────────────────────────────────────────────────────
#  GET /api/users/profile — Profilni olish
# ────────────────────────────────────────────────────────────
@app.get("/api/users/profile")
def get_profile(phone: str):
    if not phone.startswith("+"):
        phone = "+" + phone
    user = get_registered_user(phone)
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    return user

# ── Ishga tushirish ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"\n🚀 Server: http://localhost:{port}")
    print(f"📋 API docs: http://localhost:{port}/docs\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
