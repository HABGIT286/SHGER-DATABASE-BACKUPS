"""Madarek Knowledge Library dashboard API.

Runs separately from the Telegram polling process. It reads and commits the shared
library_index.json file to GitHub. Configure GITHUB_TOKEN, GITHUB_REPOSITORY and (optionally) GITHUB_REF_NAME as environment secrets.
This API is passwordless; anyone with access to the deployed URL can manage the library.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx



ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "library_index.json"
GH_TOKEN = os.getenv("GITHUB_TOKEN", os.getenv("GH_TOKEN", "")).strip()
GH_REPO = os.getenv("GITHUB_REPOSITORY", "").strip()
GH_BRANCH = os.getenv("GITHUB_REF_NAME", "main").strip() or "main"
API_LOCK = __import__("asyncio").Lock()



app = FastAPI(title="Madarek Knowledge Library API", version="1.0.0")
app.mount("/assets", StaticFiles(directory=str(ROOT)), name="assets")


class NoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    text: str = Field(min_length=1, max_length=10000)

class SettingsInput(BaseModel):
    welcome_text: str | None = Field(default=None, max_length=10000)
    pause_text: str | None = Field(default=None, max_length=10000)
    library_name: str | None = Field(default=None, max_length=180)
class PauseInput(BaseModel):
    paused: bool
class SourceEditInput(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=10000)
    year: str | None = Field(default=None, max_length=10)
class UserBanInput(BaseModel):
    blocked: bool




def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_configuration() -> None:
    if not (GH_TOKEN and GH_REPO):
        raise HTTPException(status_code=503, detail="لم يتم إعداد GitHub token أو repository في إعدادات الخدمة")



def empty_index() -> dict[str, Any]:
    return {"version": 3, "next": {"file": 1, "photo": 1, "link": 1}, "sources": [], "users": {}, "banned": [], "paused": False, "settings": {}, "download_events": [], "ban_history": [], "notes": []}


def normalize_notes(db: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the bot's legacy dictionary notes or list notes to API view objects.
    Preserve note fields (photo file IDs, type, publication status, counters) so
    dashboard edits do not erase data used by the Telegram bot.
    """
    notes = db.get("notes", [])
    if isinstance(notes, dict):
        result = []
        for key, value in notes.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", str(key))
                item.setdefault("title", "ملاحظة الإدارة")
                item.setdefault("text", item.get("content", ""))
                item.setdefault("created_at", item.get("date", ""))
                item.setdefault("updated_at", item.get("date", item.get("created_at", "")))
                result.append(item)
            else:
                result.append({"id": str(key), "title": "ملاحظة", "text": str(value), "created_at": "", "updated_at": ""})
        return result
    if isinstance(notes, list):
        result = []
        for value in notes:
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", str(item.get("note_id") or item.get("created_at") or secrets.token_hex(4)))
                item.setdefault("title", "ملاحظة الإدارة")
                item.setdefault("text", item.get("content", ""))
                item.setdefault("created_at", item.get("date", ""))
                item.setdefault("updated_at", item.get("date", item.get("created_at", "")))
                result.append(item)
        return result
    return []



async def github_read() -> tuple[dict[str, Any], str | None]:
    ensure_configuration()
    api_url = f"https://api.github.com/repos/{GH_REPO}/contents/library_index.json"
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(api_url, headers=headers, params={"ref": GH_BRANCH})
        if response.status_code == 404:
            return empty_index(), None
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"تعذر قراءة ملف الفهرس من GitHub ({response.status_code})")
        payload = response.json()
        try:
            content = base64.b64decode(payload["content"]).decode("utf-8")
            db = json.loads(content)
        except Exception as error:
            raise HTTPException(status_code=502, detail="ملف library_index.json غير صالح") from error
        db.setdefault("sources", [])
        db.setdefault("users", {})
        db.setdefault("download_events", [])
        db.setdefault("settings", {})
        db.setdefault("banned", [])
        db.setdefault("notes", [])
        return db, payload.get("sha")



async def github_write(db: dict[str, Any], sha: str | None) -> None:
    ensure_configuration()
    api_url = f"https://api.github.com/repos/{GH_REPO}/contents/library_index.json"
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    content = base64.b64encode(json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {"message": "Update Madarek library index from dashboard", "content": content, "branch": GH_BRANCH}
    if sha:
        payload["sha"] = sha
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.put(api_url, headers=headers, json=payload)
        if response.status_code not in (200, 201):
            if response.status_code == 409:
                raise HTTPException(status_code=409, detail="تغير الفهرس بالتزامن. أعد التحديث وحاول مرة أخرى.")
            raise HTTPException(status_code=502, detail=f"فشل حفظ GitHub ({response.status_code})")



@app.get("/")
async def home() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/style.css")
async def css() -> FileResponse:
    return FileResponse(ROOT / "style.css", media_type="text/css")
@app.get("/app.js")
async def javascript() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "madarek-dashboard", "timestamp": now_iso()}


@app.get("/api/dashboard")
async def dashboard() -> dict[str, Any]:
    async with API_LOCK:
        db, _sha = await github_read()
    sources = db.get("sources", [])
    users = db.get("users", {})
    if not isinstance(users, dict):
        users = {}
    return {
        "connected": True,
        "paused": bool(db.get("paused", False)),
        "sources": sources,
        "users": list(users.values()),
        "download_events": db.get("download_events", []),
        "notes": normalize_notes(db),
        "settings": db.get("settings", {}),
        "banned": db.get("banned", []),
        "summary": {"sources": len(sources), "users": len(users), "requests": len(db.get("download_events", [])), "notes": len(normalize_notes(db))},
    }


