"""
MathReady GA — Backend Server
FastAPI + JSON file persistence
"""

from fastapi import FastAPI, HTTPException
import fastapi
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
import random
import os
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
teachers      = _load("teachers.json",   [])   # list of {id, name, pin, classIds:[]}

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
    correct:       Optional[str] = ""
    answer:        Optional[Any] = None

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
    untimed:        Optional[bool] = False
    timeLimitSecs:  Optional[int] = 1800         # default 30 min
    warnSecs:       Optional[int] = 300          # default warn at 5 min
    oneAttempt:     Optional[bool] = False        # limit to one submission per student
    classIds:       Optional[List[str]] = []       # classes assigned to this test

class NewClass(BaseModel):
    name: str

class AddStudents(BaseModel):
    students: List[str]  # list of names (one or many)

class NewTeacher(BaseModel):
    name: str
    pin:  str
    classIds: Optional[List[str]] = []

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

@app.get("/test/attempt-check")
def check_attempt(code: str, studentId: str = "", studentName: str = ""):
    """Return whether a student has already submitted this test code."""
    code = code.strip().upper()
    already = any(
        s.get("testCode","").upper() == code and (
            (studentId and s.get("studentId","") == studentId) or
            (not studentId and studentName and
             s.get("studentName","").strip().lower() == studentName.strip().lower())
        )
        for s in sessions
    )
    return {"attempted": already}

@app.get("/sessions")
def get_sessions(classIds: Optional[str] = None):
    if classIds is None or classIds.strip() == "":
        return sessions
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
        "questions":      match.get("questions", []),
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
             "timeLimitSecs": t.get("timeLimitSecs", 1800)} for t in saved_tests]

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
    if classIds is None or classIds.strip() == "":
        return roster
    ids = {i for i in classIds.split(",") if i.strip()}
    if not ids:
        return []
    return [c for c in roster if c["id"] in ids]

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
            pin = str(random.randint(10000, 99999))
            student = {"id": "s" + uuid.uuid4().hex[:8], "name": name, "pin": pin}
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
    # Return teachers without exposing PINs
    return [{"id":t["id"],"name":t["name"],"classIds":t.get("classIds",[]),
             "pinSet": bool(t.get("pin"))} for t in teachers]

@app.post("/teachers")
def create_teacher(body: NewTeacher):
    code = body.pin.strip().upper()
    if len(code) < 4 or len(code) > 8 or not all(c in "0123456789ABCDEF" for c in code):
        raise HTTPException(400, "Login code must be 4–8 hex characters (0-9, A-F)")
    body.pin = code
    # Check PIN uniqueness across everything
    used = _all_used_pins()
    if body.pin in used:
        raise HTTPException(400, "PIN already in use")
    t = {"id": "t" + uuid.uuid4().hex[:8], "name": body.name.strip(),
         "pin": body.pin, "classIds": body.classIds or []}
    teachers.append(t)
    _save("teachers.json", teachers)
    return {"ok": True, "id": t["id"]}

@app.put("/teachers/{tid}")
def update_teacher(tid: str, body: NewTeacher):
    t = next((t for t in teachers if t["id"]==tid), None)
    if not t: raise HTTPException(404, "Teacher not found")
    # Only update PIN if a new one was explicitly provided
    new_pin = body.pin.strip() if body.pin else ""
    if new_pin:
        new_pin = new_pin.upper()
        if len(new_pin) < 4 or len(new_pin) > 8 or not all(c in "0123456789ABCDEF" for c in new_pin):
            raise HTTPException(400, "Login code must be 4–8 hex characters (0-9, A-F)")
        body.pin = new_pin
        used = _all_used_pins(exclude_teacher=tid)
        if new_pin in used:
            raise HTTPException(400, "PIN already in use")
        t["pin"] = new_pin.upper()
    t["name"]     = body.name.strip()
    t["classIds"] = body.classIds or []
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

def _all_used_pins(exclude_teacher=None):
    used = set()
    used.add(os.environ.get("TEACHER_PIN", TEACHER_PIN))
    used.add(os.environ.get("ADMIN_PIN",   ADMIN_PIN))
    for t in teachers:
        if t["id"] != exclude_teacher:
            used.add(t.get("pin",""))
    for c in roster:
        for s in c["students"]:
            if s.get("pin"): used.add(s["pin"])
    return used

