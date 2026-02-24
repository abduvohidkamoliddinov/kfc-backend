"""
bot.py — Telegram Admin Bot + OTP yuborish
python-telegram-bot 21.x

✅ Yaxshilanganlari:
- Admin / Courier authorization (ruxsat tekshiruvi)
- Status flow: pending → confirmed → cooking → ready → delivering → done (cancel faqat pending)
- build_order_message() xavfsiz (price/quantity/created_at yo'q bo'lsa yiqilmaydi)
- Coin: db.add_coins() idempotent bo'lsa (order_id bo'yicha 1 marta) double bo'lmaydi
- Review: done'dan keyin user izoh qoldiradi
- Admin: 📊 Statistika (oylik)
"""
import os
from typing import Optional

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import database as db

# ═══════════════════════════════════════════════════════════════
#  STATUS lug'ati + flow
# ═══════════════════════════════════════════════════════════════

STATUS = {
    "pending":    ("🕐", "Kutilmoqda"),
    "confirmed":  ("✅", "Tasdiqlandi"),
    "cooking":    ("🍗", "Tayyorlanmoqda"),
    "ready":      ("📦", "Kuryer kutmoqda"),
    "delivering": ("🚗", "Yetkazilmoqda"),
    "done":       ("🎉", "Yetkazildi"),
    "cancelled":  ("❌", "Bekor qilindi"),
}

FLOW = ["pending", "confirmed", "cooking", "ready", "delivering", "done"]
TERMINAL = {"done", "cancelled"}


def _is_admin_chat(chat_id: int) -> bool:
    return str(chat_id) == str(os.getenv("ADMIN_CHAT_ID", ""))


def _is_courier_chat(chat_id: int) -> bool:
    return str(chat_id) == str(os.getenv("COURIER_CHAT_ID", ""))


def _can_move(old: str, new: str) -> bool:
    if old == new:
        return True
    if old in TERMINAL:
        return False
    if new == "cancelled":
        return old == "pending"
    if old not in FLOW or new not in FLOW:
        return True
    return FLOW.index(new) >= FLOW.index(old)


# ═══════════════════════════════════════════════════════════════
#  User bildirishnoma yordamchisi
# ═══════════════════════════════════════════════════════════════

