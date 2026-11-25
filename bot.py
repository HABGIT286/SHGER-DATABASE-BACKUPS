
"""
Business Administration Library
Telegram Bot + FastAPI + SQLite
Inline keyboard only.
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import html
import asyncio
import logging
import sqlite3
import mimetypes

from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramConflictError

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.middleware.cors import CORSMiddleware


# =========================================================
# CONFIGURATION
# =========================================================

BASE = Path(__file__).resolve().parent

TOKEN = os.getenv("TOKKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
ADMIN_CODE = os.getenv("ADMIN_CODE", "").strip()

GROUP_LINK = os.getenv(
    "GROUP_LINK",
    "https://t.me/+mZe3EZzQBCYxOTUy"
).strip()

SITE_URL = os.getenv("SITE_URL", "").strip()

DEV_LINK = "https://t.me/ONX44"

DB_PATH = BASE / "library.sqlite3"

BOT_USERNAME = ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("business_library")


# =========================================================
# VALIDATION
# =========================================================

if not TOKEN:
    raise RuntimeError(
        "TOKKEN is missing. Add it to GitHub Actions Secrets."
    )


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def init_db():
    with db() as conn:

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'عام',
            uploader INTEGER DEFAULT 0,
            file_id TEXT DEFAULT '',
            file_type TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            downloads INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            pinned INTEGER DEFAULT 0,
            published INTEGER DEFAULT 1,
            created_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            PRIMARY KEY(user_id, file_id)
        );

        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visitor TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            visitor TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            user_id INTEGER DEFAULT 0,
            details TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0
        );
        """)

        conn.commit()


