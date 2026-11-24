"""Business Administration Student Library: Telegram bot + FastAPI API.
Configure TOKKEN, ADMIN_ID, ADMIN_CODE and (optionally) GROUP_LINK as environment variables.
Run with: uvicorn bot:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import sqlite3
import hashlib
import logging
import mimetypes
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import httpx

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("business-library")
BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
STATIC.mkdir(parents=True, exist_ok=True)
DB = Path(os.getenv("DB_PATH", str(BASE / "data" / "library.sqlite3")))
TOKEN = os.getenv("TOKKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
ADMIN_CODE = os.getenv("ADMIN_CODE", "")
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/+mZe3EZzQBCYxOTUy")
DEV_LINK = "https://t.me/ONX44"
BOT_APP: Application | None = None


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files(
      id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL, file_name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '', uploader_id INTEGER, uploader_name TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')), likes INTEGER NOT NULL DEFAULT 0,
      downloads INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
      category TEXT NOT NULL DEFAULT 'عام');
    CREATE TABLE IF NOT EXISTS likes(file_id INTEGER NOT NULL, user_id TEXT NOT NULL,
      PRIMARY KEY(file_id,user_id), FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS visits(day TEXT PRIMARY PRIMARY KEY,count INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS downloads(day TEXT,file_id INTEGER,count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(day,file_id));
    CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,detail TEXT,created_at TEXT DEFAULT (datetime('now')));
    """)
    columns = {r["name"] for r in con.execute("PRAGMA table_info(files)").fetchall()}
    if "category" not in columns:
        con.execute("ALTER TABLE files ADD COLUMN category TEXT NOT NULL DEFAULT 'عام'")
    con.commit()
    return con



def is_admin(uid: int | None) -> bool:
    return bool(uid and ADMIN_ID and uid == ADMIN_ID)



