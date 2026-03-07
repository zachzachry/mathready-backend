"""
MathReady GA — Backend Server
FastAPI + JSON file persistence
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
import time, json, os, uuid, random, string
import uvicorn

app = FastAPI(title="MathReady GA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── File-based persistence ─────────────────────────────────
DATA_DIR = os.environ.get("DATA_DIR", ".")

def _path(name): return os.path.join(DATA_DIR, name)
def _load(filename, default):
    try:
        with open(_path(filename)) as f: return json.load(f)
    except: return default
def _save(filename, data):
    with open(_path(filename), "w") as f: json.dump(data, f, indent=2)

def gen_code():
    """Generate a random 6-char alphanumeric code."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ── State ──────────────────────────────────────────────────
sessions      = _load("sessions.json",    {})
heartbeats    = {}
question_bank = _load("questions.json",   [])
active_test   = _load("active_test.json", {"questions": [], "title": "Practice Test"})
saved_tests   = _load("saved_tests.json", [])

# ── Models ─────────────────────────────────────────────────
class Session(BaseModel):
    name:      str
    score:     int
    total:     int
    pct:       int
    submitted: str
    timeUsed:  str
    answers:   dict
    testCode:  Optional[str] = ""

class Heartbeat(BaseModel):
    name:    str
    current: int

class Question(BaseModel):
    id:            Optional[str] = None
    standard:      str
    short:         str
    dok:           Optional[int] = None
    question:      str
    questionImage: Optional[str] = None
    choices:       List[str]
    choiceImages:  Optional[List[Any]] = None
    correct:       str

class ActiveTest(BaseModel):
    questions: List[Any]
    title:     Optional[str] = "Practice Test"

class SavedTest(BaseModel):
    name:      str
    code:      Optional[str] = None
    questions: List[Any]
    title:     Optional[str] = ""

# ── Health ─────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "status": "MathReady GA server is running ✓",
        "questions": len(question_bank),
        "active": len(active_test.get("questions", [])),
        "saved_tests": len(saved_tests),
    }

# ── Sessions ───────────────────────────────────────────────
@app.post("/submit")
def submit_session(session: Session):
    sessions[session.name] = session.dict()
    _save("sessions.json", sessions)
    return {"ok": True}

@app.get("/sessions")
def get_sessions():
    return list(sessions.values())

@app.delete("/sessions")
def clear_sessions():
    sessions.clear(); heartbeats.clear()
    _save("sessions.json", sessions)
    return {"ok": True}

@app.post("/heartbeat")
def post_heartbeat(hb: Heartbeat):
    heartbeats[hb.name] = {"last_ping": time.time(), "current_question": hb.current}
    return {"ok": True}

@app.get("/active")
def get_active_students():
    now = time.time()
    return [
        {"name": n, "current_question": d["current_question"],
         "seconds_since_ping": round(now - d["last_ping"]),
         "status": "active" if now - d["last_ping"] < 35 else "slow"}
        for n, d in heartbeats.items() if now - d["last_ping"] < 60
    ]

# ── Question Bank ──────────────────────────────────────────
@app.get("/questions")
def get_questions(standard: Optional[str] = None, dok: Optional[int] = None):
    qs = question_bank
    if standard: qs = [q for q in qs if q.get("standard","").startswith(standard)]
    if dok:      qs = [q for q in qs if q.get("dok") == dok]
    return qs

@app.post("/questions")
def save_question(q: Question):
    data = q.dict()
    if not data.get("id"):
        data["id"] = "q" + uuid.uuid4().hex[:8]
    existing = next((i for i,x in enumerate(question_bank) if x.get("id")==data["id"]), None)
    if existing is not None:
        question_bank[existing] = data
    else:
        question_bank.append(data)
    _save("questions.json", question_bank)
    return {"ok": True, "id": data["id"]}

@app.delete("/questions/{qid}")
def delete_question(qid: str):
    global question_bank
    before = len(question_bank)
    question_bank = [q for q in question_bank if q.get("id") != qid]
    _save("questions.json", question_bank)
    active_test["questions"] = [q for q in active_test.get("questions",[]) if q.get("id") != qid]
    _save("active_test.json", active_test)
    return {"ok": True, "removed": before - len(question_bank)}

# ── Active Test (legacy fallback) ──────────────────────────
@app.get("/test/active")
def get_active_test():
    return active_test

@app.post("/test/activate")
def activate_test(test: ActiveTest):
    global active_test
    active_test = test.dict()
    _save("active_test.json", active_test)
    return {"ok": True, "count": len(active_test["questions"])}

# ── Lookup test by student code ────────────────────────────
@app.get("/test/code/{code}")
def get_test_by_code(code: str):
    code = code.strip().upper()
    # Search saved tests for matching code
    match = next((t for t in saved_tests if t.get("code","").upper() == code), None)
    if match:
        return {"ok": True, "found": True, "questions": match["questions"], "title": match.get("title", match.get("name",""))}
    # Fall back to active test if no code match
    return {"ok": True, "found": False, "questions": [], "title": ""}

# ── Saved Tests ────────────────────────────────────────────
@app.get("/tests/saved")
def get_saved_tests():
    return [
        {"id": t["id"], "name": t["name"], "code": t.get("code",""),
         "title": t.get("title",""), "count": len(t.get("questions",[])),
         "saved_at": t.get("saved_at","")}
        for t in saved_tests
    ]

@app.get("/tests/saved/{tid}")
def get_saved_test(tid: str):
    t = next((t for t in saved_tests if t["id"]==tid), None)
    if not t: raise HTTPException(status_code=404, detail="Test not found")
    return t

@app.post("/tests/saved")
def save_test(test: SavedTest):
    data = test.dict()
    data["id"]       = "t" + uuid.uuid4().hex[:8]
    data["saved_at"] = time.strftime("%b %d, %Y %I:%M %p")
    # Auto-generate code if not provided or empty
    if not data.get("code"):
        data["code"] = gen_code()
    else:
        data["code"] = data["code"].strip().upper()
    # Ensure code is unique
    existing_codes = {t.get("code","") for t in saved_tests}
    while data["code"] in existing_codes:
        data["code"] = gen_code()
    saved_tests.append(data)
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "id": data["id"], "code": data["code"]}

@app.put("/tests/saved/{tid}")
def update_saved_test(tid: str, test: SavedTest):
    t = next((t for t in saved_tests if t["id"]==tid), None)
    if not t: raise HTTPException(status_code=404, detail="Test not found")
    new_code = test.code.strip().upper() if test.code else t.get("code","")
    # Check uniqueness (allow keeping same code)
    existing_codes = {x.get("code","") for x in saved_tests if x["id"] != tid}
    if new_code in existing_codes:
        raise HTTPException(status_code=400, detail="Code already in use")
    t["name"]  = test.name
    t["code"]  = new_code
    t["title"] = test.title or ""
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "code": new_code}

@app.delete("/tests/saved/{tid}")
def delete_saved_test(tid: str):
    global saved_tests
    before = len(saved_tests)
    saved_tests = [t for t in saved_tests if t["id"] != tid]
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "removed": before - len(saved_tests)}

# ── Start ──────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
