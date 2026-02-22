"""
bot.py — Telegram Admin Bot + OTP yuborish
python-telegram-bot 21.x

Featurelar:
  - Admin: Zakaz boshqaruvi (pending → confirmed → cooking → ready → delivering → done)
  - Kuryer: ready bo'lganda kuryerga xabar, kuryer yetkazib beradi
  - User: Har bir statusda Telegram bildirishnomasi
  - Coin: Yetkazilgandan keyin 5% coin (har 1000 UZS = 1 coin)
  - Review: Yetkazilgandan keyin user izoh qoldirishi mumkin
  - Admin: 📊 Statistika tugmasi — oylik statistika
"""
import os
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
#  STATUS lug'ati
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

# ═══════════════════════════════════════════════════════════════
#  Coin yordamchi funksiyalari
# ═══════════════════════════════════════════════════════════════

def add_coins(phone: str, amount: int, order_id: str) -> int:
    """
    Userga coin qo'shadi.
    Har 1000 UZS = 1 coin (zakaz summasining 5%-i).
    Yangi balansni qaytaradi.
    """
    try:
        user = db.get_telegram_user(phone)
        if not user:
            return 0
        current = int(user.get("coins", 0) or 0)
        new_balance = current + amount
        db.update_telegram_user_coins(phone=phone, coins=new_balance)
        return new_balance
    except Exception as e:
        print(f"add_coins xato ({phone}): {e}")
        return 0


def get_coin_balance(phone: str) -> int:
    """Userning hozirgi coin balansini qaytaradi."""
    try:
        user = db.get_telegram_user(phone)
        if not user:
            return 0
        return int(user.get("coins", 0) or 0)
    except Exception:
        return 0

# ═══════════════════════════════════════════════════════════════
#  User bildirishnoma yordamchisi
# ═══════════════════════════════════════════════════════════════

async def notify_user(
    ctx: ContextTypes.DEFAULT_TYPE,
    phone: str,
    text: str,
    reply_markup=None,
):
    """
    Foydalanuvchiga Telegram xabar yuboradi.
    Telefon raqami orqali chat_id topiladi.
    """
    try:
        tg_user = db.get_telegram_user(phone)
        if not tg_user or not tg_user.get("chat_id"):
            return
        kwargs = dict(
            chat_id=int(tg_user["chat_id"]),
            text=text,
            parse_mode="HTML",
        )
        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        await ctx.bot.send_message(**kwargs)
    except Exception as e:
        print(f"notify_user xato ({phone}): {e}")

# ═══════════════════════════════════════════════════════════════
#  Zakaz xabari matni
# ═══════════════════════════════════════════════════════════════

def build_order_message(order: dict) -> str:
    """Adminga ko'rsatiladigan zakaz xabarini shakllantiradi."""
    items = order.get("items", [])
    lines = "\n".join(
        f"  • {i.get('fullName') or i.get('name')} x {i['quantity']} — "
        f"{i['price'] * i['quantity']:,} UZS"
        for i in items
    )
    emoji, label = STATUS.get(order["status"], ("🕐", "Kutilmoqda"))

    payment_map = {"naqt": "💵 Naqt", "card": "💳 Karta"}
    payment = payment_map.get(order.get("payment", "naqt"), "💵 Naqt")

    extra_phone = order.get("extra_phone")
    comment     = order.get("comment")
    customer    = order.get("customer_name", "")
    phone       = order.get("phone", "")
    coins_used  = order.get("coins_used", 0) or 0

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

    return (
        f"🛒 <b>Yangi zakaz #{order['id'][-6:].upper()}</b>\n"
        f"───────────────\n"
        f"📍 <b>Manzil:</b> {order['address']}\n\n"
        f"🍽 <b>Tarkib:</b>\n{lines}\n\n"
        f"💳 <b>Jami:</b> {order['total']:,} UZS\n"
        f"💰 <b>To'lov:</b> {payment}\n"
        f"{extra_lines}"
        f"⏰ <b>Vaqt:</b> {order['created_at'][:16].replace('T', ' ')}\n\n"
        f"{emoji} <b>Status:</b> {label}"
    )

