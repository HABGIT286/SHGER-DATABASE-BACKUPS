"""NOIR secure API. Keep all secrets in hosting environment, never in the frontend."""
import os,time,secrets,hmac
from datetime import datetime,timezone
from fastapi import FastAPI,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
import httpx
app=FastAPI(title="NOIR Control API",version="2.0")
origins=[x.strip() for x in os.getenv("NOIR_ALLOWED_ORIGINS","").split(",") if x.strip()]
if not origins: raise RuntimeError("Configure NOIR_ALLOWED_ORIGINS before startup")
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=["GET","POST"],allow_headers=["Content-Type","X-NOIR-SESSION"])
ADMIN=os.getenv("WEB_ADMIN_PASSWORD","");TOKEN=os.getenv("TOKENN","");CHAT=os.getenv("DEV_TELE","");GH=os.getenv("GH_TOKEN","");REPO=os.getenv("GH_REPO","");BOT_WORKFLOW=os.getenv("BOT_WORKFLOW","ملف تشغيل v6.yml");DISPATCH=os.getenv("DISPATCH_WORKFLOW","noir-control.yml");TTL=int(os.getenv("NOIR_SESSION_TTL","28800"));sessions={};attempts={}
class Login(BaseModel): password:str=Field(min_length=1,max_length=512)
class Message(BaseModel): text:str=Field(default="NOIR Control test message",max_length=4000)
class Command(BaseModel): command:str

def auth(req:Request):
 token=req.headers.get("X-NOIR-SESSION","");expiry=sessions.get(token)
 if not expiry or expiry<time.time(): sessions.pop(token,None);raise HTTPException(401,"جلسة الإدارة انتهت")
 return token
def ghheaders():return {"Authorization":f"Bearer {GH}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
@app.get("/api/health")
def health():return {"ok":True,"service":"NOIR API","time":datetime.now(timezone.utc).isoformat()}
@app.post("/api/login")
def login(data:Login,req:Request):
 if not ADMIN:raise HTTPException(503,"WEB_ADMIN_PASSWORD غير مضبوط في خادم API")
 ip=req.client.host if req.client else "unknown";now=time.time();recent=[t for t in attempts.get(ip,[]) if now-t<300]
 if len(recent)>=8:raise HTTPException(429,"محاولات كثيرة؛ انتظر خمس دقائق")
 recent.append(now);attempts[ip]=recent
 if not hmac.compare_digest(data.password,ADMIN):raise HTTPException(401,"كلمة المرور غير صحيحة")
 attempts.pop(ip,None);key=secrets.token_urlsafe(48);sessions[key]=now+TTL
 return {"ok":True,"session":key,"expires_in":TTL}
@app.post("/api/logout")
def logout(req:Request):key=auth(req);sessions.pop(key,None);return {"ok":True}
@app.get("/api/status")
async def status(req:Request):
 auth(req);tg={"ok":False,"detail":"TOKENN غير مضبوط","bot_username":None};github={"ok":False,"detail":"GH_TOKEN أو GH_REPO غير مضبوط"};wf={"checked":False,"status":None,"conclusion":None,"run_number":None,"updated_at":None,"url":None}
 async with httpx.AsyncClient(timeout=15) as c:
  if TOKEN:
   try:
    r=await c.get(f"https://api.telegram.org/bot{TOKEN}/getMe");j=r.json();tg={"ok":r.status_code==200 and j.get("ok") is True,"detail":j.get("description","Telegram response"),"bot_username":j.get("result",{}).get("username")}
   except Exception as e:tg={"ok":False,"detail":str(e),"bot_username":None}
  if GH and REPO:
   try:
    r=await c.get(f"https://api.github.com/repos/{REPO}/actions/workflows/{BOT_WORKFLOW}/runs?per_page=1",headers=ghheaders())
    if r.status_code==200:
     github={"ok":True,"detail":"GitHub API authenticated"};runs=r.json().get("workflow_runs",[])
     if runs:
      x=runs[0];wf={"checked":True,"status":x.get("status"),"conclusion":x.get("conclusion"),"run_number":x.get("run_number"),"updated_at":x.get("updated_at"),"url":x.get("html_url")}
    else:github={"ok":False,"detail":f"GitHub HTTP {r.status_code}: {r.text[:240]}"}
   except Exception as e:github={"ok":False,"detail":str(e)}
 return {"telegram":tg,"github":github,"workflow":wf,"checked_at":datetime.now(timezone.utc).isoformat()}
@app.post("/api/test-telegram")
async def testtelegram(req:Request,data:Message):
 auth(req)
 if not TOKEN or not CHAT:raise HTTPException(503,"TOKENN أو DEV_TELE غير مضبوط")
 async with httpx.AsyncClient(timeout=15) as c:r=await c.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHAT,"text":data.text})
 j=r.json()
 if r.status_code!=200 or not j.get("ok"):raise HTTPException(502,j.get("description","Telegram send failed"))
 return {"ok":True,"detail":"تم إرسال الرسالة فعليًا عبر Telegram"}
@app.post("/api/workflow-dispatch")
async def dispatch(req:Request,data:Command):
 auth(req)
 if data.command not in {"start","stop","restart"}:raise HTTPException(400,"Command not allowed")
 if not GH or not REPO:raise HTTPException(503,"GH_TOKEN أو GH_REPO غير مضبوط")
 url=f"https://api.github.com/repos/{REPO}/actions/workflows/{DISPATCH}/dispatches"
 async with httpx.AsyncClient(timeout=20) as c:r=await c.post(url,headers=ghheaders(),json={"ref":"main","inputs":{"command":data.command}})
 if r.status_code!=204:raise HTTPException(r.status_code,f"GitHub dispatch failed: {r.text[:350]}")
 return {"ok":True,"detail":"GitHub Actions accepted dispatch"}
