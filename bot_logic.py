# -*- coding: utf-8 -*-
import os
import sqlite3
import logging
from typing import Optional, Tuple
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ApplicationHandlerStop
)
from telegram.request import HTTPXRequest
from pydub import AudioSegment, effects as pd_effects
from deep_translator import GoogleTranslator

# --- FFmpeg for pydub ---
AudioSegment.converter = "ffmpeg"
AudioSegment.ffprobe = "ffprobe"

# --- Config ---
ADMIN_ID = 5765026394
CARD_NUMBER = "6037-xxxx-xxxx-xxxx"
DB_PATH = "bot.db"

# --- UI ---
main_menu = [
    ["🌐 ترجمه متن"],
    ["🎤 تغییر صدا", "🎛️ انتخاب افکت"],
    ["📊 موجودی", "💳 خرید امتیاز"]
]


def effects_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Pitch ↑", callback_data="eff:pitch_up"),
         InlineKeyboardButton("Pitch ↓", callback_data="eff:pitch_down")],
        [InlineKeyboardButton("Speed ↑", callback_data="eff:speed_up"),
         InlineKeyboardButton("Slow", callback_data="eff:slow_down")],
        [InlineKeyboardButton("Robot 🤖", callback_data="eff:robot"),
         InlineKeyboardButton("Echo 🌊", callback_data="eff:echo")],
        [InlineKeyboardButton("Voice ♀️", callback_data="eff:female"),
         InlineKeyboardButton("Voice ♂️", callback_data="eff:male")],
        [InlineKeyboardButton("حذف افکت (عادی)", callback_data="eff:none")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 آمار", callback_data="admin:stats"),
         InlineKeyboardButton("👤 جستجوی کاربر", callback_data="admin:search")],
        [InlineKeyboardButton("➕ افزودن امتیاز", callback_data="admin:add"),
         InlineKeyboardButton("➖ کسر امتیاز", callback_data="admin:sub")]
    ])


def translate_lang_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇷 فارسی", callback_data="trg:fa"),
         InlineKeyboardButton("🇬🇧 English", callback_data="trg:en")],
        [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="trg:tr"),
         InlineKeyboardButton("🇸🇦 العربية", callback_data="trg:ar")],
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="trg:ru"),
         InlineKeyboardButton("🇵🇰 اردو", callback_data="trg:ur")]
    ])


def translate_session_keyboard(trg):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔁 تغییر زبان مقصد (فعلی: {trg})", callback_data="tr:change_lang")],
        [InlineKeyboardButton("⬅️ بازگشت به منوی اصلی",
                              callback_data="tr:back_home")]
    ])


# --- Flags ---
FLAG_AWAIT_TRANSLATE = "await_translate"
KEY_TRG_LANG = "trg_lang"

# --- Logging ---
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("bot")

# ========== DB ==========


def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    return c


def init_db():
    c = get_conn()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, name TEXT, points INTEGER DEFAULT 0, effect TEXT DEFAULT 'none')""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, points INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.commit()
    c.close()


def ensure_user(uid, name=None):
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users(user_id,name,points,effect) VALUES(?,?,0,'none')", (uid, name or ""))
    elif name:
        cur.execute("UPDATE users SET name=? WHERE user_id=?", (name, uid))
    c.commit()
    c.close()


