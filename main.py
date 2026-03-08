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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = os.environ.get("DATA_DIR", ".")

def _path(name): return os.path.join(DATA_DIR, name)
def _load(filename, default):
    try:
        with open(_path(filename)) as f: return json.load(f)
    except: return default
def _save(filename, data):
    with open(_path(filename), "w") as f: json.dump(data, f, indent=2)

def gen_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ── State ──────────────────────────────────────────────────
# Sessions: migrate from old dict format to list
_raw_sessions = _load("sessions.json", [])
if isinstance(_raw_sessions, dict):
    sessions = list(_raw_sessions.values())
    _save("sessions.json", sessions)
else:
    sessions = _raw_sessions

heartbeats    = {}
question_bank = _load("questions.json",   [])
active_test   = _load("active_test.json", {"questions": [], "title": "Practice Test"})
saved_tests   = _load("saved_tests.json", [])
roster        = _load("roster.json",      [])   # list of {id, name, students:[{id,name}]}

# ── Models ─────────────────────────────────────────────────
class Session(BaseModel):
    studentId:   Optional[str] = ""
    studentName: str
    classId:     Optional[str] = ""
    className:   Optional[str] = ""
    testCode:    Optional[str] = ""
    testTitle:   Optional[str] = ""
    score:       int
    total:       int
    pct:         int
    submitted:   str
    timeUsed:    str
    answers:     dict

class Heartbeat(BaseModel):
    name: str; current: int

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
    name:           str
    code:           Optional[str] = None
    questions:      List[Any]
    title:          Optional[str] = ""
    adaptive:       Optional[bool] = False
    type:           Optional[str] = "test"       # "test" | "drill"
    drillStandards: Optional[List[str]] = []
    drillCount:     Optional[int] = 10

class NewClass(BaseModel):
    name: str

class AddStudents(BaseModel):
    students: List[str]  # list of names (one or many)

# ── Health ─────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "MathReady GA ✓", "questions": len(question_bank),
            "sessions": len(sessions), "saved_tests": len(saved_tests), "classes": len(roster)}

# ── Sessions (append model) ────────────────────────────────
@app.post("/submit")
def submit_session(session: Session):
    sessions.append(session.dict())
    _save("sessions.json", sessions)
    return {"ok": True}

@app.get("/sessions")
def get_sessions():
    return sessions

@app.get("/student/history/{student_id}")
def get_student_history(student_id: str):
    """Return all sessions for a specific student (by studentId or name)."""
    history = [s for s in sessions
               if s.get("studentId") == student_id or s.get("studentName") == student_id]
    return history

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
    return [{"name": n, "current_question": d["current_question"],
             "seconds_since_ping": round(now - d["last_ping"]),
             "status": "active" if now - d["last_ping"] < 35 else "slow"}
            for n, d in heartbeats.items() if now - d["last_ping"] < 60]

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
    if not data.get("id"): data["id"] = "q" + uuid.uuid4().hex[:8]
    idx = next((i for i,x in enumerate(question_bank) if x.get("id")==data["id"]), None)
    if idx is not None: question_bank[idx] = data
    else: question_bank.append(data)
    _save("questions.json", question_bank)
    return {"ok": True, "id": data["id"]}

@app.delete("/questions/{qid}")
def delete_question(qid: str):
    global question_bank
    before = len(question_bank)
    question_bank = [q for q in question_bank if q.get("id") != qid]
    _save("questions.json", question_bank)
    return {"ok": True, "removed": before - len(question_bank)}

# ── Active Test ────────────────────────────────────────────
@app.get("/test/active")
def get_active_test(): return active_test

@app.post("/test/activate")
def activate_test(test: ActiveTest):
    global active_test
    active_test = test.dict()
    _save("active_test.json", active_test)
    return {"ok": True}

