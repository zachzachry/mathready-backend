"""
MathReady GA — Backend Server
FastAPI + in-memory storage (swap for a database later)

Run with:
    pip install fastapi uvicorn
    python server.py

Server starts at http://localhost:8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import time
import uvicorn

app = FastAPI(title="MathReady GA API")

# ── Allow your React frontend to talk to this server ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # In production, replace * with your actual domain
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory storage (resets when server restarts) ───────────────
sessions  = {}   # { student_name: session_data }
heartbeats = {}  # { student_name: last_ping_timestamp }


# ── Data models ───────────────────────────────────────────────────

class Session(BaseModel):
    name:      str
    score:     int
    total:     int
    pct:       int
    submitted: str
    timeUsed:  str
    answers:   dict


class Heartbeat(BaseModel):
    name:    str
    current: int   # current question number (0-indexed)


# ── Routes ────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "MathReady GA server is running ✓"}


# Student submits their finished test
@app.post("/submit")
def submit_session(session: Session):
    sessions[session.name] = session.dict()
    return {"ok": True, "message": f"Score saved for {session.name}"}


# Teacher dashboard fetches all scores
@app.get("/sessions")
def get_sessions():
    return list(sessions.values())


# Clear all sessions (teacher dashboard reset button)
@app.delete("/sessions")
def clear_sessions():
    sessions.clear()
    heartbeats.clear()
    return {"ok": True, "message": "All sessions cleared"}


# Student app pings this every 30 seconds while test is active
@app.post("/heartbeat")
def post_heartbeat(hb: Heartbeat):
    heartbeats[hb.name] = {
        "last_ping": time.time(),
        "current_question": hb.current,
    }
    return {"ok": True}


# Teacher can see who is actively testing right now
@app.get("/active")
def get_active():
    now = time.time()
    active = []
    for name, data in heartbeats.items():
        seconds_since_ping = now - data["last_ping"]
        if seconds_since_ping < 60:   # active if pinged within last 60 seconds
            active.append({
                "name": name,
                "current_question": data["current_question"],
                "seconds_since_ping": round(seconds_since_ping),
                "status": "active" if seconds_since_ping < 35 else "slow",
            })
    return active


# ── Start server ─────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
