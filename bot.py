import os, asyncio, sqlite3, json, time, logging, hashlib
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import httpx

logging.basicConfig(level=logging.INFO)
BASE=Path(__file__).parent
DB=Path(os.getenv('DB_PATH','/data/library.sqlite3'))
TOKEN=os.getenv('TOKKEN','')
ADMIN_ID=int(os.getenv('ADMIN_ID','0'))
ADMIN_CODE=os.getenv('ADMIN_CODE','')
CHANNEL_LINK=os.getenv('GROUP_LINK','https://t.me/+mZe3EZzQBCYxOTUy')
DEV_LINK='https://t.me/ONX44'
BOT_APP=None

if not TOKEN: logging.warning('TOKKEN is not set')

def db():
    DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL, file_name TEXT NOT NULL, description TEXT DEFAULT '', uploader_id INTEGER, uploader_name TEXT, created_at TEXT DEFAULT (datetime('now')), likes INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0)''')
    con.execute('''CREATE TABLE IF NOT EXISTS likes(file_id INTEGER, user_id INTEGER, PRIMARY KEY(file_id,user_id))''')
    con.execute('''CREATE TABLE IF NOT EXISTS visits(day TEXT PRIMARY PRIMARY KEY, count INTEGER DEFAULT 0)''')
    con.execute('''CREATE TABLE IF NOT EXISTS downloads(day TEXT, file_id INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(day,file_id))''')
    con.commit(); return con


def is_admin(uid): return uid==ADMIN_ID and ADMIN_ID!=0


async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    context.user_data.clear()
    msg=(f'مرحبًا {u.mention_html()} ✦\n\nأهلًا بك في بوابة مكتبة إدارة الأعمال.\nأرسل الملف الذي ترغب في إضافته إلى المكتبة، ثم أرفق وصفًا مختصرًا أو رابطًا.\n\nبوابة رفع الكتب والملفات العلمية.')
    kb=[[InlineKeyboardButton('JOIN-GROP',url=CHANNEL_LINK)],[InlineKeyboardButton('DEV',url=DEV_LINK)]]
    await update.message.reply_html(msg,reply_markup=InlineKeyboardMarkup(kb))

async def receive_doc(update:Update,context:ContextTypes.DEFAULT_TYPE):
    m=update.message; doc=m.document
    if not doc:
        await m.reply_text('أرسل الملف بصيغة مستند من فضلك.'); return
    context.user_data['pending']={'file_id':doc.file_id,'file_name':doc.file_name or 'document','uploader_id':update.effective_user.id,'uploader_name':update.effective_user.full_name}
    context.user_data['awaiting_description']=True
    await m.reply_text('✦ تم استلام الملف\n\nأرسل وصفًا مختصرًا للملف أو رابطًا متعلقًا به. يمكنك إرسال كلمة «بدون وصف» إذا رغبت.')

async def receive_text(update:Update,context:ContextTypes.DEFAULT_TYPE):
    text=(update.message.text or '').strip(); u=update.effective_user
    if is_admin(u.id) and ADMIN_CODE and text==ADMIN_CODE:
        con=db(); rows=con.execute('SELECT id,file_name FROM files ORDER BY id DESC LIMIT 60').fetchall(); con.close()
        if not rows: await update.message.reply_text('الفهرس فارغ حاليًا.'); return
        lines=['✦ لوحة المالك — فهرس الملفات','أرسل رقم الملف المطلوب:']+[f'{r["id"]:03d} — {r["file_name"]}' for r in rows]
        context.user_data['admin_mode']='choose_file'
        await update.message.reply_text('\n'.join(lines)); return
    if is_admin(u.id) and context.user_data.get('admin_mode')=='choose_file' and text.isdigit():
        con=db(); row=con.execute('SELECT id,file_name,description FROM files WHERE id=?',(int(text),)).fetchone(); con.close()
        if not row: await update.message.reply_text('رقم غير موجود، حاول مجددًا.'); return
        context.user_data['admin_file']=row['id']; context.user_data['admin_mode']='action'
        kb=[[InlineKeyboardButton('حذف',callback_data='admin:delete')],[InlineKeyboardButton('تعديل الملاحظة',callback_data='admin:edit')]]
        await update.message.reply_text(f'الملف {row["id"]:03d}\n{row["file_name"]}\n\nالملاحظة الحالية:\n{row["description"] or "—"}',reply_markup=InlineKeyboardMarkup(kb)); return
    if is_admin(u.id) and context.user_data.get('admin_mode')=='edit_description':
        fid=context.user_data.get('admin_file'); con=db(); con.execute('UPDATE files SET description=? WHERE id=?',(text,int(fid))); con.commit(); con.close(); context.user_data.clear(); await update.message.reply_text('تم تعديل الملاحظة بنجاح.'); return
    if context.user_data.get('awaiting_description') and context.user_data.get('pending'):
        p=context.user_data.pop('pending'); context.user_data.pop('awaiting_description',None); p['description']='' if text.lower() in ['بدون وصف','-','none'] else text
        context.user_data['ready']=p
        kb=[[InlineKeyboardButton('رفع الآن',callback_data='publish')],[InlineKeyboardButton('إلغاء العملية',callback_data='cancel')]]
        await update.message.reply_text(f'✦ مراجعة بيانات الملف\n\nالملف: {p["file_name"]}\nالوصف: {p["description"] or "بدون وصف"}\n\nاضغط «رفع الآن» لإضافته إلى المكتبة.',reply_markup=InlineKeyboardMarkup(kb)); return
    await update.message.reply_text('لرفع ملف، أرسله هنا أولًا. استخدم /start لعرض الترحيب.')

async def callbacks(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=='publish':
        p=context.user_data.pop('ready',None)
        if not p: await q.edit_message_text('انتهت جلسة الرفع. أرسل الملف من جديد.'); return
        con=db(); cur=con.execute('INSERT INTO files(file_id,file_name,description,uploader_id,uploader_name) VALUES(?,?,?,?,?)',(p['file_id'],p['file_name'],p['description'],p['uploader_id'],p['uploader_name'])); con.commit(); n=cur.lastrowid; con.close()
        await q.edit_message_text(f'✓ تم الرفع بنجاح\n\nرقم الملف في المكتبة: {n:03d}\nسيظهر ضمن الفهرس بعد تحديث الموقع.')
    elif q.data=='cancel': context.user_data.clear(); await q.edit_message_text('تم إلغاء العملية.')
    elif q.data=='admin:delete':
        fid=context.user_data.get('admin_file')
        if not is_admin(q.from_user.id) or not fid: await q.edit_message_text('غير مصرح.'); return
        kb=[[InlineKeyboardButton('تأكيد الحذف',callback_data='admin:confirm_delete')],[InlineKeyboardButton('إلغاء',callback_data='cancel')]]
        await q.edit_message_text('هل تريد حذف هذا الملف نهائيًا من المكتبة؟',reply_markup=InlineKeyboardMarkup(kb))
    elif q.data=='admin:confirm_delete':
        fid=context.user_data.get('admin_file')
        if not is_admin(q.from_user.id) or not fid: await q.edit_message_text('غير مصرح.'); return
        con=db(); con.execute('DELETE FROM likes WHERE file_id=?',(fid,)); con.execute('DELETE FROM downloads WHERE file_id=?',(fid,)); con.execute('DELETE FROM files WHERE id=?',(fid,)); con.commit(); con.close(); context.user_data.clear(); await q.edit_message_text('تم حذف الملف من المكتبة بنجاح.')
    elif q.data=='admin:edit':
        if not is_admin(q.from_user.id): await q.edit_message_text('غير مصرح.'); return
        context.user_data['admin_mode']='edit_description'; await q.edit_message_text('أرسل الملاحظة أو الرابط الجديد الآن.')

async def on_startup():
    global BOT_APP
    if TOKEN:
        BOT_APP=Application.builder().token(TOKEN).build()
        BOT_APP.add_handler(CommandHandler('start',start))
        BOT_APP.add_handler(MessageHandler(filters.Document.ALL,receive_doc))
        BOT_APP.add_handler(CallbackQueryHandler(callbacks))
        BOT_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,receive_text))
        await BOT_APP.initialize(); await BOT_APP.start(); await BOT_APP.updater.start_polling(drop_pending_updates=False)

async def on_shutdown():
    if BOT_APP:
        await BOT_APP.updater.stop(); await BOT_APP.stop(); await BOT_APP.shutdown()

@asynccontextmanager
async def lifespan(app:FastAPI):
    db(); await on_startup()
    yield
    await on_shutdown()
app=FastAPI(lifespan=lifespan)

@app.get('/api/files')
def files():
    con=db(); rows=con.execute('SELECT id,file_name,description,created_at,likes,downloads,pinned FROM files ORDER BY pinned DESC,id DESC').fetchall(); con.close(); return [dict(r) for r in rows]
@app.get('/api/stats')
def stats():
    con=db(); total=con.execute('SELECT COUNT(*) n FROM files').fetchone()['n']; today=con.execute("SELECT COUNT(*) n FROM files WHERE date(created_at)=date('now')").fetchone()['n']; downloads=con.execute('SELECT COALESCE(SUM(count),0) n FROM downloads').fetchone()['n']; visits=con.execute('SELECT COALESCE(SUM(count),0) n FROM visits').fetchone()['n']; con.close(); return {'files':total,'today':today,'downloads':downloads,'visits':visits}
@app.post('/api/visit')
def visit():
    con=db(); con.execute("INSERT INTO visits(day,count) VALUES(date('now'),1) ON CONFLICT(day) DO UPDATE SET count=count+1"); con.commit(); con.close(); return {'ok':True}
@app.post('/api/files/{fid}/like')
def like(fid:int,request:Request):
    uid=request.headers.get('x-user-key') or request.client.host
    con=db(); exists=con.execute('SELECT id FROM files WHERE id=?',(fid,)).fetchone()
    if not exists: con.close(); raise HTTPException(404,'not found')
    try: con.execute('INSERT INTO likes(file_id,user_id) VALUES(?,?)',(fid,hashlib.sha256(uid.encode()).hexdigest())); con.execute('UPDATE files SET likes=likes+1 WHERE id=?',(fid,)); con.commit(); liked=True
    except sqlite3.IntegrityError: liked=False
    con.close(); return {'liked':liked}
@app.get('/api/files/{fid}/download')
async def download(fid:int):
    con=db(); row=con.execute('SELECT file_id,file_name FROM files WHERE id=?',(fid,)).fetchone()
    if not row: con.close(); raise HTTPException(404,'not found')
    con.execute("INSERT INTO downloads(day,file_id,count) VALUES(date('now'),?,1) ON CONFLICT(day,file_id) DO UPDATE SET count=count+1",(fid,)); con.execute('UPDATE files SET downloads=downloads+1 WHERE id=?',(fid,)); con.commit(); con.close()
    async with httpx.AsyncClient(timeout=60) as client:
        r=await client.get(f'https://api.telegram.org/bot{TOKEN}/getFile',params={'file_id':row['file_id']})
        if r.status_code!=200 or not r.json().get('ok'): raise HTTPException(502,'telegram file retrieval failed')
        path=r.json()['result']['file_path']; stream=await client.get(f'https://api.telegram.org/file/bot{TOKEN}/{path}')
        if stream.status_code!=200: raise HTTPException(502,'file download failed')
        return StreamingResponse(iter([stream.content]),media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{row["file_name"]}"'})
@app.get('/')
def home(): return FileResponse(BASE/'index.html')
@app.get('/health')
def health(): return {'ok':True}