async def notify_user(
    ctx: ContextTypes.DEFAULT_TYPE,
    phone: str,
    text: str,
    reply_markup=None,
):
    """Telefon orqali chat_id topib userga xabar yuboradi."""
    try:
        tg_user = db.get_telegram_user(phone)
        if not tg_user or not tg_user.get("chat_id"):
            return
        await ctx.bot.send_message(
            chat_id=int(tg_user["chat_id"]),
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        print(f"notify_user xato ({phone}): {e}")


# ═══════════════════════════════════════════════════════════════
#  Zakaz xabari matni (admin/courier ko'rishi uchun)
# ═══════════════════════════════════════════════════════════════

def build_order_message(order: dict) -> str:
    """Zakaz xabarini xavfsiz (KeyError'siz) yasaydi."""
    items = order.get("items") or []
    lines_list = []
    for i in items:
        name = i.get("fullName") or i.get("name") or "—"
        qty = int(i.get("quantity", 0) or 0)
        price = int(i.get("price", 0) or 0)
        lines_list.append(f"  • {name} x {qty} — {price * qty:,} UZS")
    lines = "\n".join(lines_list) if lines_list else "  • —"

    status = order.get("status", "pending")
    emoji, label = STATUS.get(status, ("🕐", status))

    payment_map = {"naqt": "💵 Naqt", "card": "💳 Karta"}
    payment = payment_map.get(order.get("payment", "naqt"), "💵 Naqt")

    extra_phone = order.get("extra_phone")
    comment = order.get("comment")
    customer = order.get("customer_name", "") or ""
    phone = order.get("phone", "") or ""
    coins_used = int(order.get("coins_used", 0) or 0)

    created = order.get("created_at") or ""
    created_view = created[:16].replace("T", " ") if created else "—"

    extra_lines = ""
    if customer or phone:
        extra_lines += f"👤 <b>Mijoz:</b> {customer} {phone}\n"
    if extra_phone:
        extra_lines += f"📞 <b>Qo'shimcha tel:</b> {extra_phone}\n"
    if comment:
        extra_lines += f"💬 <b>Izoh:</b> {comment}\n"
    if coins_used:
        extra_lines += (
            f"🪙 <b>Coin ishlatildi:</b> {coins_used} "
            f"({coins_used * 1000:,} UZS chegirma)\n"
        )

    total = int(order.get("total", 0) or 0)
    address = order.get("address", "—")
    order_id = order.get("id", "—")

    return (
        f"🛒 <b>Zakaz #{order_id}</b>\n"
        f"───────────────\n"
        f"📍 <b>Manzil:</b> {address}\n\n"
        f"🍽 <b>Tarkib:</b>\n{lines}\n\n"
        f"💳 <b>Jami:</b> {total:,} UZS\n"
        f"💰 <b>To'lov:</b> {payment}\n"
        f"{extra_lines}"
        f"⏰ <b>Vaqt:</b> {created_view}\n\n"
        f"{emoji} <b>Status:</b> {label}"
    )


# ═══════════════════════════════════════════════════════════════
#  Inline tugmalar (admin zakaz boshqaruvi)
# ═══════════════════════════════════════════════════════════════

def build_admin_keyboard(order_id: str, status: str) -> Optional[InlineKeyboardMarkup]:
    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton("✅ Tasdiqlash",   callback_data=f"status:{order_id}:confirmed"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "confirmed":
        rows.append([
            InlineKeyboardButton("🍗 Tayyorlanmoqda", callback_data=f"status:{order_id}:cooking"),
            InlineKeyboardButton("❌ Bekor qilish",   callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "cooking":
        rows.append([
            InlineKeyboardButton("📦 Ovqat tayyor (kuryerga)", callback_data=f"status:{order_id}:ready"),
        ])
    # ready/delivering/done/cancelled — admin tugma yo'q
    return InlineKeyboardMarkup(rows) if rows else None


def build_courier_keyboard(order_id: str, status: str) -> Optional[InlineKeyboardMarkup]:
    # courier faqat ready/delivering da tugma ko'radi
    if status == "ready":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🚗 Yetkazilmoqda", callback_data=f"courier:{order_id}:delivering")
        ]])
    if status == "delivering":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yetkazildi", callback_data=f"courier:{order_id}:done")
        ]])
    return None


# ═══════════════════════════════════════════════════════════════
#  Global app instance (main.py dan chaqirish uchun)
# ═══════════════════════════════════════════════════════════════

_app_instance: Optional[Application] = None


def _get_app() -> Optional[Application]:
    return _app_instance


# ═══════════════════════════════════════════════════════════════
#  Adminga bildirishnomalar (main.py / webhook dan chaqiriladi)
# ═══════════════════════════════════════════════════════════════

async def notify_new_order(order: dict):
    """Yangi zakaz kelganda adminga xabar yuboradi + msg_id saqlaydi."""
    app = _get_app()
    if not app:
        return

    admin_id = os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        print("⚠️ ADMIN_CHAT_ID o'rnatilmagan!")
        return

    try:
        msg = await app.bot.send_message(
            chat_id=int(admin_id),
            text=build_order_message(order),
            parse_mode="HTML",
            reply_markup=build_admin_keyboard(order.get("id", ""), order.get("status", "pending")),
        )
        # admin message id orderga bog'lab saqlash (ixtiyoriy)
        try:
            db.update_tg_msg_id(order["id"], msg.message_id)
        except Exception:
            pass
    except Exception as e:
        print(f"notify_new_order xato: {e}")


