"""
MathReady GA — Backend Server
FastAPI + JSON file persistence
"""

from dotenv import load_dotenv
import os as _os
load_dotenv(dotenv_path=_os.path.join(_os.path.dirname(__file__), ".env"))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import fastapi
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
import time, json, os, uuid, random, string, tempfile
import uvicorn
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

app = FastAPI(title="MathReady GA API")

# ── CORS — only allow our Vercel frontend ─────────────────
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://mathready-frontend.vercel.app,http://localhost:3000,http://localhost:8001"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


DATA_DIR = os.environ.get("DATA_DIR", ".")

def _path(name): return os.path.join(DATA_DIR, name)
def _load(filename, default):
    try:
        with open(_path(filename)) as f: return json.load(f)
    except Exception as e:
        print(f"Warning: failed to load {filename}: {e}")
        return default
def _save(filename, data):
    target = _path(filename)
    dir_name = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def _strip_answers(questions):
    """Return a copy of each question dict with the 'correct' key removed."""
    stripped = []
    for q in questions:
        q_copy = {k: v for k, v in q.items() if k not in ("correct", "answer")}
        stripped.append(q_copy)
    return stripped


def _grade_answer(q, given):
    """Server-side grading — mirrors frontend gradeAnswer() but has access to full answer key."""
    if given is None or given == "":
        return False
    qtype = q.get("type", "")
    correct_val = q.get("answer") or q.get("correct")
    if correct_val is None:
        return False
    if qtype == "plotpoint":
        ans = correct_val if isinstance(correct_val, list) else json.loads(correct_val)
        return given == json.dumps(ans)
    if qtype == "multiselect":
        correct_list = correct_val if isinstance(correct_val, list) else []
        try:
            given_list = json.loads(given) if isinstance(given, str) else given
            return sorted(given_list) == sorted(correct_list)
        except Exception:
            return False
    if qtype == "keypad":
        return str(correct_val).strip().lower() == str(given).strip().lower()
    if qtype == "dragdrop":
        try:
            g = json.loads(given) if isinstance(given, str) else given
            correct_map = q.get("correct") or q.get("answer") or {}
            if not isinstance(correct_map, dict):
                return False
            items = q.get("items") or []
            for item in items:
                c = correct_map.get(item)
                if c == "distractor":
                    if g.get(item) is not None:
                        return False
                else:
                    if g.get(item) != c:
                        return False
            return True
        except Exception:
            return False
    # Default: MCQ — exact string match
    return str(given).strip() == str(correct_val).strip()


def _server_score(test_code, answers):
    """Look up saved test by code, grade all answers server-side. Returns (score, total) or None."""
    if not test_code:
        return None
    code_upper = test_code.strip().upper()
    test = next((t for t in saved_tests if t.get("code", "").upper() == code_upper), None)
    if not test:
        return None
    questions = test.get("questions", [])
    total = len(questions)
    score = 0
    for q in questions:
        qid = q.get("id", "")
        given = answers.get(qid)
        if _grade_answer(q, given):
            score += 1
    return score, total

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
test_control  = {"paused": False, "stopped": False, "extensions": {}}
# extensions = { studentName: extraSecondsGranted }
question_bank = _load("questions.json",   [])

def _is_qid(qid):
    """True if qid matches the Q00001 format (Q + 5 digits)."""
    return bool(qid and qid.startswith("Q") and len(qid) == 6 and qid[1:].isdigit())

def _next_question_id():
    """Return next sequential question ID like Q00001, Q00042."""
    existing = set()
    for q in question_bank:
        qid = q.get("id","")
        if _is_qid(qid):
            existing.add(int(qid[1:]))
    n = 1
    while n in existing:
        n += 1
    return f"Q{n:05d}"
active_test   = _load("active_test.json", {"questions": [], "title": "Practice Test"})
saved_tests   = _load("saved_tests.json", [])
roster        = _load("roster.json",      [])   # list of {id, name, students:[{id,name}]}
teachers      = _load("teachers.json",   [])   # list of {id, name, email, role, classIds:[]}
fluency_data  = _load("fluency_data.json", {})  # {studentId: {add,sub,mul,div, sessions:[]}}
assignments   = _load("test_assignments.json", {})  # {aid: {testId,classId,studentIds,completedIds,...}}

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
    violations:  Optional[int] = 0
    violationLog: Optional[list] = []     # [{reason, time, questionNum}]
    mode:        Optional[str] = "test"   # "test" | "drill" | "practice"

class Heartbeat(BaseModel):
    name: str; current: int

class Question(BaseModel):
    id:            Optional[str] = None
    standard:      str
    short:         str
    dok:           Optional[int] = None
    question:      str
    questionImage: Optional[str] = None
    type:          Optional[str] = "mcq"
    choices:       Optional[List[str]] = []
    choiceImages:  Optional[List[Any]] = None
    correct:       Optional[Any] = ""                # str for mcq, object for dragdrop
    answer:        Optional[Any] = None
    zones:         Optional[List[str]] = None      # dragdrop: category names / blank labels
    items:         Optional[List[str]] = None      # dragdrop: draggable items / answer tiles
    ddLayout:      Optional[str] = "categories"    # "categories" | "blanks"
    subject:       Optional[str] = "math"          # "math" | "science"

class ActiveTest(BaseModel):
    questions: List[Any]
    title:     Optional[str] = "Practice Test"

class TestAssignmentBody(BaseModel):
    testId:        str
    classId:       str
    studentIds:    List[str]
    createdBy:     Optional[str] = ""
    createdByName: Optional[str] = ""

class SavedTest(BaseModel):
    name:           str
    code:           Optional[str] = None
    questions:      List[Any]
    title:          Optional[str] = ""
    adaptive:       Optional[bool] = False
    type:           Optional[str] = "test"       # "test" | "drill"
    drillStandards: Optional[List[str]] = []
    drillCount:     Optional[int] = 10
    untimed:        Optional[bool] = False
    timeLimitSecs:  Optional[int] = 1800         # default 30 min
    warnSecs:       Optional[int] = 300          # default warn at 5 min
    oneAttempt:     Optional[bool] = False        # limit to one submission per student
    classIds:       Optional[List[str]] = []       # classes assigned to this test
    subject:        Optional[str] = "math"         # "math" | "science"

class NewClass(BaseModel):
    name: str
    teacherId: Optional[str] = None
    gcCourseId: Optional[str] = None

class AddStudents(BaseModel):
    students: List[Any]  # list of name strings OR {name, email} dicts