def get_user(uid):
    c = get_conn()
    cur = c.cursor()
    cur.execute(
        "SELECT user_id,name,points,effect FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    c.close()
    if not row:
        ensure_user(uid)
        return get_user(uid)
    return row


def add_points(uid, d): c = get_conn(); c.execute(
    "UPDATE users SET points=points+? WHERE user_id=?", (d, uid)); c.commit(); c.close()


def sub_points(uid, d): c = get_conn(); c.execute(
    "UPDATE users SET points=MAX(points-?,0) WHERE user_id=?", (d, uid)); c.commit(); c.close()


def set_effect(uid, e): c = get_conn(); c.execute(
    "UPDATE users SET effect=? WHERE user_id=?", (e, uid)); c.commit(); c.close()


def get_stats(): c = get_conn(); cur = c.cursor(); cur.execute(
    "SELECT COUNT(*),SUM(points) FROM users"); a, b = cur.fetchone(); c.close(); return (a or 0, b or 0)


def save_payment(uid, amt, pts): c = get_conn(); c.execute(
    "INSERT INTO payments(user_id,amount,points) VALUES(?,?,?)", (uid, amt, pts)); c.commit(); c.close()

# ========== Effects ==========


def get_effect_label(c): return {"none": "بدون افکت", "pitch_up": "Pitch ↑", "pitch_down": "Pitch ↓", "speed_up": "Speed ↑",
                                 "slow_down": "Slow", "robot": "Robot 🤖", "echo": "Echo 🌊", "female": "Voice ♀️", "male": "Voice ♂️"}.get(c, c)


def apply_effect(s, e):
    try:
        if e == "none":
            return s
        if e == "pitch_up":
            return s._spawn(s.raw_data, overrides={"frame_rate": int(s.frame_rate*1.2)}).set_frame_rate(s.frame_rate)
        if e == "pitch_down":
            return s._spawn(s.raw_data, overrides={"frame_rate": int(s.frame_rate*0.85)}).set_frame_rate(s.frame_rate)
        if e == "speed_up":
            return pd_effects.speedup(s, 1.25, 50, 10)
        if e == "slow_down":
            return s._spawn(s.raw_data, overrides={"frame_rate": int(s.frame_rate*0.85)}).set_frame_rate(s.frame_rate)
        if e == "robot":
            return (s.low_pass_filter(4000).high_pass_filter(200)-8).overlay(s, position=15)
        if e == "echo":
            out = s
            for i, d in enumerate([120, 240, 360], 1):
                out = out.overlay(s-(8*i), position=d)
            return out
        if e == "female":
            return s._spawn(s.raw_data, overrides={"frame_rate": int(s.frame_rate*1.15)}).set_frame_rate(s.frame_rate).high_pass_filter(300)
        if e == "male":
            return s._spawn(s.raw_data, overrides={"frame_rate": int(s.frame_rate*0.9)}).set_frame_rate(s.frame_rate).low_pass_filter(3000)
        return s
    except:
        logger.exception("fx")
        return s

# ========== Handlers ==========


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id, update.effective_user.full_name)
    context.user_data[FLAG_AWAIT_TRANSLATE] = False
    await update.message.reply_text(
        "سلام 👋\nبه ربات تغییر صدا + ترجمه خوش اومدی!",
        reply_markup=ReplyKeyboardMarkup(main_menu, resize_keyboard=True)
    )


async def ping(u, c): await u.message.reply_text("✅ ربات روشنه.")


async def balance(u, c): ensure_user(u.effective_user.id); _, _, p, e = get_user(
    u.effective_user.id); await u.message.reply_text(f"📊 موجودی: {p}\n🎛️ افکت: {get_effect_label(e)}")


async def buy(u, c): await u.message.reply_text(
    f"💳 شماره کارت:\n{CARD_NUMBER}\n\nبه ازای هر ۱۰هزار تومان → ۲۰۰ امتیاز")


async def choose_effect(u, c): _, _, _, e = get_user(u.effective_user.id); await u.message.reply_text(
    f"🎛️ افکت فعلی: {get_effect_label(e)}", reply_markup=effects_keyboard())


async def effect_callback(u, c): code = u.callback_query.data.split(":")[1]; set_effect(
    u.effective_user.id, code); await u.callback_query.edit_message_text(f"✅ افکت: {get_effect_label(code)}")


async def change_voice(u, c): await u.message.reply_text(
    "🎤 یک ویس یا فایل صوتی بفرست.")