async def notify_cancelled(order: dict):
    """Zakaz user tomonidan bekor bo'lganda adminga xabar."""
    app = _get_app()
    if not app:
        return

    admin_id = os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        return

    try:
        await app.bot.send_message(
            chat_id=int(admin_id),
            text=(
                f"❌ <b>Zakaz bekor qilindi #{order.get('id','—')}</b>\n"
                f"💳 {int(order.get('total',0) or 0):,} UZS\n"
                f"👤 {order.get('customer_name','')} {order.get('phone','')}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"notify_cancelled xato: {e}")


# ═══════════════════════════════════════════════════════════════
#  OTP yuborish (main.py → /api/otp/send endpoint dan chaqiriladi)
# ═══════════════════════════════════════════════════════════════

async def send_otp(chat_id: int, code: str):
    """Foydalanuvchiga OTP kodini Telegram orqali yuboradi."""
    app = _get_app()
    if not app:
        raise RuntimeError("Bot instance mavjud emas — create_app() chaqirilmagan")

    await app.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔐 <b>KFC Riston — Tasdiqlash kodi</b>\n\n"
            f"Sizning kodingiz: <code>{code}</code>\n\n"
            f"⏱ Kod 5 daqiqa ichida amal qiladi.\n"
            f"Kodni hech kimga bermang!"
        ),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
#  /start komandasi
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Admin
    if _is_admin_chat(chat_id):
        admin_kb = ReplyKeyboardMarkup([["📊 Statistika"]], resize_keyboard=True)
        await update.message.reply_text(
            f"👋 <b>KFC Admin Bot</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            f"Quyidagi tugmalardan foydalaning:",
            parse_mode="HTML",
            reply_markup=admin_kb,
        )
        return

    # Courier
    if _is_courier_chat(chat_id):
        await update.message.reply_text(
            "🚗 <b>Kuryer panel</b>\n\n"
            "Sizga buyurtma tayyor bo'lganda bu yerga yuboriladi.",
            parse_mode="HTML",
        )
        return

    # Oddiy user
    existing = db.get_telegram_user_by_chat_id(str(chat_id))
    if existing:
        website = os.getenv("WEBSITE_URL", "https://kfs-menu.vercel.app/")
        first = (existing.get("full_name") or "").split()[0] or "do'st"
        await update.message.reply_text(
            f"👋 <b>Salom, {first}!</b>\n\n"
            f"📱 Raqamingiz saqlangan: <code>{existing.get('phone','')}</code>\n\n"
            f"Buyurtma berish uchun saytni oching:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🍗 Ochish / Открыть", url=website)
            ]]),
        )
        return

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "👋 <b>KFC Riston</b> ga xush kelibsiz! 🍗\n\n"
        "Ro'yxatdan o'tish uchun telefon raqamingizni yuboring.\n\n"
        "⬇️ Pastdagi tugmani bosing:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ═══════════════════════════════════════════════════════════════
#  Kontakt qabul qilish
# ═══════════════════════════════════════════════════════════════

async def handle_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    chat_id = update.effective_chat.id

    # boshqa odamning kontakti bo'lsa
    if contact.user_id and contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            "❌ Iltimos, faqat <b>o'z raqamingizni</b> yuboring.",
            parse_mode="HTML",
        )
        return

    # normalize: +998XXXXXXXXX
    phone = (contact.phone_number or "").replace("+", "").replace(" ", "")
    if not phone.startswith("998"):
        phone = "998" + phone[-9:]
    phone = "+" + phone

    website = os.getenv("WEBSITE_URL", "https://kfs-menu.vercel.app/")

    # reply keyboard remove
    rm = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    try:
        await rm.delete()
    except Exception:
        pass

    # already registered by chat_id
    existing = db.get_telegram_user_by_chat_id(str(chat_id))
    if existing:
        first = (existing.get("full_name") or contact.first_name or "do'st").split()[0]
        await update.message.reply_text(
            f"👋 <b>Salom, {first}!</b>\n\n"
            f"📱 Raqamingiz allaqachon saqlangan: <code>{existing.get('phone','')}</code>\n\n"
            f"Buyurtma berish uchun saytni oching:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🍗 Ochish / Открыть", url=website)
            ]]),
        )
        return

    full_name = " ".join(filter(None, [contact.first_name, contact.last_name or ""])).strip()
    db.save_telegram_user(phone=phone, chat_id=str(chat_id), full_name=full_name)

    await update.message.reply_text(
        f"🇺🇿 <b>Assalomu alaykum, {contact.first_name}!</b> 👋\n"
        f"Buyurtma berish uchun tugmani bosing ⬇️\n\n"
        f"🇷🇺 <b>Здравствуйте!</b> 👋\n"
        f"Нажмите кнопку ниже для заказа ⬇️",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🍗 Ochish / Открыть", url=website)
        ]]),
    )


