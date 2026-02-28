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

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Header
from fastapi.responses import JSONResponse
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
from bot import create_app, notify_new_order, notify_cancelled, send_otp

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
        _bot_app = create_app()
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
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ───────────────────────────────────────────────────────────────
# Static files (uploaded images)
# ───────────────────────────────────────────────────────────────

_UPLOADS_DIR = db.DATA_DIR / "uploads" / "menu"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
_CAT_UPLOADS_DIR = db.DATA_DIR / "uploads" / "menu" / "categories"
_CAT_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(db.DATA_DIR / "uploads")), name="static")


# ───────────────────────────────────────────────────────────────
# Admin key helper
# ───────────────────────────────────────────────────────────────

def require_admin(x_admin_key: str | None = Header(default=None)):
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(500, "ADMIN_KEY is not configured")
    if x_admin_key != admin_key:
        raise HTTPException(401, "Invalid admin key")

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


# ───────────────────────────────────────────────────────────────
# Menu: Public endpoints
# ───────────────────────────────────────────────────────────────

@app.get("/api/menu/categories")
def get_menu_categories(active_only: bool = True):
    return db.menu_get_categories(active_only=active_only)


@app.get("/api/menu/foods")
def get_menu_foods(category: str | None = None, search: str | None = None, active_only: bool = True):
    return db.menu_get_foods(category=category, search=search, active_only=active_only)


# ───────────────────────────────────────────────────────────────
# Menu: Admin CRUD (requires X-Admin-Key header)
# ───────────────────────────────────────────────────────────────

import uuid as _uuid


@app.post("/api/menu/categories", status_code=201)
async def create_category(
    key: str = Form(...),
    title: str = Form(...),
    sort_order: int = Form(0),
    is_active: str = Form("true"),
    image: UploadFile | None = File(None),
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    active = is_active.lower() in ("true", "1", "yes")
    image_url = ""
    if image and image.filename:
        ext = Path(image.filename).suffix or ".jpg"
        fname = f"{_uuid.uuid4().hex}{ext}"
        dest = _CAT_UPLOADS_DIR / fname
        dest.write_bytes(await image.read())
        image_url = f"/static/menu/categories/{fname}"
    cat = db.menu_create_category({
        "key": key.strip(),
        "title": title.strip(),
        "sort_order": sort_order,
        "is_active": active,
        "image_url": image_url,
    })
    return cat


@app.put("/api/menu/categories/{cat_id}")
async def update_category(
    cat_id: int,
    key: str = Form(None),
    title: str = Form(None),
    sort_order: int = Form(None),
    is_active: str = Form(None),
    image: UploadFile | None = File(None),
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    patch = {}
    if key is not None:
        patch["key"] = key.strip()
    if title is not None:
        patch["title"] = title.strip()
    if sort_order is not None:
        patch["sort_order"] = sort_order
    if is_active is not None:
        patch["is_active"] = is_active.lower() in ("true", "1", "yes")
    if image and image.filename:
        ext = Path(image.filename).suffix or ".jpg"
        fname = f"{_uuid.uuid4().hex}{ext}"
        dest = _CAT_UPLOADS_DIR / fname
        dest.write_bytes(await image.read())
        patch["image_url"] = f"/static/menu/categories/{fname}"
    result = db.menu_update_category(cat_id, patch)
    if not result:
        raise HTTPException(404, "Category not found")
    return result


@app.delete("/api/menu/categories/{cat_id}")
def delete_category(
    cat_id: int,
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    try:
        deleted = db.menu_delete_category(cat_id)
    except ValueError as e:
        if "CATEGORY_HAS_FOODS" in str(e):
            raise HTTPException(409, "Category has foods — delete them first")
        raise
    if not deleted:
        raise HTTPException(404, "Category not found")
    return {"success": True}


@app.post("/api/menu/foods", status_code=201)
async def create_food(
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form(...),
    fullName: str = Form(None),
    description: str = Form(""),
    is_active: bool = Form(True),
    image: UploadFile | None = File(None),
    image_emoji: str = Form(None),
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    image_value = ""
    if image and image.filename:
        ext = Path(image.filename).suffix or ".jpg"
        fname = f"{_uuid.uuid4().hex}{ext}"
        dest = _UPLOADS_DIR / fname
        dest.write_bytes(await image.read())
        image_value = f"/static/menu/{fname}"
    elif image_emoji:
        image_value = image_emoji.strip()

    food = db.menu_create_food({
        "name": name.strip(),
        "fullName": (fullName or "").strip() or None,
        "description": (description or "").strip(),
        "price": price,
        "category": category.strip(),
        "image": image_value,
        "is_active": is_active,
    })
    return food


@app.put("/api/menu/foods/{food_id}")
async def update_food(
    food_id: int,
    name: str = Form(None),
    price: int = Form(None),
    category: str = Form(None),
    fullName: str = Form(None),
    description: str = Form(None),
    is_active: bool = Form(None),
    image: UploadFile | None = File(None),
    image_emoji: str = Form(None),
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    patch = {}
    if name is not None:
        patch["name"] = name.strip()
    if fullName is not None:
        patch["fullName"] = fullName.strip() or None
    if description is not None:
        patch["description"] = description.strip()
    if price is not None:
        patch["price"] = price
    if category is not None:
        patch["category"] = category.strip()
    if is_active is not None:
        patch["is_active"] = is_active

    if image and image.filename:
        ext = Path(image.filename).suffix or ".jpg"
        fname = f"{_uuid.uuid4().hex}{ext}"
        dest = _UPLOADS_DIR / fname
        dest.write_bytes(await image.read())
        patch["image"] = f"/static/menu/{fname}"
    elif image_emoji is not None:
        patch["image"] = image_emoji.strip()

    result = db.menu_update_food(food_id, patch)
    if not result:
        raise HTTPException(404, "Food not found")
    return result


@app.delete("/api/menu/foods/{food_id}")
def delete_food(
    food_id: int,
    x_admin_key: str | None = Header(default=None),
):
    require_admin(x_admin_key)
    deleted = db.menu_delete_food(food_id)
    if not deleted:
        raise HTTPException(404, "Food not found")
    return {"success": True}


# ───────────────────────────────────────────────────────────────
# Run local
# ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"\n🚀 Server: http://localhost:{port}")
    print(f"📋 API docs: http://localhost:{port}/docs\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