# ── PIN Migration ─────────────────────────────────────────
@app.post("/roster/pins/generate-missing")
def generate_missing_pins():
    """Assign PINs to any students who don't have one yet."""
    used = {s.get("pin") for c in roster for s in c["students"] if s.get("pin")}
    count = 0
    for cls in roster:
        for s in cls["students"]:
            if not s.get("pin"):
                for _ in range(200):
                    pin = str(random.randint(10000, 99999))
                    if pin not in used:
                        s["pin"] = pin
                        used.add(pin)
                        count += 1
                        break
    _save("roster.json", roster)
    return {"ok": True, "generated": count}

# ── Auth (hex login codes) ────────────────────────────────
TEACHER_PIN = "00000"   # legacy fallback — set TEACHER_PIN env var in Railway
ADMIN_PIN   = "99999"   # admin — set ADMIN_PIN env var in Railway

@app.get("/auth/teacher-pin-check")
def teacher_pin_check():
    """Debug: confirm what teacher PIN the server is using (masked)."""
    tp = os.environ.get("TEACHER_PIN", TEACHER_PIN)
    return {"length": len(tp), "first_digit": tp[0] if tp else "?", "source": "env" if os.environ.get("TEACHER_PIN") else "default"}

@app.get("/auth/pin/{pin}")
def auth_pin(pin: str):
    """Resolve a hex login code to a role + identity."""
    pin = pin.strip().upper()
    teacher_pin = os.environ.get("TEACHER_PIN", TEACHER_PIN)
    admin_pin   = os.environ.get("ADMIN_PIN",   ADMIN_PIN)
    if pin == admin_pin and admin_pin != teacher_pin:
        return {"role": "admin"}
    # Check teacher accounts first
    for t in teachers:
        if t.get("pin") == pin:
            return {
                "role":        "teacher",
                "teacherId":   t["id"],
                "teacherName": t["name"],
                "classIds":    t.get("classIds", []),
                "isLegacy":    False,
            }
    # Legacy single teacher PIN
    if pin == teacher_pin:
        return {"role": "teacher", "teacherId": None, "teacherName": "Teacher",
                "classIds": None, "isLegacy": True}
    # Student — search roster
    for cls in roster:
        for s in cls["students"]:
            if s.get("pin") == pin:
                return {
                    "role":        "student",
                    "studentId":   s["id"],
                    "studentName": s["name"],
                    "classId":     cls["id"],
                    "className":   cls["name"],
                }
    return {"role": "unknown"}

@app.put("/roster/class/{cid}/student/{sid}/pin")
def set_student_pin(cid: str, sid: str):
    """Generate a new PIN for a student."""
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    s = next((s for s in cls["students"] if s["id"]==sid), None)
    if not s: raise HTTPException(404, "Student not found")
    # Generate unique PIN
    used = {st.get("pin") for c in roster for st in c["students"]}
    for _ in range(100):
        pin = str(random.randint(10000, 99999))
        if pin not in used:
            s["pin"] = pin
            _save("roster.json", roster)
            return {"ok": True, "pin": pin}
    raise HTTPException(500, "Could not generate unique PIN")

@app.put("/roster/class/{cid}/student/{sid}/pin/set")
def set_student_pin_manual(cid: str, sid: str, pin: str):
    """Manually set a specific PIN for a student."""
    if len(pin) != 5 or not pin.isdigit():
        raise HTTPException(400, "PIN must be exactly 5 digits")
    cls = next((c for c in roster if c["id"]==cid), None)
    if not cls: raise HTTPException(404, "Class not found")
    s = next((s for s in cls["students"] if s["id"]==sid), None)
    if not s: raise HTTPException(404, "Student not found")
    # Check uniqueness across all PINs
    used = _all_used_pins()
    used.discard(s.get("pin",""))  # allow keeping same PIN
    if pin in used:
        raise HTTPException(400, "PIN already in use")
    s["pin"] = pin
    _save("roster.json", roster)
    return {"ok": True, "pin": pin}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