async def voice_handler(u, c):
    uid = u.effective_user.id
    ensure_user(uid)
    _, _, pts, eff = get_user(uid)
    if pts <= 0:
        await u.message.reply_text("❌ امتیاز کافی ندارید.")
        return
    f = await (u.message.voice or u.message.audio).get_file()
    name = "in.ogg"
    out = "out.ogg"
    await f.download_to_drive(name)
    s = AudioSegment.from_file(name)
    p = apply_effect(s, eff)
    p.export(out, format="ogg")
    sub_points(uid, 1)
    await u.message.reply_voice(open(out, "rb"), caption=f"✅ انجام شد. افکت: {get_effect_label(eff)}")
    os.remove(name)
    os.remove(out)


async def translate_menu(u, c): c.user_data[FLAG_AWAIT_TRANSLATE] = True; c.user_data[KEY_TRG_LANG] = "fa"; await u.message.reply_text(
    "🌐 زبان مقصد:", reply_markup=translate_lang_keyboard())


async def translate_lang_callback(u, c): trg = u.callback_query.data.split(
    ":")[1]; c.user_data[FLAG_AWAIT_TRANSLATE] = True; c.user_data[KEY_TRG_LANG] = trg; await u.callback_query.edit_message_text(f"✅ زبان مقصد: {trg}")


async def translate_text_interceptor(u, c):
    if not c.user_data.get(FLAG_AWAIT_TRANSLATE):
        return
    t = u.message.text
    if t.startswith("/"):
        return
    trg = c.user_data.get(KEY_TRG_LANG, "fa")
    try:
        out = GoogleTranslator(source="auto", target=trg).translate(t)
        await u.message.reply_text(f"🔁 ترجمه ({trg}):\n{out}", reply_markup=translate_session_keyboard(trg))
    except:
        await u.message.reply_text("❌ خطا در ترجمه.")
    raise ApplicationHandlerStop


async def tr_cmd(u, c): trg, txt = c.args[0], " ".join(c.args[1:]); out = GoogleTranslator(
    source="auto", target=trg).translate(txt); await u.message.reply_text(f"🔁 {out}")


async def receipt_handler(u, c): await c.bot.forward_message(
    ADMIN_ID, u.message.chat_id, u.message.message_id); await u.message.reply_text("✅ رسید ارسال شد.")


async def confirm(u, c):
    if u.effective_user.id != ADMIN_ID:
        return
    uid, amt = int(c.args[0]), int(c.args[1])
    pts = (amt//10000)*200
    add_points(uid, pts)
    save_payment(uid, amt, pts)
    await c.bot.send_message(uid, f"✅ {pts} امتیاز اضافه شد.")


async def admin_panel(u, c): await u.message.reply_text(
    "🛠 پنل ادمین:", reply_markup=admin_keyboard())


async def admin_callback(u, c):
    d = u.callback_query.data
    if d == "admin:stats":
        a, b = get_stats()
        await u.callback_query.edit_message_text(f"📈 کاربران: {a}, امتیاز: {b}")

# ========== Build ==========


def build_application(token, request: HTTPXRequest) -> Application:
    init_db()
    app = Application.builder().token(token).request(request).build()
    app.add_handler(MessageHandler(
        filters.TEXT, translate_text_interceptor), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("tr", tr_cmd))
    app.add_handler(MessageHandler(filters.Regex("📊 موجودی"), balance))
    app.add_handler(MessageHandler(filters.Regex("💳 خرید امتیاز"), buy))
    app.add_handler(MessageHandler(filters.Regex("🎤 تغییر صدا"), change_voice))
    app.add_handler(MessageHandler(
        filters.Regex("🎛️ انتخاب افکت"), choose_effect))
    app.add_handler(CallbackQueryHandler(effect_callback, pattern=r"^eff:"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    app.add_handler(CallbackQueryHandler(
        translate_lang_callback, pattern=r"^trg:"))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO, voice_handler))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))
    return app