def log_activity(action, user_id=0, details=""):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO activity(action, user_id, details, created_at)
            VALUES(?,?,?,?)
            """,
            (action, user_id, details, int(time.time()))
        )


def get_file(file_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM files WHERE id=?",
            (file_id,)
        ).fetchone()


def file_exists(file_id):
    with db() as conn:
        return conn.execute(
            "SELECT id FROM files WHERE id=?",
            (file_id,)
        ).fetchone() is not None


# =========================================================
# INLINE KEYBOARDS
# All buttons are InlineKeyboardButton.
# =========================================================

def main_keyboard():

    rows = []

    if SITE_URL:
        rows.append([
            InlineKeyboardButton(
                text="📚 فتح المكتبة الإلكترونية",
                url=SITE_URL
            )
        ]

    rows.extend([
        [
            InlineKeyboardButton(
                text="📖 مجموعة المكتبة",
                url=GROUP_LINK
            )
        ],
        [
            InlineKeyboardButton(
                text="ℹ️ معلومات المكتبة",
                callback_data="library_info"
            )
        ],
        [
            InlineKeyboardButton(
                text="👨‍💻 المطور",
                url=DEV_LINK
            )
        ],
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def info_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📚 المكتبة",
                    url=SITE_URL
                )
            ] if SITE_URL else [
                InlineKeyboardButton(
                    text="📖 المجموعة",
                    url=GROUP_LINK
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 العودة",
                    callback_data="home"
                )
            ]
        ]
    )


def admin_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📚 عرض الملفات",
                    callback_data="admin_files"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 الإحصائيات",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 الرئيسية",
                    callback_data="home"
                )
            ]
        ]
    )


def file_admin_keyboard(file_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📌 تثبيت / إلغاء تثبيت",
                    callback_data=f"pin:{file_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ تعديل الوصف",
                    callback_data=f"edit:{file_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 حذف الملف",
                    callback_data=f"delete:{file_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 الإدارة",
                    callback_data="admin_files"
                )
            ]
        ]
    )


# =========================================================
# ADMIN STATE
# =========================================================

admin_sessions = set()
awaiting_admin_code = set()
awaiting_description = set()
awaiting_edit_description = {}

pending_uploads = {}


def is_admin(user_id):
    if not ADMIN_ID:
        return False

    return str(user_id) == ADMIN_ID


def admin_allowed(user_id):
    return user_id in admin_sessions and is_admin(user_id)


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(message: Message):

    text = (
        "╭━━━ ❮Ᏼ𝙾𝙾𝙺 • 𝗕𝗢𝗧 ❯ ━━━╮\n\n"
        "أهلاً بك في المكتبة الإلكترونية "
        "الخاصة بقسم إدارة الأعمال 📚\n\n"
        "منصة تجمع الكتب والملازم والمواد "
        "الدراسية وتتيح الوصول إليها بسهولة.\n\n"
        "اختر أحد الخيارات أدناه:\n\n"
        "╰━━━━━━━━━━━━━━━━━━╯"
    )

    await message.answer(
        text,
        reply_markup=main_keyboard()
    )


# =========================================================
# HELP
# =========================================================

@dp.message(Command("help"))
async def help_command(message: Message):

    text = (
        "📚 <b>دليل استخدام المكتبة</b>\n\n"
        "/start — فتح القائمة الرئيسية\n"
        "/help — عرض المساعدة\n"
        "/about — معلومات المكتبة\n"
        "/cancel — إلغاء العملية الحالية\n\n"
        "يمكنك استخدام أزرار الرسائل للتنقل "
        "داخل المكتبة."
    )

    await message.answer(
        text,
        reply_markup=main_keyboard()
    )


@dp.message(Command("about"))
async def about_command(message: Message):

    await message.answer(
        "📖 <b>عن المكتبة</b>\n\n"
        "مكتبة إلكترونية مخصصة لطلبة قسم إدارة الأعمال، "
        "تهدف إلى تنظيم المواد والكتب الدراسية وتسهيل "
        "الوصول إلى المصادر التعليمية.\n\n"
        "نسعى إلى جعل المحتوى الدراسي أكثر سهولة "
        "وتنظيماً.",
        reply_markup=info_keyboard()
    )


# =========================================================
# CANCEL
# =========================================================

@dp.message(Command("cancel"))
async def cancel_command(message: Message):

    user_id = message.from_user.id

    awaiting_admin_code.discard(user_id)
    awaiting_description.discard(user_id)
    awaiting_edit_description.pop(user_id, None)
    pending_uploads.pop(user_id, None)

    await message.answer(
        "تم إلغاء العملية.",
        reply_markup=main_keyboard()
    )


# =========================================================
# CALLBACKS
# =========================================================

@dp.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery):

    await callback.answer()

    try:
        await callback.message.edit_text(
            "❮Ᏼ𝙾𝙾𝙆 • 𝗕𝗢𝗧 ❯\n\n"
            "القائمة الرئيسية للمكتبة 📚\n"
            "اختر ما تريد:",
            reply_markup=main_keyboard()
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "library_info")
async def library_info_callback(callback: CallbackQuery):

    await callback.answer()

    text = (
        "📚 <b>مكتبة إدارة الأعمال</b>\n\n"
        "مساحة تعليمية تجمع مصادر ومراجع ومواد "
        "دراسية متخصصة في إدارة الأعمال.\n\n"
        "يمكنك زيارة الموقع الإلكتروني أو الانضمام "
        "إلى مجموعة المكتبة للوصول إلى التحديثات."
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=info_keyboard()
        )
    except TelegramBadRequest:
        pass


# =========================================================
# ADMIN LOGIN
# =========================================================

@dp.message(Command("admin"))
async def admin_login(message: Message):

    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.answer("⛔ ليس لديك صلاحية الإدارة.")
        return

    awaiting_admin_code.add(user_id)

    await message.answer(
        "🔐 <b>لوحة الإدارة</b>\n\n"
        "أرسل رمز الإدارة لإكمال تسجيل الدخول.\n"
        "للإلغاء استخدم /cancel"
    )


@dp.message(lambda m: m.from_user and m.from_user.id in awaiting_admin_code)
async def check_admin_code(message: Message):

    user_id = message.from_user.id

    awaiting_admin_code.discard(user_id)

    if not ADMIN_CODE or message.text != ADMIN_CODE:
        await message.answer("❌ رمز الإدارة غير صحيح.")
        return

    admin_sessions.add(user_id)

    await message.answer(
        "✅ تم تسجيل دخول الإدارة بنجاح.",
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADMIN MENU CALLBACKS
# =========================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):

    if not admin_allowed(callback.from_user.id):
        await callback.answer("غير مصرح.", show_alert=True)
        return

    await callback.answer()

    with db() as conn:

        files_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE published=1"
        ).fetchone()[0]

        downloads_count = conn.execute(
            "SELECT COUNT(*) FROM downloads"
        ).fetchone()[0]

        visits_count = conn.execute(
            "SELECT COUNT(*) FROM visits"
        ).fetchone()[0]

        likes_count = conn.execute(
            "SELECT COUNT(*) FROM likes"
        ).fetchone()[0]

    text = (
        "📊 <b>إحصائيات المكتبة</b>\n\n"
        f"📚 الملفات المنشورة: <b>{files_count}</b>\n"
        f"⬇️ التنزيلات: <b>{downloads_count}</b>\n"
        f"👁 الزيارات: <b>{visits_count}</b>\n"
        f"❤️ الإعجابات: <b>{likes_count}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )


@dp.callback_query(F.data == "admin_files")
async def admin_files_callback(callback: CallbackQuery):

    if not admin_allowed(callback.from_user.id):
        await callback.answer("غير مصرح.", show_alert=True)
        return

    await callback.answer()

    with db() as conn:
        files = conn.execute(
            """
            SELECT * FROM files
            ORDER BY pinned DESC, id DESC
            LIMIT 30
            """
        ).fetchall()

    if not files:

        await callback.message.edit_text(
            "لا توجد ملفات في المكتبة حتى الآن.",
            reply_markup=admin_keyboard()
        )

        return

    for f in files:

        text = (
            f"📄 <b>{html.escape(f['name'])}</b>\n"
            f"🆔 ID: <code>{f['id']}</code>\n"
            f"📂 القسم: {html.escape(f['category'])}\n"
            f"📌 مثبت: {'نعم' if f['pinned'] else 'لا'}\n"
            f"⬇️ تنزيلات: {f['downloads']}\n"
            f"❤️ إعجابات: {f['likes']}\n\n"
            f"{html.escape(f['description'][:700])}"
        )

        await callback.message.answer(
            text,
            reply_markup=file_admin_keyboard(f["id"])
        )

    await callback.message.answer(
        "⚙️ <b>إدارة الملفات</b>",
        reply_markup=admin_keyboard()
    )


# =========================================================
# PIN / UNPIN
# =========================================================

@dp.callback_query(F.data.startswith("pin:"))
async def pin_file_callback(callback: CallbackQuery):

    if not admin_allowed(callback.from_user.id):
        await callback.answer("غير مصرح.", show_alert=True)
        return

    file_id = int(callback.data.split(":")[1])

    f = get_file(file_id)

    if not f:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return

    new_value = 0 if f["pinned"] else 1

    with db() as conn:
        conn.execute(
            "UPDATE files SET pinned=? WHERE id=?",
            (new_value, file_id)
        )

    log_activity(
        "pin" if new_value else "unpin",
        callback.from_user.id,
        str(file_id)
    )

    await callback.answer(
        "تم تثبيت الملف 📌" if new_value else "تم إلغاء التثبيت"
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=file_admin_keyboard(file_id)
        )
    except TelegramBadRequest:
        pass


# =========================================================
# EDIT DESCRIPTION
# =========================================================

@dp.callback_query(F.data.startswith("edit:"))
async def edit_file_callback(callback: CallbackQuery):

    if not admin_allowed(callback.from_user.id):
        await callback.answer("غير مصرح.", show_alert=True)
        return

    file_id = int(callback.data.split(":")[1])

    f = get_file(file_id)

    if not f:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return

    awaiting_edit_description[callback.from_user.id] = file_id

    await callback.answer()

    await callback.message.answer(
        f"✏️ أرسل الوصف الجديد للملف:\n"
        f"<b>{html.escape(f['name'])}</b>\n\n"
        "استخدم /cancel للإلغاء."
    )


@dp.message(lambda m: m.from_user and m.from_user.id in awaiting_edit_description)
async def receive_edit_description(message: Message):

    user_id = message.from_user.id

    file_id = awaiting_edit_description.pop(user_id, None)

    if not file_id:
        return

    if not message.text:
        await message.answer("أرسل الوصف كنص.")
        awaiting_edit_description[user_id] = file_id
        return

    with db() as conn:
        conn.execute(
            "UPDATE files SET description=? WHERE id=?",
            (message.text[:4000], file_id)
        )

    log_activity("edit_description", user_id, str(file_id))

    await message.answer(
        "✅ تم تحديث وصف الملف.",
        reply_markup=admin_keyboard()
    )


# =========================================================
# DELETE FILE
# =========================================================

@dp.callback_query(F.data.startswith("delete:"))
async def delete_file_callback(callback: CallbackQuery):

    if not admin_allowed(callback.from_user.id):
        await callback.answer("غير مصرح.", show_alert=True)
        return

    file_id = int(callback.data.split(":")[1])

    f = get_file(file_id)

    if not f:
        await callback.answer("الملف غير موجود.", show_alert=True)
        return

    with db() as conn:

        conn.execute("DELETE FROM likes WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM downloads WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    filepath = BASE / f["filename"]

    try:
        if filepath.exists():
            filepath.unlink()
    except Exception:
        logger.exception("Unable to delete physical file")

    log_activity("delete_file", callback.from_user.id, str(file_id))

    await callback.answer("تم حذف الملف.", show_alert=True)

    try:
        await callback.message.edit_text(
            "🗑 تم حذف الملف بنجاح."
        )
    except TelegramBadRequest:
        pass


# =========================================================
# DOCUMENT UPLOAD
# =========================================================

@dp.message(F.document)
async def receive_document(message: Message):

    user_id = message.from_user.id

    if not admin_allowed(user_id):
        await message.answer(
            "📩 لاستقبال ملفات المكتبة، "
            "يجب أن تكون ضمن الإدارة."
        )
        return

    document = message.document

    pending_uploads[user_id] = {
        "file_id": document.file_id,
        "file_unique_id": document.file_unique_id,
        "name": document.file_name or "document",
        "size": document.file_size or 0,
        "mime": document.mime_type or "",
    }

    awaiting_description.add(user_id)

    await message.answer(
        "📄 <b>تم استلام الملف</b>\n\n"
        f"الاسم: {html.escape(document.file_name or 'ملف')}\n"
        f"الحجم: {document.file_size or 0:,} bytes\n\n"
        "الآن أرسل وصف الملف ليتم نشره في المكتبة.\n\n"
        "يمكنك استخدام /cancel للإلغاء."
    )


# =========================================================
# RECEIVE DESCRIPTION & PUBLISH
# =========================================================

@dp.message(lambda m: m.from_user and m.from_user.id in awaiting_description)
async def receive_description(message: Message):

    user_id = message.from_user.id

    if not admin_allowed(user_id):
        awaiting_description.discard(user_id)
        pending_uploads.pop(user_id, None)
        return

    if not message.text:
        await message.answer("أرسل وصف الملف كنص.")
        return

    upload = pending_uploads.get(user_id)

    if not upload:
        awaiting_description.discard(user_id)
        await message.answer("انتهت جلسة رفع الملف. أرسل الملف مجددًا.")
        return

    description = message.text[:4000]

    safe_name = re.sub(
        r'[\\/:*?"<>|]+',
        "_",
        upload["name"]
    ).strip()

    unique_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"

    try:

        telegram_file = await bot.get_file(upload["file_id"])

        destination = BASE / unique_name

        await bot.download_file(
            telegram_file.file_path,
            destination=destination
        )

        with db() as conn:

            cursor = conn.execute(
                """
                INSERT INTO files(
                    name, filename, description, category,
                    uploader, file_id, file_type, size,
                    created_at, published
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    upload["name"],
                    unique_name,
                    description,
                    "عام",
                    user_id,
                    upload["file_id"],
                    upload["mime"],
                    upload["size"],
                    int(time.time()),
                    1
                )
            )

            new_id = cursor.lastrowid

        log_activity(
            "publish_file",
            user_id,
            str(new_id)
        )

        awaiting_description.discard(user_id)
        pending_uploads.pop(user_id, None)

        await message.answer(
            "✅ <b>تم نشر الملف بنجاح!</b>\n\n"
            f"📄 الاسم: {html.escape(upload['name'])}\n"
            f"🆔 رقم الملف: <code>{new_id}</code>\n\n"
            "أصبح الملف متاحًا في واجهة المكتبة.",
            reply_markup=admin_keyboard()
        )

    except Exception:

        logger.exception("Upload/publish failed")

        awaiting_description.discard(user_id)
        pending_uploads.pop(user_id, None)

        await message.answer(
            "❌ حدث خطأ أثناء حفظ الملف. "
            "راجع سجل التشغيل وحاول مرة أخرى."
        )