class NewTeacher(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = "teacher"   # super_admin | school_admin | teacher | observer
    classIds: Optional[List[str]] = []

# ── Health ─────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "MathReady GA ✓", "questions": len(question_bank),
            "sessions": len(sessions), "saved_tests": len(saved_tests), "classes": len(roster)}

# ── Sessions (append model) ────────────────────────────────
@app.post("/submit")
def submit_session(session: Session):
    d = session.dict()
    # Server-side re-score: override client-computed score to prevent tampering
    # and fix the bug where _strip_answers removes 'correct' before client can grade
    result = _server_score(d.get("testCode"), d.get("answers", {}))
    if result:
        score, total = result
        d["score"] = score
        d["total"] = total
        d["pct"] = round(score / total * 100) if total else 0
    sessions.append(d)
    _save("sessions.json", sessions)
    # Auto-complete assignment if student was assigned this test
    sid = d.get("studentId", "")
    code = d.get("testCode", "").upper()
    if sid and code:
        for aid, a in assignments.items():
            if a.get("testCode", "").upper() == code and sid in a.get("studentIds", []):
                if sid not in a.get("completedIds", []):
                    a.setdefault("completedIds", []).append(sid)
                    _save("test_assignments.json", assignments)
                break
    return {"ok": True, "score": d["score"], "total": d["total"], "pct": d["pct"]}

@app.get("/test/attempt-check")
def check_attempt(code: str, studentId: str = "", studentName: str = ""):
    """Return whether a student has already submitted this test code."""
    code = code.strip().upper()
    if studentId:
        already = any(
            s.get("testCode","").upper() == code and s.get("studentId","") == studentId
            for s in sessions
        )
    elif studentName:
        name_lower = studentName.strip().lower()
        already = any(
            s.get("testCode","").upper() == code and s.get("studentName","").strip().lower() == name_lower
            for s in sessions
        )
    else:
        already = False
    return {"attempted": already}

@app.get("/sessions")
def get_sessions(classIds: Optional[str] = None):
    if classIds is None:
        return sessions  # Admin: no filter
    if classIds.strip() == "":
        return []  # Teacher with no classes: empty, not all
    ids = {i for i in classIds.split(",") if i.strip()}
    if not ids:
        return []
    return [s for s in sessions if s.get("classId") in ids]

@app.get("/student/history/{student_id}")
def get_student_history(student_id: str):
    """Return all sessions for a specific student (by studentId or name)."""
    history = [s for s in sessions
               if s.get("studentId") == student_id or s.get("studentName") == student_id]
    return history

@app.delete("/sessions/class/{cid}")
def clear_class_sessions(cid: str):
    """Clear test sessions (not drills) for a specific class."""
    cls = next((c for c in roster if c["id"] == cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    keep = [s for s in sessions if not (s.get("classId") == cid and s.get("mode","test") not in ("drill","practice"))]
    removed = len(sessions) - len(keep)
    sessions.clear(); sessions.extend(keep)
    _save("sessions.json", sessions)
    return {"ok": True, "removed": removed, "className": cls["name"]}

@app.delete("/sessions")
def clear_sessions(mode: Optional[str] = None):
    """Clear all sessions or only those matching mode: 'tests' or 'drills'."""
    if mode == "tests":
        keep = [s for s in sessions if s.get("mode","test") not in ("test","")]
        removed = len(sessions) - len(keep)
        sessions.clear(); sessions.extend(keep)
    elif mode == "drills":
        keep = [s for s in sessions if s.get("mode") not in ("drill","practice")]
        removed = len(sessions) - len(keep)
        sessions.clear(); sessions.extend(keep)
    else:
        removed = len(sessions)
        sessions.clear(); heartbeats.clear()
    _save("sessions.json", sessions)
    return {"ok": True, "removed": removed}

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

# ── Test Control ──────────────────────────────────────────
@app.get("/test/control")
def get_test_control():
    return test_control

@app.post("/test/control")
def post_test_control(body: dict):
    if "paused"  in body: test_control["paused"]  = bool(body["paused"])
    if "stopped" in body: test_control["stopped"] = bool(body["stopped"])
    if not body.get("stopped", test_control["stopped"]):
        pass  # keep extensions alive across pause/resume
    if body.get("stopped") == False and body.get("paused") == False:
        # Full reset — clear extensions too
        test_control["extensions"] = {}
    return test_control

@app.post("/test/control/extend")
def extend_student_time(body: dict):
    """Grant extra seconds to a specific student. studentName + extraSecs."""
    name  = body.get("studentName", "").strip()
    extra = int(body.get("extraSecs", 0))
    if not name or extra <= 0:
        raise HTTPException(400, "studentName and extraSecs required")
    current = test_control["extensions"].get(name, 0)
    test_control["extensions"][name] = current + extra
    return {"ok": True, "studentName": name, "totalExtraSecs": test_control["extensions"][name]}

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
    qid = data.get("id","")
    # Assign new ID if missing or old short-format (Q001 style)
    if not qid or (qid.startswith("Q") and len(qid) < 6 and qid[1:].isdigit()):
        data["id"] = _next_question_id()
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
        "questions":      _strip_answers(match.get("questions", [])),
        "title":          match.get("title", match.get("name", "")),
        "code":           code,
        "adaptive":       match.get("adaptive", False),
        "type":           match.get("type", "test"),
        "drillStandards": match.get("drillStandards", []),
        "drillCount":     match.get("drillCount", 10),
        "untimed":        match.get("untimed", False),
        "timeLimitSecs":  match.get("timeLimitSecs", 1800),
        "warnSecs":       match.get("warnSecs", 300),
        "oneAttempt":     match.get("oneAttempt", False),
        "classIds":       match.get("classIds", []),
        "roster":         [c for c in roster if c["id"] in match.get("classIds", [])],
    }

# ── Saved Tests ────────────────────────────────────────────
@app.get("/tests/saved")
def get_saved_tests():
    return [{"id": t["id"], "name": t["name"], "code": t.get("code",""),
             "title": t.get("title",""), "count": len(t.get("questions",[])),
             "saved_at": t.get("saved_at",""),
             "type": t.get("type","test"),
             "drill_count": t.get("drillCount", 10),
             "drill_standards": t.get("drillStandards",[]),
             "classIds": t.get("classIds",[]),
             "oneAttempt": t.get("oneAttempt", False),
             "untimed": t.get("untimed", False),
             "timeLimitSecs": t.get("timeLimitSecs", 1800),
             "adaptive": t.get("adaptive", False),
             "subject": t.get("subject", "math")} for t in saved_tests]

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
    t["name"]          = test.name
    t["code"]          = new_code
    t["title"]         = test.title or ""
    t["adaptive"]      = test.adaptive
    t["untimed"]       = test.untimed
    t["timeLimitSecs"] = test.timeLimitSecs
    t["warnSecs"]      = test.warnSecs
    t["oneAttempt"]    = test.oneAttempt
    t["classIds"]      = test.classIds or []
    t["subject"]       = test.subject or "math"
    if test.questions:
        t["questions"] = [q.dict() for q in test.questions]
        t["count"]     = len(test.questions)
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "code": new_code}

