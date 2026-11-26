# -*- coding: utf-8 -*-
"""مدارك المعرفة | Telegram Business Library Bot
Single-file bot. Persistent index is committed to GitHub via Actions GITHUB_TOKEN.
"""
from __future__ import annotations
import asyncio, base64, difflib, html, json, logging, os, re, unicodedata, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
                          InputMediaPhoto, BufferedInputFile)
from aiogram.client.default import DefaultBotProperties



# ------------------------------ Configuration ------------------------------
TOKEN = os.getenv("TOKENN", "").strip()
CHANNEL = os.getenv("JOIN_CHANEL", "").strip()
OWNER_ID = int(os.getenv("ID_TELE", "7753969972"))
DEV_CONTACT = os.getenv("DEV_TELE", "ONX44").strip()
GH_TOKEN = os.getenv("GITHUB_TOKEN", os.getenv("GH_TOKEN", "")).strip()
GH_REPO = os.getenv("GITHUB_REPOSITORY", "").strip()
GH_BRANCH = os.getenv("GITHUB_REF_NAME", "main").strip() or "main"
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
DATA_PATH = Path("library_index.json")
CHANNEL_URL = "https://t.me/Kh_JOORE_BusinessLibrary"
CONTACT_URL = "https://t.me/ONX44"
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "https://imgur.com/a/0C4FbCi").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("madarek")

if not TOKEN:
    raise RuntimeError("Missing TOKENN environment secret")
if not CHANNEL:
    raise RuntimeError("Missing JOIN_CHANEL environment secret")


# ------------------------------ Helpers ------------------------------
def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def channel_chat_id() -> str:
    return CHANNEL

def display_user(user) -> str:
    return (user.full_name or "مستخدم")[:100]