@app.get("/test/code/{code}")
def get_test_by_code(code: str):
    code = code.strip().upper()
    match = next((t for t in saved_tests if t.get("code","").upper() == code), None)
    if not match:
        return {"found": False}
    return {
        "found":          True,
        "questions":      match.get("questions", []),
        "title":          match.get("title", match.get("name", "")),
        "code":           code,
        "adaptive":       match.get("adaptive", False),
        "type":           match.get("type", "test"),
        "drillStandards": match.get("drillStandards", []),
        "drillCount":     match.get("drillCount", 10),
    }

# ── Saved Tests ────────────────────────────────────────────
@app.get("/tests/saved")
def get_saved_tests():
    return [{"id": t["id"], "name": t["name"], "code": t.get("code",""),
             "title": t.get("title",""), "count": len(t.get("questions",[])),
             "saved_at": t.get("saved_at",""),
             "type": t.get("type","test"),
             "drill_count": t.get("drillCount", 10),
             "drill_standards": t.get("drillStandards",[])} for t in saved_tests]

@app.get("/tests/saved/{tid}")
def get_saved_test(tid: str):
    t = next((t for t in saved_tests if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Not found")
    return t

@app.post("/tests/saved")
def save_test(test: SavedTest):
    data = test.dict()
    data["id"]       = "t" + uuid.uuid4().hex[:8]
    data["saved_at"] = time.strftime("%b %d, %Y %I:%M %p")
    if not data.get("code"): data["code"] = gen_code()
    else: data["code"] = data["code"].strip().upper()
    existing = {t.get("code","") for t in saved_tests}
    while data["code"] in existing: data["code"] = gen_code()
    saved_tests.append(data)
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "id": data["id"], "code": data["code"]}

@app.put("/tests/saved/{tid}")
def update_saved_test(tid: str, test: SavedTest):
    t = next((t for t in saved_tests if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Not found")
    new_code = test.code.strip().upper() if test.code else t.get("code","")
    if new_code in {x.get("code","") for x in saved_tests if x["id"] != tid}:
        raise HTTPException(400, "Code already in use")
    t["name"] = test.name; t["code"] = new_code; t["title"] = test.title or ""
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "code": new_code}

@app.delete("/tests/saved/{tid}")
def delete_saved_test(tid: str):
    global saved_tests
    before = len(saved_tests)
    saved_tests = [t for t in saved_tests if t["id"] != tid]
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "removed": before - len(saved_tests)}

# ── Roster ─────────────────────────────────────────────────
@app.get("/roster")
def get_roster(): return roster

@app.post("/roster/class")
def create_class(body: NewClass):
    cls = {"id": "c" + uuid.uuid4().hex[:8], "name": body.name.strip(), "students": []}
    roster.append(cls)
    _save("roster.json", roster)
    return {"ok": True, "id": cls["id"]}

@app.put("/roster/class/{cid}")
def rename_class(cid: str, body: NewClass):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    cls["name"] = body.name.strip()
    _save("roster.json", roster)
    return {"ok": True}

@app.delete("/roster/class/{cid}")
def delete_class(cid: str):
    global roster
    roster = [c for c in roster if c["id"] != cid]
    _save("roster.json", roster)
    return {"ok": True}

@app.post("/roster/class/{cid}/students")
def add_students(cid: str, body: AddStudents):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    added = []
    existing_names = {s["name"].lower() for s in cls["students"]}
    for name in body.students:
        name = name.strip()
        if name and name.lower() not in existing_names:
            student = {"id": "s" + uuid.uuid4().hex[:8], "name": name}
            cls["students"].append(student)
            existing_names.add(name.lower())
            added.append(student)
    _save("roster.json", roster)
    return {"ok": True, "added": len(added), "students": added}

@app.delete("/roster/class/{cid}/student/{sid}")
def remove_student(cid: str, sid: str):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    before = len(cls["students"])
    cls["students"] = [s for s in cls["students"] if s["id"] != sid]
    _save("roster.json", roster)
    return {"ok": True, "removed": before - len(cls["students"])}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