# ═══════════════════════════════════════════════════════════════
#  Inline tugmalar (admin zakaz boshqaruvi)
# ═══════════════════════════════════════════════════════════════

def build_keyboard(order_id: str, status: str) -> InlineKeyboardMarkup | None:
    """
    Har bir status uchun tegishli admin tugmalarini yaratadi.
    pending    → Tasdiqlash / Bekor qilish
    confirmed  → Tayyorlanmoqda / Bekor qilish
    cooking    → Ovqat tayyor (kuryerga yuborish)
    ready      → (kuryer boshqaradi, admin tugma yo'q)
    delivering → (kuryer boshqaradi)
    done/cancelled → tugma yo'q
    """
    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton("✅ Tasdiqlash",    callback_data=f"status:{order_id}:confirmed"),
            InlineKeyboardButton("❌ Bekor qilish",  callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "confirmed":
        rows.append([
            InlineKeyboardButton("🍗 Tayyorlanmoqda", callback_data=f"status:{order_id}:cooking"),
            InlineKeyboardButton("❌ Bekor qilish",   callback_data=f"status:{order_id}:cancelled"),
        ])
    elif status == "cooking":
        rows.append([
            InlineKeyboardButton("🍗 Ovqat tayyor", callback_data=f"status:{order_id}:ready"),
        ])
    # ready, delivering, done, cancelled — admin inline tugma kerak emas

    return InlineKeyboardMarkup(rows) if rows else None

# ═══════════════════════════════════════════════════════════════
#  Global app instance (main.py dan chaqirish uchun)
# ═══════════════════════════════════════════════════════════════

_app_instance = None


def _get_app() -> Application | None:
    return _app_instance

# ═══════════════════════════════════════════════════════════════
#  Adminga bildirishnomalar (main.py / webhook dan chaqiriladi)
# ═══════════════════════════════════════════════════════════════

async def notify_new_order(order: dict):
    """
    Yangi zakaz kelganda adminga xabar yuboradi.
    Telegram message ID ni orders.json ga saqlaydi.
    """
    app = _get_app()
    if not app:
        return
    admin_id = os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        print("⚠️  ADMIN_CHAT_ID environment variable o'rnatilmagan!")
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
        print(f"notify_new_order xato: {e}")


async def notify_cancelled(order: dict):
    """
    Zakaz bekor qilinganda adminga xabar yuboradi.
    (User tomonidan bekor qilinganda chaqiriladi.)
    """
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
                f"❌ <b>Zakaz bekor qilindi #{order['id'][-6:].upper()}</b>\n"
                f"💳 {order['total']:,} UZS\n"
                f"👤 {order.get('customer_name', '')} {order.get('phone', '')}"
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
    """
    Admin: Chat ID + 📊 Statistika tugmasi ko'rsatiladi.
    User:  Telefon raqam so'raladi (ReplyKeyboard button).
    """
    chat_id  = update.effective_chat.id
    admin_id = os.getenv("ADMIN_CHAT_ID", "")

    if str(chat_id) == str(admin_id):
        admin_kb = ReplyKeyboardMarkup(
            [["📊 Statistika"]],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            f"👋 <b>KFC Admin Bot</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            f"Quyidagi tugmalardan foydalaning:",
            parse_mode="HTML",
            reply_markup=admin_kb,
        )
        return

    # Allaqachon ro'yxatdan o'tganmi? (chat_id bo'yicha tekshirish)
    existing = db.get_telegram_user_by_chat_id(str(chat_id))
    if existing:
        WEBSITE_URL = os.getenv("WEBSITE_URL", "https://your-site.com")
        first = (existing.get("full_name") or "").split()[0] or "do'st"
        await update.message.reply_text(
            f"👋 <b>Salom, {first}!</b>\n\n"
            f"📱 Raqamingiz saqlangan: <code>{existing.get('phone', '')}</code>\n\n"
            f"Buyurtma berish uchun saytni oching:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🍗 Ochish / Открыть", url=WEBSITE_URL)
            ]]),
        )
        return

    # Yangi foydalanuvchi — telefon so'raladi
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
#  Kontakt qabul qilish (foydalanuvchi telefon yuboradi)
# ═══════════════════════════════════════════════════════════════