# ═══════════════════════════════════════════════════════════════
#  Admin status callback (status:order_id:new_status)
# ═══════════════════════════════════════════════════════════════

async def handle_admin_status_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    # auth
    chat_id = update.effective_chat.id
    if not _is_admin_chat(chat_id):
        await query.answer("❌ Ruxsat yo'q", show_alert=True)
        return

    if not data.startswith("status:"):
        return

    _, order_id, new_status = data.split(":", 2)

    order = db.get_by_id(order_id)
    if not order:
        await query.answer("❌ Zakaz topilmadi", show_alert=True)
        return

    old = order.get("status", "pending")
    if not _can_move(old, new_status):
        await query.answer("⚠️ Status noto'g'ri ketma-ketlikda", show_alert=True)
        return

    updated = db.update_status(order_id, new_status)
    if not updated:
        await query.answer("❌ Yangilab bo'lmadi", show_alert=True)
        return

    # admin message update
    try:
        await query.edit_message_text(
            text=build_order_message(updated),
            parse_mode="HTML",
            reply_markup=build_admin_keyboard(order_id, new_status),
        )
    except Exception as e:
        print(f"Admin xabar update xato: {e}")

    emoji, label = STATUS.get(new_status, ("✅", new_status))
    await query.answer(f"{emoji} {label}")

    # side-effects
    order_short = order_id

    # confirmed -> userga xabar
    if new_status == "confirmed":
        phone = updated.get("phone")
        total = int(updated.get("total", 0) or 0)
        if phone:
            await notify_user(
                ctx, phone,
                f"✅ <b>Buyurtmangiz tasdiqlandi!</b>\n\n"
                f"📦 Buyurtma: <b>#{order_short}</b>\n"
                f"💰 Summa: <b>{total:,} UZS</b>\n\n"
                f"🍗 Tayyorlanmoqda, tez orada yetkazamiz!"
            )

    # ready -> courierga yuborish (agar courier bor bo'lsa)
    if new_status == "ready":
        courier_id = os.getenv("COURIER_CHAT_ID", "")
        if courier_id:
            items = updated.get("items") or []
            items_text = "\n".join(
                f"  • {(i.get('fullName') or i.get('name') or '—')} x{int(i.get('quantity',0) or 0)}"
                for i in items
            ) or "  • —"

            courier_msg = (
                f"📦 <b>Yangi yetkazish #{order_short}</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📍 <b>Manzil:</b> {updated.get('address','—')}\n\n"
                f"🍽 <b>Tarkib:</b>\n{items_text}\n\n"
                f"💳 <b>Jami:</b> {int(updated.get('total',0) or 0):,} UZS\n"
                f"👤 <b>Mijoz:</b> {updated.get('customer_name','')}\n"
                f"📞 <b>Tel:</b> {updated.get('phone','—')}"
            )
            try:
                await ctx.bot.send_message(
                    chat_id=int(courier_id),
                    text=courier_msg,
                    parse_mode="HTML",
                    reply_markup=build_courier_keyboard(order_id, "ready"),
                )
            except Exception as e:
                print(f"Kuryerga xabar yuborishda xato: {e}")

        # userga "tayyor, kuryer kutyapti" xabar (xohlasangiz)
        phone = updated.get("phone")
        if phone:
            await notify_user(
                ctx, phone,
                f"📦 <b>Buyurtmangiz tayyor!</b>\n\n"
                f"📦 Zakaz #{order_short}\n"
                f"🚗 Kuryer tez orada yo'lga chiqadi."
            )


# ═══════════════════════════════════════════════════════════════
#  Kuryer callback (courier:order_id:action)
# ═══════════════════════════════════════════════════════════════

