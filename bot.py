"""Business Administration Library: Telegram bot + FastAPI API.
Environment: TOKKEN, ADMIN_ID, ADMIN_CODE, GROUP_LINK, SITE_URL, PORT(optional).
"""
from __future__ import annotations
import os, html, sqlite3, logging, asyncio, uuid, mimetypes
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("business_library")

ROOT = Path(__file__).resolve().parent
TOKEN = os.getenv("TOKKEN", "").strip()
ADMIN_CODE = os.getenv("ADMIN_CODE", "").strip()
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
except ValueError:
    ADMIN_ID = 0
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/+mZe3EZzQBCYxOTUy").strip()
SITE_URL = os.getenv("SITE_URL", "").strip()
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "library.sqlite3"

bot: Bot | None = Bot(TOKEN) if TOKEN else None
dp: Dispatcher | None = Dispatcher(storage=MemoryStorage()) if bot else None


class Upload(StatesGroup):
    title = State()
    description = State()
    review = State()

class Admin(StatesGroup):
    code = State()
    select = State()
    title = State()
    description = State()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def init_db() -> None:
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY, telegram_file_id TEXT NOT NULL, file_name TEXT,
            title TEXT, description TEXT, file_size INTEGER, mime_type TEXT,
            created_at TEXT, uploader_id INTEGER, pinned INTEGER NOT NULL DEFAULT 0
        )""")
        con.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)")
        con.executemany("INSERT OR IGNORE INTO stats(key,value) VALUES(?,0)", [("visits",), ("downloads",)])

def list_books() -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(row) for row in con.execute("SELECT * FROM books ORDER BY pinned DESC, created_at DESC")]

def get_book(book_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return dict(row) if row else None

def stat_values() -> dict[str, int]:
    with connect() as con:
        return {row["key"]: row["value"] for row in con.execute("SELECT key,value FROM stats")}

def inc_stat(key: str) -> None:
    with connect() as con:
        con.execute("UPDATE stats SET value=value+1 WHERE key=?", (key,))

def is_admin(uid: int | None) -> bool:
    return bool(uid and ADMIN_ID and uid == ADMIN_ID)

def safe_text(value: Any) -> str:
    return html.escape(str(value or ""))

# Each inline button is created with exactly one action field: URL or callback_data.
def url_button(label: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, url=url)
def callback_button(label: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=callback)


def home_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if SITE_URL.startswith(("https://", "http://")):
        rows.append([url_button("📚 فتح المكتبة", SITE_URL)])
    if GROUP_LINK.startswith(("https://", "http://")):
        rows.append([url_button("👥 JOIN GROUP", GROUP_LINK), url_button("🤖 MY BOT", "https://t.me/ONX44")])
    rows.append([callback_button("📤 طريقة إضافة كتاب", "help:upload")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def upload_review_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[callback_button("⬆️ نشر الكتاب", "upload:publish")]]
    links: list[InlineKeyboardButton] = []
    if GROUP_LINK.startswith(("https://", "http://")):
        links.append(url_button("👥 JOIN GROUP", GROUP_LINK))
    links.append(url_button("🤖 MY BOT", "https://t.me/ONX44"))
    rows.append(links)
    rows.append([callback_button("إلغاء", "upload:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_books_keyboard(books: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for b in books:
        title = (b.get("title") or b.get("file_name") or "كتاب")[:55]
        rows.append([callback_button(f"📘 {title}", f"admin:book:{b['id']}")])
    rows.append([callback_button("↩️ إغلاق", "admin:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_actions_keyboard(book: dict) -> InlineKeyboardMarkup:
    pin_label = "📌 إلغاء التثبيت" if book.get("pinned") else "📌 تثبيت الكتاب"
    return InlineKeyboardMarkup(inline_keyboard=[
        [callback_button("✏️ تعديل العنوان", "admin:edit_title")],
        [callback_button("📝 تعديل الوصف / الرابط", "admin:edit_desc")],
        [callback_button(pin_label, "admin:toggle_pin")],
        [callback_button("🗑 حذف الكتاب", "admin:delete")],
        [callback_button("📚 العودة إلى القائمة", "admin:list"), callback_button("إغلاق", "admin:close")],
    ])

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    polling: asyncio.Task | None = None
    if bot and dp:
        # Do not launch multiple polling loops within this process.
        polling = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False))
        log.info("Telegram polling task started")
    elif not TOKEN:
        log.error("TOKKEN is empty; bot polling will not start")
    try:
        yield
    finally:
        if dp and polling:
            await dp.stop_polling()
            polling.cancel()
            try:
                await polling
            except asyncio.CancelledError:
                pass
        if bot:
            await bot.session.close()

app = FastAPI(title="Business Administration Library API", version="3.0", lifespan=lifespan)

@app.get("/")
def site_home():
    page = ROOT / "index.html"
    if not page.exists():
        raise HTTPException(404, "index.html is missing in repository root")
    return FileResponse(page)

@app.get("/1.jpg")
def site_image():
    img = ROOT / "1.jpg"
    if not img.exists():
        raise HTTPException(404, "1.jpg not found")
    return FileResponse(img)
@app.get("/api/books")
def api_books():
    books = list_books()
    s = stat_values()
    fields = ("id", "file_name", "title", "description", "file_size", "mime_type", "created_at", "pinned")
    return {"books": [{key: b.get(key) for key in fields} for b in books], "total": len(books), "downloads": s.get("downloads", 0)}
@app.get("/api/stats")
def api_stats():
    s = stat_values()
    return {"visits": s.get("visits", 0), "downloads": s.get("downloads", 0), "books": len(list_books())}
@app.post("/api/visit")
def api_visit():
    inc_stat("visits")
    return {"ok": True}
@app.post("/api/download/{book_id}")
def api_download_count(book_id: str):
    if not get_book(book_id):
        raise HTTPException(404, "Book not found")
    inc_stat("downloads")
    return {"ok": True}
@app.post("/api/pin/{book_id}")
def api_pin(book_id: str):
    # This endpoint is retained for compatibility with the frontend.
    with connect() as con:
        row = con.execute("SELECT pinned FROM books WHERE id=?", (book_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Book not found")
        con.execute("UPDATE books SET pinned=? WHERE id=?", (0 if row["pinned"] else 1, book_id))
    return {"ok": True}
@app.get("/api/file/{book_id}")
async def api_file(book_id: str):
    book = get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if not bot:
        raise HTTPException(503, "Telegram bot is not configured")
    try:
        telegram_file = await bot.get_file(book["telegram_file_id"])
        tg_url = f"https://api.telegram.org/file/bot{TOKEN}/{telegram_file.file_path}"
        filename = (book.get("file_name") or book.get("title") or "download").replace('"', "").replace("\\", "_")
        mime = book.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        async def stream_file():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("GET", tg_url) as response:
                    if response.status_code != 200:
                        raise RuntimeError("Telegram file request failed")
                    async for chunk in response.aiter_bytes():
                        yield chunk
        return StreamingResponse(stream_file(), media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as exc:
        log.exception("File proxy failed")
        raise HTTPException(502, "Unable to fetch file from Telegram") from exc


# ---------------- Bot handlers ----------------
if dp:
    @dp.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        first_name = safe_text(message.from_user.first_name or "طالب")
        # Explicit InlineKeyboardButton objects; each button has URL or callback_data.
        await message.answer(
            f"أهلاً بك {first_name} ✨\n\nمكتبة طلاب إدارة الأعمال. لإضافة كتاب أرسل الملف هنا، ثم أدخل عنوانه ووصفه.",
            reply_markup=home_keyboard()
        )

    @dp.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("تم إلغاء العملية.")

    @dp.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ هذا الأمر خاص بالمدير.")
            return
        await state.clear()
        await state.set_state(Admin.code)
        await message.answer("🔐 أرسل رمز الإدارة الآن:")

    @dp.message(F.document)
    async def document_received(message: Message, state: FSMContext):
        await state.clear()
        doc = message.document
        await state.update_data(file_id=doc.file_id, filename=doc.file_name or "file", size=doc.file_size, mime=doc.mime_type, owner=message.from_user.id)
        await state.set_state(Upload.title)
        await message.answer("📖 وصل الملف بنجاح.\n\nأرسل عنوان الكتاب:")

    @dp.message(Upload.title)
    async def upload_title(message: Message, state: FSMContext):
        if not message.text or not message.text.strip():
            await message.answer("أرسل عنوانًا نصيًا للكتاب.")
            return
        await state.update_data(title=message.text.strip()[:250])
        await state.set_state(Upload.description)
        await message.answer("✍️ أرسل وصفًا أو رابطًا، أو اكتب (بدون):")

    @dp.message(Upload.description)
    async def upload_description(message: Message, state: FSMContext):
        desc = (message.text or "").strip()
        if desc.lower() in ("بدون", "لا يوجد", "-"):
            desc = ""
        await state.update_data(description=desc)
        data = await state.get_data()
        await state.set_state(Upload.review)
        review = f"📚 <b>مراجعة النشر</b>\n\n<b>العنوان:</b> {safe_text(data.get('title'))}\n<b>الوصف:</b> {safe_text(desc or '—')}\n<b>الملف:</b> {safe_text(data.get('filename'))}"
        await message.answer(review, parse_mode="HTML", reply_markup=upload_review_keyboard())

    @dp.callback_query(F.data == "upload:publish")
    async def upload_publish(query: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        if not data.get("file_id") or not data.get("title"):
            await query.answer("انتهت بيانات الملف؛ أرسل الملف من جديد.", show_alert=True)
            return
        book_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with connect() as con:
            con.execute("INSERT INTO books(id,telegram_file_id,file_name,title,description,file_size,mime_type,created_at,uploader_id,pinned) VALUES(?,?,?,?,?,?,?,?,?,0)", (book_id, data["file_id"], data.get("filename"), data["title"], data.get("description"), data.get("size"), data.get("mime"), now, data.get("owner")))
        await state.clear()
        if query.message:
            await query.message.edit_text("✨ تم نشر الكتاب في المكتبة بنجاح. يمكنك فتح الموقع لمشاهدته.")
        await query.answer("تم نشر الكتاب")

    @dp.callback_query(F.data == "upload:cancel")
    async def upload_cancel(query: CallbackQuery, state: FSMContext):
        await state.clear()
        if query.message:
            await query.message.edit_text("تم إلغاء نشر الكتاب.")
        await query.answer()

    @dp.callback_query(F.data == "help:upload")
    async def upload_help(query: CallbackQuery):
        if query.message:
            await query.message.answer("📤 <b>طريقة إضافة كتاب</b>\n1. أرسل الملف مباشرة إلى البوت.\n2. اكتب عنوان الكتاب.\n3. اكتب وصفًا أو رابطًا، أو (بدون).\n4. راجع البيانات واضغط نشر الكتاب.", parse_mode="HTML")
        await query.answer()

    @dp.message(Admin.code)
    async def admin_code_handler(message: Message, state: FSMContext):
        if not is_admin(message.from_user.id) or not ADMIN_CODE or (message.text or "").strip() != ADMIN_CODE:
            await state.clear()
            await message.answer("❌ رمز الإدارة غير صحيح أو لا تملك صلاحية الإدارة.")
            return
        await state.set_state(Admin.select)
        books = list_books()
        if not books:
            await message.answer("لا توجد كتب في المكتبة حتى الآن.")
            return
        lines = [f"{i}. {safe_text(b.get('title') or b.get('file_name') or 'كتاب')}" for i, b in enumerate(books, 1)]
        await message.answer("🛠 <b>لوحة الإدارة</b>\n\n" + "\n".join(lines) + "\n\nاختر كتابًا من الأزرار:", parse_mode="HTML", reply_markup=admin_books_keyboard(books))

    @dp.callback_query(F.data.startswith("admin:"))
    async def admin_callbacks(query: CallbackQuery, state: FSMContext):
        if not is_admin(query.from_user.id):
            await query.answer("هذه الأزرار خاصة بالمدير.", show_alert=True)
            return
        action = query.data or ""
        if action == "admin:close":
            await state.clear()
            if query.message:
                await query.message.edit_text("تم إغلاق لوحة الإدارة.")
            await query.answer()
            return
        if action == "admin:list":
            books = list_books()
            await state.set_state(Admin.select)
            if query.message:
                await query.message.edit_text("اختر كتابًا:", reply_markup=admin_books_keyboard(books))
            await query.answer()
            return
        if action.startswith("admin:book:"):
            book_id = action.split(":", 2)[2]
            book = get_book(book_id)
            if not book:
                await query.answer("الكتاب غير موجود.", show_alert=True)
                return
            await state.update_data(book_id=book_id)
            if query.message:
                await query.message.edit_text(f"📘 <b>{safe_text(book.get('title') or book.get('file_name'))}</b>\nاختر إجراءً:", parse_mode="HTML", reply_markup=admin_actions_keyboard(book))
            await query.answer()
            return
        data = await state.get_data()
        book_id = data.get("book_id")
        book = get_book(book_id) if book_id else None
        if not book:
            await query.answer("اختر كتابًا أولًا.", show_alert=True)
            return
        if action == "admin:edit_title":
            await state.set_state(Admin.title)
            if query.message:
                await query.message.answer("أرسل العنوان الجديد:")
        elif action == "admin:edit_desc":
            await state.set_state(Admin.description)
            if query.message:
                await query.message.answer("أرسل الوصف الجديد أو الرابط، أو (بدون) لمسحه:")
        elif action == "admin:toggle_pin":
            with connect() as con:
                con.execute("UPDATE books SET pinned=? WHERE id=?", (0 if book["pinned"] else 1, book_id))
            refreshed = get_book(book_id)
            if query.message and refreshed:
                await query.message.edit_reply_markup(reply_markup=admin_actions_keyboard(refreshed))
            await query.answer("تم تحديث التثبيت")
        elif action == "admin:delete":
            with connect() as con:
                con.execute("DELETE FROM books WHERE id=?", (book_id,))
            await state.clear()
            if query.message:
                await query.message.edit_text("🗑 تم حذف الكتاب من المكتبة.")
            await query.answer("تم الحذف")

    @dp.message(Admin.title)
    async def admin_edit_title(message: Message, state: FSMContext):
        if not message.text or not message.text.strip():
            await message.answer("أرسل عنوانًا نصيًا.")
            return
        data = await state.get_data()
        with connect() as con:
            con.execute("UPDATE books SET title=? WHERE id=?", (message.text.strip()[:250], data.get("book_id")))
        await state.clear()
        await message.answer("✏️ تم تحديث العنوان.")

    @dp.message(Admin.description)
    async def admin_edit_description(message: Message, state: FSMContext):
        data = await state.get_data()
        desc = (message.text or "").strip()
        if desc.lower() in ("بدون", "لا يوجد", "-"):
            desc = ""
        with connect() as con:
            con.execute("UPDATE books SET description=? WHERE id=?", (desc, data.get("book_id")))
        await state.clear()
        await message.answer("📝 تم تحديث الوصف.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False, workers=1)
