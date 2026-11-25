"""Telegram student library bot + FastAPI backend. Config is read from environment."""
import os, json, sqlite3, logging, html, asyncio
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import httpx

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("business_library")
TOKEN=os.getenv("TOKKEN","").strip(); ADMIN_CODE=os.getenv("ADMIN_CODE",""); ADMIN_ID=int(os.getenv("ADMIN_ID","0") or 0); GROUP_LINK=os.getenv("GROUP_LINK","https://t.me/+mZe3EZzQBCYxOTUy")
BASE=Path(__file__).resolve().parent; STATIC=BASE/"static"; DATA_DIR=Path(os.getenv("DATA_DIR",str(BASE))); DATA_DIR.mkdir(parents=True,exist_ok=True); DB=DATA_DIR/"library.sqlite3"
if not TOKEN: log.warning("TOKKEN environment variable is not set")

bot=Bot(TOKEN) if TOKEN else None; dp=Dispatcher(storage=MemoryStorage()) if TOKEN else None
class UploadFlow(StatesGroup): title=State(); description=State(); confirm=State()
class AdminFlow(StatesGroup): code=State(); pick=State(); action=State(); edit_title=State(); edit_description=State()
def db():
 c=sqlite3.connect(DB);c.execute('''CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY, telegram_file_id TEXT NOT NULL, file_name TEXT, title TEXT, description TEXT, file_size INTEGER, mime_type TEXT, created_at TEXT, uploader_id INTEGER, pinned INTEGER DEFAULT 0)''');c.execute('CREATE TABLE IF NOT EXISTS stats(key TEXT PRIMARY KEY,value INTEGER DEFAULT 0)');c.execute("INSERT OR IGNORE INTO stats(key,value) VALUES('visits',0),('downloads',0)");c.commit();c.close()
def book_rows():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;rows=c.execute('SELECT * FROM books ORDER BY pinned DESC,created_at DESC').fetchall();c.close();return [dict(r) for r in rows]
def get_book(book_id):
 c=sqlite3.connect(DB);c.row_factory=sqlite.Row;r=c.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone();c.close();return dict(r) if r else None
def stats():
 c=sqlite3.connect(DB);d=dict(c.execute('SELECT key,value FROM stats').fetchall());c.close();return d
def increment(key):
 c=sqlite3.connect(DB);c.execute('UPDATE stats SET value=value+1 WHERE key=?',(key,));c.commit();c.close()
def is_admin(user_id):return user_id==ADMIN_ID and ADMIN_ID!=0
@asynccontextmanager
async def lifespan(app):
 db(); polling_task=None
 if bot and dp:
  await bot.delete_webhook(drop_pending_updates=False)
  polling_task=asyncio.create_task(dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types()))
 yield
 if bot and dp:
  try: await dp.stop_polling()
  except Exception: pass
  if polling_task:
   try: await polling_task
   except Exception as e: log.exception('Bot polling stopped: %s',e)
  await bot.session.close()
app=FastAPI(title='Business Administration Student Library',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(STATIC),check_dir=False),name='static')
@app.get('/')
def home():return FileResponse(STATIC/'index.html')
@app.get('/api/books')
def api_books():
 books=book_rows();return {'books':[{k:b.get(k) for k in ['id','file_name','title','description','file_size','mime_type','created_at','pinned']} for b in books], 'total':len(books),'downloads':stats().get('downloads',0)}
@app.get('/api/stats')
def api_stats():return {'visits':stats().get('visits',0),'downloads':stats().get('downloads',0),'books':len(book_rows())}
@app.post('/api/visit')
def visit():increment('visits');return {'ok':True}
@app.post('/api/download/{book_id}')
def count_download(book_id:str):
 if not get_book(book_id):raise HTTPException(404,'Book not found')
 increment('downloads');return {'ok':True}
@app.post('/api/pin/{book_id}')
def pin(book_id:str):
 c=sqlite3.connect(DB);r=c.execute('SELECT pinned FROM books WHERE id=?',(book_id,)).fetchone()
 if not r:c.close();raise HTTPException(404,'Book not found')
 c.execute('UPDATE books SET pinned=? WHERE id=?',(0 if r[0] else 1,book_id));c.commit();c.close();return {'ok':True}