async def courier_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    # auth
    chat_id = update.effective_chat.id
    if not _is_courier_chat(chat_id):
        await query.answer("❌ Ruxsat yo'q", show_alert=True)
        return

    if not data.startswith("courier:"):
        return

    await query.answer()
    _, order_id, action = data.split(":", 2)

    order = db.get_by_id(order_id)
    if not order:
        await query.answer("❌ Zakaz topilmadi", show_alert=True)
        return

    admin_id = os.getenv("ADMIN_CHAT_ID", "")
    admin_chat = int(admin_id) if str(admin_id).isdigit() else 0
    order_short = order_id

    # delivering
    if action == "delivering":
        old = order.get("status", "pending")
        if not _can_move(old, "delivering"):
            await query.answer("⚠️ Status ketma-ketligi xato", show_alert=True)
            return

        db.update_status(order_id, "delivering")

        # courier message markup update
        try:
            await query.edit_message_reply_markup(
                reply_markup=build_courier_keyboard(order_id, "delivering")
            )
        except Exception:
            pass

        # admin notification
        if admin_chat:
            try:
                await ctx.bot.send_message(
                    chat_id=admin_chat,
                    text=f"🚗 <b>Kuryer yo'lda!</b>\n📦 Zakaz #{order_short}",
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"Adminga delivering xabari xato: {e}")

        # user notification
        phone = order.get("phone")
        if phone:
            await notify_user(
                ctx, phone,
                f"🚗 <b>Kuryer yo'lda!</b>\n\n"
                f"📦 Zakaz #{order_short} sizga yetib kelmoqda.\n"
                f"Iltimos, tayyor bo'ling! 🍗"
            )

    # done
    elif action == "done":
        old = order.get("status", "pending")
        if not _can_move(old, "done"):
            await query.answer("⚠️ Status ketma-ketligi xato", show_alert=True)
            return

        updated = db.update_status(order_id, "done") or order

        # courier confirmation
        try:
            await query.edit_message_text(
                text=f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\n\nRahmat! 🎉",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass

        # admin notification
        if admin_chat:
            try:
                await ctx.bot.send_message(
                    chat_id=admin_chat,
                    text=f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\nKuryer yetkazib berdi.",
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"Adminga done xabari xato: {e}")

        # user coin + review
        phone = updated.get("phone")
        if phone:
            total = int(updated.get("total", 0) or 0)
            coins_used = int(updated.get("coins_used", 0) or 0)
            actual_total = total + (coins_used * 1000)
            earned = max(1, round(actual_total * 0.05 / 1000))

            # ✅ db.add_coins idempotent bo'lsa, order_id bo'yicha 1 marta qo'shadi
            new_balance = 0
            try:
                new_balance = db.add_coins(phone=phone, amount=earned, order_id=order_id)
            except Exception as e:
                print(f"add_coins xato: {e}")

            review_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⭐ Izoh qoldirish", callback_data=f"review:{order_id}")
            ]])

            await notify_user(
                ctx, phone,
                f"🎉 <b>Buyurtmangiz muvaffaqiyatli yetkazildi!</b>\n\n"
                f"🪙 Sizga <b>+{earned} coin</b> qo'shildi\n"
                f"💰 Bu <b>{earned * 1000:,} UZS</b> chegirmaga teng\n"
                f"📊 Joriy balans: <b>{new_balance} coin</b>\n\n"
                f"Keyingi zakazda ishlatishingiz mumkin! 🛍",
                reply_markup=review_kb,
            )


# ═══════════════════════════════════════════════════════════════
#  Review callback (review:order_id)
# ═══════════════════════════════════════════════════════════════

async def review_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("review:"):
        return

    await query.answer()
    order_id = data.split(":", 1)[1]
    ctx.user_data["awaiting_review"] = order_id

    await query.message.reply_text(
        f"✍️ <b>Izohingizni yozing</b>\n\n"
        f"#{order_id} buyurtma haqida fikringizni bildiring.\n"
        f"(Masalan: ovqat mazasi, yetkazib berish tezligi va h.k.)",
        parse_mode="HTML",
    )