@app.patch("/tests/saved/{tid}/classes")
def set_test_classes(tid: str, body: dict):
    t = next((t for t in saved_tests if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Not found")
    t["classIds"] = body.get("classIds", [])
    _save("saved_tests.json", saved_tests)
    return {"ok": True}

@app.delete("/tests/saved/{tid}")
def delete_saved_test(tid: str):
    global saved_tests
    before = len(saved_tests)
    saved_tests = [t for t in saved_tests if t["id"] != tid]
    _save("saved_tests.json", saved_tests)
    return {"ok": True, "removed": before - len(saved_tests)}

# ── Roster ─────────────────────────────────────────────────
@app.get("/roster")
def get_roster(classIds: Optional[str] = None):
    if classIds is None:
        return roster  # Admin: no filter
    if classIds.strip() == "":
        return []  # Teacher with no classes: empty, not all
    ids = {i for i in classIds.split(",") if i.strip()}
    if not ids:
        return []
    return [c for c in roster if c["id"] in ids]

@app.get("/roster/class/{cid}")
def get_class(cid: str):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    return cls

@app.post("/roster/class")
def create_class(body: NewClass):
    name = body.name.strip()
    if any(c["name"].strip().lower() == name.lower() for c in roster):
        raise HTTPException(400, f"A class named \"{name}\" already exists. Use a unique name.")
    cls = {"id": "c" + uuid.uuid4().hex[:8], "name": name, "students": [], "gcCourseId": body.gcCourseId, "hideTimer": True}
    roster.append(cls)
    _save("roster.json", roster)
    # Link to teacher if provided
    if body.teacherId:
        t = next((t for t in teachers if t["id"] == body.teacherId), None)
        if t:
            if "classIds" not in t or t["classIds"] is None:
                t["classIds"] = []
            if cls["id"] not in t["classIds"]:
                t["classIds"].append(cls["id"])
            _save("teachers.json", teachers)
    return {"ok": True, "id": cls["id"]}

class UpdateClass(BaseModel):
    name: Optional[str] = None
    students: Optional[List[Any]] = None  # full student objects with accommodations
    gcCourseId: Optional[str] = None
    hideTimer: Optional[bool] = None

@app.put("/roster/class/{cid}")
def update_class(cid: str, body: UpdateClass):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    if body.name is not None:
        new_name = body.name.strip()
        if any(c["name"].strip().lower() == new_name.lower() and c["id"] != cid for c in roster):
            raise HTTPException(400, f"A class named \"{new_name}\" already exists.")
        cls["name"] = new_name
    if body.gcCourseId is not None:
        cls["gcCourseId"] = body.gcCourseId
    if body.hideTimer is not None:
        cls["hideTimer"] = body.hideTimer
    if body.students is not None:
        # Merge accommodations into existing student records
        existing = {s["id"]: s for s in cls["students"]}
        merged = []
        for s in body.students:
            sid = s.get("id")
            base = existing.get(sid, {})
            base.update({k: v for k, v in s.items() if k in ("extendedTime", "reduceChoices", "name")})
            merged.append(base)
        cls["students"] = merged
    _save("roster.json", roster)
    return {"ok": True}

@app.delete("/roster/class/{cid}")
def delete_class(cid: str):
    global roster
    roster = [c for c in roster if c["id"] != cid]
    _save("roster.json", roster)
    # Remove this class from all teachers' assignments
    changed = False
    for t in teachers:
        if cid in (t.get("classIds") or []):
            t["classIds"] = [c for c in t["classIds"] if c != cid]
            changed = True
    if changed:
        _save("teachers.json", teachers)
    return {"ok": True}

@app.post("/roster/class/{cid}/students")
def add_students(cid: str, body: AddStudents):
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    added = []
    existing_names = {s["name"].lower() for s in cls["students"]}
    for item in body.students:
        # item can be a plain name string OR a dict with name+email
        if isinstance(item, dict):
            name  = (item.get("name") or "").strip()
            email = (item.get("email") or "").strip().lower()
        else:
            name  = str(item).strip()
            email = ""
        if name and name.lower() not in existing_names:
            student = {"id": "s" + uuid.uuid4().hex[:8], "name": name}
            if email:
                student["email"] = email
            cls["students"].append(student)
            existing_names.add(name.lower())
            added.append(student)
    _save("roster.json", roster)
    return {"ok": True, "added": len(added), "students": added}


@app.delete("/roster/class/{cid}/student/{sid}")
def remove_student(cid: str, sid: str):
    if not sid or sid == "undefined" or sid == "null":
        raise HTTPException(400, "Invalid student ID")
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    before = len(cls["students"])
    cls["students"] = [s for s in cls["students"] if s["id"] != sid]
    if len(cls["students"]) == before:
        raise HTTPException(404, "Student not found")
    _save("roster.json", roster)
    return {"ok": True, "removed": before - len(cls["students"])}

# ── Bulk seed ─────────────────────────────────────────────
@app.post("/questions/seed")
def seed_questions(questions_in: List[Any] = fastapi.Body(...)):
    """Bulk-load questions — appends only questions with IDs not already in bank."""
    global question_bank
    existing_ids = {q.get("id") for q in question_bank}
    added = 0
    skipped_duplicate = []
    reassigned = []
    for q in questions_in:
        q = dict(q)
        qid = q.get("id","")
        # Normalize old short format (Q001 -> assign new) or missing/hex
        needs_new_id = (
            not qid or
            not qid.startswith("Q") or
            not qid[1:].isdigit() or
            (qid.startswith("Q") and len(qid) < 6)
        )
        if needs_new_id:
            q["id"] = _next_question_id()
            question_bank.append(q)
            existing_ids.add(q["id"])
            added += 1
        elif qid in existing_ids:
            skipped_duplicate.append(qid)
        else:
            question_bank.append(q)
            existing_ids.add(qid)
            added += 1
    _save("questions.json", question_bank)
    return {
        "ok": True,
        "added": added,
        "total": len(question_bank),
        "duplicates": skipped_duplicate,
        "duplicate_count": len(skipped_duplicate),
    }

# ── Admin analytics ───────────────────────────────────────
@app.get("/admin/overview")
def admin_overview():
    """School-wide summary for principal/IC view."""
    class_stats = []
    for cls in roster:
        cls_sessions = [s for s in sessions if s.get("classId") == cls["id"] or s.get("className") == cls["name"]]
        test_sessions   = [s for s in cls_sessions if s.get("mode","test") in ("test","")]
        drill_sessions  = [s for s in cls_sessions if s.get("mode") == "drill"]
        scores = [s["score"] for s in test_sessions if "score" in s]
        avg = round(sum(scores)/len(scores), 1) if scores else None
        # Standard breakdown
        std_map = {}
        for s in test_sessions:
            for r in s.get("results", []):
                std = r.get("standard","?")
                if std not in std_map: std_map[std] = {"correct":0,"total":0}
                std_map[std]["total"]   += 1
                std_map[std]["correct"] += 1 if r.get("correct") else 0
        standards = [{"standard":k,"pct":round(v["correct"]/v["total"]*100) if v["total"] else 0,"total":v["total"]}
                     for k,v in std_map.items()]
        standards.sort(key=lambda x: x["pct"])
        class_stats.append({
            "id":         cls["id"],
            "name":       cls["name"],
            "studentCount": len(cls["students"]),
            "sessionCount": len(test_sessions),
            "drillCount":   len(drill_sessions),
            "avgScore":     avg,
            "standards":    standards,
            "recentActivity": max((s.get("timestamp","") for s in cls_sessions), default=None),
        })
    # School-wide standard gaps
    all_std = {}
    for s in sessions:
        if s.get("mode","test") not in ("test",""): continue
        for r in s.get("results",[]):
            std = r.get("standard","?")
            if std not in all_std: all_std[std] = {"correct":0,"total":0}
            all_std[std]["total"]   += 1
            all_std[std]["correct"] += 1 if r.get("correct") else 0
    gaps = [{"standard":k,"pct":round(v["correct"]/v["total"]*100) if v["total"] else 0,"total":v["total"]}
            for k,v in all_std.items() if v["total"] >= 5]
    gaps.sort(key=lambda x: x["pct"])
    total_students = sum(len(c["students"]) for c in roster)
    tested_ids = {s.get("studentId") for s in sessions if s.get("studentId")}
    return {
        "classes":       class_stats,
        "schoolGaps":    gaps[:10],
        "totalStudents": total_students,
        "testedStudents": len(tested_ids),
        "totalSessions": len(sessions),
    }

# ── Teacher accounts ──────────────────────────────────────
@app.get("/teachers")
def get_teachers():
    valid_class_ids = {c["id"] for c in roster}
    return [{"id":t["id"],"name":t["name"],
             "classIds":[cid for cid in t.get("classIds",[]) if cid in valid_class_ids],
             "email": t.get("email",""),
             "role":  t.get("role","teacher"),
             } for t in teachers]

@app.post("/teachers")
def create_teacher(body: NewTeacher):
    t = {"id": "t" + uuid.uuid4().hex[:8], "name": body.name.strip(),
         "email": (body.email or "").lower().strip(),
         "role": body.role or "teacher",
         "classIds": body.classIds or []}
    teachers.append(t)
    _save("teachers.json", teachers)
    return {"ok": True, "id": t["id"]}

@app.put("/teachers/{tid}")
def update_teacher(tid: str, body: NewTeacher):
    t = next((t for t in teachers if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Teacher not found")
    t["name"]     = body.name.strip()
    t["classIds"] = body.classIds or []
    if body.email is not None:
        t["email"] = body.email.lower().strip()
    if body.role is not None:
        t["role"] = body.role
    _save("teachers.json", teachers)
    return {"ok": True}

@app.delete("/teachers/{tid}")
def delete_teacher(tid: str):
    global teachers
    teachers = [t for t in teachers if t["id"] != tid]
    _save("teachers.json", teachers)
    return {"ok": True}

@app.put("/teachers/{tid}/classes")
def set_teacher_classes(tid: str, body: AddStudents):
    # reuse AddStudents — body.students is a list of classIds here
    t = next((t for t in teachers if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Teacher not found")
    t["classIds"] = body.students
    _save("teachers.json", teachers)
    return {"ok": True}



class GoogleVerifyBody(BaseModel):
    token: str
    code: Optional[str] = None      # test code — verify against test's assigned classes
    classId: Optional[str] = None   # class ID  — verify against that class directly

@app.post("/auth/google/teacher")
def google_teacher_verify(body: GoogleVerifyBody):
    """Verify a Google ID token and match email to a teacher account."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google auth not configured on server.")
    try:
        info = id_token.verify_oauth2_token(
            body.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")
    email = (info.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(401, "No email in token.")
    t = next((t for t in teachers if (t.get("email") or "").lower().strip() == email), None)
    if not t:
        raise HTTPException(403, "Your Google account is not registered as a teacher. Contact your administrator.")
    # Filter out stale classIds that reference deleted classes
    valid_class_ids = {c["id"] for c in roster}
    raw_ids = t.get("classIds", [])
    clean_ids = [cid for cid in raw_ids if cid in valid_class_ids]
    if len(clean_ids) != len(raw_ids):
        t["classIds"] = clean_ids
        _save("teachers.json", teachers)
    return {
        "role":        "teacher",
        "teacherRole": t.get("role", "teacher"),
        "teacherId":   t["id"],
        "teacherName": t["name"],
        "classIds":    clean_ids,
    }


def _match_student(info: dict, classes_to_search: list):
    """Match a verified Google token to a roster student.
    Priority: googleSub → name.
    On first name-match, saves the sub so future logins are instant.
    Returns (student, cls) or (None, None).
    """
    sub     = info.get("sub", "")
    gc_name = (info.get("name") or "").strip().lower()

    # 1. Sub match — returning user, bulletproof
    for cls in classes_to_search:
        for s in cls["students"]:
            if sub and s.get("googleSub") == sub:
                return s, cls

    # 2. Name match — first login; write sub so next time is instant
    if gc_name:
        for cls in classes_to_search:
            for s in cls["students"]:
                if s["name"].strip().lower() == gc_name:
                    if sub and not s.get("googleSub"):
                        s["googleSub"] = sub
                        _save("roster.json", roster)
                    return s, cls

    return None, None


@app.post("/auth/google/verify")
def google_verify(body: GoogleVerifyBody):
    """Verify a Google ID token and match to the roster for tests/practice."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google auth not configured on server.")
    try:
        info = id_token.verify_oauth2_token(
            body.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    if body.classId:
        classes_to_search = [c for c in roster if c["id"] == body.classId]
    elif body.code:
        code = body.code.strip().upper()
        test = next((t for t in saved_tests if t.get("code") == code), None)
        if not test:
            raise HTTPException(404, "Test code not found.")
        ids = set(test.get("classIds") or [])
        classes_to_search = [c for c in roster if c["id"] in ids]
    else:
        raise HTTPException(400, "Provide code or classId.")

    student, cls = _match_student(info, classes_to_search)
    if student:
        return {"ok": True, "student": student, "cls": {"id": cls["id"], "name": cls["name"]}}
    raise HTTPException(403, "Your Google account is not on the class roster. Check with your teacher.")


@app.post("/auth/google/drill")
def google_drill_auth(body: GoogleVerifyBody):
    """Verify Google token for fluency drill."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google auth not configured on server.")
    try:
        info = id_token.verify_oauth2_token(
            body.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    student, cls = _match_student(info, roster)
    if student:
        return {"ok": True, "student": student, "cls": {"id": cls["id"], "name": cls["name"], "hideTimer": cls.get("hideTimer", True)}}

    # Allow teachers (especially super_admin) to drill without being on a roster
    email = (info.get("email") or "").lower().strip()
    t = next((t for t in teachers if (t.get("email") or "").lower().strip() == email), None)
    if t:
        fake_student = {"id": t["id"], "name": t["name"]}
        first_class = roster[0] if roster else {"id": "demo", "name": "Demo"}
        return {"ok": True, "student": fake_student, "cls": {"id": first_class["id"], "name": first_class.get("name", "Demo")}}

    raise HTTPException(403, "Your Google account is not on a class roster. Ask your teacher to add you.")



# ── Fluency Drills ─────────────────────────────────────────

class FluencySession(BaseModel):
    studentId:   str
    studentName: str
    classId:     Optional[str] = ""
    className:   Optional[str] = ""
    testCode:    Optional[str] = ""
    levels:      dict   # {add: int, sub: int, mul: int, div: int}
    log:         List[Any]  # [{op,level,display,answer,studentAnswer,correct}]
    submitted:   Optional[str] = ""
    stars:       Optional[int] = 0  # 1-5 stars from frontend accuracy thresholds

@app.get("/fluency/progress/{student_id}")
def get_fluency_progress(student_id: str):
    """Return stored fluency levels, personal bests, and session history for a student."""
    d = fluency_data.get(student_id, {})
    sess = d.get("sessions", [])
    pb = d.get("personalBests", {"bestAccuracy": 0, "bestPPM": 0})
    return {
        "add": max(1, min(10, d.get("add", 1))),
        "sub": max(1, min(10, d.get("sub", 1))),
        "mul": max(1, min(10, d.get("mul", 1))),
        "div": max(1, min(10, d.get("div", 1))),
        "personalBests":  pb,
        "streakDays":     d.get("streakDays", 0),
        "lastDrillDate":  d.get("lastDrillDate", ""),
        "sessions": [
            {
                "levels":    s.get("levels", {}),
                "pct":       s.get("pct", 0),
                "ppm":       s.get("ppm"),
                "stars":     s.get("stars"),
                "ops":       s.get("ops"),
                "submitted": s.get("submitted", ""),
            }
            for s in sess[-20:]
        ],
    }

@app.post("/fluency/session")
def save_fluency_session(session: FluencySession):
    """Save session results and update student fluency levels."""
    sid = session.studentId
    if not sid:
        raise HTTPException(400, "studentId required")
    if sid not in fluency_data:
        fluency_data[sid] = {"add": 1, "sub": 1, "mul": 1, "div": 1, "sessions": []}
    # Update levels — validate that the client-provided level only changed by ±1
    for op in ("add", "sub", "mul", "div"):
        if op in session.levels:
            stored = fluency_data[sid].get(op, 1)
            requested = max(1, min(10, int(session.levels[op])))
            if abs(requested - stored) <= 1:
                fluency_data[sid][op] = requested
            else:
                # Client sent a level more than ±1 away; clamp to ±1
                if requested > stored:
                    fluency_data[sid][op] = min(10, stored + 1)
                else:
                    fluency_data[sid][op] = max(1, stored - 1)
    # ── Per-operation breakdown from log ──────────────────────
    ops = {op: {"total": 0, "correct": 0} for op in ("add", "sub", "mul", "div")}
    for entry in session.log:
        op = entry.get("op", "")
        if op in ops:
            ops[op]["total"] += 1
            if entry.get("correct"):
                ops[op]["correct"] += 1
    for op, data in ops.items():
        data["pct"] = round(data["correct"] / data["total"] * 100) if data["total"] else None

    # ── Aggregate totals ───────────────────────────────────────
    total   = len(session.log)
    correct = sum(1 for e in session.log if e.get("correct"))
    if "sessions" not in fluency_data[sid]:
        fluency_data[sid]["sessions"] = []
    pct = round(correct / total * 100) if total else 0
    ppm = round(total / 3, 1)  # 3-minute drill
    stars = max(1, min(5, int(session.stars or 0))) if session.stars else (
        5 if pct >= 90 else 4 if pct >= 75 else 3 if pct >= 60 else 2 if pct >= 40 else 1
    )

    # ── Practice streak ────────────────────────────────────────
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    last_date = fluency_data[sid].get("lastDrillDate", "")
    yesterday_str = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    if last_date == today_str:
        streak = fluency_data[sid].get("streakDays", 1)          # already drilled today
    elif last_date == yesterday_str:
        streak = fluency_data[sid].get("streakDays", 0) + 1      # consecutive day
    else:
        streak = 1                                                 # gap or first drill
    fluency_data[sid]["streakDays"]   = streak
    fluency_data[sid]["lastDrillDate"] = today_str

    # ── Append session record ──────────────────────────────────
    fluency_data[sid]["sessions"].append({
        "submitted":   session.submitted or time.strftime("%b %d, %Y %I:%M %p"),
        "studentName": session.studentName,
        "classId":     session.classId,
        "className":   session.className,
        "testCode":    session.testCode,
        "levels":      session.levels,
        "total":       total,
        "correct":     correct,
        "pct":         pct,
        "ppm":         ppm,
        "stars":       stars,
        "ops":         ops,   # {add:{total,correct,pct}, sub:…, mul:…, div:…}
    })

    # ── Personal bests ─────────────────────────────────────────
    if "personalBests" not in fluency_data[sid]:
        fluency_data[sid]["personalBests"] = {"bestAccuracy": 0, "bestPPM": 0, "bestStars": 0}
    pb = fluency_data[sid]["personalBests"]
    new_best_accuracy = pct  > pb.get("bestAccuracy", 0)
    new_best_ppm      = ppm  > pb.get("bestPPM", 0)
    new_best_stars    = stars > pb.get("bestStars", 0)
    if new_best_accuracy: pb["bestAccuracy"] = pct
    if new_best_ppm:      pb["bestPPM"]      = ppm
    if new_best_stars:    pb["bestStars"]    = stars

    _save("fluency_data.json", fluency_data)
    return {
        "ok": True,
        "newBestAccuracy": new_best_accuracy,
        "newBestPPM":      new_best_ppm,
        "pct":             pct,
        "ppm":             ppm,
        "stars":           stars,
        "streak":          streak,
    }

@app.get("/fluency/class/{cid}/report")
def get_fluency_class_report(cid: str):
    """Return fluency progress for every student in a class."""
    cls = next((c for c in roster if c["id"] == cid), None)
    if not cls:
        raise HTTPException(404, "Class not found")
    result = []
    for student in cls["students"]:
        sid = student["id"]
        d   = fluency_data.get(sid, {})
        sess = d.get("sessions", [])
        pcts = [s.get("pct", 0) for s in sess if s.get("pct") is not None]
        avg_accuracy = round(sum(pcts) / len(pcts)) if pcts else 0
        # Trend: compare last 3 sessions avg vs prior 3
        trend = "stable"
        if len(pcts) >= 6:
            recent = sum(pcts[-3:]) / 3
            prior  = sum(pcts[-6:-3]) / 3
            if recent > prior + 5:
                trend = "improving"
            elif recent < prior - 5:
                trend = "declining"
        elif len(pcts) >= 3:
            recent = sum(pcts[-3:]) / 3
            prior  = sum(pcts[:-3]) / max(1, len(pcts) - 3) if len(pcts) > 3 else pcts[0]
            if recent > prior + 5:
                trend = "improving"
            elif recent < prior - 5:
                trend = "declining"
        pb = d.get("personalBests", {"bestAccuracy": 0, "bestPPM": 0, "bestStars": 0})

        # Per-operation averages across all sessions that have ops data
        op_totals = {op: {"total": 0, "correct": 0} for op in ("add", "sub", "mul", "div")}
        for s in sess:
            for op, data in (s.get("ops") or {}).items():
                if op in op_totals:
                    op_totals[op]["total"]   += data.get("total", 0)
                    op_totals[op]["correct"] += data.get("correct", 0)
        op_avgs = {
            op: round(v["correct"] / v["total"] * 100) if v["total"] else None
            for op, v in op_totals.items()
        }

        result.append({
            "student":      {"id": sid, "name": student["name"]},
            "levels":       {
                "add": d.get("add", 1), "sub": d.get("sub", 1),
                "mul": d.get("mul", 1), "div": d.get("div", 1),
            },
            "sessionCount": len(sess),
            "avgAccuracy":  avg_accuracy,
            "trend":        trend,
            "personalBests": pb,
            "opAvgs":       op_avgs,   # {add: 92, sub: 87, mul: 74, div: null}
            "streakDays":   d.get("streakDays", 0),
            "lastDrillDate": d.get("lastDrillDate", ""),
            "lastSession":  sess[-1] if sess else None,
        })
    return result


@app.get("/fluency/class/{cid}/leaderboard")
def get_fluency_leaderboard(cid: str):
    """Return Top 5 students by best accuracy for a class."""
    cls = next((c for c in roster if c["id"] == cid), None)
    if not cls:
        raise HTTPException(404, "Class not found")
    entries = []
    for student in cls["students"]:
        sid = student["id"]
        d = fluency_data.get(sid, {})
        pb = d.get("personalBests", {})
        best_acc = pb.get("bestAccuracy", 0)
        best_ppm = pb.get("bestPPM", 0)
        sess_count = len(d.get("sessions", []))
        if sess_count > 0:
            entries.append({
                "studentName": student["name"],
                "bestAccuracy": best_acc,
                "bestPPM": best_ppm,
                "sessionCount": sess_count,
            })
    entries.sort(key=lambda x: x["bestAccuracy"], reverse=True)
    return entries[:5]


# ── Fluency Data Reset ─────────────────────────────────────
@app.delete("/fluency/student/{student_id}")
def reset_fluency_student(student_id: str):
    """Reset a single student's fluency data (levels, sessions, streaks)."""
    if student_id in fluency_data:
        del fluency_data[student_id]
        _save("fluency_data.json", fluency_data)
        return {"ok": True, "message": f"Fluency data cleared for student {student_id}"}
    raise HTTPException(404, "No fluency data found for this student")

@app.delete("/fluency/class/{cid}")
def reset_fluency_class(cid: str):
    """Reset fluency data for all students in a class."""
    cls = next((c for c in roster if c["id"] == cid), None)
    if not cls:
        raise HTTPException(404, "Class not found")
    count = 0
    for student in cls["students"]:
        sid = student["id"]
        if sid in fluency_data:
            del fluency_data[sid]
            count += 1
    _save("fluency_data.json", fluency_data)
    return {"ok": True, "message": f"Fluency data cleared for {count} students in {cls['name']}"}

@app.delete("/fluency/all")
def reset_fluency_all():
    """Reset ALL fluency data school-wide."""
    count = len(fluency_data)
    fluency_data.clear()
    _save("fluency_data.json", fluency_data)
    return {"ok": True, "message": f"All fluency data cleared ({count} students)"}


# ── Parent Report ───────────────────────────────────────────

@app.get("/fluency/report/{student_id}")
def get_parent_report(student_id: str):
    """
    Parent-facing fluency report for a single student.
    Returns all data needed to render a printable progress report.
    """
    d = fluency_data.get(student_id)
    if not d:
        raise HTTPException(404, "No fluency data found for this student")

    sess = d.get("sessions", [])
    pb   = d.get("personalBests", {"bestAccuracy": 0, "bestPPM": 0, "bestStars": 0})

    # ── Accuracy trend (last 10 sessions for chart) ────────────
    recent_sessions = [
        {
            "submitted": s.get("submitted", ""),
            "pct":       s.get("pct", 0),
            "ppm":       s.get("ppm"),
            "stars":     s.get("stars"),
            "levels":    s.get("levels", {}),
            "ops":       s.get("ops"),
        }
        for s in sess[-10:]
    ]

    # ── Overall averages ───────────────────────────────────────
    pcts = [s.get("pct", 0) for s in sess if s.get("pct") is not None]
    avg_accuracy = round(sum(pcts) / len(pcts)) if pcts else 0

    ppms = [s["ppm"] for s in sess if s.get("ppm") is not None]
    avg_ppm = round(sum(ppms) / len(ppms), 1) if ppms else None

    total_stars = sum(s.get("stars", 0) for s in sess)

    # ── Trend ──────────────────────────────────────────────────
    trend = "stable"
    if len(pcts) >= 6:
        recent = sum(pcts[-3:]) / 3
        prior  = sum(pcts[-6:-3]) / 3
        if recent > prior + 5:   trend = "improving"
        elif recent < prior - 5: trend = "declining"
    elif len(pcts) >= 3:
        recent = sum(pcts[-3:]) / 3
        prior  = pcts[0]
        if recent > prior + 5:   trend = "improving"
        elif recent < prior - 5: trend = "declining"

    # ── Per-operation lifetime averages ────────────────────────
    op_totals = {op: {"total": 0, "correct": 0} for op in ("add", "sub", "mul", "div")}
    for s in sess:
        for op, data in (s.get("ops") or {}).items():
            if op in op_totals:
                op_totals[op]["total"]   += data.get("total", 0)
                op_totals[op]["correct"] += data.get("correct", 0)
    op_avgs = {
        op: round(v["correct"] / v["total"] * 100) if v["total"] else None
        for op, v in op_totals.items()
    }

    # ── Practice consistency ───────────────────────────────────
    import datetime as _dt
    # Sessions per week (last 4 weeks)
    sessions_per_week = None
    dated_sessions = [s for s in sess if s.get("submitted", "")]
    if dated_sessions:
        try:
            # Try to parse dates — handle multiple formats
            def _parse(raw):
                for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p", "%b %d, %Y"):
                    try:
                        return _dt.datetime.strptime(raw.strip(), fmt).date()
                    except Exception:
                        pass
                return None
            four_weeks_ago = _dt.date.today() - _dt.timedelta(weeks=4)
            recent_count = sum(
                1 for s in dated_sessions
                if _parse(s.get("submitted", "")) and _parse(s.get("submitted", "")) >= four_weeks_ago
            )
            sessions_per_week = round(recent_count / 4, 1)
        except Exception:
            pass

    # ── Grade-level context ────────────────────────────────────
    # Per-operation level descriptions aligned to GA K-5 Math Standards
    _LEVEL_DESC = {
        "add": [
            "Add within 5",                              # 1 - K
            "Add within 10",                             # 2 - K
            "Add within 20 (single digits)",             # 3 - Gr 1
            "2-digit + 1-digit, within 100",             # 4 - Gr 1
            "2-digit + 2-digit, within 100",             # 5 - Gr 2
            "3-digit + 2-digit, within 1,000",           # 6 - Gr 2
            "3-digit + 3-digit, within 1,000",           # 7 - Gr 3
            "4-digit + 3-digit, within 10,000",          # 8 - Gr 3
            "5-digit + 4-digit, within 100,000",         # 9 - Gr 4
            "Add through hundred-thousands",             # 10 - Gr 4
        ],
        "sub": [
            "Subtract within 5",                         # 1 - K
            "Subtract within 10",                        # 2 - K
            "Subtract within 20",                        # 3 - Gr 1
            "2-digit − 1-digit, within 100",             # 4 - Gr 1
            "2-digit − 2-digit, within 100",             # 5 - Gr 2
            "3-digit − 2-digit, within 1,000",           # 6 - Gr 2
            "3-digit − 3-digit, within 1,000",           # 7 - Gr 3
            "4-digit − 3-digit, within 10,000",          # 8 - Gr 3
            "5-digit − 4-digit, within 100,000",         # 9 - Gr 4
            "Subtract through hundred-thousands",        # 10 - Gr 4
        ],
        "mul": [
            "Equal groups / arrays to 5×5",              # 1 - Gr 2
            "× 0 and × 1",                              # 2 - Gr 3
            "× 2, × 5, × 10",                           # 3 - Gr 3
            "× 3 and × 4",                              # 4 - Gr 3
            "× 6 and × 7",                              # 5 - Gr 3
            "× 8 and × 9 (within 100)",                  # 6 - Gr 3
            "× multiples of 10",                         # 7 - Gr 3
            "2-digit × 1-digit",                         # 8 - Gr 4
            "2-digit × 2-digit",                         # 9 - Gr 4
            "3-digit × 2-digit",                         # 10 - Gr 5
        ],
        "div": [
            "÷ 1 and ÷ 2, within 100",                  # 1 - Gr 3
            "÷ 3 and ÷ 4, within 100",                  # 2 - Gr 3
            "÷ 5 and ÷ 6, within 100",                  # 3 - Gr 3
            "÷ 7, ÷ 8, ÷ 9, within 100",                # 4 - Gr 3
            "÷ multiples of 10",                         # 5 - Gr 3
            "2-digit ÷ 1-digit",                         # 6 - Gr 4
            "3-digit ÷ 1-digit",                         # 7 - Gr 4
            "4-digit ÷ 1-digit",                         # 8 - Gr 4
            "÷ 2-digit, 2–3-digit dividend",             # 9 - Gr 5
            "÷ 2-digit, up to 4-digit",                  # 10 - Gr 5
        ],
    }

    current_levels = {
        "add": d.get("add", 1), "sub": d.get("sub", 1),
        "mul": d.get("mul", 1), "div": d.get("div", 1),
    }
    grade_context = {}
    for op, lvl in current_levels.items():
        descs = _LEVEL_DESC.get(op, [])
        idx = max(0, min(lvl - 1, len(descs) - 1))
        grade_context[op] = descs[idx] if descs else f"Level {lvl}"

    # ── Find student name from sessions ───────────────────────
    student_name = ""
    class_name   = ""
    if sess:
        student_name = sess[-1].get("studentName", "")
        class_name   = sess[-1].get("className", "")

    # ── Days practiced this week ──────────────────────────────
    days_this_week = 0
    try:
        today = _dt.date.today()
        start_of_week = today - _dt.timedelta(days=today.weekday())  # Monday
        week_dates = set()
        for s in sess:
            d_parsed = _parse(s.get("submitted", ""))
            if d_parsed and d_parsed >= start_of_week:
                week_dates.add(d_parsed)
        days_this_week = len(week_dates)
    except Exception:
        pass

    # ── Action item: weakest operation that has data ────────
    action_item = None
    practiced_ops = {op: v for op, v in op_avgs.items() if v is not None}
    if practiced_ops:
        weakest_op = min(practiced_ops, key=lambda k: practiced_ops[k])
        if practiced_ops[weakest_op] < 85:
            op_name = {"add": "addition", "sub": "subtraction", "mul": "multiplication", "div": "division"}[weakest_op]
            action_item = f"Practice {op_name} facts at home — flashcards, games, or another MathReady drill session."
    # If no weak op but missing ops, suggest starting them
    if not action_item:
        missing = [op for op in ("mul", "div") if op_avgs.get(op) is None]
        if missing:
            op_name = {"mul": "multiplication", "div": "division"}[missing[0]]
            action_item = f"Ready to start {op_name} practice — encourage your child to keep drilling!"

    return {
        "studentId":       student_id,
        "studentName":     student_name,
        "className":       class_name,
        "generatedOn":     _dt.date.today().isoformat(),
        "totalSessions":   len(sess),
        "totalStars":      total_stars,
        "avgAccuracy":     avg_accuracy,
        "avgPPM":          avg_ppm,
        "trend":           trend,
        "streakDays":      d.get("streakDays", 0),
        "lastDrillDate":   d.get("lastDrillDate", ""),
        "sessionsPerWeek": sessions_per_week,
        "daysThisWeek":    days_this_week,
        "personalBests":   pb,
        "currentLevels":   current_levels,
        "gradeContext":    grade_context,
        "opAvgs":          op_avgs,
        "recentSessions":  recent_sessions,
        "actionItem":      action_item,
    }

@app.get("/fluency/report/class/{cid}")
def get_class_parent_reports(cid: str):
    """Batch parent reports for all students in a class with fluency data."""
    cls = next((c for c in roster if c["id"] == cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    reports = []
    for st in cls.get("students", []):
        try:
            report = get_parent_report(st["id"])
            reports.append(report)
        except Exception:
            pass
    return {"className": cls["name"], "reports": reports}


# ── Test Assignments ────────────────────────────────────────

@app.post("/assignments")
def create_assignment(body: TestAssignmentBody):
    """Assign a saved test to specific students in a class."""
    test = next((t for t in saved_tests if t["id"] == body.testId), None)
    if not test:
        raise HTTPException(404, "Saved test not found")
    cls = next((c for c in roster if c["id"] == body.classId), None)
    if not cls:
        raise HTTPException(404, "Class not found")
    aid = "a" + uuid.uuid4().hex[:8]
    assignments[aid] = {
        "testId":        body.testId,
        "testCode":      test.get("code", ""),
        "testTitle":     test.get("name", test.get("title", "Test")),
        "classId":       body.classId,
        "className":     cls["name"],
        "studentIds":    body.studentIds,
        "completedIds":  [],
        "createdBy":     body.createdBy,
        "createdByName": body.createdByName,
        "createdAt":     __import__("datetime").datetime.now().isoformat(),
    }
    _save("test_assignments.json", assignments)
    return {"ok": True, "id": aid, "assignment": assignments[aid]}

@app.get("/assignments")
def list_assignments(classIds: Optional[str] = None):
    """List assignments, optionally filtered by classIds."""
    if classIds:
        ids = {i.strip() for i in classIds.split(",") if i.strip()}
        filtered = {k: v for k, v in assignments.items() if v.get("classId") in ids}
    else:
        filtered = assignments
    result = []
    for aid, a in filtered.items():
        result.append({
            "id": aid,
            **a,
            "totalStudents":  len(a.get("studentIds", [])),
            "completedCount": len(a.get("completedIds", [])),
        })
    return result

@app.get("/assignments/student/{student_id}")
def get_student_assignment(student_id: str):
    """Get active (uncompleted) assignments for a student."""
    active = []
    for aid, a in assignments.items():
        if student_id in a.get("studentIds", []) and student_id not in a.get("completedIds", []):
            test = next((t for t in saved_tests if t["id"] == a["testId"]), None)
            if test:
                active.append({
                    "assignmentId": aid,
                    "testId":       a["testId"],
                    "testCode":     a.get("testCode", ""),
                    "testTitle":    a.get("testTitle", "Test"),
                    "className":    a.get("className", ""),
                    "classId":      a.get("classId", ""),
                    "questions":    test.get("questions", []),
                    "adaptive":     test.get("adaptive", False),
                    "untimed":      test.get("untimed", False),
                    "timeLimitSecs": test.get("timeLimitSecs", 1800),
                    "warnSecs":     test.get("warnSecs", 300),
                    "oneAttempt":   test.get("oneAttempt", False),
                })
    return {"assignments": active}

@app.patch("/assignments/{aid}/students")
def update_assignment_students(aid: str, body: dict):
    """Update which students are assigned (add/remove absent kids)."""
    if aid not in assignments:
        raise HTTPException(404, "Assignment not found")
    assignments[aid]["studentIds"] = body.get("studentIds", [])
    _save("test_assignments.json", assignments)
    return {"ok": True}

@app.patch("/assignments/{aid}/complete")
def complete_assignment(aid: str, body: dict):
    """Mark a student as completed."""
    if aid not in assignments:
        raise HTTPException(404, "Assignment not found")
    sid = body.get("studentId", "")
    if sid and sid not in assignments[aid].get("completedIds", []):
        assignments[aid].setdefault("completedIds", []).append(sid)
        _save("test_assignments.json", assignments)
    return {"ok": True}

@app.patch("/assignments/{aid}/reopen")
def reopen_assignment(aid: str, body: dict):
    """Re-enable a student for retake (remove from completedIds)."""
    if aid not in assignments:
        raise HTTPException(404, "Assignment not found")
    sid = body.get("studentId", "")
    if sid:
        assignments[aid]["completedIds"] = [s for s in assignments[aid].get("completedIds", []) if s != sid]
        _save("test_assignments.json", assignments)
    return {"ok": True}

@app.delete("/assignments/{aid}")
def delete_assignment(aid: str):
    """Remove a test assignment."""
    if aid not in assignments:
        raise HTTPException(404, "Assignment not found")
    del assignments[aid]
    _save("test_assignments.json", assignments)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True,
                reload_dirs=[os.path.dirname(os.path.abspath(__file__))])