def safe_name(value: str | None, maxlen: int = 160) -> str:
    value = (value or "file").replace("\x00", "").replace('"', "'").replace("\\", "_")
    return value[:maxlen] or "file"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    context.user_data.clear()
    user = update.effective_user
    greeting = (f"مرحبًا {user.mention_html()} ✦\n\nأهلًا بك في <b>مكتبة إدارة الأعمال</b>.\n"
               "مكتبة طلابية تجمع الكتب والمحاضرات والملفات العلمية وتسهّل الوصول إليها.\n\n"
               "لإضافة ملف: أرسل مستندًا إلى البوت، ثم أرفق وصفًا، وراجع بياناته قبل النشر.")
    buttons = [[InlineKeyboardButton("مجموعة المكتبة", url=GROUP_LINK)],
               [InlineKeyboardButton("المطور", url=DEV_LINK)]]
    if is_admin(user.id):
        buttons.insert(0, [InlineKeyboardButton("إرشادات الإدارة", callback_data="admin:help")])
    await update.message.reply_html(greeting, reply_markup=InlineKeyboardMarkup(buttons))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("📚 <b>طريقة استخدام المكتبة</b>\n• أرسل ملفًا كمستند.\n• أرسل وصف الملف أو «بدون وصف».\n"
            "• راجع المعلومات واضغط نشر.\n• /cancel لإلغاء عملية رفع جارية.")
    if is_admin(update.effective_user.id):
        text += "\n\n🔐 للوحة الإدارة أرسل رمز الإدارة الخاص بك في رسالة منفصلة."
    if update.message:
        await update.message.reply_html(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("تم إلغاء العملية الحالية.")


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document if message else None
    if not document:
        return
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.reply_text("حجم الملف أكبر من حد 20 ميغابايت المسموح به في هذا المسار.")
        return
    user = update.effective_user
    context.user_data["pending"] = {
        "file_id": document.file_id,
        "file_name": safe_name(document.file_name or "document"),
        "uploader_id": user.id,
        "uploader_name": safe_name(user.full_name, 100),
    }
    context.user_data["awaiting_description"] = True
    await message.reply_text("✓ استلمت الملف.\n\nأرسل وصفًا مختصرًا له، أو أرسل «بدون وصف».")


async def admin_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute("SELECT id,file_name,pinned FROM files ORDER BY id DESC LIMIT 50").fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("لا توجد ملفات في المكتبة بعد.")
        return
    context.user_data["admin_mode"] = "choose_file"
    lines = ["✦ لوحة الإدارة — آخر 50 ملفًا", "أرسل رقم الملف لفتح خياراته:"]
    lines.extend(f"{r['id']:04d} {'📌' if r['pinned'] else '·'} {r['file_name']}" for r in rows)
    await update.message.reply_text("\n".join(lines))


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    uid = update.effective_user.id
    if text.startswith("/"):
        return
    if is_admin(uid) and ADMIN_CODE and text == ADMIN_CODE:
        context.user_data.clear()
        await admin_index(update, context)
        return
    if is_admin(uid) and context.user_data.get("admin_mode") == "choose_file" and text.isdigit():
        file_number = int(text)
        con = db()
        row = con.execute("SELECT id,file_name,description,pinned FROM files WHERE id=?", (file_number,)).fetchone()
        con.close()
        if not row:
            await message.reply_text("رقم الملف غير موجود.")
            return
        context.user_data["admin_file"] = file_number
        context.user_data["admin_mode"] = "actions"
        keyboard = [[InlineKeyboardButton("تعديل الوصف", callback_data="admin:edit")],
                    [InlineKeyboardButton("تثبيت / إلغاء التثبيت", callback_data="admin:pin")],
                    [InlineKeyboardButton("حذف الملف", callback_data="admin:delete")],
                    [InlineKeyboardButton("رجوع إلى الفهرس", callback_data="admin:list")]]
        await message.reply_text(
            f"ملف #{file_number:04d}\n{row['file_name']}\n\nالوصف: {row['description'] or '—'}\n"
            f"التثبيت: {'مثبت' if row['pinned'] else 'غير مثبت'}",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if is_admin(uid) and context.user_data.get("admin_mode") == "edit_description":
        file_number = context.user_data.get("admin_file")
        con = db()
        con.execute("UPDATE files SET description=? WHERE id=?", (text, int(file_number)))
        con.execute("INSERT INTO activity(kind,detail) VALUES(?,?)", ("edit", f"updated description for file {file_number}"))
        con.commit()
        con.close()
        context.user_data.clear()
        await message.reply_text("تم حفظ الوصف الجديد.")
        return
    if context.user_data.get("awaiting_description") and context.user_data.get("pending"):
        pending = context.user_data.pop("pending")
        context.user_data.pop("awaiting_description", None)
        pending["description"] = "" if text.lower() in ("بدون وصف", "-", "none") else text
        context.user_data["ready"] = pending
        keyboard = [[InlineKeyboardButton("نشر في المكتبة", callback_data="publish")],
                    [InlineKeyboardButton("إلغاء", callback_data="cancel")]]
        await message.reply_text(
            f"مراجعة قبل النشر\n\nالملف: {pending['file_name']}\nالوصف: {pending['description'] or 'بدون وصف'}\n\nهل تريد إضافته إلى المكتبة؟",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return
    await message.reply_text("لرفع ملف، أرسله أولًا كمستند. استخدم /help لعرض الإرشادات.")


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    uid = query.from_user.id
    if data == "publish":
        pending = context.user_data.get("ready")
        if not pending or pending.get("uploader_id") != uid:
            await query.edit_message_text("لا يمكن نشر ملف تابع لمستخدم آخر أو انتهت جلسة الرفع.")
            return
        pending = context.user_data.pop("ready")
        con = db()
        cursor = con.execute("INSERT INTO files(file_id,file_name,description,uploader_id,uploader_name) VALUES(?,?,?,?,?)",
                          (pending["file_id"], pending["file_name"], pending["description"], pending["uploader_id"], pending["uploader_name"]))
        file_number = cursor.lastrowid
        con.execute("INSERT INTO activity(kind,detail) VALUES(?,?)", ("publish", f"file {file_number} published"))
        con.commit()
        con.close()
        context.user_data.clear()
        await query.edit_message_text(f"✓ تم النشر بنجاح\nرقم الملف: {file_number:04d}\nسيظهر في المكتبة عند تحديث الصفحة.")
        return
    if data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("تم إلغاء العملية.")
        return
    if data == "admin:help":
        await query.edit_message_text("أرسل رمز الإدارة في رسالة منفصلة لفتح الفهرس.")
        return
    if not is_admin(uid):
        await query.edit_message_text("غير مصرح لك بتنفيذ هذا الإجراء.")
        return
    if data == "admin:list":
        context.user_data.clear()
        await query.edit_message_text("أرسل رمز الإدارة مجددًا لفتح القائمة.")
        return
    file_number = context.user_data.get("admin_file")
    if not file_number:
        await query.edit_message_text("انتهت جلسة الإدارة. أرسل رمز الإدارة مجددًا.")
        return
    if data == "admin:edit":
        context.user_data["admin_mode"] = "edit_description"
        await query.edit_message_text("أرسل الوصف الجديد الآن.")
        return
    if data == "admin:pin":
        con = db()
        row = con.execute("SELECT pinned FROM files WHERE id=?", (file_number,)).fetchone()
        if row:
            con.execute("UPDATE files SET pinned=? WHERE id=?", (0 if row["pinned"] else 1, file_number))
            con.execute("INSERT INTO activity(kind,detail) VALUES(?,?)", ("pin", f"pin state toggled for {file_number}"))
            con.commit()
        con.close()
        context.user_data.clear()
        await query.edit_message_text("تم تحديث حالة تثبيت الملف.")
        return
    if data == "admin:delete":
        keyboard = [[InlineKeyboardButton("تأكيد الحذف النهائي", callback_data="admin:confirm_delete")],
                    [InlineKeyboardButton("إلغاء", callback_data="cancel")]]
        await query.edit_message_text("هل تريد حذف الملف نهائيًا؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data == "admin:confirm_delete":
        con = db()
        con.execute("DELETE FROM likes WHERE file_id=?", (file_number,))
        con.execute("DELETE FROM downloads WHERE file_id=?", (file_number,))
        con.execute("DELETE FROM files WHERE id=?", (file_number,))
        con.execute("INSERT INTO activity(kind,detail) VALUES(?,?)", ("delete", f"file {file_number} deleted"))
        con.commit()
        con.close()
        context.user_data.clear()
        await query.edit_message_text("تم حذف الملف من المكتبة.")


async def bot_startup():
    global BOT_APP
    if not TOKEN:
        log.error("TOKKEN environment secret is missing. Bot polling will not start.")
        return
    if BOT_APP:
        return
    BOT_APP = Application.builder().token(TOKEN).build()
    BOT_APP.add_handler(CommandHandler("start", start))
    BOT_APP.add_handler(CommandHandler("help", help_cmd))
    BOT_APP.add_handler(CommandHandler("cancel", cancel))
    BOT_APP.add_handler(MessageHandler(filters.Document.ALL, receive_document))
    BOT_APP.add_handler(CallbackQueryHandler(callbacks))
    BOT_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))
    await BOT_APP.initialize()
    await BOT_APP.start()
    await BOT_APP.updater.start_polling(drop_pending_updates=False)
    log.info("Telegram polling started")


async def bot_shutdown():
    global BOT_APP
    if BOT_APP:
        try:
            if BOT_APP.updater and BOT_APP.updater.running:
                await BOT_APP.updater.stop()
        except Exception:
            log.exception("Error stopping Telegram updater")
        try:
            if BOT_APP.running:
                await BOT_APP.stop()
        finally:
            await BOT_APP.shutdown()
            BOT_APP = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    db()
    await bot_startup()
    try:
        yield
    finally:
        await bot_shutdown()


app = FastAPI(title="Business Administration Library API", version="2.1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")


@app.get("/api/files")
def get_files(q: str | None = Query(default=None), limit: int = Query(default=500, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    con = db()
    query = "SELECT id,file_name,description,created_at,likes,downloads,pinned,category FROM files"
    params: list[object] = []
    if q:
        query += " WHERE file_name LIKE ? OR description LIKE ? OR category LIKE ?"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    query += " ORDER BY pinned DESC,id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = con.execute(query, params).fetchall()
    con.close()
    return [dict(row) for row in rows]


@app.get("/api/stats")
def stats():
    con = db()
    total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    today = con.execute("SELECT COUNT(*) FROM files WHERE date(created_at)=date('now')").fetchone()[0]
    likes = con.execute("SELECT COALESCE(SUM(likes),0) FROM files").fetchone()[0]
    downloads = con.execute("SELECT COALESCE(SUM(downloads),0) FROM files").fetchone()[0]
    visits = con.execute("SELECT COALESCE(SUM(count),0) FROM visits").fetchone()[0]
    con.close()
    return {"files": total, "today": today, "downloads": downloads, "visits": visits, "likes": likes}


@app.post("/api/visit")
def visit():
    con = db()
    con.execute("INSERT INTO visits(day,count) VALUES(date('now'),1) ON CONFLICT(day) DO UPDATE SET count=count+1")
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/files/{fid}/like")
def like(fid: int, request: Request):
    user_key = request.headers.get("x-user-key") or (request.client.host if request.client else "unknown")
    con = db()
    row = con.execute("SELECT id FROM files WHERE id=?", (fid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "File not found")
    digest = hashlib.sha256(user_key.encode()).hexdigest()
    try:
        con.execute("INSERT INTO likes(file_id,user_id) VALUES(?,?)", (fid, digest))
        con.execute("UPDATE files SET likes=likes+1 WHERE id=?", (fid,))
        con.commit()
        result = {"liked": True}
    except sqlite3.IntegrityError:
        result = {"liked": False, "message": "already liked"}
    con.close()
    return result


@app.get("/api/files/{fid}/download")
async def download(fid: int):
    if not TOKEN:
        raise HTTPException(503, "Bot token is not configured")
    con = db()
    row = con.execute("SELECT file_id,file_name FROM files WHERE id=?", (fid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "File not found")
    con.execute("INSERT INTO downloads(day,file_id,count) VALUES(date('now'),?,1) ON CONFLICT(day,file_id) DO UPDATE SET count=count+1", (fid,))
    con.execute("UPDATE files SET downloads=downloads+1 WHERE id=?", (fid,))
    con.commit()
    con.close()
    async with httpx.AsyncClient(timeout=90) as client:
        api = await client.get(f"https://api.telegram.org/bot{TOKEN}/getFile", params={"file_id": row["file_id"]})
        if not api.is_success or not api.json().get("ok"):
            raise HTTPException(502, "Telegram file lookup failed")
        file_path = api.json()["result"]["file_path"]
        remote = await client.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
        if not remote.is_success:
            raise HTTPException(502, "File download failed")
        filename = safe_name(row["file_name"])
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{httpx.URL('https://x.invalid/' + filename).raw_path.decode().split('/')[-1]}"}
        return StreamingResponse(iter([remote.content]), media_type=media_type, headers=headers)


@app.get("/health")
def health():
    return {"status": "ok", "bot_configured": bool(TOKEN), "database": str(DB)}


@app.get("/")
def home():
    index = BASE / "index.html"
    if not index.exists():
        raise HTTPException(404, "index.html is not present in the project folder")
    return FileResponse(index)