@app.get("/api/users/{user_id}")
async def user_detail(user_id: str) -> dict[str, Any]:
    async with API_LOCK:
        db, _sha = await github_read()
    user = db.get("users", {}).get(str(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    published = [source for source in db.get("sources", []) if str(source.get("user_id")) == str(user_id)]
    downloads = [event for event in db.get("download_events", []) if str(event.get("user_id")) == str(user_id)]
    return {"user": user, "published": published, "downloads": downloads}


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str) -> dict[str, bool]:
    async with API_LOCK:
        db, sha = await github_read()
        before = len(db.get("sources", []))
        db["sources"] = [source for source in db.get("sources", []) if str(source.get("source_id")) != str(source_id)]
        if before == len(db["sources"]):
            raise HTTPException(status_code=404, detail="المصدر غير موجود")
        for user in db.get("users", {}).values():
            published_ids = user.get("published_source_ids", [])
            user["published_source_ids"] = [item for item in published_ids if str(item) != str(source_id)]
        await github_write(db, sha)
    return {"ok": True}


@app.patch("/api/sources/{source_id}")
async def edit_source(source_id: str, payload: SourceEditInput) -> dict[str, bool]:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="لا توجد بيانات للتعديل")
    async with API_LOCK:
        db, sha = await github_read()
        source = next((item for item in db.get("sources", []) if str(item.get("source_id")) == str(source_id)), None)
        if not source:
            raise HTTPException(status_code=404, detail="المصدر غير موجود")
        for key, value in updates.items():
            source[key] = value.strip()
        await github_write(db, sha)
    return {"ok": True}


@app.post("/api/users/{user_id}/ban")
async def set_user_ban(user_id: str, payload: UserBanInput) -> dict[str, bool]:
    async with API_LOCK:
        db, sha = await github_read()
        users = db.setdefault("users", {})
        user = users.get(str(user_id))
        if not user:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")
        banned = set(map(str, db.get("banned", [])))
        if payload.blocked:
            banned.add(str(user_id))
        else:
            banned.discard(str(user_id))
        db["banned"] = sorted(banned)
        user["blocked"] = bool(payload.blocked)
        await github_write(db, sha)
    return {"ok": True, "blocked": bool(payload.blocked)}


@app.post("/api/notes")
async def create_note(payload: NoteInput) -> dict[str, Any]:
    async with API_LOCK:
        db, sha = await github_read()
        notes = normalize_notes(db)
        note = {"id": secrets.token_hex(8), "title": payload.title.strip(), "text": payload.text.strip(), "created_at": now_iso(), "updated_at": now_iso()}
        notes.insert(0, note)
        # Keep the bot's original notes structure (dictionary keyed by note ID).
        db["notes"] = {str(item["id"]): item for item in notes}
        await github_write(db, sha)
    return {"note": note, "ok": True}


@app.patch("/api/notes/{note_id}")
async def edit_note(note_id: str, payload: NoteInput) -> dict[str, bool]:
    async with API_LOCK:
        db, sha = await github_read()
        notes = normalize_notes(db)
        note = next((item for item in notes if str(item.get("id")) == str(note_id)), None)
        if not note:
            raise HTTPException(status_code=404, detail="الملاحظة غير موجودة")
        note["title"] = payload.title.strip()
        note["text"] = payload.text.strip()
        note["updated_at"] = now_iso()
        # Keep note type, file_id, publication status, and counters intact.
        db["notes"] = {str(item["id"]): item for item in notes}
        await github_write(db, sha)
    return {"ok": True}


@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: str) -> dict[str, bool]:
    async with API_LOCK:
        db, sha = await github_read()
        notes = normalize_notes(db)
        kept = [note for note in notes if str(note.get("id")) != str(note_id)]
        if len(kept) == len(notes):
            raise HTTPException(status_code=404, detail="الملاحظة غير موجودة")
        db["notes"] = {str(item["id"]): item for item in kept}
        await github_write(db, sha)
    return {"ok": True}


@app.patch("/api/settings")
async def update_settings(payload: SettingsInput) -> dict[str, bool]:
    allowed = {"welcome_text", "pause_text", "library_name"}
    updates = payload.model_dump(exclude_none=True)
    if not updates or not set(updates).issubset(allowed):
        raise HTTPException(status_code=400, detail="لا توجد إعدادات صالحة للتحديث")
    async with API_LOCK:
        db, sha = await github_read()
        settings = db.setdefault("settings", {})
        for key, value in updates.items():
            settings[key] = value.strip()
        await github_write(db, sha)
    return {"ok": True}


@app.post("/api/bot/pause")
async def set_pause(payload: PauseInput) -> dict[str, Any]:
    async with API_LOCK:
        db, sha = await github_read()
        db["paused"] = bool(payload.paused)
        db.setdefault("settings", {})["web_pause_updated_at"] = now_iso()
        await github_write(db, sha)
    return {"ok": True, "paused": bool(payload.paused)}



if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("web_api:app", host="0.0.0.0", port=port, reload=False)