# =========================================================
# ADMIN TEXT COMMANDS
# =========================================================

@dp.message(Command("stats"))
async def stats_command(message: Message):

    if not admin_allowed(message.from_user.id):
        await message.answer("⛔ غير مصرح.")
        return

    with db() as conn:

        files_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE published=1"
        ).fetchone()[0]

        visits_count = conn.execute(
            "SELECT COUNT(*) FROM visits"
        ).fetchone()[0]

        downloads_count = conn.execute(
            "SELECT COUNT(*) FROM downloads"
        ).fetchone()[0]

        likes_count = conn.execute(
            "SELECT COUNT(*) FROM likes"
        ).fetchone()[0]

    await message.answer(
        "📊 <b>الإحصائيات</b>\n\n"
        f"📚 الملفات: {files_count}\n"
        f"👁 الزيارات: {visits_count}\n"
        f"⬇️ التنزيلات: {downloads_count}\n"
        f"❤️ الإعجابات: {likes_count}",
        reply_markup=admin_keyboard()
    )


# =========================================================
# FASTAPI LIFESPAN
# =========================================================

polling_task: Optional[asyncio.Task] = None


async def polling_runner():

    try:

        logger.info("Starting Telegram polling...")

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=False,
            close_bot_session=False
        )

    except TelegramConflictError:

        logger.critical(
            "Telegram polling conflict: another bot instance "
            "is using the same token."
        )

    except asyncio.CancelledError:

        raise

    except Exception:

        logger.exception("Telegram polling stopped due to error")