def norm(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_year(text: str) -> Optional[str]:
    m = re.search(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", text or "")
    return m.group(0) if m else None

def valid_url(s: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+$", (s or "").strip(), re.I))

def shorten(s: str, n: int = 80) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n-1] + "…"

def source_type_label(t: str) -> str:
    return {"file":"ملف", "photo":"صورة", "link":"رابط"}.get(t, t)

def type_code(t: str) -> str:
    return {"file":"DOC", "photo":"IMG", "link":"URL"}.get(t, "SRC")

def record_title(rec: dict) -> str:
    return rec.get("title") or rec.get("note") or rec.get("url") or rec.get("filename") or "مصدر بدون عنوان"

def record_meta(rec: dict) -> str:
    lines = [f"🆔 رمز المصدر: <code>{html.escape(rec.get('source_id',''))}</code>",
             f"📚 النوع: {html.escape(source_type_label(rec.get('type','')))}",
             f"📖 العنوان: {html.escape(record_title(rec))}"]
    if rec.get("note"):
        lines.append(f"📝 التفاصيل: {html.escape(rec['note'])}")
    if rec.get("year"):
        lines.append(f"📅 سنة النشر: {html.escape(str(rec['year']))}")
    lines.append(f"🗓 تاريخ الإضافة: {html.escape(rec.get('created_at','')[:10])}")
    lines.append(f"🔗 القناة: <a href='{CHANNEL_URL}'>مدارك المعرفة</a>")
    lines.append("⚠️ جميع المصادر مجانية، يمنع بيعها أو إعادة استغلالها تجاريًا.")
    return "\n".join(lines)

def make_keyboard(rows: list[list[tuple[str,str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows])


def main_keyboard(owner: bool = False) -> InlineKeyboardMarkup:
    rows = [[("📂 اختيار نوع المصدر", "menu:submit"), ("🔎 البحث عن مصدر", "menu:search")],
            [("📚 قناة البوت", "open:channel"), ("⚠️ الإبلاغ عن مشكلة", "open:report")],
            [("📘 دليل التشغيل", "menu:guide")]]
    if owner:
        rows += [[("👥 MY-BOTS", "admin:contributors"), ("🚫 حظر مستخدم", "admin:ban")],
                 [("⏸ إيقاف البوت مؤقتًا" if not DB.get("paused") else "▶️ تشغيل البوت", "admin:pause")]]
    return make_keyboard(rows)


# ------------------------------ Storage ------------------------------
def empty_db() -> dict:
    return {"version": 2, "next": {"file":1,"photo":1,"link":1}, "sources": [], "users": {}, "banned": [], "paused": False, "settings": {}}

DB: dict[str, Any] = empty_db()
DB_LOCK = asyncio.Lock()
GH_LOCK = asyncio.Lock()


async def load_db() -> None:
    global DB
    if GH_TOKEN and GH_REPO:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"https://api.github.com/repos/{GH_REPO}/contents/library_index.json", headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept":"application/vnd.github+json"}, params={"ref": GH_BRANCH})
                if r.status_code == 200:
                    payload = r.json()
                    DB = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
                    log.info("Loaded persistent index from GitHub")
                    return
                elif r.status_code != 404:
                    log.warning("GitHub index load returned %s: %s", r.status_code, r.text[:250])
        except Exception as e:
            log.exception("Failed to load GitHub index: %s", e)
    try:
        if DATA_PATH.exists():
            DB = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            log.info("Loaded local index fallback")
    except Exception:
        log.exception("Local index unreadable; initialized empty")
        DB = empty_db()
    DB.setdefault("sources", []); DB.setdefault("users", {}); DB.setdefault("banned", []); DB.setdefault("next", {"file":1,"photo":1,"link":1}); DB.setdefault("paused", False)

async def save_db() -> bool:
    async with GH_LOCK:
        try:
            DATA_PATH.write_text(json.dumps(DB, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Local save failed: %s", e)
        if not (GH_TOKEN and GH_REPO):
            log.warning("Persistent GitHub saving is not configured; Actions storage is ephemeral")
            return False
        api = f"https://api.github.com/repos/{GH_REPO}/contents/library_index.json"
        headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
        async with httpx.AsyncClient(timeout=45) as client:
            old = await client.get(api, headers=headers, params={"ref":GH_BRANCH})
            sha = old.json().get("sha") if old.status_code == 200 else None
            body = {"message":"Update Madarek library index", "content":base64.b64encode(json.dumps(DB,ensure_ascii=False,indent=2).encode()).decode(), "branch":GH_BRANCH}
            if sha: body["sha"] = sha
            put = await client.put(api, headers=headers, json=body)
            if put.status_code not in (200,201):
                log.error("GitHub persistence failed (%s): %s", put.status_code, put.text[:500]); return False
            return True

async def user_touch(user) -> None:
    if not user: return
    key = str(user.id)
    old = DB["users"].get(key, {})
    DB["users"][key] = {"id": user.id, "name": display_user(user), "username": user.username,
                        "first_seen": old.get("first_seen", utcnow()), "last_seen": utcnow(), "blocked": key in set(map(str,DB.get("banned",[])))}

async def next_id(t: str) -> str:
    number = int(DB.get("next", {}).get(t,1))
    DB["next"][t] = number + 1
    return f"{type_code(t)}-{number:04d}"

async def commit_record(rec: dict) -> None:
    DB["sources"].append(rec)
    uid = str(rec["user_id"])
    user = DB["users"].setdefault(uid, {"id":rec["user_id"], "name":rec.get("user_name"), "username":rec.get("username"), "first_seen":rec["created_at"], "last_seen":rec["created_at"]})
    user["name"] = rec.get("user_name") or user.get("name")
    user["username"] = rec.get("username")
    await save_db()

async def persist_contributor_txt(rec_user_id: int) -> None:
    """Generate contributor TXT in repository when write permissions are available."""
    if not (GH_TOKEN and GH_REPO): return
    records = [r for r in DB["sources"] if int(r.get("user_id",0)) == rec_user_id and r.get("status") == "published"]
    usr = DB.get("users",{}).get(str(rec_user_id),{})
    lines = ["MADAREK KNOWLEDGE LIBRARY - CONTRIBUTOR RECORD", "="*50,
             f"Name: {usr.get('name','')}", f"Username: @{usr.get('username') or '—'}", f"Telegram ID: {rec_user_id}",
             f"Published sources: {len(records)}", f"Generated: {utcnow()}", "", "SOURCES", "="*50]
    for r in records:
        lines += [f"\n{r.get('source_id')} | {r.get('type')} | {record_title(r)}", f"Note: {r.get('note','')}", f"Year: {r.get('year') or '—'}", f"Added: {r.get('created_at')}", f"Channel message IDs: {r.get('channel_message_ids',[])}"]
    path = f"contributors/{rec_user_id}/Contributor_{rec_user_id}.txt"
    await github_put_text(path, "\n".join(lines))

async def github_put_text(path: str, content: str) -> bool:
    if not (GH_TOKEN and GH_REPO): return False
    api = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization":f"Bearer {GH_TOKEN}","Accept":"application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=40) as client:
        old = await client.get(api,headers=headers,params={"ref":GH_BRANCH})
        sha = old.json().get("sha") if old.status_code == 200 else None
        payload = {"message":f"Update {path}","content":base64.b64encode(content.encode("utf-8")).decode(),"branch":GH_BRANCH}
        if sha: payload["sha"] = sha
        res = await client.put(api,headers=headers,json=payload)
        if res.status_code not in (200,201): log.warning("GitHub TXT write failed: %s",res.text[:300]); return False
        return True


# ------------------------------ FSM ------------------------------
class Flow(StatesGroup):
    awaiting_file = State(); awaiting_file_note = State(); awaiting_photo = State(); awaiting_photo_note = State()
    awaiting_link = State(); awaiting_link_note = State(); awaiting_edit_note = State()
    awaiting_search = State(); awaiting_ban = State()

PENDING: dict[int, dict] = {}
PHOTO_GROUPS: dict[int, dict] = {}
SEARCH_RESULTS: dict[int, list[str]] = {}


# ------------------------------ Bot setup ------------------------------
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

async def safe_send(chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(chat_id, text, **kwargs)
    except (TelegramNetworkError, TelegramBadRequest) as e:
        log.warning("send_message to %s failed: %s", chat_id, e)
        return None

async def is_owner(user_id: int) -> bool:
    return int(user_id) == OWNER_ID
async def is_banned(user_id: int) -> bool:
    return str(user_id) in set(map(str, DB.get("banned", [])))
async def paused_for(user_id: int) -> bool:
    return DB.get("paused", False) and int(user_id) != OWNER_ID
async def ensure_allowed(message: Message) -> bool:
    await user_touch(message.from_user)
    if await is_banned(message.from_user.id):
        await message.answer("⛔ تم حظر استخدام البوت."); return False
    if await paused_for(message.from_user.id):
        await message.answer("⏸ البوت متوقف مؤقتًا، يرجى المحاولة لاحقًا."); return False
    return True

async def publish_record(rec: dict) -> bool:
    """Publish source payload to the archive channel and attach an information post."""
    mids = []
    typ = rec["type"]
    try:
        if typ == "file":
            sent = await bot.send_document(CHANNEL, rec["file_id"], caption=f"📎 {html.escape(rec.get('title') or rec.get('filename') or 'ملف')}")
            mids.append(sent.message_id)
        elif typ == "photo":
            photo_ids = rec.get("file_ids", [])
            if len(photo_ids) == 1:
                sent = await bot.send_photo(CHANNEL, photo_ids[0]); mids.append(sent.message_id)
            else:
                medias = [InputMediaPhoto(media=fid) for fid in photo_ids[:10]]
                sent_group = await bot.send_media_group(CHANNEL, medias)
                mids.extend(x.message_id for x in sent_group)
                if len(photo_ids) > 10:
                    for fid in photo_ids[10:]:
                        s = await bot.send_photo(CHANNEL, fid); mids.append(s.message_id)
        elif typ == "link":
            text = "🔗 <b>مصدر إلكتروني</b>\n" + html.escape(rec.get("url", ""))
            sent = await bot.send_message(CHANNEL, text, disable_web_page_preview=False); mids.append(sent.message_id)
        info = await bot.send_message(CHANNEL, "📚 <b>بطاقة المصدر</b>\n" + record_meta(rec), disable_web_page_preview=True)
        mids.append(info.message_id)
        rec["channel_message_ids"] = mids
        rec["status"] = "published"
        rec["published_at"] = utcnow()
        await save_db()
        await persist_contributor_txt(int(rec["user_id"]))
        return True
    except Exception as e:
        log.exception("Publishing source %s failed: %s", rec.get("source_id"), e)
        rec["channel_message_ids"] = mids
        rec["status"] = "publication_failed"
        await save_db()
        return False

async def deliver_record(chat_id: int, rec: dict) -> None:
    """Deliver original file/photo/link and metadata to requester."""
    await safe_send(chat_id, "📚 <b>بيانات المصدر</b>\n" + record_meta(rec), disable_web_page_preview=True)
    try:
        if rec["type"] == "file":
            await bot.send_document(chat_id, rec["file_id"], caption=shorten(record_title(rec), 950))
        elif rec["type"] == "photo":
            ids = rec.get("file_ids", [])
            if len(ids) == 1: await bot.send_photo(chat_id, ids[0])
            else:
                for start in range(0,len(ids),10):
                    group = [InputMediaPhoto(media=fid) for fid in ids[start:start+10]]
                    await bot.send_media_group(chat_id, group)
        elif rec["type"] == "link":
            await safe_send(chat_id, "🔗 " + html.escape(rec.get("url","")), disable_web_page_preview=False)
    except Exception as e:
        log.warning("Delivery failed for %s: %s", rec.get("source_id"), e)
        await safe_send(chat_id, "تعذر إرسال المصدر حاليًا. حاول مجددًا أو تواصل مع الإدارة.")

async def confirm_markup() -> InlineKeyboardMarkup:
    return make_keyboard([[ ("✅ تأكيد الإرسال", "pending:confirm"), ("✏️ تعديل", "pending:edit")], [("🗑 حذف", "pending:delete")]])
async def preview_pending(message: Message, rec: dict) -> None:
    PENDING[message.from_user.id] = rec
    await message.answer("🔍 <b>مراجعة المصدر قبل النشر</b>\n\n" + record_meta(rec) + "\n\nراجع البيانات، ثم اختر تأكيد الإرسال أو تعديل أو حذف.", reply_markup=await confirm_markup(), disable_web_page_preview=True)

async def source_buttons(records: list[dict], page: int, prefix: str = "search") -> InlineKeyboardMarkup:
    per = 10; total_pages = max(1,(len(records)+per-1)//per); page=max(0,min(page,total_pages-1)); slice_=records[page*per:(page+1)*per]
    rows = [[(f"{r.get('source_id')} | {shorten(record_title(r),42)}", f"{prefix}:open:{r.get('source_id')}")] for r in slice_]
    nav=[]
    if page>0: nav.append(("⬅️ السابق",f"{prefix}:page:{page-1}"))
    if page<total_pages-1: nav.append(("التالي ➡️",f"{prefix}:page:{page+1}"))
    if nav: rows.append(nav)
    rows.append([("🏠 القائمة الرئيسية","menu:home")])
    return make_keyboard(rows)

# ------------------------------ /start and main menu ------------------------------
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if not await ensure_allowed(message): return
    user = message.from_user
    welcome = ("╭━━━━━━━━━━━━━━━━━━━━╮\n"
              "💾 <b>مــدارك الـمـعـرفــة</b>\n"
              "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
              f"أهلاً بك {html.escape(display_user(user))} ›𓈒𓏲 👋\n\n"
              "🗂 <b>شـارك مـصـادرك مـعنـا</b> 🔹\n"
              "📂 <b>الـحصـول على مـصادر</b> 🔹\n"
              "━━━━━━━━━━━━━━━━━━━\n"
              "📌 جميع المصادر تقدم مجاناً ويمنع بيعها\n\n"
              "🪩 <b>زكـاة الـعلـم نـشــره</b> 🔹\n"
              "━━━━━━━━━━━━━━━━━━━━\n"
              f"🔷 قناة مـدارك الــمعـرفة: <a href='{CHANNEL_URL}'>@Kh_JOORE_BusinessLibrary</a>\n"
              "🔷 إدارة الأعمال: مكتبة مصادر تخصصية\n"
              "🔷 دفعة: -2026-")
    kb = main_keyboard(await is_owner(user.id))
    # Image URLs from galleries are often not direct; attempt photo and gracefully fall back to text.
    if WELCOME_IMAGE:
        try: await message.answer_photo(WELCOME_IMAGE, caption=welcome, reply_markup=kb)
        except Exception: await message.answer(welcome, reply_markup=kb, disable_web_page_preview=True)
    else: await message.answer(welcome, reply_markup=kb, disable_web_page_preview=True)

@dp.message(Command("menu"))
async def menu_command(message: Message, state: FSMContext):
    await state.clear()
    if not await ensure_allowed(message): return
    await message.answer("🏛 <b>القائمة الرئيسية — مدارك المعرفة</b>", reply_markup=main_keyboard(await is_owner(message.from_user.id)))

# ------------------------------ Menu callbacks ------------------------------
@dp.callback_query(F.data == "menu:home")
async def menu_home(call: CallbackQuery, state: FSMContext):
    await state.clear(); await call.answer()
    if await is_banned(call.from_user.id): return
    await call.message.answer("🏛 <b>القائمة الرئيسية — مدارك المعرفة</b>", reply_markup=main_keyboard(await is_owner(call.from_user.id)))

@dp.callback_query(F.data == "menu:submit")
async def choose_source(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    await state.clear(); await call.answer()
    await call.message.answer("📂 <b>اختر نوع المصدر الذي تريد مشاركته:</b>", reply_markup=make_keyboard([[ ("📄 ملف", "submit:file"), ("🖼 صورة", "submit:photo")], [("🔗 رابط", "submit:link")], [("↩️ الرئيسية", "menu:home")]]))

async def ensure_callback_allowed(call: CallbackQuery) -> bool:
    await user_touch(call.from_user)
    if await is_banned(call.from_user.id): await call.answer("تم حظرك",show_alert=True); return False
    if await paused_for(call.from_user.id): await call.answer("البوت متوقف مؤقتًا",show_alert=True); return False
    return True


@dp.callback_query(F.data.startswith("submit:"))
async def submission_type(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    typ = call.data.split(":",1)[1]; await state.clear(); await call.answer()
    if typ == "file":
        await state.set_state(Flow.awaiting_file)
        await call.message.answer("📄 أرسل الملف الآن. يمكنك إرسال كتاب أو بحث أو رسالة أو مستند. بعد وصول الملف ستتمكن من إضافة وصف اختياري ثم مراجعة النشر.")
    elif typ == "photo":
        PHOTO_GROUPS[call.from_user.id] = {"photos":[], "created_at":utcnow()}
        await state.set_state(Flow.awaiting_photo)
        await call.message.answer("🖼 أرسل الصور واحدة تلو الأخرى أو كألبوم. بعد الانتهاء اضغط زر <b>انتهيت من إرسال الصور</b>.", reply_markup=make_keyboard([[ ("✅ انتهيت من إرسال الصور", "photos:done")],[("↩️ إلغاء", "pending:delete")]]))
    elif typ == "link":
        await state.set_state(Flow.awaiting_link)
        await call.message.answer("🔗 أرسل رابط المصدر (مكتبة رقمية، مجلة، مستودع، موقع أو بحث). أرسل رابطًا واحدًا في كل مرة.")

# ------------------------------ File source workflow ------------------------------
@dp.message(Flow.awaiting_file, F.document)
async def receive_file(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    d = message.document
    rec = {"type":"file", "file_id":d.file_id, "file_unique_id":d.file_unique_id, "filename":d.file_name or "ملف", "title":d.file_name or "ملف", "user_id":message.from_user.id, "user_name":display_user(message.from_user), "username":message.from_user.username, "created_at":utcnow(), "year":None, "note":"", "channel_message_ids":[], "status":"pending"}
    await state.update_data(pending_rec=rec); await state.set_state(Flow.awaiting_file_note)
    await message.answer("📄 استلمت الملف: <b>"+html.escape(rec["filename"])+"</b>\n\nأرسل وصفًا أو عنوانًا أو سنة النشر إن رغبت، أو اكتب <code>تخطي</code> لإكمال دون وصف.")

@dp.message(Flow.awaiting_file_note)
async def file_note(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    data = await state.get_data(); rec = data.get("pending_rec")
    if not rec: await state.clear(); return await message.answer("انتهت جلسة الإضافة. ابدأ من اختيار نوع المصدر.")
    note = message.text.strip() if message.text else ""
    if note.lower() not in ("تخطي","skip","—"):
        rec["note"] = note; rec["title"] = note.splitlines()[0][:160] or rec["filename"]; rec["year"] = parse_year(note)
    await state.clear(); await preview_pending(message,rec)

# ------------------------------ Photo workflow ------------------------------
@dp.message(Flow.awaiting_photo, F.photo)
async def receive_photo(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    group = PHOTO_GROUPS.setdefault(message.from_user.id,{"photos":[],"created_at":utcnow()})
    group["photos"].append(message.photo[-1].file_id)
    await message.answer(f"🖼 تم استلام الصورة ({len(group['photos'])}). أرسل المزيد أو اضغط <b>انتهيت من إرسال الصور</b>.")

@dp.callback_query(F.data == "photos:done")
async def photos_done(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    group = PHOTO_GROUPS.get(call.from_user.id)
    if not group or not group.get("photos"): await call.answer("لم تصل أي صورة بعد.",show_alert=True); return
    await call.answer(); await state.set_state(Flow.awaiting_photo_note)
    await call.message.answer(f"🖼 تم تجميع {len(group['photos'])} صورة. أرسل عنوانًا أو وصفًا اختياريًا أو اكتب <code>تخطي</code>.")

@dp.message(Flow.awaiting_photo_note)
async def photo_note(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    group = PHOTO_GROUPS.pop(message.from_user.id,None)
    if not group: await state.clear(); return await message.answer("انتهت جلسة الصور. أعد المحاولة.")
    note = message.text.strip() if message.text else ""
    if note.lower() in ("تخطي","skip","—"): note=""
    rec = {"type":"photo","file_ids":group["photos"],"title":note.splitlines()[0][:160] if note else f"مجموعة صور ({len(group['photos'])})", "note":note,"year":parse_year(note),"user_id":message.from_user.id,"user_name":display_user(message.from_user),"username":message.from_user.username,"created_at":utcnow(),"channel_message_ids":[],"status":"pending"}
    await state.clear(); await preview_pending(message,rec)

# ------------------------------ Link workflow ------------------------------
@dp.message(Flow.awaiting_link)
async def receive_link(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    raw = message.text or ""
    urls = [x.strip() for x in raw.splitlines() if x.strip()]
    urls = [u for u in urls if valid_url(u)]
    if not urls:
        return await message.answer("❌ لم أجد رابطًا صالحًا. أرسل رابطًا يبدأ بـ http:// أو https://.")
    if len(urls) > 1:
        await message.answer("أرسل رابطًا واحدًا فقط في كل عملية، حتى تُراجع بياناته وتؤكد نشره بصورة مستقلة.")
        return
    rec = {"type":"link","url":urls[0],"title":urls[0],"note":"","year":None,"user_id":message.from_user.id,"user_name":display_user(message.from_user),"username":message.from_user.username,"created_at":utcnow(),"channel_message_ids":[],"status":"pending"}
    await state.update_data(pending_rec=rec); await state.set_state(Flow.awaiting_link_note)
    await message.answer("🔗 تم استلام الرابط. أرسل اسم المصدر أو وصفًا مختصرًا (اختياري)، أو اكتب <code>تخطي</code>.")

@dp.message(Flow.awaiting_link_note)
async def link_note(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    data = await state.get_data(); rec = data.get("pending_rec")
    if not rec: await state.clear(); return await message.answer("انتهت جلسة الإضافة.")
    note = message.text.strip() if message.text else ""
    if note.lower() not in ("تخطي","skip","—"):
        rec["note"] = note; rec["title"] = note.splitlines()[0][:160] or rec["url"]; rec["year"] = parse_year(note)
    await state.clear(); await preview_pending(message,rec)

# ------------------------------ Pending review callbacks ------------------------------
@dp.callback_query(F.data == "pending:confirm")
async def pending_confirm(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    rec = PENDING.get(call.from_user.id)
    if not rec: await call.answer("لا توجد عملية بانتظار التأكيد.",show_alert=True); return
    await call.answer("جاري النشر…")
    rec["source_id"] = await next_id(rec["type"])
    ok = await publish_record(rec)
    if ok:
        await commit_record(rec)
        PENDING.pop(call.from_user.id,None)
        await call.message.answer("✅ <b>تم اعتماد المصدر ونشره في القناة.</b>\n" + record_meta(rec),disable_web_page_preview=True)
    else:
        await call.message.answer("⚠️ لم يكتمل النشر إلى القناة. بقي المصدر في حالة فشل النشر ولم يتم اعتماده نهائيًا. أبلغ الإدارة أو أعد المحاولة.")

@dp.callback_query(F.data == "pending:edit")
async def pending_edit(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    if call.from_user.id not in PENDING: await call.answer("لا توجد مراجعة معلقة.",show_alert=True); return
    await call.answer(); await state.set_state(Flow.awaiting_edit_note)
    await call.message.answer("✏️ أرسل العنوان أو الوصف الجديد. سيحل محل الوصف السابق.")

@dp.message(Flow.awaiting_edit_note)
async def edit_pending_note(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    rec = PENDING.get(message.from_user.id)
    if not rec: await state.clear(); return await message.answer("لا توجد مراجعة معلقة.")
    note = message.text.strip() if message.text else ""
    if not note: return await message.answer("أرسل نصًا صالحًا للتعديل.")
    rec["note"] = note; rec["title"] = note.splitlines()[0][:160]; rec["year"] = parse_year(note)
    await state.clear(); await preview_pending(message,rec)

@dp.callback_query(F.data == "pending:delete")
async def pending_delete(call: CallbackQuery, state: FSMContext):
    await state.clear(); PENDING.pop(call.from_user.id,None); PHOTO_GROUPS.pop(call.from_user.id,None)
    await call.answer("تم إلغاء المصدر قبل النشر.")
    await call.message.answer("🗑 تم حذف/إلغاء المصدر غير المنشور.",reply_markup=main_keyboard(await is_owner(call.from_user.id)))

# ------------------------------ Search / catalog ------------------------------
@dp.callback_query(F.data == "menu:search")
async def search_menu(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    await call.answer(); await state.clear()
    await call.message.answer("🔎 <b>الحصول على المصادر</b>", reply_markup=make_keyboard([[ ("🔍 البحث عن عنوان معين","search:query")],[("📋 إظهار العناوين بشكل قوائم","search:list:0")],[("📄 تحميل العناوين بشكل ملف TXT","search:txt")],[("🏠 الرئيسية","menu:home")]]))

@dp.callback_query(F.data == "search:query")
async def ask_search(call: CallbackQuery, state: FSMContext):
    if not await ensure_callback_allowed(call): return
    await call.answer(); await state.set_state(Flow.awaiting_search)
    await call.message.answer("🔎 اكتب العنوان أو جزءًا منه، وسأبحث عن المصادر الأقرب.")

@dp.message(Flow.awaiting_search)
async def search_text(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    data = await state.get_data()
    if data.get("admin_search_contrib"):
        if not await is_owner(message.from_user.id):
            await state.clear(); return await message.answer("غير مصرح.")
        value=(message.text or "").strip().lstrip("@").lower()
        matches=[(uid,u) for uid,u in DB.get("users",{}).items() if value in str(uid) or value in (u.get("username") or "").lower() or value in (u.get("name") or "").lower()]
        await state.clear()
        if not matches: return await message.answer("لم أجد مساهمًا بهذا الاسم أو المعرف.")
        rows=[]
        for uid,u in matches[:30]:
            count=sum(1 for r in DB["sources"] if str(r.get("user_id"))==uid and r.get("status")=="published")
            rows.append([(f"{shorten(u.get('name','مساهم'),24)} | {uid} | {count}",f"admin:user:{uid}:0")])
        return await message.answer(f"نتائج البحث عن المساهمين: {len(matches)}",reply_markup=make_keyboard(rows))
    q = norm(message.text or "")
    if not q: return await message.answer("اكتب كلمة أو عنوانًا للبحث.")
    records = [r for r in DB["sources"] if r.get("status")=="published"]
    scored=[]
    for r in records:
        t=norm(record_title(r)); n=norm(r.get("note", "")); candidate=t+" "+n
        score = 0.0
        if q in t: score=1.0
        elif q in candidate: score=.88
        else: score=max(difflib.SequenceMatcher(None,q,t).ratio(), difflib.SequenceMatcher(None,q,candidate).ratio())
        # reward matching individual query terms
        terms=q.split()
        if terms: score=max(score, sum(1 for term in terms if term in candidate)/len(terms)*.75)
        if score>=.25: scored.append((score,r))
    scored.sort(key=lambda x:x[0],reverse=True)
    await state.clear()
    if not scored: return await message.answer("لا توجد نتائج مطابقة. جرّب كلمات أخرى أو افتح قائمة العناوين.")
    hits=[r for _,r in scored[:35]]
    SEARCH_RESULTS[message.from_user.id]=[r.get("source_id") for r in hits]
    await message.answer(f"🔍 نتائج البحث: <b>{len(hits)}</b> مصدر/مصادر ذات صلة.",reply_markup=await source_buttons(hits,0,"search"))

@dp.callback_query(F.data.startswith("search:page:"))
async def search_page(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    page=int(call.data.split(":")[-1]); ids=SEARCH_RESULTS.get(call.from_user.id,[])
    records=[r for sid in ids for r in DB["sources"] if r.get("source_id")==sid and r.get("status")=="published"]
    await call.answer()
    if not records: return await call.message.answer("انتهت نتائج البحث. أعد البحث من جديد.")
    await call.message.answer("🔎 نتائج البحث",reply_markup=await source_buttons(records,page,"search"))

@dp.callback_query(F.data.startswith("search:open:"))
async def open_source(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    sid=call.data.split(":",2)[2]
    rec=next((r for r in DB["sources"] if r.get("source_id")==sid and r.get("status")=="published"),None)
    await call.answer()
    if not rec: return await call.message.answer("المصدر غير متاح أو حُذف.")
    await deliver_record(call.from_user.id,rec)

@dp.callback_query(F.data.startswith("search:list:"))
async def titles_page(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    page=int(call.data.split(":")[-1]); records=[r for r in DB["sources"] if r.get("status")=="published"]
    records.sort(key=lambda r:r.get("created_at",""),reverse=True)
    await call.answer()
    if not records: return await call.message.answer("📭 لا توجد مصادر منشورة حتى الآن.")
    await call.message.answer(f"📋 <b>فهرس المصادر</b> — {page+1}",reply_markup=await source_buttons(records,page,"list"))

@dp.callback_query(F.data.startswith("list:page:"))
async def list_page(call: CallbackQuery):
    await titles_page_proxy(call, int(call.data.split(":")[-1]))
async def titles_page_proxy(call: CallbackQuery, page: int):
    if not await ensure_callback_allowed(call): return
    records=[r for r in DB["sources"] if r.get("status")=="published"]
    records.sort(key=lambda r:r.get("created_at",""),reverse=True)
    await call.answer()
    await call.message.answer(f"📋 <b>فهرس المصادر</b> — {page+1}",reply_markup=await source_buttons(records,page,"list"))


@dp.callback_query(F.data.startswith("list:open:"))
async def open_from_list(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    sid=call.data.split(":",2)[2]
    rec=next((r for r in DB["sources"] if r.get("source_id")==sid and r.get("status")=="published"),None)
    await call.answer()
    if rec: await deliver_record(call.from_user.id,rec)
    else: await call.message.answer("المصدر غير موجود أو تم حذفه.")

@dp.callback_query(F.data == "search:txt")
async def titles_txt(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    await call.answer("يتم تجهيز الملف…")
    records=[r for r in DB["sources"] if r.get("status")=="published"]
    records.sort(key=lambda r:r.get("source_id",""))
    lines=["╔══════════════════════════════════════╗","        مدارك المعرفة | فهرس المصادر","╚══════════════════════════════════════╝",f"عدد المصادر: {len(records)}",f"تاريخ إعداد الفهرس: {utcnow()}","", "=== الملفات ==="]
    for typ,section in [("file","الملفات"),("photo","الصور"),("link","الروابط")]:
        lines += [f"\n=== {section} ==="]
        for r in records:
            if r.get("type")==typ:
                lines += [f"[{r.get('source_id')}] {record_title(r)}", f"الوصف: {r.get('note') or '—'}", f"السنة: {r.get('year') or '—'}", f"تاريخ الإضافة: {r.get('created_at','')[:10]}"]
                if typ=="link": lines.append(f"الرابط: {r.get('url','')}")
    lines += ["", "قناة مدارك المعرفة: " + CHANNEL_URL, "جميع المصادر مجانية ويمنع بيعها."]
    file=BufferedInputFile("\n".join(lines).encode("utf-8"),filename="TAQ_BusinessLibrary.txt")
    await bot.send_document(call.from_user.id,file,caption="📄 فهرس عناوين مكتبة مدارك المعرفة")

# ------------------------------ Channel and guide ------------------------------
@dp.callback_query(F.data == "open:channel")
async def open_channel(call: CallbackQuery):
    await call.answer(); await call.message.answer(f"📚 قناة مدارك المعرفة:\n{CHANNEL_URL}")
@dp.callback_query(F.data == "open:report")
async def open_report(call: CallbackQuery):
    await call.answer(); await call.message.answer(f"⚠️ للإبلاغ عن مشكلة أو التواصل مع الإدارة:\n{CONTACT_URL}")
@dp.callback_query(F.data == "menu:guide")
async def guide(call: CallbackQuery):
    if not await ensure_callback_allowed(call): return
    await call.answer()
    text=("📘 <b>دليل تشغيل مدارك المعرفة</b>\n\n"
          "1️⃣ <b>اختيار نوع المصدر:</b> اختر ملفًا أو صورة أو رابطًا. أرسل المحتوى، ثم أضف وصفًا اختياريًا، وراجع بطاقة المصدر قبل تأكيد النشر.\n\n"
          "2️⃣ <b>الملفات:</b> أرسل الكتاب أو البحث أو المستند. اكتب عنوانًا أو سنة أو نبذة، أو اختر تخطي.\n\n"
          "3️⃣ <b>الصور:</b> أرسل الصور تباعًا أو ضمن ألبوم، ثم اضغط (انتهيت من إرسال الصور) واكتب وصفًا اختياريًا.\n\n"
          "4️⃣ <b>الروابط:</b> أرسل رابطًا مباشرًا يبدأ بـ http أو https ثم اكتب اسم الموقع أو وصف المصدر.\n\n"
          "5️⃣ <b>المراجعة:</b> تأكيد الإرسال ينشر المصدر في القناة ويثبته. تعديل يغيّر الوصف قبل النشر. حذف يلغي المصدر المعلّق. بعد التأكيد لا يستطيع المساهم تعديل أو حذف المصدر.\n\n"
          "6️⃣ <b>البحث:</b> اكتب عنوانًا أو جزءًا منه للعثور على مصادر مشابهة.\n\n"
          "7️⃣ <b>قوائم العناوين:</b> تصفح الفهرس على صفحات، واختر المصدر ليصلك في الخاص.\n\n"
          "8️⃣ <b>ملف TXT:</b> حمّل فهرس المصادر المكتوب مع الرموز والعناوين والروابط.\n\n"
          "9️⃣ <b>قناة البوت:</b> القناة هي أرشيف المصادر المنشورة.\n\n"
          "🔟 <b>الإبلاغ:</b> يفتح حساب التواصل الإداري.\n\n"
          "📌 المصادر مجانية ويمنع بيعها أو نسبها إلى غير أصحابها.")
    await call.message.answer(text,reply_markup=make_keyboard([[ ("🏠 القائمة الرئيسية","menu:home")]]))


# ------------------------------ Owner panel ------------------------------
@dp.callback_query(F.data.in_({"admin:contributors", "admin:ban", "admin:pause"}))
async def admin_router(call: CallbackQuery, state: FSMContext):
    if not await is_owner(call.from_user.id): return await call.answer("هذه القائمة خاصة بالمالك.",show_alert=True)
    action=call.data.split(":",1)[1]
    await call.answer()
    if action=="contributors":
        await admin_contributors(call,0)
    elif action=="ban":
        await call.message.answer("🚫 <b>إدارة حظر المستخدمين</b>",reply_markup=make_keyboard([[ ("✍️ اكتب معرف المستخدم ليتم حظره","admin:ban:prompt")],[("👥 معرفات المستخدمين","admin:users:0")],[("🏠 الرئيسية","menu:home")]]))
    elif action=="pause":
        DB["paused"]=not DB.get("paused",False); await save_db()
        status="تم إيقاف البوت مؤقتًا." if DB["paused"] else "تم تشغيل البوت مجددًا."
        await call.message.answer(status,reply_markup=main_keyboard(True))

async def admin_contributors(call: CallbackQuery, page: int):
    contribs={}
    for r in DB["sources"]:
        if r.get("status")=="published":
            uid=str(r.get("user_id")); contribs.setdefault(uid,[]).append(r)
    ids=sorted(contribs.keys(),key=lambda uid:len(contribs[uid]),reverse=True)
    per=10; maxpage=max(1,(len(ids)+per-1)//per); page=max(0,min(page,maxpage-1))
    rows=[]
    for uid in ids[page*per:(page+1)*per]:
        u=DB.get("users",{}).get(uid,{})
        rows.append([(f"👤 {shorten(u.get('name','مستخدم'),24)} | {len(contribs[uid])} مصدر",f"admin:user:{uid}:0")])
    nav=[]
    if page>0: nav.append(("⬅️ السابق",f"admin:contributors:{page-1}"))
    if page<maxpage-1: nav.append(("التالي ➡️",f"admin:contributors:{page+1}"))
    if nav: rows.append(nav)
    rows.append([ ("🔍 البحث عن مساهم","admin:findcontrib")])
    rows.append([ ("🏠 الرئيسية","menu:home")])
    await call.message.answer(f"👥 <b>MY-BOTS — المساهمون</b>\nعدد المساهمين: {len(ids)}\nصفحة {page+1}/{maxpage}",reply_markup=make_keyboard(rows))

@dp.callback_query(F.data.startswith("admin:contributors:"))
async def admin_contributors_page(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    await call.answer(); await admin_contributors(call,int(call.data.split(":")[-1]))

@dp.callback_query(F.data.startswith("admin:user:"))
async def admin_user_detail(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    _,_,uid,page=call.data.split(":",3); page=int(page)
    records=[r for r in DB["sources"] if str(r.get("user_id"))==uid and r.get("status")=="published"]
    records.sort(key=lambda r:r.get("created_at",""),reverse=True)
    usr=DB.get("users",{}).get(uid,{})
    counts={t:sum(r.get("type")==t for r in records) for t in ("file","photo","link")}
    lines=f"👤 <b>بيانات المساهم</b>\nالاسم: {html.escape(usr.get('name',''))}\nالمعرف: <code>{uid}</code>\nالمستخدم: @{html.escape(usr.get('username') or '—')}\nعدد المصادر: {len(records)}\n📄 ملفات: {counts['file']} | 🖼 صور: {counts['photo']} | 🔗 روابط: {counts['link']}"
    per=8; total=max(1,(len(records)+per-1)//per); page=max(0,min(page,total-1)); subset=records[page*per:(page+1)*per]
    rows=[[ (f"{r.get('source_id')} · {shorten(record_title(r),35)}",f"admin:source:{r.get('source_id')}")] for r in subset]
    nav=[]
    if page>0: nav.append(("⬅️ السابق",f"admin:user:{uid}:{page-1}"))
    if page<total-1: nav.append(("التالي ➡️",f"admin:user:{uid}:{page+1}"))
    if nav: rows.append(nav)
    rows.append([(f"📄 إرسال سجل المساهم TXT",f"admin:txt:{uid}")])
    rows.append([( "⬅️ المساهمون", "admin:contributors:0")])
    await call.answer(); await call.message.answer(lines+f"\nصفحة {page+1}/{total}",reply_markup=make_keyboard(rows))

@dp.callback_query(F.data.startswith("admin:source:"))
async def admin_source_actions(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    sid=call.data.split(":",2)[2]; rec=next((r for r in DB["sources"] if r.get("source_id")==sid),None)
    if not rec: return await call.answer("المصدر غير موجود",show_alert=True)
    await call.answer()
    await call.message.answer("🗃 <b>إدارة المصدر</b>\n"+record_meta(rec),reply_markup=make_keyboard([[ ("📥 إرسال المصدر للمالك",f"admin:download:{sid}")],[("🗑 حذف المصدر من القناة والفهرس",f"admin:delask:{sid}")],[("⬅️ عودة للمساهم",f"admin:user:{rec.get('user_id')}:0")]]))

@dp.callback_query(F.data.startswith("admin:download:"))
async def admin_download(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    sid=call.data.split(":",2)[2]; rec=next((r for r in DB["sources"] if r.get("source_id")==sid and r.get("status")=="published"),None)
    await call.answer()
    if rec: await deliver_record(call.from_user.id,rec)
    else: await call.message.answer("المصدر غير موجود.")

@dp.callback_query(F.data.startswith("admin:delask:"))
async def admin_delete_confirm(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    sid=call.data.split(":",2)[2]; await call.answer()
    await call.message.answer(f"⚠️ هل تريد حذف المصدر <code>{sid}</code> من القناة والفهرس؟",reply_markup=make_keyboard([[ ("نعم، احذف",f"admin:delete:{sid}"),("إلغاء",f"admin:source:{sid}")]]))

@dp.callback_query(F.data.startswith("admin:delete:"))
async def admin_delete(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    sid=call.data.split(":",2)[2]; rec=next((r for r in DB["sources"] if r.get("source_id")==sid),None)
    await call.answer("جارٍ الحذف")
    if not rec: return await call.message.answer("المصدر غير موجود.")
    failed=[]
    for mid in rec.get("channel_message_ids",[]):
        try: await bot.delete_message(CHANNEL,int(mid))
        except Exception as e: failed.append(str(mid)); log.warning("Delete channel message %s failed: %s",mid,e)
    if failed:
        await call.message.answer("⚠️ تعذر حذف بعض رسائل القناة. لم أحذف سجل المصدر من الفهرس حتى لا تضيع بياناته. الرسائل: "+", ".join(failed)); return
    DB["sources"]=[r for r in DB["sources"] if r.get("source_id")!=sid]
    await save_db(); await persist_contributor_txt(int(rec["user_id"]))
    await call.message.answer(f"🗑 تم حذف المصدر {sid} ورسائله من القناة وتحديث الفهرس.")

@dp.callback_query(F.data.startswith("admin:txt:"))
async def admin_contributor_txt(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    uid=int(call.data.split(":",2)[2]); await call.answer("يتم تجهيز الملف…")
    records=[r for r in DB["sources"] if int(r.get("user_id",0))==uid and r.get("status")=="published"]
    usr=DB.get("users",{}).get(str(uid),{})
    types={t:sum(r.get("type")==t for r in records) for t in ("file","photo","link")}
    lines=["MADAREK KNOWLEDGE LIBRARY — CONTRIBUTOR", "="*48, f"Name: {usr.get('name','')}",f"Username: @{usr.get('username') or '—'}",f"Telegram ID: {uid}",f"Total sources: {len(records)}",f"Files: {types['file']} | Photos: {types['photo']} | Links: {types['link']}",f"First contribution: {min((r.get('created_at','') for r in records),default='—')}",f"Latest contribution: {max((r.get('created_at','') for r in records),default='—')}","", "SOURCE DETAILS", "="*48]
    for r in sorted(records,key=lambda x:x.get("created_at","")):
        lines += [f"\n{r.get('source_id')} | {source_type_label(r.get('type'))}",f"Title: {record_title(r)}",f"Note: {r.get('note') or '—'}",f"Year: {r.get('year') or '—'}",f"Submitted: {r.get('created_at')}",f"Channel message IDs: {r.get('channel_message_ids',[])}"]
    file=BufferedInputFile("\n".join(lines).encode(),filename=f"Contributor_{uid}.txt")
    await bot.send_document(call.from_user.id,file,caption=f"📄 سجل مساهم — {usr.get('name','')}")
    await persist_contributor_txt(uid)

@dp.callback_query(F.data == "admin:findcontrib")
async def admin_find_contributor(call: CallbackQuery, state: FSMContext):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    await call.answer(); await call.message.answer("أرسل Telegram ID أو اسم المستخدم للمساهم."); await state.set_state(Flow.awaiting_search)
    await state.update_data(admin_search_contrib=True)

@dp.callback_query(F.data == "admin:ban:prompt")
async def admin_ban_prompt(call: CallbackQuery, state: FSMContext):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    await call.answer(); await state.set_state(Flow.awaiting_ban)
    await call.message.answer("🚫 أرسل Telegram ID أو @username للمستخدم الذي تريد حظره.")

@dp.message(Flow.awaiting_ban)
async def admin_ban_user(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id): await state.clear(); return
    value=(message.text or "").strip()
    uid=None
    if value.isdigit(): uid=value
    elif value.startswith("@"):
        matches=[u for u in DB.get("users",{}).values() if (u.get("username") or "").lower()==value[1:].lower()]
        if matches: uid=str(matches[0]["id"])
    if not uid: await state.clear(); return await message.answer("لم أجد المستخدم. استخدم ID رقميًا لمستخدم سبق أن تعامل مع البوت.")
    if uid not in DB["banned"]: DB["banned"].append(uid)
    if uid in DB["users"]: DB["users"][uid]["blocked"]=True
    await save_db(); await state.clear()
    await message.answer(f"🚫 تم حظر {uid} من استخدام البوت.",reply_markup=make_keyboard([[ ("📋 عرض المستخدمين","admin:users:0")],[("🏠 الرئيسية","menu:home")]]))

@dp.callback_query(F.data.startswith("admin:users:"))
async def admin_users_page(call: CallbackQuery):
    if not await is_owner(call.from_user.id): return await call.answer("غير مصرح",show_alert=True)
    users=list(DB.get("users",{}).values()); users.sort(key=lambda x:x.get("last_seen",""),reverse=True)
    page=int(call.data.split(":")[-1]); per=10; pages=max(1,(len(users)+per-1)//per); page=max(0,min(page,pages-1)); rows=[]
    for u in users[page*per:(page+1)*per]:
        label=f"{shorten(u.get('name','مستخدم'),28)} | {u.get('id')}" + (" 🚫" if u.get("blocked") else "")
        rows.append([(label,f"admin:user:{u.get('id')}:0")])
    nav=[]
    if page>0: nav.append(("⬅️ السابق",f"admin:users:{page-1}"))
    if page<pages-1: nav.append(("التالي ➡️",f"admin:users:{page+1}"))
    if nav: rows.append(nav)
    rows.append([( "🏠 الرئيسية", "menu:home")])
    await call.answer(); await call.message.answer(f"👥 المستخدمون المسجلون: {len(users)} | صفحة {page+1}/{pages}",reply_markup=make_keyboard(rows))

# ------------------------------ Catch-all and startup ------------------------------
@dp.message()
async def catch_all(message: Message, state: FSMContext):
    if not await ensure_allowed(message): return
    if message.text and message.text.startswith("/"): return
    await message.answer("لم أفهم الرسالة. استخدم القائمة الرئيسية واختر العملية المطلوبة.",reply_markup=main_keyboard(await is_owner(message.from_user.id)))

async def main():
    await load_db()
    log.info("Madarek library bot started. Persistent GitHub: %s", bool(GH_TOKEN and GH_REPO))
    # Telegram polling retries are handled by aiogram; on transient errors the outer loop restarts.
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Polling crashed; restarting after 8 seconds")
            await asyncio.sleep(8)

if __name__ == "__main__":
    asyncio.run(main())