async def handle_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi telefon raqamini yuborganda:
    1. Raqam normallashtiriladi (+998XXXXXXXXX)
    2. telegram_users.json ga saqlanadi
    3. Saytni ochish uchun inline button yuboriladi
    """
    contact = update.message.contact
    chat_id = update.effective_chat.id

    # Boshqaning kontaktini tekshirish
    if contact.user_id and contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            "❌ Iltimos, faqat <b>o'z raqamingizni</b> yuboring.",
            parse_mode="HTML",
        )
        return

    # Raqamni normallashtirish
    phone = contact.phone_number.replace("+", "").replace(" ", "")
    if not phone.startswith("998"):
        phone = "998" + phone[-9:]
    phone = "+" + phone

    WEBSITE_URL = os.getenv("WEBSITE_URL", "https://your-site.com")

    # Klaviaturani yopamiz (ReplyKeyboardRemove yuborib darhol o'chiramiz)
    remove_msg = await update.message.reply_text(
        "⏳", reply_markup=ReplyKeyboardRemove()
    )
    try:
        await remove_msg.delete()
    except Exception:
        pass

    # Allaqachon ro'yxatdan o'tganmi? — qayta saqlamaymiz
    existing = db.get_telegram_user_by_chat_id(str(chat_id))
    if existing:
        first = (existing.get("full_name") or contact.first_name or "do'st").split()[0]
        await update.message.reply_text(
            f"👋 <b>Salom, {first}!</b>\n\n"
            f"📱 Raqamingiz allaqachon saqlangan: <code>{existing.get('phone', '')}</code>\n\n"
            f"Buyurtma berish uchun saytni oching:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🍗 Ochish / Открыть", url=WEBSITE_URL)
            ]]),
        )
        return

    # Yangi user — saqlash
    full_name = " ".join(filter(None, [
        contact.first_name,
        contact.last_name or "",
    ]))
    db.save_telegram_user(phone=phone, chat_id=str(chat_id), full_name=full_name)

    await update.message.reply_text(
        f"🇺🇿 <b>Assalomu alaykum, {contact.first_name}!</b> 👋\n"
        f"Buyurtma berish uchun tugmani bosing ⬇️\n\n"
        f"🇷🇺 <b>Здравствуйте!</b> 👋\n"
        f"Нажмите кнопку ниже для заказа ⬇️",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🍗 Ochish / Открыть", url=WEBSITE_URL)
        ]]),
    )

# ═══════════════════════════════════════════════════════════════
#  Admin status callback (inline tugmalar: status:id:status)
# ═══════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Admin tomonidan inline tugma bosilganda ishga tushadi.
    Statusni yangilaydi va kerakli bildirishnomalarni yuboradi.

    confirmed → userga xabar
    ready     → kuryerga xabar (COURIER_CHAT_ID)
    done      → userga coin + review tugma  (agar courier yo'q bo'lsa)
    """
    query = update.callback_query

    data = query.data
    if not data.startswith("status:"):
        return

    parts    = data.split(":")
    order_id, new_status = parts[1], parts[2]

    order = db.get_by_id(order_id)
    if not order:
        await query.answer("❌ Zakaz topilmadi", show_alert=True)
        return

    updated  = db.update_status(order_id, new_status)
    keyboard = build_keyboard(order_id, new_status)

    try:
        await query.edit_message_text(
            text=build_order_message(updated),
            parse_mode="HTML",
            reply_markup=keyboard or InlineKeyboardMarkup([]),
        )
    except Exception as e:
        print(f"Xabarni yangilashda xato: {e}")

    emoji, label = STATUS.get(new_status, ("✅", new_status))
    await query.answer(f"{emoji} {label}")

    order_short = order_id[-6:].upper()

    # ── "confirmed" → userga tasdiqlash xabari ──────────────
    if new_status == "confirmed":
        phone = updated.get("phone")
        total = updated.get("total", 0)
        if phone:
            await notify_user(
                ctx, phone,
                f"✅ <b>Buyurtmangiz tasdiqlandi!</b>\n\n"
                f"📦 Buyurtma: <b>#{order_short}</b>\n"
                f"💰 Summa: <b>{total:,} UZS</b>\n\n"
                f"🍗 Tayyorlanmoqda, tez orada yetkazamiz!"
            )

    # ── "ready" → kuryerga yetkazish xabari ─────────────────
    if new_status == "ready":
        COURIER_CHAT_ID = os.getenv("COURIER_CHAT_ID", "")
        if COURIER_CHAT_ID:
            address   = updated.get("address", "—")
            items     = updated.get("items", [])
            items_text = "\n".join(
                f"  • {i.get('fullName') or i.get('name')} x{i['quantity']}"
                for i in items
            )
            total    = updated.get("total", 0)
            phone    = updated.get("phone", "—")
            customer = updated.get("customer_name", "")

            courier_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🚗 Yetkazilmoqda",
                    callback_data=f"courier:{order_id}:delivering"
                ),
            ]])
            try:
                await ctx.bot.send_message(
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
                    reply_markup=courier_kb,
                )
            except Exception as e:
                print(f"Kuryerga xabar yuborishda xato: {e}")
        else:
            # Kuryer yo'q — admin o'zi yetkazadi
            # "done" statusini admin inline tugmasiz ham bosishi uchun
            pass

    # ── "done" → coin + review (agar COURIER_CHAT_ID yo'q bo'lsa) ──
    if new_status == "done":
        COURIER_CHAT_ID = os.getenv("COURIER_CHAT_ID", "")
        if not COURIER_CHAT_ID:
            phone      = updated.get("phone")
            total      = updated.get("total", 0)
            coins_used = updated.get("coins_used", 0) or 0
            if phone:
                actual_total = total + (coins_used * 1000)
                earned       = max(1, round(actual_total * 0.05 / 1000))
                new_balance  = add_coins(phone=phone, amount=earned, order_id=order_id)

                review_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⭐ Izoh qoldirish",
                        callback_data=f"review:{order_id}"
                    )
                ]])
                await notify_user(
                    ctx, phone,
                    f"🎉 <b>Buyurtmangiz yetkazildi!</b>\n\n"
                    f"🪙 Sizga <b>+{earned} coin</b> qo'shildi\n"
                    f"💰 Bu <b>{earned * 1000:,} UZS</b> chegirmaga teng\n"
                    f"📊 Joriy balans: <b>{new_balance} coin</b>\n\n"
                    f"Keyingi zakazda ishlatishingiz mumkin! 🛍",
                    reply_markup=review_kb,
                )