@asynccontextmanager
async def lifespan(app: FastAPI):

    global polling_task, BOT_USERNAME

    init_db()

    try:

        me = await bot.get_me()
        BOT_USERNAME = me.username or ""

        logger.info(
            "Telegram bot connected: @%s",
            BOT_USERNAME
        )

    except Exception:

        logger.exception("Unable to connect to Telegram")
        raise

    polling_task = asyncio.create_task(polling_runner())

    logger.info("FastAPI started.")

    try:

        yield

    finally:

        logger.info("Stopping bot polling...")

        if polling_task:

            polling_task.cancel()

            try:
                await polling_task
            except asyncio.CancelledError:
                pass

        await bot.session.close()

        logger.info("Application shutdown complete.")


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="Business Administration Library",
    description="Business Administration electronic library API",
    version="3.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# WEBSITE
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def index():

    index_path = BASE / "index.html"

    if not index_path.exists():
        return HTMLResponse(
            "<h1>Business Administration Library</h1>"
            "<p>index.html not found.</p>",
            status_code=404
        )

    return HTMLResponse(
        index_path.read_text(encoding="utf-8")
    )


# =========================================================
# FILE SERVING
# =========================================================

@app.get("/files/{filename}")
async def serve_file(filename: str):

    if "/" in filename or "\\" in filename:
        raise HTTPException(400, "Invalid filename")

    filepath = BASE / filename

    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(404, "File not found")

    with db() as conn:

        row = conn.execute(
            "SELECT id FROM files WHERE filename=? AND published=1",
            (filename,)
        ).fetchone()

    if not row:
        raise HTTPException(404, "File not published")

    return FileResponse(
        filepath,
        filename=filename
    )


