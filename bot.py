"""
bot.py — Telegram Admin Bot + OTP yuborish
python-telegram-bot 21.x
"""
import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import database as db
from database import add_coins, get_coins

# ── Status ──────────────────────────────────────────────────
STATUS = {
    "pending":    ("🕐", "Kutilmoqda"),
    "confirmed":  ("✅", "Tasdiqlandi"),
    "cooking":    ("🍗", "Tayyorlanmoqda"),
    "ready":      ("📦", "Kuryer kutmoqda"),
    "delivering": ("🚗", "Yetkazilmoqda"),
    "done":       ("🎉", "Yetkazildi"),
    "cancelled":  ("❌", "Bekor qilindi"),
}

# ── Zakaz xabari matni ───────────────────────────────────────
def build_order_message(order: dict) -> str:
    items = order.get("items", [])
    lines = "\n".join(
        f"  • {i.get('fullName') or i.get('name')} x {i['quantity']} — "
        f"{i['price'] * i['quantity']:,} UZS"
        for i in items
    )
    emoji, label = STATUS.get(order["status"], ("🕐", "Kutilmoqda"))

    payment_map = {"naqt": "💵 Naqt", "card": "💳 Karta"}
    payment = payment_map.get(order.get("payment", "naqt"), "💵 Naqt")

    customer   = order.get("customer_name", "")
    phone      = order.get("phone", "")
    extra_phone = order.get("extra_phone")
    comment    = order.get("comment")
    coins_used = order.get("coins_used", 0)
    total      = order.get("total", 0)

    # Jami qatorini tayyorlash
    coins_used = coins_used or 0
    if coins_used > 0:
        original = total + coins_used * 1000
        total_line = (
            f"💳 <b>Jami:</b> {original:,} UZS\n"
            f"🪙 <b>Coin chegirma:</b> −{coins_used * 1000:,} UZS ({coins_used} coin)\n"
            f"✅ <b>To'lov:</b> {total:,} UZS\n"
        )
    else:
        total_line = f"💳 <b>Jami:</b> {total:,} UZS\n"

    # Qo'shimcha qatorlar
    extra = ""
    if comment:
        extra += f"💬 <b>Izoh:</b> {comment}\n"
    if extra_phone:
        extra += f"📱 <b>Qo'sh. tel:</b> {extra_phone}\n"

    vaqt = order.get("created_at", "")[:16].replace("T", " ")

    return (
        f"🛒 <b>Yangi zakaz #{order['id'][-6:].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 <b>Manzil:</b> {order['address']}\n\n"
        f"🍽 <b>Tarkib:</b>\n{lines}\n\n"
        f"{total_line}"
        f"💰 <b>To'lov:</b> {payment}\n"
        f"👤 <b>Mijoz:</b> {customer}\n"
        f"📞 <b>Telefon:</b> {phone}\n"
        f"{extra}"
        f"⏰ <b>Vaqt:</b> {vaqt}\n\n"
        f"{emoji} <b>Status:</b> {label}"
    )

