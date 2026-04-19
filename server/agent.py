"""
Messaging agent — FastAPI, default port 8767.

Endpoints:
  GET  /contacts
  GET  /thread?contact_id=<str>&limit=<int>
  POST /suggest-messages   {contact_id, extra_context?}
  POST /send-message       {contact_id, message}

Start:
  uvicorn server.agent:app --port 8767 --reload
  (or --port 8766 if nothing else owns it)
"""

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Load .env from repo root if present (before any other imports that need the key)
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server import applescript as asc
from server import messages_db as db
from server import llm


# ── startup ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    asc.activate_messages()
    yield


app = FastAPI(title="Manifestation Messaging Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _find_contact(contact_id: str) -> db.Contact:
    contacts = db.get_contacts()
    for c in contacts:
        if c.id == contact_id:
            return c
    raise HTTPException(status_code=404, detail=f"Contact not found: {contact_id!r}")


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/contacts")
def get_contacts():
    try:
        contacts = db.get_contacts()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "contacts": [
            {
                "id": c.id,
                "chat_id": c.chat_id,
                "name": c.name,
                "handle": c.handle,
                "last_message": c.last_message,
                "last_ts": c.last_ts,
                "unread": c.unread,
                "is_group": c.is_group,
            }
            for c in contacts
        ]
    }


@app.get("/thread")
def get_thread(
    contact_id: str = Query(..., description="chat_identifier"),
    limit: int = Query(40, ge=1, le=200),
):
    contact = _find_contact(contact_id)
    try:
        messages = db.get_thread(contact.chat_id, limit=limit)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "contact": {
            "id": contact.id,
            "name": contact.name,
            "handle": contact.handle,
            "is_group": contact.is_group,
        },
        "messages": [
            {
                "id": m.id,
                "text": m.text,
                "from_me": m.from_me,
                "ts": m.ts,
            }
            for m in messages
        ],
    }


class SuggestRequest(BaseModel):
    contact_id: str
    extra_context: Optional[str] = ""


@app.post("/suggest-messages")
def suggest_messages(req: SuggestRequest):
    contact = _find_contact(req.contact_id)
    try:
        thread = db.get_thread(contact.chat_id, limit=40)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        result = llm.suggest_replies(
            contact_name=contact.name,
            thread=thread,
            extra_context=req.extra_context or "",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")
    return result


class SendRequest(BaseModel):
    contact_id: str
    message: str


@app.post("/send-message")
def send_message(req: SendRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    contact = _find_contact(req.contact_id)
    try:
        asc.send_imessage(contact.handle, req.message)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    db.invalidate_thread(contact.chat_id)
    return {"ok": True, "sent_at": time.time()}