# =========================================================
# API FILES
# =========================================================

@app.get("/api/files")
async def api_files(
    category: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=1000)
):

    query = """
        SELECT id, name, filename, description, category,
               file_type, size, downloads, likes, pinned,
               created_at
        FROM files
        WHERE published=1
    """

    params = []

    if category:

        query += " AND category=?"
        params.append(category)

    if search:

        query += " AND (name LIKE ? OR description LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term])

    query += " ORDER BY pinned DESC, created_at DESC LIMIT ?"
    params.append(limit)

    with db() as conn:

        rows = conn.execute(
            query,
            params
        ).fetchall()

    return {
        "success": True,
        "count": len(rows),
        "files": [
            {
                **dict(row),
                "url": f"/files/{row['filename']}"
            }
            for row in rows
        ]
    }


# =========================================================
# API STATS
# =========================================================

@app.get("/api/stats")
async def api_stats():

    with db() as conn:

        files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE published=1"
        ).fetchone()[0]

        visits = conn.execute(
            "SELECT COUNT(*) FROM visits"
        ).fetchone()[0]

        downloads = conn.execute(
            "SELECT COUNT(*) FROM downloads"
        ).fetchone()[0]

        likes = conn.execute(
            "SELECT COUNT(*) FROM likes"
        ).fetchone()[0]

    return {
        "success": True,
        "files": files,
        "visits": visits,
        "downloads": downloads,
        "likes": likes
    }