# ═══════════════════════════════════════════════════════════════
#  Kuryer callback (courier:id:action)
# ═══════════════════════════════════════════════════════════════

async def courier_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Kuryer inline tugmalarini boshqaradi:
    delivering → adminga + userga "yo'lda" xabari
    done       → adminga + userga "yetkazildi" + coin + review
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("courier:"):
        return

    parts    = data.split(":")
    order_id = parts[1]
    action   = parts[2]

    order = db.get_by_id(order_id)
    if not order:
        await query.answer("❌ Zakaz topilmadi", show_alert=True)
        return

    order_short = order_id[-6:].upper()
    admin_id_str = os.getenv("ADMIN_CHAT_ID", "0")
    ADMIN_CHAT  = int(admin_id_str) if admin_id_str.isdigit() else 0

    # ── Kuryer yo'lga chiqdi ─────────────────────────────────
    if action == "delivering":
        db.update_status(order_id, "delivering")

        # Kuryerga "Yetkazildi" tugmasi chiqadi
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Yetkazildi",
                    callback_data=f"courier:{order_id}:done"
                )
            ]])
        )

        # Adminga xabar
        if ADMIN_CHAT:
            try:
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT,
                    text=f"🚗 <b>Kuryer yo'lda!</b>\n📦 Zakaz #{order_short}",
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"Adminga delivering xabari xato: {e}")

        # Userga xabar
        phone = order.get("phone")
        if phone:
            await notify_user(
                ctx, phone,
                f"🚗 <b>Kuryer yo'lda!</b>\n\n"
                f"📦 Zakaz #{order_short} sizga yetib kelmoqda.\n"
                f"Iltimos, tayyor bo'ling! 🍗"
            )

    # ── Yetkazildi ───────────────────────────────────────────
    elif action == "done":
        updated = db.update_status(order_id, "done")

        # Kuryerga tasdiqlash
        await query.edit_message_text(
            text=f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\n\nRahmat! 🎉",
            parse_mode="HTML",
            reply_markup=None,
        )

        # Adminga xabar
        if ADMIN_CHAT:
            try:
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT,
                    text=(
                        f"✅ <b>Zakaz #{order_short} yetkazildi!</b>\n"
                        f"Kuryer yetkazib berdi."
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"Adminga done xabari xato: {e}")

        # Userga coin + review
        phone = (updated or order).get("phone")
        if phone:
            total      = (updated or order).get("total", 0)
            coins_used = (updated or order).get("coins_used", 0) or 0
            actual_total = total + (coins_used * 1000)
            earned       = max(1, round(actual_total * 0.05 / 1000))
            new_balance  = add_coins(phone=phone, amount=earned, order_id=order_id)

            review_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⭐ Izoh qoldirish",
                    callback_data=f"review:{order_id}"
                )
            ]])
            await notify_user(
                ctx, phone,
                f"🎉 <b>Buyurtmangiz muvaffaqiyatli yetkazildi!</b>\n\n"
                f"🪙 Tabriklaymiz! Sizga <b>+{earned} coin</b> qo'shildi\n"
                f"💰 Bu <b>{earned * 1000:,} UZS</b> chegirmaga teng\n"
                f"📊 Joriy balans: <b>{new_balance} coin</b>\n\n"
                f"Keyingi zakazda ishlatishingiz mumkin! 🛍",
                reply_markup=review_kb,
            )