# ── Inline tugmalar ──────────────────────────────────────────
def build_keyboard(order_id: str, status: str):
    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton("✅ Tasdiqlash",    callback_data=f"status:{order_id}:confirmed"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "confirmed":
        rows.append([
            InlineKeyboardButton("🍗 Tayyorlanmoqda", callback_data=f"status:{order_id}:cooking"),
            InlineKeyboardButton("❌ Bekor qilish",   callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "cooking":
        rows.append([InlineKeyboardButton("🍗 Ovqat tayyor", callback_data=f"status:{order_id}:ready")])
    elif status == "ready":
        pass  # Kuryer boshqaradi
    elif status == "delivering":
        rows.append([InlineKeyboardButton("🎉 Yetkazildi", callback_data=f"status:{order_id}:done")])
    return InlineKeyboardMarkup(rows) if rows else None

# ── Global app instance ──────────────────────────────────────
_app_instance = None

def _get_app():
    return _app_instance

# ── Yangi zakaz — adminga xabar ─────────────────────────────

async def notify_user(context, phone: str, text: str, reply_markup=None):
    """Userga Telegram xabar yuborish (telefon orqali chat_id topib)"""
    try:
        tg_user = db.get_telegram_user(phone)
        if tg_user and tg_user.get("chat_id"):
            kwargs = dict(chat_id=int(tg_user["chat_id"]), text=text, parse_mode="HTML")
            if reply_markup:
                kwargs["reply_markup"] = reply_markup
            await context.bot.send_message(**kwargs)
    except Exception as e:
        print(f"Userga xabar yuborishda xato: {e}")

async def notify_new_order(order: dict):
    app = _get_app()
    if not app:
        return
    admin_id = os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        print("ADMIN_CHAT_ID yoq!")
        return
    try:
        msg = await app.bot.send_message(
            chat_id=int(admin_id),
            text=build_order_message(order),
            parse_mode="HTML",
            reply_markup=build_keyboard(order["id"], order["status"]),
        )
        db.update_tg_msg_id(order["id"], msg.message_id)
    except Exception as e:
        print(f"Telegram xabar yuborishda xato: {e}")

# ── Bekor qilish — adminga xabar ────────────────────────────
async def notify_cancelled(order: dict):
    app = _get_app()
    if not app:
        return
    admin_id = os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        return
    try:
        await app.bot.send_message(
            chat_id=int(admin_id),
            text=f"❌ <b>Zakaz bekor qilindi #{order['id'][-6:].upper()}</b>\n"
                 f"💳 {order['total']:,} UZS",
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"notify_cancelled xato: {e}")

# ── OTP yuborish (frontenddan chaqiriladi) ───────────────────
async def send_otp(chat_id: int, code: str):
    app = _get_app()
    if not app:
        raise RuntimeError("Bot ishlamayapti")
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

# ── /start komandasi ─────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    admin_id = os.getenv("ADMIN_CHAT_ID", "")

    if str(chat_id) == str(admin_id):
        await update.message.reply_text(
            f"👋 KFC Admin Bot\n\nChat ID: <code>{chat_id}</code>",
            parse_mode="HTML"
        )
        return

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "👋 <b>KFC Riston</b> ga xush kelibsiz! 🍗\n\n"
        "Ro'yxatdan o'tish uchun telefon raqamingizni saqlang.\n\n"
        "⬇️ Pastdagi tugmani bosing:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

# ── Kontakt qabul qilish ─────────────────────────────────────
async def handle_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    chat_id = update.effective_chat.id

    if contact.user_id and contact.user_id != update.effective_user.id:
        await update.message.reply_text("❌ Faqat o'z raqamingizni yuboring.")
        return

    phone = contact.phone_number.replace("+", "").replace(" ", "")
    if not phone.startswith("998"):
        phone = "998" + phone[-9:]
    phone = "+" + phone

    full_name = " ".join(filter(None, [contact.first_name, contact.last_name or ""]))
    db.save_telegram_user(phone=phone, chat_id=str(chat_id), full_name=full_name)

    WEBSITE_URL = os.getenv("WEBSITE_URL", "https://kfs-menu.vercel.app")

    await update.message.reply_text(
        f"🇺🇿 <b>Assalomu alaykum!</b> 👋\n"
        f"Buyurtma berish uchun quyidagi tugmani bosing ⬇️\n\n"
        f"🇷🇺 <b>Здравствуйте!</b> 👋\n"
        f"Нажмите кнопку ниже, чтобы оформить заказ ⬇️",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🍗 Ochish / Открыть", url=WEBSITE_URL)
        ]])
    )

# ── Callback (inline tugmalar) ───────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        data = query.data
        if not data.startswith("status:"):
            await query.answer()
            return

        parts = data.split(":")
        order_id, new_status = parts[1], parts[2]

        order = db.get_by_id(order_id)
        if not order:
            await query.answer("❌ Zakaz topilmadi", show_alert=True)
            return

        updated = db.update_status(order_id, new_status)
        keyboard = build_keyboard(order_id, new_status)

        # ─── "ready" bo'lsa kuryerga xabar ───
        if new_status == "ready":
            COURIER_CHAT_ID = os.getenv("COURIER_CHAT_ID", "")
            if COURIER_CHAT_ID:
                order_short = order_id[-6:].upper()
                address = updated.get("address", "—")
                items = updated.get("items", [])
                items_text = "\n".join(
                    f"  • {i.get('fullName') or i.get('name')} x{i['quantity']}"
                    for i in items
                )
                total = updated.get("total", 0)
                phone = updated.get("phone", "—")
                customer = updated.get("customer_name", "")
                courier_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚗 Yetkazilmoqda", callback_data=f"courier:{order_id}:delivering"),
                ]])
                try:
                    await context.bot.send_message(
                        chat_id=int(COURIER_CHAT_ID),
                        text=(
                            f"📦 <b>Yangi yetkazish #{order_short}</b>\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"📍 <b>Manzil:</b> {address}\n\n"
                            f"🍽 <b>Tarkib:</b>\n{items_text}\n\n"
                            f"💳 <b>Jami:</b> {total:,} UZS\n"
                            f"👤 <b>Mijoz:</b> {customer}\n"
                            f"📞 <b>Tel:</b> {phone}"
                        ),
                        parse_mode="HTML",
                        reply_markup=courier_keyboard
                    )
                except Exception as e:
                    print(f"Kuryerga xabar yuborishda xato: {e}")

        # ─── "confirmed" bo'lsa userga tasdiqlash xabari ───
        if new_status == "confirmed":
            phone = updated.get("phone")
            order_short = order_id[-6:].upper()
            total = updated.get("total", 0)
            if phone:
                await notify_user(
                    context, phone,
                    f"✅ <b>Buyurtmangiz tasdiqlandi!</b>\n\n"
                    f"📦 Buyurtma: <b>#{order_short}</b>\n"
                    f"💰 Summa: <b>{total:,} UZS</b>\n\n"
                    f"🍗 Tayyorlanmoqda, tez orada yetkazamiz!"
                )

        # ─── "done" bo'lsa coin qo'shish (5% = har 1000 sum = 1 coin) ───
        coin_msg = ""
        if new_status == "done":
            phone = updated.get("phone")
            total = updated.get("total", 0)
            coins_used = updated.get("coins_used", 0)
            actual_total = total + (coins_used * 1000)
            earned = max(1, round(actual_total * 0.05 / 1000))
            if phone:
                new_balance = add_coins(phone=phone, amount=earned, order_id=order_id)
                # Adminga coin haqida xabar
                coin_msg = ""
                # Userga Telegram orqali xabar yuborish (coin + review tugmasi)
                from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
                review_keyboard = IKM([[
                    IKB("⭐ Izoh qoldirish", callback_data=f"review:{order_id}")
                ]])
                await notify_user(
                    context, phone,
                    f"🎉 <b>Buyurtmangiz muvaffaqiyatli yetkazildi!</b>\n\n"
                    f"🪙 Tabriklaymiz! Sizga <b>+{earned} coin</b> qo'shildi\n"
                    f"💰 Bu <b>{earned * 1000:,} UZS</b> chegirmaga teng\n"
                    f"📊 Joriy balans: <b>{new_balance} coin</b>\n\n"
                    f"Keyingi zakazda ishlatishingiz mumkin! 🛍",
                    reply_markup=review_keyboard
                )

        try:
            extra = coin_msg if new_status == "done" else ""
            await query.edit_message_text(
                text=build_order_message(updated) + extra,
                parse_mode="HTML",
                reply_markup=keyboard or InlineKeyboardMarkup([]),
            )
        except Exception as e:
            print(f"Xabarni yangilashda xato: {e}")

        emoji, label = STATUS.get(new_status, ("✅", new_status))
        await query.answer(f"{emoji} {label}")

    except Exception as e:
        print(f"Callback error: {e}")
        try:
            await query.answer("❌ Xatolik yuz berdi", show_alert=True)
        except:
            pass