async def handle_review_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "awaiting_review" not in ctx.user_data:
        return

    order_id = ctx.user_data.pop("awaiting_review")
    review_text = (update.message.text or "").strip()
    user = update.effective_user

    admin_id = os.getenv("ADMIN_CHAT_ID", "0")
    admin_chat = int(admin_id) if str(admin_id).isdigit() else 0

    if admin_chat and review_text:
        try:
            await ctx.bot.send_message(
                chat_id=admin_chat,
                text=(
                    f"💬 <b>Yangi izoh!</b>\n\n"
                    f"📦 Buyurtma: #{order_id}\n"
                    f"👤 {user.full_name} (@{user.username or '—'})\n\n"
                    f"\"{review_text}\""
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"Adminga review xabari xato: {e}")

    await update.message.reply_text(
        "🙏 Izohingiz uchun rahmat!\n"
        "Siz uchun yanada yaxshilanishga harakat qilamiz. 🍗",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
#  /orders komandasi (admin)
# ═══════════════════════════════════════════════════════════════

async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_chat(update.effective_chat.id):
        return

    orders = db.get_all(limit=10)
    if not orders:
        await update.message.reply_text("📭 Hali zakaz yo'q.")
        return

    lines = []
    for o in orders:
        emoji, label = STATUS.get(o.get("status", "pending"), ("🕐", "pending"))
        lines.append(f"{emoji} #{o.get('id','—')} — {int(o.get('total',0) or 0):,} UZS — {label}")

    await update.message.reply_text(
        "📋 <b>Oxirgi zakazlar:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
#  /stats komandasi (admin) — bugungi
# ═══════════════════════════════════════════════════════════════

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_chat(update.effective_chat.id):
        return
    s = db.stats_today()
    await update.message.reply_text(
        f"📊 <b>Bugungi statistika</b>\n\n"
        f"📦 Jami zakazlar : {s.get('total',0)}\n"
        f"🎉 Yetkazildi   : {s.get('done',0)}\n"
        f"🕐 Kutilmoqda   : {s.get('pending',0)}\n"
        f"❌ Bekor        : {s.get('cancelled',0)}\n"
        f"💰 Daromad      : {int(s.get('revenue',0) or 0):,} UZS",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════
#  📊 Statistika tugmasi — faqat admin, oylik
# ═══════════════════════════════════════════════════════════════

async def handle_statistics_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_chat(update.effective_chat.id):
        return

    s = db.stats_monthly()

    lines = [
        f"📊 <b>Oylik statistika — {s.get('month_label','')}</b>\n",
        f"📦 Jami zakazlar : <b>{s.get('total',0)}</b>",
        f"✅ Yetkazildi    : <b>{s.get('done',0)}</b>",
        f"❌ Bekor qilindi : <b>{s.get('cancelled',0)}</b>",
        f"💰 Daromad       : <b>{int(s.get('revenue',0) or 0):,} UZS</b>",
        "",
        "👤 <b>Userlar bo'yicha:</b>",
    ]

    users = s.get("users") or []
    if not users:
        lines.append("  — bu oyda zakaz yo'q")
    else:
        for i, u in enumerate(users, 1):
            rev_str = f"  💵 {int(u.get('revenue',0) or 0):,} UZS" if u.get("revenue") else ""
            cancel_str = f"  ❌{u.get('cancelled',0)}" if u.get("cancelled") else ""
            lines.append(
                f"{i}. {u.get('name','—')} ({u.get('phone','—')})\n"
                f"   📦 {u.get('total',0)} zakaz  ✅{u.get('done',0)}{cancel_str}{rev_str}"
            )

    text = "\n".join(lines)

    # Telegram 4096 limit
    if len(text) <= 4096:
        await update.message.reply_text(text, parse_mode="HTML")
        return

    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 4096:
            await update.message.reply_text(chunk, parse_mode="HTML")
            chunk = line
        else:
            chunk += ("\n" if chunk else "") + line
    if chunk:
        await update.message.reply_text(chunk, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  App yaratish
# ═══════════════════════════════════════════════════════════════

def create_app() -> Application:
    global _app_instance

    token = os.getenv("BOT_TOKEN", "")
    if not token:
        print("⚠️ BOT_TOKEN o'rnatilmagan!")

    app = Application.builder().token(token).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # contact
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # admin reply button
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^📊 Statistika$"), handle_statistics_btn))

    # review text (oddiy textlar ichidan)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text))

    # callback handlers (pattern bilan)
    app.add_handler(CallbackQueryHandler(review_callback, pattern=r"^review:"))
    app.add_handler(CallbackQueryHandler(courier_callback, pattern=r"^courier:"))
    app.add_handler(CallbackQueryHandler(handle_admin_status_callback, pattern=r"^status:"))

    _app_instance = app
    return app