@app.get('/api/file/{book_id}')
async def file_proxy(book_id:str):
 b=get_book(book_id)
 if not b:raise HTTPException(404,'Book not found')
 if not bot:raise HTTPException(503,'Bot not configured')
 try:
  f=await bot.get_file(b['telegram_file_id']);url=f'https://api.telegram.org/file/bot{TOKEN}/{f.file_path}'
  async def stream():
   async with httpx.AsyncClient(timeout=120) as client:
    async with client.stream('GET',url) as resp:
     if resp.status_code!=200:raise RuntimeError('Telegram file download failed')
     async for chunk in resp.aiter_bytes():yield chunk
  filename=b.get('file_name') or b.get('title') or 'book'
  safe=filename.replace('"','').replace('\\','_')
  return StreamingResponse(stream(),media_type=b.get('mime_type') or 'application/octet-stream',headers={'Content-Disposition':f'inline; filename="{safe}"'})
 except Exception as e:log.exception('File proxy error');raise HTTPException(502,'Telegram could not serve this file')

# Bot conversation handlers
@dp.message(CommandStart())
async def start(m:Message,state:FSMContext):
 await state.clear()
 name=m.from_user.first_name or 'طالب'
 # Inline buttons are attached to the message; no reply keyboard is shown.
 builder=InlineKeyboardBuilder()
 site_url=os.getenv('SITE_URL','').strip()
 if site_url and site_url.startswith(('https://','http://')):
  builder.row(InlineKeyboardButton(text='📚 فتح المكتبة',url=site_url))
 builder.row(
  InlineKeyboardButton(text='👥 JOIN GROUP',url=GROUP_LINK),
  InlineKeyboardButton(text='🤖 MY BOT',url='https://t.me/ONX44')
 )
 kb=builder.as_markup()
 await m.answer(
  f'أهلاً بك {html.escape(name)} ✨\n\nمكتبة طلاب إدارة الأعمال — لإضافة كتاب أرسل الملف مباشرة إلى البوت، ثم اكتب عنوانه ووصفه.',
  reply_markup=kb
 )
@dp.message(Command('cancel'))
async def cancel(m:Message,state:FSMContext):await state.clear();await m.answer('تم إلغاء العملية.')
@dp.message(F.document)
async def receive_doc(m:Message,state:FSMContext):
 await state.clear();d=m.document;await state.update_data(file_id=d.file_id,file_name=d.file_name or 'file',size=d.file_size,mime=d.mime_type,owner=m.from_user.id)
 await state.set_state(UploadFlow.title);await m.answer('📖 وصل الملف بنجاح.\n\nأرسل الآن <b>عنوان الكتاب</b> الذي سيظهر في المكتبة.',parse_mode='HTML')
@dp.message(UploadFlow.title)
async def title_received(m:Message,state:FSMContext):
 await state.update_data(title=m.text.strip()[:250]);await state.set_state(UploadFlow.description);await m.answer('✍️ أرسل وصف الكتاب أو ملاحظة أو رابط.\nإذا لا توجد ملاحظة، اكتب: بدون')
@dp.message(UploadFlow.description)
async def desc_received(m:Message,state:FSMContext):
 data=await state.get_data();desc=(m.text or '').strip();desc='' if desc.lower() in ('بدون','لا يوجد','-') else desc
 await state.update_data(description=desc);await state.set_state(UploadFlow.confirm)
 kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬆️ رفع الآن',callback_data='upload:confirm')],[InlineKeyboardButton(text='JOIN GROUP',url=GROUP_LINK),InlineKeyboardButton(text='MY BOT',url='https://t.me/ONX44')],[InlineKeyboardButton(text='إلغاء',callback_data='upload:cancel')]])
 await m.answer('تم تجهيز بيانات الكتاب. راجعها ثم اختر <b>رفع الآن</b> ليظهر في المكتبة.\n\n<b>العنوان:</b> '+html.escape(data.get('title',''))+'\n<b>الوصف:</b> '+html.escape(desc or '—'),parse_mode='HTML',reply_markup=kb)
@dp.callback_query(F.data=='upload:confirm')
async def confirm_upload(q:CallbackQuery,state:FSMContext):
 data=await state.get_data()
 if not data.get('file_id'):await q.answer('انتهت بيانات الملف، أرسل الملف مجدداً',show_alert=True);return
 import uuid
 bookid=uuid.uuid4().hex[:12];now=datetime.now(timezone.utc).isoformat()
 c=sqlite3.connect(DB);c.execute('INSERT INTO books(id,telegram_file_id,file_name,title,description,file_size,mime_type,created_at,uploader_id,pinned) VALUES(?,?,?,?,?,?,?,?,?,0)',(bookid,data['file_id'],data.get('file_name'),data.get('title'),data.get('description'),data.get('size'),data.get('mime'),now,data.get('owner')));c.commit();c.close();await state.clear()
 await q.message.edit_text('✨ تم رفع الكتاب إلى المكتبة بنجاح.\n\nيمكنك مشاهدة الكتاب من الموقع، ولا تُخزَّن نسخة إضافية على استضافة الموقع.');await q.answer('تم نشر الكتاب')