# ── /orders komandasi ────────────────────────────────────────
async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    orders = db.get_all(limit=10)
    if not orders:
        await update.message.reply_text("📭 Hali zakaz yo'q.")
        return
    lines = []
    for o in orders:
        emoji, label = STATUS.get(o["status"], ("🕐", o["status"]))
        lines.append(f"{emoji} #{o['id'][-6:].upper()} — {o['total']:,} UZS — {label}")
    await update.message.reply_text(
        "📋 <b>Oxirgi zakazlar:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )

# ── /stats komandasi ─────────────────────────────────────────
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = db.stats_today()
    await update.message.reply_text(
        f"📊 <b>Bugungi statistika</b>\n\n"
        f"📦 Jami zakazlar: {s['total']}\n"
        f"🎉 Yetkazildi: {s['done']}\n"
        f"🕐 Kutilmoqda: {s['pending']}\n"
        f"❌ Bekor: {s['cancelled']}\n"
        f"💰 Daromad: {s['revenue']:,} UZS",
        parse_mode="HTML"
    )


# ── Izoh (review) callback ────────────────────────────────────
async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("review:"):
        return
    order_id = query.data.split(":")[1]
    context.user_data["awaiting_review"] = order_id
    await query.message.reply_text(
        f"✍️ <b>Izohingizni yozing</b>\n\n"
        f"#{order_id[-6:].upper()} buyurtma haqida fikringizni yozing.\n"
        f"(ovqat mazasi, yetkazib berish tezligi va h.k.)",
        parse_mode="HTML"
    )

async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "awaiting_review" not in context.user_data:
        return
    order_id = context.user_data.pop("awaiting_review")
    review_text = update.message.text
    user = update.effective_user
    ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
    if ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"💬 <b>Yangi izoh!</b>\n\n"
                f"📦 Buyurtma: #{order_id[-6:].upper()}\n"
                f"👤 {user.full_name} (@{user.username or '-'})\n\n"
                f"\"{review_text}\""
            ),
            parse_mode="HTML"
        )
    await update.message.reply_text(
        "🙏 Rahmat! Izohingiz uchun minnatdormiz. 🍗",
        parse_mode="HTML"
    )