# =========================================================
# VISITS
# =========================================================

@app.post("/api/visit")
async def api_visit(request: Request):

    visitor = (
        request.headers.get("x-forwarded-for")
        or request.client.host
        if request.client
        else "unknown"
    )

    visitor = visitor.split(",")[0][:100]

    with db() as conn:

        conn.execute(
            "INSERT INTO visits(visitor, created_at) VALUES(?,?)",
            (visitor, int(time.time()))
        )

    return {
        "success": True
    }


# =========================================================
# LIKE
# =========================================================

@app.post("/api/files/{file_id}/like")
async def api_like(
    file_id: int,
    request: Request
):

    if not file_exists(file_id):
        raise HTTPException(404, "File not found")

    visitor = (
        request.headers.get("x-forwarded-for")
        or request.client.host
        if request.client
        else "unknown"
    )

    visitor = visitor.split(",")[0][:100]

    with db() as conn:

        existing = conn.execute(
            "SELECT id FROM files WHERE id=? AND published=1",
            (file_id,)
        ).fetchone()

        if not existing:
            raise HTTPException(404, "File not found")

        # API likes are visitor-based for websites
        # The visitor key is used as a stable lightweight identity.
        pseudo_user = abs(hash(visitor)) % (2**31)

        liked = conn.execute(
            "SELECT 1 FROM likes WHERE user_id=? AND file_id=?",
            (pseudo_user, file_id)
        ).fetchone()

        if liked:

            conn.execute(
                "DELETE FROM likes WHERE user_id=? AND file_id=?",
                (pseudo_user, file_id)
            )

            conn.execute(
                "UPDATE files SET likes=MAX(0, likes-1) WHERE id=?",
                (file_id,)
            )

            state = False

        else:

            conn.execute(
                "INSERT OR IGNORE INTO likes(user_id,file_id) VALUES(?,?)",
                (pseudo_user, file_id)
            )

            conn.execute(
                "UPDATE files SET likes=likes+1 WHERE id=?",
                (file_id,)
            )

            state = True

        count = conn.execute(
            "SELECT likes FROM files WHERE id=?",
            (file_id,)
        ).fetchone()[0]

    return {
        "success": True,
        "liked": state,
        "likes": count
    }


# =========================================================
# DOWNLOAD TRACKING
# =========================================================

@app.post("/api/files/{file_id}/download")
async def api_download(
    file_id: int,
    request: Request
):

    with db() as conn:

        f = conn.execute(
            """
            SELECT filename, name FROM files
            WHERE id=? AND published=1
            """,
            (file_id,)
        ).fetchone()

        if not f:
            raise HTTPException(404, "File not found")

        filepath = BASE / f["filename"]

        if not filepath.exists():
            raise HTTPException(404, "File unavailable")

        visitor = (
            request.headers.get("x-forwarded-for")
            or request.client.host
            if request.client
            else "unknown"
        )

        visitor = visitor.split(",")[0][:100]

        conn.execute(
            "UPDATE files SET downloads=downloads+1 WHERE id=?",
            (file_id,)
        )

        conn.execute(
            "INSERT INTO downloads(file_id, visitor, created_at) VALUES(?,?,?)",
            (file_id, visitor, int(time.time()))
        )

    return FileResponse(
        filepath,
        filename=f["name"]
    )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": "Business Administration Library",
        "bot": "configured",
        "time": int(time.time())
    }


# =========================================================
# TELEGRAM BOT STATUS
# =========================================================

@app.get("/api/bot")
async def bot_status():

    return {
        "success": True,
        "username": BOT_USERNAME,
        "url": (
            f"https://t.me/{BOT_USERNAME}"
            if BOT_USERNAME
            else None
        )
    }


# =========================================================
# RUN DIRECTLY
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "bot:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        log_level="info"
    )