@dp.callback_query(F.data=='upload:cancel')
async def cancel_upload(q:CallbackQuery,state:FSMContext):await state.clear();await q.message.edit_text('تم إلغاء رفع الكتاب.');await q.answer()

# Admin: send ADMIN_CODE as a standalone message, then choose book and action.
@dp.message(AdminFlow.code)
async def admin_code(m:Message,state:FSMContext):
 if not is_admin(m.from_user.id) or not ADMIN_CODE or m.text!=ADMIN_CODE:await state.clear();await m.answer('رمز غير صحيح أو لا تملك صلاحية الإدارة.');return
 await state.clear();books=book_rows()
 if not books:await m.answer('لا توجد ملفات مسجلة.');return
 await state.set_state(AdminFlow.pick);await state.update_data(admin_ok=True)
 await m.answer('🛠 <b>لوحة إدارة الكتب</b>\n\n'+ '\n'.join(f'{i+1}. {html.escape(b.get("title") or b.get("file_name") or "كتاب")} — {b["id"]}' for i,b in enumerate(books)),parse_mode='HTML')
 await m.answer('أرسل رقم الكتاب الذي تريد تعديله.')
@dp.message(AdminFlow.pick)
async def admin_pick(m:Message,state:FSMContext):
 try:idx=int(m.text)-1;books=book_rows();b=books[idx]
 except:await m.answer('رقم غير صالح، أرسل رقماً من القائمة.');return
 await state.update_data(book_id=b['id']);await state.set_state(AdminFlow.action)
 kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='تعديل عنوان الملف',callback_data='admin:title')],[InlineKeyboardButton(text='تعديل وصف / ملاحظة',callback_data='admin:desc')],[InlineKeyboardButton(text='حذف الكتاب من الموقع',callback_data='admin:delete')],[InlineKeyboardButton(text='تثبيت / فك تثبيت',callback_data='admin:pin')]])
 await m.answer(f'اختر الإجراء للكتاب: <b>{html.escape(b.get("title") or "بدون عنوان")}</b>',parse_mode='HTML',reply_markup=kb)
@dp.callback_query(F.data.startswith('admin:'))
async def admin_action(q:CallbackQuery,state:FSMContext):
 data=await state.get_data();bid=data.get('book_id');b=get_book(bid)
 if not b:await q.answer('الكتاب غير موجود',show_alert=True);return
 action=q.data.split(':')[1]
 if action=='title':await state.set_state(AdminFlow.edit_title);await q.message.answer('أرسل العنوان الجديد:')
 elif action=='desc':await state.set_state(AdminFlow.edit_description);await q.message.answer('أرسل الوصف الجديد أو الرابط. اكتب «بدون» لمسح الوصف:')
 elif action=='delete':
  c=sqlite3.connect(DB);c.execute('DELETE FROM books WHERE id=?',(bid,));c.commit();c.close();await state.clear();await q.message.answer('حُذف الكتاب من الموقع.');
 elif action=='pin':
  c=sqlite3.connect(DB);c.execute('UPDATE books SET pinned=? WHERE id=?',(0 if b['pinned'] else 1,bid));c.commit();c.close();await state.clear();await q.message.answer('تم تحديث حالة تثبيت الكتاب.');
 await q.answer()
@dp.message(AdminFlow.edit_title)
async def edit_title(m:Message,state:FSMContext):
 d=await state.get_data();c=sqlite3.connect(DB);c.execute('UPDATE books SET title=? WHERE id=?',(m.text.strip()[:250],d['book_id']));c.commit();c.close();await state.clear();await m.answer('تم تحديث العنوان.')
@dp.message(AdminFlow.edit_description)
async def edit_description(m:Message,state:FSMContext):
 d=await state.get_data();desc='' if (m.text or '').strip().lower()=='بدون' else (m.text or '').strip();c=sqlite3.connect(DB);c.execute('UPDATE books SET description=? WHERE id=?',(desc,d['book_id']));c.commit();c.close();await state.clear();await m.answer('تم تحديث الوصف.')
@dp.message()
async def admin_trigger(m:Message,state:FSMContext):
 if is_admin(m.from_user.id) and ADMIN_CODE and m.text==ADMIN_CODE:await state.set_state(AdminFlow.code);await admin_code(m,state)
 elif m.text and m.text.startswith('/admin') and is_admin(m.from_user.id):await state.set_state(AdminFlow.code);await m.answer('أرسل رمز الإدارة.');
if __name__=='__main__':
 import uvicorn
 uvicorn.run('bot:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')),reload=False)