# ── Kuryer callback ───────────────────────────────────────────
async def courier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("courier:"):
        return

    parts = data.split(":")
    order_id, action = parts[1], parts[2]

    order = db.get_by_id(order_id)
    if not order:
        await query.answer("❌ Zakaz topilmadi", show_alert=True)
        return

    order_short = order_id[-6:].upper()
    ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

    if action == "delivering":
        db.update_status(order_id, "delivering")
        # Kuryerga "Yetkazildi" tugmasi
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Yetkazildi", callback_data=f"courier:{order_id}:done")
            ]])
        )
        # Adminga xabar
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🚗 <b>Kuryer yo'lda!</b>\n📦 Zakaz #{order_short}",
                parse_mode="HTML"
            )

    elif action == "done":
        updated = db.update_status(order_id, "done")
        # Kuryerga tasdiqlash
        await query.edit_message_text(
            text=f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\n\nRahmat! 🎉",
            parse_mode="HTML",
            reply_markup=None
        )
        # Adminga xabar
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\nKuryer yetkazib berdi.",
                parse_mode="HTML"
            )
        # Usergа coin + xabar
        phone = updated.get("phone")
        if phone:
            total = updated.get("total", 0)
            coins_used = updated.get("coins_used", 0)
            actual_total = total + (coins_used * 1000)
            earned = max(1, round(actual_total * 0.05 / 1000))
            new_balance = add_coins(phone=phone, amount=earned, order_id=order_id)
            review_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⭐ Izoh qoldirish", callback_data=f"review:{order_id}")
            ]])
            await notify_user(
                context, phone,
                f"🎉 <b>Buyurtmangiz muvaffaqiyatli yetkazildi!</b>\n\n"
                f"🪙 Tabriklaymiz! Sizga <b>+{earned} coin</b> qo'shildi\n"
                f"💰 Bu <b>{earned * 1000:,} UZS</b> chegirmaga teng\n"
                f"📊 Joriy balans: <b>{new_balance} coin</b>\n\n"
                f"Keyingi zakazda ishlatishingiz mumkin! 🛍",
                reply_markup=review_keyboard
            )

# ── App yaratish ─────────────────────────────────────────────
def create_app() -> Application:
    global _app_instance
    token = os.getenv("BOT_TOKEN", "")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^status:"))
    app.add_handler(CallbackQueryHandler(review_callback, pattern="^review:"))
    app.add_handler(CallbackQueryHandler(courier_callback, pattern="^courier:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text))
    _app_instance = app
    return app
