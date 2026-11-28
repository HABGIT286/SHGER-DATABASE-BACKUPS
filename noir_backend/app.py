"""NOIR Control backend. Secrets stay server-side. GitHub Actions is the bot runtime."""
from __future__ import annotations
import os, secrets, time, hmac
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(title="NOIR Control API", version="1.0.0")
@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse("index.html")

REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "ملف تشغيل v6.yml").strip()
WORKFLOW_REF = os.getenv("WORKFLOW_REF", "main").strip()
INDEX_FILE = os.getenv("LIBRARY_INDEX_FILE", "library_index.json").strip()
SESSION_COOKIE = "noir_admin_session"
SESSION_SECONDS = int(os.getenv("SESSION_SECONDS", "28800"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()]
_sessions: dict[str, float] = {}

if ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True,
                      allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

class LoginBody(BaseModel):
    password: str

class ControlBody(BaseModel):
    action: str


def config_check() -> list[str]:
    missing=[]
    if not REPOSITORY: missing.append("GITHUB_REPOSITORY")
    if not GH_TOKEN: missing.append("GH_TOKEN")
    if not ADMIN_PASSWORD: missing.append("WEB_ADMIN_PASSWORD")
    return missing

def gh_headers() -> dict[str,str]:
    if not GH_TOKEN: raise HTTPException(503, "GH_TOKEN is not configured on backend")
    return {"Authorization": f"Bearer {GH_TOKEN}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}

def gh_repo() -> str:
    if not REPOSITORY: raise HTTPException(503, "GITHUB_REPOSITORY is not configured on backend")
    return REPOSITORY

async def gh_get(path: str, params: dict[str,Any] | None=None) -> Any:
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.get("https://api.github.com/repos/"+gh_repo()+path, headers=gh_headers(), params=params)
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"GitHub API GET failed ({response.status_code})")
    return response.json()
async def gh_post(path: str, payload: dict[str,Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.post("https://api.github.com/repos/"+gh_repo()+path, headers=gh_headers(), json=payload)
    if response.status_code >= 400:
        detail="GitHub API POST failed: "+str(response.status_code)
        try: detail += " · " + response.json().get("message", "")
        except Exception: pass
        raise HTTPException(response.status_code, detail)
    return response
async def gh_patch(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.post("https://api.github.com/repos/"+gh_repo()+path, headers=gh_headers())
    if response.status_code >= 400:
        detail="GitHub API cancel failed: "+str(response.status_code)
        try: detail += " · " + response.json().get("message", "")
        except Exception: pass
        raise HTTPException(response.status_code, detail)
    return response

def check_origin(request: Request) -> None:
    origin=request.headers.get("origin")
    if not origin:
        return
    own_origin=f"{request.url.scheme}://{request.url.netloc}"
    if origin != own_origin and origin not in ALLOWED_ORIGINS:
        raise HTTPException(403, "Origin not allowed; set CORS_ORIGINS for the dashboard host")

def authorized(request: Request) -> None:
    token=request.cookies.get(SESSION_COOKIE, "")
    expiry=_sessions.get(token, 0)
    if not token or expiry < time.time():
        if token: _sessions.pop(token, None)
        raise HTTPException(401, "Admin login required")
    _sessions[token]=time.time()+SESSION_SECONDS


def iso_elapsed(start: str | None, end: str | None) -> str | None:
    try:
        a=datetime.fromisoformat(start.replace("Z", "+00:00")); b=datetime.fromisoformat((end or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
        seconds=max(0,int((b-a).total_seconds()))
        return f"{seconds//86400}d {(seconds%86400)//3600:02d}h {(seconds%3600)//60:02d}m"
    except Exception: return None

async def latest_run() -> dict | None:
    data=await gh_get("/actions/workflows/"+WORKFLOW_FILE+"/runs", {"branch":WORKFLOW_REF,"per_page":5})
    runs=data.get("workflow_runs", [])
    return runs[0] if runs else None
async def library_index() -> dict | None:
    try:
        return await gh_get("/contents/"+INDEX_FILE, {"ref":WORKFLOW_REF})
    except HTTPException:
        return None
async def decode_index(payload: dict | None) -> dict | None:
    if not payload: return None
    import base64, json
    try: return json.loads(base64.b64decode(payload.get("content", "")).decode("utf-8"))
    except Exception: return None

@app.get("/api/health")
async def health():
    missing=config_check()
    return {"ok":not missing, "service":"noir-control-api", "missing_backend_env":missing}

@app.post("/api/admin/login")
async def admin_login(body: LoginBody, request: Request, response: Response):
    check_origin(request)
    if not ADMIN_PASSWORD: raise HTTPException(503, "WEB_ADMIN_PASSWORD is missing on backend")
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Invalid admin password")
    token=secrets.token_urlsafe(40); _sessions[token]=time.time()+SESSION_SECONDS
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=COOKIE_SECURE, samesite="none" if COOKIE_SECURE else "lax", max_age=SESSION_SECONDS, path="/")
    return {"authenticated":True,"expires_in":SESSION_SECONDS}
@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response):
    check_origin(request)
    token=request.cookies.get(SESSION_COOKIE, ""); _sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"logged_out":True}
@app.get("/api/status")
async def status(request: Request):
    authorized(request); started=time.perf_counter(); run=await latest_run(); idx=await decode_index(await library_index()); latency=round((time.perf_counter()-started)*1000)
    run_status=(run or {}).get("status"); run_conclusion=(run or {}).get("conclusion"); active=run_status=="in_progress"
    stats=None; users=[]
    if idx is not None:
        source_list=idx.get("sources",[]) or []; user_map=idx.get("users",{}) or {}; events=idx.get("download_events",[]) or []
        stats={"users_count":len(user_map),"sources_count":len(source_list),"download_events_count":len(events),"banned_count":len(idx.get("banned",[]) or [])}
        for key,usr in user_map.items():
            users.append({"id":usr.get("id",key),"name":usr.get("name") or usr.get("username") or "User","username":usr.get("username"),"first_seen":usr.get("first_seen"),"last_seen":usr.get("last_seen"),"download_count":usr.get("download_count",0),"blocked":bool(usr.get("blocked",False))})
        users.sort(key=lambda u:str(u.get("last_seen") or ""),reverse=True)
    return {"ok":True,"api_latency_ms":latency,"workflow_running":active if run else None,"bot_running":active if run else None,"workflow_status":run_status or "not_found","workflow_conclusion":run_conclusion,"workflow_run":{"id":run.get("id"),"run_number":run.get("run_number"),"name":run.get("name"),"html_url":run.get("html_url"),"elapsed":iso_elapsed(run.get("run_started_at"),run.get("updated_at"))} if run else None,"library_stats":stats,"users":users if idx is not None else None,"events":[]}
@app.post("/api/control")
async def control(body: ControlBody, request: Request):
    authorized(request); check_origin(request); action=body.action.strip().lower()
    if action not in {"start","stop","restart"}: raise HTTPException(400,"Unsupported action")
    latest=await latest_run(); active=bool(latest and latest.get("status")=="in_progress")
    run_id=latest.get("id") if latest else None
    if action=="start" and active:
        return {"accepted":False,"action":"start","running":True,"status":"running","message":"The workflow is already running; use restart if you intend to restart it."}
    if action=="stop" and not active:
        return {"accepted":False,"action":"stop","running":False,"status":"not_running","message":"No active workflow run was found."}
    if action in {"stop","restart"} and active and run_id:
        await gh_patch(f"/actions/runs/{run_id}/cancel")
    if action in {"start","restart"}:
        await gh_post(f"/actions/workflows/{WORKFLOW_FILE}/dispatches", {"ref":WORKFLOW_REF})
        return {"accepted":True,"action":action,"running":None,"status":"dispatch_queued","message":"GitHub accepted workflow dispatch; run status will update after GitHub reports it."}
    return {"accepted":True,"action":"stop","running":False,"status":"cancel_requested","message":"Cancellation requested; refresh status to confirm."}
@app.get("/api/runs")
async def runs(request: Request):
    authorized(request); data=await gh_get("/actions/workflows/"+WORKFLOW_FILE+"/runs",{"branch":WORKFLOW_REF,"per_page":20})
    return {"runs":[{"id":r.get("id"),"name":r.get("name"),"status":r.get("status"),"conclusion":r.get("conclusion"),"created_at":r.get("created_at"),"updated_at":r.get("updated_at"),"html_url":r.get("html_url"),"run_number":r.get("run_number")} for r in data.get("workflow_runs",[])]}
@app.get("/api/users")
async def users(request: Request):
    authorized(request); index=await decode_index(await library_index())
    if index is None: raise HTTPException(502,"library_index.json could not be read from GitHub")
    result=[]
    for key,usr in (index.get("users",{}) or {}).items():
        result.append({"id":usr.get("id",key),"name":usr.get("name") or usr.get("username") or "User","username":usr.get("username"),"first_seen":usr.get("first_seen"),"last_seen":usr.get("last_seen"),"download_count":usr.get("download_count",0),"blocked":bool(usr.get("blocked",False))})
    return {"users":result}