# ═══════════════════════════════════════════════════════════════
#  Review callback (review:order_id)
# ═══════════════════════════════════════════════════════════════

async def review_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    "⭐ Izoh qoldirish" tugmasi bosilganda:
    Foydalanuvchidan matnli izoh so'raladi.
    user_data["awaiting_review"] ga order_id saqlanadi.
    """
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("review:"):
        return

    order_id = query.data.split(":")[1]
    ctx.user_data["awaiting_review"] = order_id

    await query.message.reply_text(
        f"✍️ <b>Izohingizni yozing</b>\n\n"
        f"#{order_id[-6:].upper()} buyurtma haqida fikringizni bildiring.\n"
        f"(Masalan: ovqat mazasi, yetkazib berish tezligi va h.k.)",
        parse_mode="HTML",
    )


async def handle_review_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi izoh matni yuborganda adminga jo'natiladi.
    Faqat "awaiting_review" key user_data da bo'lsa ishlaydi.
    """
    if "awaiting_review" not in ctx.user_data:
        return

    order_id    = ctx.user_data.pop("awaiting_review")
    review_text = update.message.text
    user        = update.effective_user

    ADMIN_ID_STR = os.getenv("ADMIN_CHAT_ID", "0")
    ADMIN_CHAT   = int(ADMIN_ID_STR) if ADMIN_ID_STR.isdigit() else 0

    if ADMIN_CHAT:
        try:
            await ctx.bot.send_message(
                chat_id=ADMIN_CHAT,
                text=(
                    f"💬 <b>Yangi izoh!</b>\n\n"
                    f"📦 Buyurtma: #{order_id[-6:].upper()}\n"
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
#  /orders komandasi
# ═══════════════════════════════════════════════════════════════

async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Oxirgi 10 ta zakaz ro'yxatini ko'rsatadi."""
    orders = db.get_all(limit=10)
    if not orders:
        await update.message.reply_text("📭 Hali zakaz yo'q.")
        return
    lines = []
    for o in orders:
        emoji, label = STATUS.get(o["status"], ("🕐", o["status"]))
        lines.append(
            f"{emoji} #{o['id'][-6:].upper()} — "
            f"{o['total']:,} UZS — {label}"
        )
    await update.message.reply_text(
        "📋 <b>Oxirgi zakazlar:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )

# ═══════════════════════════════════════════════════════════════
#  /stats komandasi — bugungi statistika
# ═══════════════════════════════════════════════════════════════

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Bugungi zakaz statistikasini ko'rsatadi."""
    s = db.stats_today()
    await update.message.reply_text(
        f"📊 <b>Bugungi statistika</b>\n\n"
        f"📦 Jami zakazlar : {s['total']}\n"
        f"🎉 Yetkazildi   : {s['done']}\n"
        f"🕐 Kutilmoqda   : {s['pending']}\n"
        f"❌ Bekor        : {s['cancelled']}\n"
        f"💰 Daromad      : {s['revenue']:,} UZS",
        parse_mode="HTML",
    )

# ═══════════════════════════════════════════════════════════════
#  📊 Statistika tugmasi — faqat admin, oylik
# ═══════════════════════════════════════════════════════════════

async def handle_statistics_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Admin "📊 Statistika" tugmasini bosganida chaqiriladi.
    Joriy oy bo'yicha har bir user nechta zakaz qilganini ko'rsatadi.
    Har oy avtomatik yangilanadi (joriy oy hisoblanadi).
    """
    chat_id  = update.effective_chat.id
    admin_id = os.getenv("ADMIN_CHAT_ID", "")

    if str(chat_id) != str(admin_id):
        return  # Boshqalar uchun hech narsa

    s = db.stats_monthly()

    lines = [
        f"📊 <b>Oylik statistika — {s['month_label']}</b>\n",
        f"📦 Jami zakazlar : <b>{s['total']}</b>",
        f"✅ Yetkazildi    : <b>{s['done']}</b>",
        f"❌ Bekor qilindi : <b>{s['cancelled']}</b>",
        f"💰 Daromad       : <b>{s['revenue']:,} UZS</b>",
        "",
        "👤 <b>Userlar bo'yicha:</b>",
    ]

    if not s["users"]:
        lines.append("  — bu oyda zakaz yo'q")
    else:
        for i, u in enumerate(s["users"], 1):
            rev_str    = f"  💵 {u['revenue']:,} UZS" if u["revenue"] else ""
            cancel_str = f"  ❌{u['cancelled']}" if u["cancelled"] else ""
            lines.append(
                f"{i}. {u['name']} ({u['phone']})\n"
                f"   📦 {u['total']} zakaz  ✅{u['done']}{cancel_str}{rev_str}"
            )

    text = "\n".join(lines)

    # Telegram 4096 belgi cheklovi
    if len(text) <= 4096:
        await update.message.reply_text(text, parse_mode="HTML")
    else:
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
#  App yaratish va handlerlarni ro'yxatga olish
# ═══════════════════════════════════════════════════════════════

def create_app() -> Application:
    global _app_instance

    token = os.getenv("BOT_TOKEN", "")
    if not token:
        print("⚠️  BOT_TOKEN environment variable o'rnatilmagan!")

    app = Application.builder().token(token).build()

    # ── Komandalar ───────────────────────────────────────────
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("stats",  cmd_stats))

    # ── Kontakt (user telefon yuboradi) ──────────────────────
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # ── Admin tugmalar (ReplyKeyboard) ────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^📊 Statistika$"),
        handle_statistics_btn,
    ))

    # ── User izoh matni ───────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_review_text,
    ))

    # ── Inline callback handlers (aniq pattern bilan) ─────────
    app.add_handler(CallbackQueryHandler(review_callback,  pattern=r"^review:"))
    app.add_handler(CallbackQueryHandler(courier_callback, pattern=r"^courier:"))
    app.add_handler(CallbackQueryHandler(handle_callback,  pattern=r"^status:"))

    _app_instance = app
    return app
  
