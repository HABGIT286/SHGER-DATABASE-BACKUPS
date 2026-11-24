"""Business Administration student library: Telegram bot + FastAPI API."""
from __future__ import annotations
import os, sqlite3, hashlib, logging, mimetypes
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import date
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import httpx


logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'))
log=logging.getLogger('business-library')
BASE=Path(__file__).resolve().parent
DB=Path(os.getenv('DB_PATH', str(BASE/'data'/'library.sqlite3')))
TOKEN=os.getenv('TOKKEN','').strip()
ADMIN_ID=int(os.getenv('ADMIN_ID','0') or 0)
ADMIN_CODE=os.getenv('ADMIN_CODE','')
GROUP_LINK=os.getenv('GROUP_LINK','https://t.me/+mZe3EZzQBCYxOTUy')
DEV_LINK='https://t.me/ONX44'
BOT_APP: Application|None=None


def db():
    DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB,timeout=20); con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
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
    ''')
    # Upgrade databases created by earlier versions.
    cols={r['name'] for r in con.execute('PRAGMA table_info(files)').fetchall()}
    if 'category' not in cols: con.execute("ALTER TABLE files ADD COLUMN category TEXT NOT NULL DEFAULT 'عام'")
    con.commit(); return con


def is_admin(uid:int|None)->bool: return bool(uid and ADMIN_ID and uid==ADMIN_ID)
def short_name(s:str,maxlen=80)->str: return (s or 'file').replace('\x00','')[:maxlen]

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    context.user_data.clear(); user=update.effective_user
    text=(f'مرحبًا {user.mention_html()} ✦\n\nأهلًا بك في <b>مكتبة إدارة الأعمال</b>.\n'
          'مكتبة طلابية تجمع الكتب والمحاضرات والملفات العلمية وتسهّل الوصول إليها.\n\n'
          'لإضافة ملف: أرسل مستندًا إلى البوت، ثم أرفق وصفًا، وراجع بياناته قبل النشر.')
    kb=[[InlineKeyboardButton('مجموعة المكتبة',url=GROUP_LINK)],[InlineKeyboardButton('المطور',url=DEV_LINK)]]
    if is_admin(user.id): kb.insert(0,[InlineKeyboardButton('إرشادات الإدارة',callback_data='admin:help')])
    await update.message.reply_html(text,reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    txt='📚 <b>طريقة استخدام المكتبة</b>\n• أرسل ملفًا كمستند.\n• أرسل وصف الملف أو «بدون وصف».\n• راجع المعلومات واضغط نشر.\n• /cancel لإلغاء عملية رفع جارية.'
    if is_admin(update.effective_user.id): txt+='\n\n🔐 للوحة الإدارة أرسل رمز الإدارة الخاص بك في رسالة منفصلة.'
    await update.message.reply_html(txt)

async def cancel(update:Update,context:ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); await update.message.reply_text('تم إلغاء العملية الحالية.')

async def receive_document(update:Update,context:ContextTypes.DEFAULT_TYPE):
    m=update.message; doc=m.document
    if not doc: return
    if doc.file_size and doc.file_size>20*1024*1024:
        await m.reply_text('حجم الملف أكبر من الحد الذي يدعمه هذا المسار.'); return
    context.user_data['pending']={'file_id':doc.file_id,'file_name':short_name(doc.file_name or 'document'),
      'uploader_id':update.effective_user.id,'uploader_name':short_name(update.effective_user.full_name,100)}
    context.user_data['awaiting_description']=True
    await m.reply_text('✓ استلمت الملف.\n\nأرسل وصفًا مختصرًا له، أو أرسل «بدون وصف».')

async def admin_index(update:Update,context:ContextTypes.DEFAULT_TYPE):
    con=db(); rows=con.execute('SELECT id,file_name,pinned FROM files ORDER BY id DESC LIMIT 50').fetchall(); con.close()
    if not rows: await update.message.reply_text('لا توجد ملفات في المكتبة بعد.'); return
    context.user_data['admin_mode']='choose_file'
    lines=['✦ لوحة الإدارة — آخر 50 ملفًا','أرسل رقم الملف لفتح خياراته:']
    lines.extend(f"{r['id']:04d} {'📌' if r['pinned'] else '·'} {r['file_name']}" for r in rows)
    await update.message.reply_text('\n'.join(lines))

async def receive_text(update:Update,context:ContextTypes.DEFAULT_TYPE):
    msg=update.message; text=(msg.text or '').strip(); uid=update.effective_user.id
    if text.startswith('/'):
        return
    if is_admin(uid) and ADMIN_CODE and text==ADMIN_CODE:
        context.user_data.clear(); await admin_index(update,context); return
    if is_admin(uid) and context.user_data.get('admin_mode')=='choose_file' and text.isdigit():
        fid=int(text); con=db(); row=con.execute('SELECT id,file_name,description,pinned FROM files WHERE id=?',(fid,)).fetchone(); con.close()
        if not row: await msg.reply_text('رقم الملف غير موجود.'); return
        context.user_data['admin_file']=fid; context.user_data['admin_mode']='actions'
        kb=[[InlineKeyboardButton('تعديل الوصف',callback_data='admin:edit')],
             [InlineKeyboardButton('تثبيت / إلغاء التثبيت',callback_data='admin:pin')],
             [InlineKeyboardButton('حذف الملف',callback_data='admin:delete')],
             [InlineKeyboardButton('رجوع إلى الفهرس',callback_data='admin:list')]]
        await msg.reply_text(f"ملف #{fid:04d}\n{row['file_name']}\n\nالوصف: {row['description'] or '—'}\nالتثبيت: {'مثبت' if row['pinned'] else 'غير مثبت'}",reply_markup=InlineKeyboardMarkup(kb)); return
    if is_admin(uid) and context.user_data.get('admin_mode')=='edit_description':
        fid=context.user_data.get('admin_file'); con=db(); con.execute('UPDATE files SET description=? WHERE id=?',(text,int(fid))); con.execute('INSERT INTO activity(kind,detail) VALUES(?,?)',('edit',f'updated description for file {fid}')); con.commit(); con.close(); context.user_data.clear(); await msg.reply_text('تم حفظ الوصف الجديد.'); return
    if context.user_data.get('awaiting_description') and context.user_data.get('pending'):
        pending=context.user_data.pop('pending'); context.user_data.pop('awaiting_description',None)
        pending['description']='' if text.lower() in ('بدون وصف','-','none') else text
        context.user_data['ready']=pending
        kb=[[InlineKeyboardButton('نشر في المكتبة',callback_data='publish')],[InlineKeyboardButton('إلغاء',callback_data='cancel')]]
        await msg.reply_text(f"مراجعة قبل النشر\n\nالملف: {pending['file_name']}\nالوصف: {pending['description'] or 'بدون وصف'}\n\nهل تريد إضافته إلى المكتبة؟",reply_markup=InlineKeyboardMarkup(kb)); return
    await msg.reply_text('لرفع ملف، أرسله أولًا كمستند. استخدم /help لعرض الإرشادات.')

async def callbacks(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); data=q.data or ''; uid=q.from_user.id
    if data=='publish':
        pending=context.user_data.pop('ready',None)
        if not pending: await q.edit_message_text('انتهت جلسة الرفع. أرسل الملف مرة أخرى.'); return
        con=db(); cur=con.execute('INSERT INTO files(file_id,file_name,description,uploader_id,uploader_name) VALUES(?,?,?,?,?)',
            (pending['file_id'],pending['file_name'],pending['description'],pending['uploader_id'],pending['uploader_name']))
        fid=cur.lastrowid; con.execute('INSERT INTO activity(kind,detail) VALUES(?,?)',('publish',f'file {fid} published')); con.commit(); con.close()
        await q.edit_message_text(f'✓ تم النشر بنجاح\nرقم الملف: {fid:04d}\nسيظهر في المكتبة عند تحديث الصفحة.'); return
    if data=='cancel': context.user_data.clear(); await q.edit_message_text('تم إلغاء العملية.'); return
    if data=='admin:help': await q.edit_message_text('أرسل رمز الإدارة في رسالة منفصلة لفتح الفهرس.'); return
    if not is_admin(uid): await q.edit_message_text('غير مصرح لك بتنفيذ هذا الإجراء.'); return
    if data=='admin:list': await q.edit_message_text('أرسل رمز الإدارة مجددًا لفتح القائمة.'); context.user_data.clear(); return
    fid=context.user_data.get('admin_file')
    if not fid: await q.edit_message_text('انتهت جلسة الإدارة. أرسل رمز الإدارة مجددًا.'); return
    if data=='admin:edit': context.user_data['admin_mode']='edit_description'; await q.edit_message_text('أرسل الوصف الجديد الآن.'); return
    if data=='admin:pin':
        con=db(); row=con.execute('SELECT pinned FROM files WHERE id=?',(fid,)).fetchone()
        if row: con.execute('UPDATE files SET pinned=? WHERE id=?',(0 if row['pinned'] else 1,fid)); con.execute('INSERT INTO activity(kind,detail) VALUES(?,?)',('pin',f'pin state toggled for {fid}')); con.commit()
        con.close(); context.user_data.clear(); await q.edit_message_text('تم تحديث حالة تثبيت الملف.'); return
    if data=='admin:delete':
        kb=[[InlineKeyboardButton('تأكيد الحذف النهائي',callback_data='admin:confirm_delete')],[InlineKeyboardButton('إلغاء',callback_data='cancel')]]
        await q.edit_message_text('هل تريد حذف الملف نهائيًا؟',reply_markup=InlineKeyboardMarkup(kb)); return
    if data=='admin:confirm_delete':
        con=db(); con.execute('DELETE FROM likes WHERE file_id=?',(fid,)); con.execute('DELETE FROM downloads WHERE file_id=?',(fid,)); con.execute('DELETE FROM files WHERE id=?',(fid,)); con.execute('INSERT INTO activity(kind,detail) VALUES(?,?)',('delete',f'file {fid} deleted')); con.commit(); con.close(); context.user_data.clear(); await q.edit_message_text('تم حذف الملف من المكتبة.'); return

async def bot_startup():
    global BOT_APP
    if not TOKEN: log.warning('Telegram token missing: bot polling disabled'); return
    BOT_APP=Application.builder().token(TOKEN).build()
    BOT_APP.add_handler(CommandHandler('start',start)); BOT_APP.add_handler(CommandHandler('help',help_cmd)); BOT_APP.add_handler(CommandHandler('cancel',cancel))
    BOT_APP.add_handler(MessageHandler(filters.Document.ALL,receive_document)); BOT_APP.add_handler(CallbackQueryHandler(callbacks))
    BOT_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,receive_text))
    await BOT_APP.initialize(); await BOT_APP.start(); await BOT_APP.updater.start_polling(drop_pending_updates=False)
    log.info('Telegram polling started')
async def bot_shutdown():
    global BOT_APP
    if BOT_APP:
        try: await BOT_APP.updater.stop()
        except Exception: pass
        await BOT_APP.stop(); await BOT_APP.shutdown(); BOT_APP=None

@asynccontextmanager
async def lifespan(app:FastAPI):
    db(); await bot_startup()
    yield
    await bot_shutdown()
app=FastAPI(title='Business Administration Library API',version='2.0',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')

@app.get('/api/files')
def get_files(q:str|None=Query(default=None),limit:int=Query(default=500,ge=1,le=1000),offset:int=Query(default=0,ge=0)):
    con=db(); query='SELECT id,file_name,description,created_at,likes,downloads,pinned,category FROM files'; params=[]
    if q:
        query+=' WHERE file_name LIKE ? OR description LIKE ? OR category LIKE ?'; params.extend([f'%{q}%',f'%{q}%',f'%{q}%'])
    query+=' ORDER BY pinned DESC,id DESC LIMIT ? OFFSET ?'; params.extend([limit,offset])
    rows=con.execute(query,params).fetchall(); con.close(); return [dict(row) for row in rows]
@app.get('/api/stats')
def stats():
    con=db(); total=con.execute('SELECT COUNT(*) FROM files').fetchone()[0]; today=con.execute("SELECT COUNT(*) FROM files WHERE date(created_at)=date('now')").fetchone()[0]
    likes=con.execute('SELECT COALESCE(SUM(likes),0) FROM files').fetchone()[0]; downloads=con.execute('SELECT COALESCE(SUM(downloads),0) FROM files').fetchone()[0]
    visits=con.execute('SELECT COALESCE(SUM(count),0) FROM visits').fetchone()[0]; con.close()
    return {'files':total,'today':today,'downloads':downloads,'visits':visits,'likes':likes}
@app.post('/api/visit')
def visit():
    con=db(); con.execute("INSERT INTO visits(day,count) VALUES(date('now'),1) ON CONFLICT(day) DO UPDATE SET count=count+1"); con.commit(); con.close(); return {'ok':True}
@app.post('/api/files/{fid}/like')
def like(fid:int,request:Request):
    user_key=request.headers.get('x-user-key') or (request.client.host if request.client else 'unknown')
    con=db(); row=con.execute('SELECT id FROM files WHERE id=?',(fid,)).fetchone()
    if not row: con.close(); raise HTTPException(404,'File not found')
    digest=hashlib.sha256(user_key.encode()).hexdigest()
    try:
        con.execute('INSERT INTO likes(file_id,user_id) VALUES(?,?)',(fid,digest)); con.execute('UPDATE files SET likes=likes+1 WHERE id=?',(fid,)); con.commit(); result={'liked':True}
    except sqlite3.IntegrityError: result={'liked':False,'message':'already liked'}
    con.close(); return result
@app.get('/api/files/{fid}/download')
async def download(fid:int):
    con=db(); row=con.execute('SELECT file_id,file_name FROM files WHERE id=?',(fid,)).fetchone()
    if not row: con.close(); raise HTTPException(404,'File not found')
    con.execute("INSERT INTO downloads(day,file_id,count) VALUES(date('now'),?,1) ON CONFLICT(day,file_id) DO UPDATE SET count=count+1",(fid,)); con.execute('UPDATE files SET downloads=downloads+1 WHERE id=?',(fid,)); con.commit(); con.close()
    async with httpx.AsyncClient(timeout=90) as client:
        api=await client.get(f'https://api.telegram.org/bot{TOKEN}/getFile',params={'file_id':row['file_id']})
        if not api.is_success or not api.json().get('ok'): raise HTTPException(502,'Telegram file lookup failed')
        file_path=api.json()['result']['file_path']; remote=await client.get(f'https://api.telegram.org/file/bot{TOKEN}/{file_path}')
        if not remote.is_success: raise HTTPException(502,'File download failed')
        name=short_name(row['file_name'],160).replace('"','')
        return StreamingResponse(iter([remote.content]),media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{name}"'})
@app.get('/health')
def health(): return {'status':'ok','bot_configured':bool(TOKEN),'database':str(DB)}
@app.get('/')
def home(): return FileResponse(BASE/'index.html')
