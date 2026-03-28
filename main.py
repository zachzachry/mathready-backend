"""
MathReady GA — Backend Server
FastAPI + Supabase persistence
"""

from dotenv import load_dotenv
import os as _os
load_dotenv(dotenv_path=_os.path.join(_os.path.dirname(__file__), ".env"))

from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.responses import JSONResponse
import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List, Any
import time, json, os, uuid, random, string
import uvicorn
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from supabase import create_client

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# ── Supabase client ────────────────────────────────────────
_sb_error = None
try:
    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
except Exception as _e:
    _sb_error = str(_e)
    sb = None
    print(f"⚠ Supabase init failed: {_sb_error}")

app = FastAPI(title="MathReady GA API")

# ── CORS ───────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://milestoneready.com,https://www.milestoneready.com,https://mathready-frontend.vercel.app,http://localhost:3000,http://localhost:8001"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory state (intentionally NOT persisted to DB) ───
heartbeats      = {}
test_control    = {"paused": False, "stopped": False, "extensions": {}}
active_test     = {"questions": [], "title": "Practice Test"}
teacher_sessions: dict = {}  # token (UUID str) → teacher email

# ── Teacher auth dependency ────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

def require_teacher(creds: HTTPAuthorizationCredentials = Security(_bearer)):
    """Dependency: validates a teacher session token issued at login."""
    if not creds:
        raise HTTPException(401, "Teacher authentication required. Please log in.")
    token = creds.credentials
    if token not in teacher_sessions:
        raise HTTPException(401, "Invalid or expired session. Please log in again.")
    return teacher_sessions[token]  # returns teacher email


# ── Helpers ────────────────────────────────────────────────

def gen_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _is_qid(qid):
    return bool(qid and qid.startswith("Q") and len(qid) == 6 and qid[1:].isdigit())


def _next_question_id():
    try:
        res = sb.table("questions").select("id").execute()
        existing = set()
        for row in (res.data or []):
            qid = row.get("id", "")
            if _is_qid(qid):
                existing.add(int(qid[1:]))
        n = 1
        while n in existing:
            n += 1
        return f"Q{n:05d}"
    except Exception:
        return f"Q{random.randint(1,99999):05d}"


def _parse_jsonb(val, default):
    """Return val as a Python object.
    Handles double-encoded JSONB values that Supabase returns as strings:
      '"400"'      -> '400'        (JSON-encoded string)
      '["a","b"]'  -> ['a','b']    (JSON-encoded array)
      '{"k":"v"}'  -> {'k':'v'}    (JSON-encoded object)
    Plain strings not starting with " [ { are returned as-is.
    None returns default.
    Already-parsed dicts/lists are returned as-is.
    """
    if val is None:
        return default
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s and s[0] in ('"', '[', '{'):
            try:
                return json.loads(s)
            except Exception:
                pass
    return val


def _strip_answers(questions):
    stripped = []
    for q in questions:
        q_copy = {k: v for k, v in q.items() if k not in ("correct", "answer")}
        stripped.append(q_copy)
    return stripped


def _grade_answer(q, given):
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
    if qtype == "hotspot":
        try:
            import math
            g = json.loads(given) if isinstance(given, str) else given
            correct_map = q.get("answer") or {}
            asset_type = q.get("assetType", "tile")
            is_dot = asset_type in ("dot", "pin")
            snap_points = q.get("snapPoints") or []
            correct_sps = [sp for sp in snap_points if correct_map.get(sp["id"])]
            TOL = 2.5
            if not isinstance(g, list) or len(g) != len(correct_sps):
                return False
            matched = 0
            for sp in correct_sps:
                for pt in g:
                    d = math.sqrt((sp["x"] - pt.get("x", 0))**2 + (sp["y"] - pt.get("y", 0))**2)
                    if d <= TOL and (is_dot or pt.get("val") == correct_map[sp["id"]]):
                        matched += 1
                        break
            return matched == len(correct_sps)
        except Exception:
            return False
    return str(given).strip() == str(correct_val).strip()


def _db_question_to_api(row: dict) -> dict:
    """Convert DB snake_case question row to camelCase API shape."""
    return {
        "id":            row.get("id"),
        "standard":      row.get("standard", ""),
        "short":         row.get("short", ""),
        "dok":           row.get("dok"),
        "question":      row.get("question", ""),
        "questionImage": row.get("question_image"),
        "type":          row.get("type", "mcq"),
        "subject":       row.get("subject", "math"),
        "choices":       _parse_jsonb(row.get("choices"), []),
        "choiceImages":  _parse_jsonb(row.get("choice_images"), None),
        "correct":       _parse_jsonb(row.get("correct"), None),
        "answer":        _parse_jsonb(row.get("answer"), None),
        "zones":         _parse_jsonb(row.get("zones"), None),
        "items":         _parse_jsonb(row.get("items"), None),
        "ddLayout":      row.get("dd_layout", "categories"),
        "snapPoints":    _parse_jsonb(row.get("snap_points"), None),
        "assetType":     row.get("asset_type"),
        "assetReuse":    row.get("asset_reuse"),
        "assetSize":     row.get("asset_size"),
    }


def _api_question_to_db(data: dict) -> dict:
    """Convert camelCase question dict to DB snake_case row."""
    return {
        "id":             data.get("id"),
        "standard":       data.get("standard", ""),
        "short":          data.get("short", ""),
        "dok":            data.get("dok"),
        "question":       data.get("question", ""),
        "question_image": data.get("questionImage"),
        "type":           data.get("type", "mcq"),
        "subject":        data.get("subject", "math"),
        "choices":        data.get("choices") or [],
        "choice_images":  data.get("choiceImages"),
        "correct":        data.get("correct"),
        "answer":         data.get("answer"),
        "zones":          data.get("zones"),
        "items":          data.get("items"),
        "dd_layout":      data.get("ddLayout", "categories"),
        "snap_points":    data.get("snapPoints"),
        "asset_type":     data.get("assetType"),
        "asset_reuse":    data.get("assetReuse"),
        "asset_size":     data.get("assetSize"),
    }


def _db_student_to_api(row: dict) -> dict:
    return {
        "id":           row.get("id"),
        "name":         row.get("name", ""),
        "email":        row.get("email", ""),
        "pin":          row.get("pin"),
        "googleSub":    row.get("google_sub"),
        "extendedTime": row.get("extended_time", False),
        "reduceChoices":row.get("reduce_choices", False),
        "class_id":     row.get("class_id"),
    }


def _db_class_to_api(row: dict, students: list = None) -> dict:
    return {
        "id":            row.get("id"),
        "name":          row.get("name", ""),
        "gcCourseId":    row.get("gc_course_id"),
        "hideTimer":     row.get("hide_timer", True),
        "drillDuration": row.get("drill_duration", 180),
        "students":      students if students is not None else [],
    }


def _db_saved_test_to_api(row: dict, questions: list = None) -> dict:
    out = {
        "id":              row.get("id"),
        "name":            row.get("name", ""),
        "code":            row.get("code", ""),
        "title":           row.get("title", ""),
        "type":            row.get("type", "test"),
        "subject":         row.get("subject", "math"),
        "adaptive":        row.get("adaptive", False),
        "untimed":         row.get("untimed", False),
        "timeLimitSecs":   row.get("time_limit_secs", 1800),
        "warnSecs":        row.get("warn_secs", 300),
        "oneAttempt":      row.get("one_attempt", False),
        "drillStandards":  row.get("drill_standards") or [],
        "drillCount":      row.get("drill_count", 10),
        "createdBy":       row.get("created_by", ""),
        "createdByName":   row.get("created_by_name", ""),
        "visibility":      row.get("visibility", "private"),
        "sharedWith":      row.get("shared_with") or [],
        "adminScoresOnly": row.get("admin_scores_only", False),
        "closeDate":       row.get("close_date"),
        "saved_at":        row.get("saved_at", ""),
        "classIds":        [],  # filled from test_classes join
    }
    if questions is not None:
        out["questions"] = questions
    return out


def _db_session_to_api(row: dict) -> dict:
    return {
        "id":           row.get("id"),
        "studentId":    row.get("student_id", ""),
        "studentName":  row.get("student_name", ""),
        "classId":      row.get("class_id", ""),
        "className":    row.get("class_name", ""),
        "testCode":     row.get("test_code", ""),
        "testTitle":    row.get("test_title", ""),
        "score":        row.get("score", 0),
        "total":        row.get("total", 0),
        "pct":          row.get("pct", 0),
        "submitted":    row.get("submitted", ""),
        "submittedAt":  row.get("submitted_at"),
        "timeUsed":     row.get("time_used", ""),
        "violations":   row.get("violations", 0),
        "mode":         row.get("mode", "test"),
        "answers":      _parse_jsonb(row.get("answers"), {}),
        "violationLog": _parse_jsonb(row.get("violation_log"), []),
        "questionTimes":_parse_jsonb(row.get("question_times"), []),
    }


def _get_test_class_ids(test_id: str) -> list:
    try:
        res = sb.table("test_classes").select("class_id").eq("test_id", test_id).execute()
        return [r["class_id"] for r in (res.data or [])]
    except Exception:
        return []


def _get_test_questions(test_id: str) -> list:
    """Fetch questions for a saved test via test_questions join."""
    try:
        tq_res = sb.table("test_questions").select("*").eq("test_id", test_id).order("position").execute()
        tq_rows = tq_res.data or []
        questions = []
        for tq in tq_rows:
            qid = tq.get("question_id")
            inline = tq.get("inline_data")
            if qid:
                q_res = sb.table("questions").select("*").eq("id", qid).execute()
                if q_res.data:
                    questions.append(_db_question_to_api(q_res.data[0]))
                elif inline:
                    questions.append(inline)
            elif inline:
                questions.append(inline)
        return questions
    except Exception:
        return []


def _server_score(test_code, answers):
    """Look up saved test by code, grade all answers server-side."""
    if not test_code:
        return None
    code_upper = test_code.strip().upper()
    try:
        res = sb.table("saved_tests").select("id").eq("code", code_upper).execute()
        if not res.data:
            return None
        test_id = res.data[0]["id"]
        questions = _get_test_questions(test_id)
        total = len(questions)
        score = 0
        for q in questions:
            qid = q.get("id", "")
            given = answers.get(qid)
            if _grade_answer(q, given):
                score += 1
        return score, total
    except Exception:
        return None


def _get_roster(class_ids=None) -> list:
    """Fetch classes with embedded students from DB."""
    try:
        q = sb.table("classes").select("*")
        if class_ids:
            q = q.in_("id", list(class_ids))
        cls_res = q.execute()
        classes = cls_res.data or []
        result = []
        for cls in classes:
            stu_res = sb.table("students").select("*").eq("class_id", cls["id"]).execute()
            students = [_db_student_to_api(s) for s in (stu_res.data or [])]
            result.append(_db_class_to_api(cls, students))
        return result
    except Exception as e:
        raise HTTPException(500, f"DB error fetching roster: {e}")


def _get_teachers() -> list:
    try:
        res = sb.table("teachers").select("*").execute()
        teachers = res.data or []
        result = []
        for t in teachers:
            tc_res = sb.table("teacher_classes").select("class_id").eq("teacher_id", t["id"]).execute()
            class_ids = [r["class_id"] for r in (tc_res.data or [])]
            result.append({
                "id":       t["id"],
                "name":     t.get("name", ""),
                "email":    t.get("email", ""),
                "role":     t.get("role", "teacher"),
                "pin":      t.get("pin"),
                "classIds": class_ids,
            })
        return result
    except Exception as e:
        raise HTTPException(500, f"DB error fetching teachers: {e}")


# ── Models ─────────────────────────────────────────────────
class Session(BaseModel):
    studentId:    Optional[str] = ""
    studentName:  str
    classId:      Optional[str] = ""
    className:    Optional[str] = ""
    testCode:     Optional[str] = ""
    testTitle:    Optional[str] = ""
    score:        int
    total:        int
    pct:          int
    submitted:    str
    timeUsed:     str
    answers:      dict
    violations:   Optional[int] = 0
    violationLog: Optional[list] = []
    mode:         Optional[str] = "test"
    questionTimes:Optional[list] = []

class Heartbeat(BaseModel):
    name: str; current: int

class Question(BaseModel):
    id:            Optional[str] = None
    standard:      str
    short:         Optional[str] = ""
    dok:           Optional[int] = None
    question:      str
    questionImage: Optional[str] = None
    type:          Optional[str] = "mcq"
    choices:       Optional[List[str]] = []
    choiceImages:  Optional[List[Any]] = None
    correct:       Optional[Any] = ""
    answer:        Optional[Any] = None
    zones:         Optional[List[str]] = None
    items:         Optional[List[str]] = None
    ddLayout:      Optional[str] = "categories"
    snapPoints:    Optional[list] = None
    assetType:     Optional[str] = None
    assetReuse:    Optional[bool] = None
    assetSize:     Optional[str] = None
    subject:       Optional[str] = "math"

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
    name:            str
    code:            Optional[str] = None
    questions:       List[Any]
    title:           Optional[str] = ""
    adaptive:        Optional[bool] = False
    type:            Optional[str] = "test"
    drillStandards:  Optional[List[str]] = []
    drillCount:      Optional[int] = 10
    untimed:         Optional[bool] = False
    timeLimitSecs:   Optional[int] = 1800
    warnSecs:        Optional[int] = 300
    oneAttempt:      Optional[bool] = False
    classIds:        Optional[List[str]] = []
    subject:         Optional[str] = "math"
    createdBy:       Optional[str] = ""
    createdByName:   Optional[str] = ""
    visibility:      Optional[str] = "private"
    sharedWith:      Optional[List[str]] = []
    adminScoresOnly: Optional[bool] = False
    closeDate:       Optional[str] = None

class NewClass(BaseModel):
    name: str
    teacherId: Optional[str] = None
    gcCourseId: Optional[str] = None

class AddStudents(BaseModel):
    students: List[Any]

class NewTeacher(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = "teacher"
    classIds: Optional[List[str]] = []

class UpdateClass(BaseModel):
    name:          Optional[str] = None
    students:      Optional[List[Any]] = None
    gcCourseId:    Optional[str] = None
    hideTimer:     Optional[bool] = None
    drillDuration: Optional[int] = None

class GoogleVerifyBody(BaseModel):
    token:   str
    code:    Optional[str] = None
    classId: Optional[str] = None


# ── Health ─────────────────────────────────────────────────
@app.get("/")
def root():
    import sys, platform
    if _sb_error:
        return {"status": "ERROR", "sb_error": _sb_error,
                "python": sys.version, "platform": platform.platform()}
    try:
        q_count = len((sb.table("questions").select("id").execute().data or []))
        s_count = len((sb.table("test_sessions").select("id").execute().data or []))
        t_count = len((sb.table("saved_tests").select("id").execute().data or []))
        c_count = len((sb.table("classes").select("id").execute().data or []))
    except Exception as e:
        return {"status": "DB_ERROR", "error": str(e)}
    return {"status": "MathReady GA ✓", "questions": q_count,
            "sessions": s_count, "saved_tests": t_count, "classes": c_count}


# ── Sessions ───────────────────────────────────────────────
@app.post("/submit")
def submit_session(session: Session):
    d = session.dict()
    result = _server_score(d.get("testCode"), d.get("answers", {}))
    if result:
        score, total = result
        d["score"] = score
        d["total"] = total
        d["pct"] = round(score / total * 100) if total else 0

    row = {
        "student_id":    d.get("studentId", ""),
        "student_name":  d.get("studentName", ""),
        "class_id":      d.get("classId", ""),
        "class_name":    d.get("className", ""),
        "test_code":     d.get("testCode", ""),
        "test_title":    d.get("testTitle", ""),
        "score":         d["score"],
        "total":         d["total"],
        "pct":           d["pct"],
        "submitted":     d.get("submitted", ""),
        "time_used":     d.get("timeUsed", ""),
        "violations":    d.get("violations", 0),
        "mode":          d.get("mode", "test"),
        "answers":       d.get("answers", {}),
        "violation_log": d.get("violationLog", []),
        "question_times":d.get("questionTimes", []),
    }
    try:
        sb.table("test_sessions").insert(row).execute()
    except Exception as e:
        raise HTTPException(500, f"Failed to save session: {e}")

    # Auto-complete assignment
    sid = d.get("studentId", "")
    code = d.get("testCode", "").upper()
    if sid and code:
        try:
            ta_res = sb.table("test_assignments").select("id").eq("test_code", code).execute()
            for ta in (ta_res.data or []):
                aid = ta["id"]
                as_res = sb.table("assignment_students").select("*").eq("assignment_id", aid).eq("student_id", sid).execute()
                if as_res.data and not as_res.data[0].get("completed"):
                    sb.table("assignment_students").update({"completed": True}).eq("assignment_id", aid).eq("student_id", sid).execute()
        except Exception:
            pass

    return {"ok": True, "score": d["score"], "total": d["total"], "pct": d["pct"]}


@app.get("/test/attempt-check")
def check_attempt(code: str, studentId: str = "", studentName: str = ""):
    code = code.strip().upper()
    try:
        q = sb.table("test_sessions").select("id").eq("test_code", code)
        if studentId:
            q = q.eq("student_id", studentId)
        elif studentName:
            q = q.eq("student_name", studentName.strip())
        else:
            return {"attempted": False}
        res = q.execute()
        return {"attempted": bool(res.data)}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/sessions")
def get_sessions(classIds: Optional[str] = None, role: Optional[str] = None):
    is_admin = role in ("super_admin", "school_admin")
    try:
        q = sb.table("test_sessions").select("*")
        if classIds is not None:
            if classIds.strip() == "":
                return []
            ids = [i for i in classIds.split(",") if i.strip()]
            if not ids:
                return []
            q = q.in_("class_id", ids)
        res = q.execute()
        rows = res.data or []

        # Backfill missing student names from students table (migrated sessions have empty student_name)
        missing_ids = list({r["student_id"] for r in rows if not r.get("student_name") and r.get("student_id")})
        if missing_ids:
            try:
                nm_res = sb.table("students").select("id,name").in_("id", missing_ids).execute()
                nm_map = {r["id"]: r["name"] for r in (nm_res.data or [])}
                for row in rows:
                    if not row.get("student_name") and row.get("student_id"):
                        row["student_name"] = nm_map.get(row["student_id"], "")
            except Exception:
                pass

        sessions = [_db_session_to_api(r) for r in rows]

        if not is_admin:
            # Filter out adminScoresOnly test codes
            try:
                at_res = sb.table("saved_tests").select("code").eq("admin_scores_only", True).execute()
                admin_codes = {r["code"].upper() for r in (at_res.data or [])}
                sessions = [s for s in sessions if s.get("testCode", "").upper() not in admin_codes]
            except Exception:
                pass
        return sessions
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/student/history/{student_id}")
def get_student_history(student_id: str):
    try:
        res = sb.table("test_sessions").select("*").or_(
            f"student_id.eq.{student_id},student_name.eq.{student_id}"
        ).execute()
        return [_db_session_to_api(r) for r in (res.data or [])]
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/sessions/class/{cid}")
def clear_class_sessions(cid: str):
    try:
        cls_res = sb.table("classes").select("id,name").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls_name = cls_res.data[0]["name"]
        # Count before
        before_res = sb.table("test_sessions").select("id").eq("class_id", cid).execute()
        before = len(before_res.data or [])
        # Delete non-drill sessions
        sb.table("test_sessions").delete().eq("class_id", cid).not_.in_("mode", ["drill", "practice"]).execute()
        after_res = sb.table("test_sessions").select("id").eq("class_id", cid).execute()
        after = len(after_res.data or [])
        return {"ok": True, "removed": before - after, "className": cls_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/sessions/test/{code}")
def delete_sessions_by_test(code: str):
    code_upper = code.strip().upper()
    try:
        before_res = sb.table("test_sessions").select("id").eq("test_code", code_upper).execute()
        before = len(before_res.data or [])
        sb.table("test_sessions").delete().eq("test_code", code_upper).execute()
        return {"ok": True, "removed": before}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/test/review/{code}")
def get_test_review(code: str, classId: Optional[str] = None):
    code_upper = code.strip().upper()
    try:
        t_res = sb.table("saved_tests").select("id,title,name").eq("code", code_upper).execute()
        if not t_res.data:
            raise HTTPException(404, "Test not found")
        test_row = t_res.data[0]
        test_id = test_row["id"]
        questions = _get_test_questions(test_id)

        q = sb.table("test_sessions").select("*").eq("test_code", code_upper).in_("mode", ["test", ""])
        if classId:
            q = q.eq("class_id", classId)
        sess_res = q.execute()
        test_sessions = [_db_session_to_api(r) for r in (sess_res.data or [])]

        review_items = []
        for q_obj in questions:
            qid = q_obj.get("id", "")
            qtype = q_obj.get("type", "mcq")
            correct_val = q_obj.get("answer") or q_obj.get("correct")
            student_answers = []
            correct_count = 0
            answer_dist = {}
            for s in test_sessions:
                ans = s.get("answers", {}).get(qid)
                is_correct = _grade_answer(q_obj, ans)
                if is_correct:
                    correct_count += 1
                student_answers.append({
                    "studentName": s.get("studentName", ""),
                    "studentId":   s.get("studentId", ""),
                    "answer":      ans,
                    "correct":     is_correct,
                })
                if qtype == "mcq" and ans:
                    answer_dist[str(ans)] = answer_dist.get(str(ans), 0) + 1
            attempted = len(student_answers)
            pct = round(correct_count / attempted * 100) if attempted else 0
            review_items.append({
                "id":               qid,
                "question":         q_obj.get("question", ""),
                "questionImage":    q_obj.get("questionImage"),
                "type":             qtype,
                "standard":         q_obj.get("standard", ""),
                "short":            q_obj.get("short", ""),
                "dok":              q_obj.get("dok"),
                "choices":          q_obj.get("choices", []),
                "correct":          str(correct_val) if correct_val is not None else "",
                "attempted":        attempted,
                "correctCount":     correct_count,
                "pct":              pct,
                "answerDistribution": answer_dist,
                "studentAnswers":   student_answers,
            })
        review_items.sort(key=lambda x: x["pct"])
        return {
            "testTitle":    test_row.get("title", test_row.get("name", "")),
            "testCode":     code_upper,
            "totalStudents": len(test_sessions),
            "items":        review_items,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/sessions")
def clear_sessions(mode: Optional[str] = None):
    try:
        q = sb.table("test_sessions").select("id")
        if mode == "tests":
            q = sb.table("test_sessions").select("id").in_("mode", ["test", ""])
        elif mode == "drills":
            q = sb.table("test_sessions").select("id").in_("mode", ["drill", "practice"])
        before_res = q.execute()
        before = len(before_res.data or [])

        dq = sb.table("test_sessions").delete()
        if mode == "tests":
            dq = dq.in_("mode", ["test", ""])
        elif mode == "drills":
            dq = dq.in_("mode", ["drill", "practice"])
        else:
            dq = dq.neq("id", 0)  # delete all
            heartbeats.clear()
        dq.execute()
        return {"ok": True, "removed": before}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


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


# ── Test Control ───────────────────────────────────────────
@app.get("/test/control")
def get_test_control():
    return test_control

@app.post("/test/control")
def post_test_control(body: dict):
    if "paused"  in body: test_control["paused"]  = bool(body["paused"])
    if "stopped" in body: test_control["stopped"] = bool(body["stopped"])
    if body.get("stopped") == False and body.get("paused") == False:
        test_control["extensions"] = {}
    return test_control

@app.post("/test/control/extend")
def extend_student_time(body: dict):
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
    try:
        q = sb.table("questions").select("*")
        if standard:
            q = q.like("standard", f"{standard}%")
        if dok:
            q = q.eq("dok", dok)
        res = q.execute()
        return [_db_question_to_api(r) for r in (res.data or [])]
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/questions")
def save_question(q: Question, _teacher: str = Depends(require_teacher)):
    data = q.dict()
    qid = data.get("id", "")
    if not qid or (qid.startswith("Q") and len(qid) < 6 and qid[1:].isdigit()):
        data["id"] = _next_question_id()
    row = _api_question_to_db(data)
    try:
        # Check if exists
        existing = sb.table("questions").select("id").eq("id", data["id"]).execute()
        if existing.data:
            sb.table("questions").update(row).eq("id", data["id"]).execute()
        else:
            sb.table("questions").insert(row).execute()
        return {"ok": True, "id": data["id"]}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/questions/{qid}")
def delete_question(qid: str, _teacher: str = Depends(require_teacher)):
    try:
        before_res = sb.table("questions").select("id").eq("id", qid).execute()
        before = len(before_res.data or [])
        sb.table("questions").delete().eq("id", qid).execute()
        return {"ok": True, "removed": before}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Active Test ────────────────────────────────────────────
@app.get("/test/active")
def get_active_test():
    return active_test

@app.post("/test/activate")
def activate_test(test: ActiveTest):
    global active_test
    active_test = test.dict()
    return {"ok": True}


# ── Test by code ───────────────────────────────────────────
@app.get("/test/code/{code}")
def get_test_by_code(code: str):
    code = code.strip().upper()
    try:
        res = sb.table("saved_tests").select("*").eq("code", code).execute()
        if not res.data:
            return {"found": False}
        match = res.data[0]
        test_id = match["id"]
        class_ids = _get_test_class_ids(test_id)
        questions = _get_test_questions(test_id)

        # Fetch roster for assigned classes
        cls_res = sb.table("classes").select("*").in_("id", class_ids).execute() if class_ids else type('obj', (object,), {'data': []})()
        roster_classes = []
        for cls in (cls_res.data or []):
            stu_res = sb.table("students").select("*").eq("class_id", cls["id"]).execute()
            students = [_db_student_to_api(s) for s in (stu_res.data or [])]
            roster_classes.append(_db_class_to_api(cls, students))

        return {
            "found":          True,
            "questions":      _strip_answers(questions),
            "title":          match.get("title", match.get("name", "")),
            "code":           code,
            "adaptive":       match.get("adaptive", False),
            "type":           match.get("type", "test"),
            "drillStandards": match.get("drill_standards") or [],
            "drillCount":     match.get("drill_count", 10),
            "untimed":        match.get("untimed", False),
            "timeLimitSecs":  match.get("time_limit_secs", 1800),
            "warnSecs":       match.get("warn_secs", 300),
            "oneAttempt":     match.get("one_attempt", False),
            "classIds":       class_ids,
            "roster":         roster_classes,
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Saved Tests ────────────────────────────────────────────
@app.get("/tests/saved")
def get_saved_tests(teacherId: Optional[str] = None, role: Optional[str] = None):
    is_admin = role in ("super_admin", "school_admin")
    try:
        res = sb.table("saved_tests").select("*").execute()
        rows = res.data or []

        def visible(t):
            cb  = t.get("created_by", "")
            vis = t.get("visibility", "private")
            if is_admin: return True
            if not cb: return True
            if cb == teacherId: return True
            if vis == "grade" and teacherId in (t.get("shared_with") or []): return True
            if vis == "global": return True
            return False

        filtered = [t for t in rows if not teacherId or visible(t)]

        # Get class IDs for each test
        result = []
        for t in filtered:
            class_ids = _get_test_class_ids(t["id"])
            # Count questions via test_questions
            try:
                tq_res = sb.table("test_questions").select("question_id").eq("test_id", t["id"]).execute()
                q_count = len(tq_res.data or [])
            except Exception:
                q_count = 0
            result.append({
                "id":             t["id"],
                "name":           t.get("name", ""),
                "code":           t.get("code", ""),
                "title":          t.get("title", ""),
                "count":          q_count,
                "saved_at":       t.get("saved_at", ""),
                "type":           t.get("type", "test"),
                "drill_count":    t.get("drill_count", 10),
                "drill_standards":t.get("drill_standards") or [],
                "classIds":       class_ids,
                "oneAttempt":     t.get("one_attempt", False),
                "untimed":        t.get("untimed", False),
                "timeLimitSecs":  t.get("time_limit_secs", 1800),
                "adaptive":       t.get("adaptive", False),
                "subject":        t.get("subject", "math"),
                "createdBy":      t.get("created_by", ""),
                "createdByName":  t.get("created_by_name", ""),
                "visibility":     t.get("visibility", "private"),
                "sharedWith":     t.get("shared_with") or [],
                "adminScoresOnly":t.get("admin_scores_only", False),
                "closeDate":      t.get("close_date"),
            })
        return result
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/tests/saved/{tid}")
def get_saved_test(tid: str, teacherId: Optional[str] = None, role: Optional[str] = None):
    try:
        res = sb.table("saved_tests").select("*").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Not found")
        t = res.data[0]
        is_admin = role in ("super_admin", "school_admin")
        class_ids = _get_test_class_ids(tid)

        if not is_admin and t.get("visibility") == "global":
            result = _db_saved_test_to_api(t, [])
            result["classIds"] = class_ids
            return result

        questions = _get_test_questions(tid)
        result = _db_saved_test_to_api(t, questions)
        result["classIds"] = class_ids
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


def _upsert_test_questions(test_id: str, questions: list):
    """Replace all test_questions rows for a test."""
    try:
        sb.table("test_questions").delete().eq("test_id", test_id).execute()
        rows = []
        for pos, q in enumerate(questions):
            q_dict = q if isinstance(q, dict) else q.dict()
            qid = q_dict.get("id")
            if qid:
                # Check question exists in question bank
                qres = sb.table("questions").select("id").eq("id", qid).execute()
                if qres.data:
                    rows.append({"test_id": test_id, "position": pos, "question_id": qid, "inline_data": None})
                else:
                    rows.append({"test_id": test_id, "position": pos, "question_id": None, "inline_data": q_dict})
            else:
                rows.append({"test_id": test_id, "position": pos, "question_id": None, "inline_data": q_dict})
        if rows:
            sb.table("test_questions").insert(rows).execute()
    except Exception as e:
        raise HTTPException(500, f"DB error saving test questions: {e}")


def _upsert_test_classes(test_id: str, class_ids: list):
    try:
        sb.table("test_classes").delete().eq("test_id", test_id).execute()
        if class_ids:
            rows = [{"test_id": test_id, "class_id": cid} for cid in class_ids]
            sb.table("test_classes").insert(rows).execute()
    except Exception as e:
        raise HTTPException(500, f"DB error saving test classes: {e}")


@app.post("/tests/saved")
def save_test(test: SavedTest, teacherId: Optional[str] = None, role: Optional[str] = None, _teacher: str = Depends(require_teacher)):
    data = test.dict()
    test_id = "t" + uuid.uuid4().hex[:8]
    saved_at = time.strftime("%b %d, %Y %I:%M %p")
    code = data.get("code", "")
    if not code:
        code = gen_code()
    else:
        code = code.strip().upper()

    try:
        # Ensure unique code
        existing_codes_res = sb.table("saved_tests").select("code").execute()
        existing_codes = {r["code"] for r in (existing_codes_res.data or [])}
        while code in existing_codes:
            code = gen_code()

        # Ownership
        created_by = data.get("createdBy", "") or teacherId or ""
        created_by_name = data.get("createdByName", "")
        if not created_by_name and created_by:
            t_res = sb.table("teachers").select("name").eq("id", created_by).execute()
            if t_res.data:
                created_by_name = t_res.data[0].get("name", "")

        is_admin = role in ("super_admin", "school_admin")
        visibility = data.get("visibility", "private")
        if not is_admin and visibility == "global":
            visibility = "private"

        row = {
            "id":               test_id,
            "name":             data.get("name", ""),
            "code":             code,
            "title":            data.get("title", ""),
            "type":             data.get("type", "test"),
            "subject":          data.get("subject", "math"),
            "adaptive":         data.get("adaptive", False),
            "untimed":          data.get("untimed", False),
            "time_limit_secs":  data.get("timeLimitSecs", 1800),
            "warn_secs":        data.get("warnSecs", 300),
            "one_attempt":      data.get("oneAttempt", False),
            "drill_standards":  data.get("drillStandards") or [],
            "drill_count":      data.get("drillCount", 10),
            "created_by":       created_by,
            "created_by_name":  created_by_name,
            "visibility":       visibility,
            "shared_with":      data.get("sharedWith") or [],
            "admin_scores_only":data.get("adminScoresOnly", False),
            "close_date":       data.get("closeDate"),
            "saved_at":         saved_at,
        }
        sb.table("saved_tests").insert(row).execute()
        _upsert_test_questions(test_id, data.get("questions", []))
        _upsert_test_classes(test_id, data.get("classIds", []))
        return {"ok": True, "id": test_id, "code": code}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.put("/tests/saved/{tid}")
def update_saved_test(tid: str, test: SavedTest, teacherId: Optional[str] = None, role: Optional[str] = None, _teacher: str = Depends(require_teacher)):
    try:
        res = sb.table("saved_tests").select("*").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Not found")
        t = res.data[0]

        new_code = test.code.strip().upper() if test.code else t.get("code", "")
        # Check code uniqueness
        code_res = sb.table("saved_tests").select("id").eq("code", new_code).neq("id", tid).execute()
        if code_res.data:
            raise HTTPException(400, "Code already in use")

        is_admin = role in ("super_admin", "school_admin")
        cb = t.get("created_by", "")
        if cb and cb != teacherId and not is_admin:
            raise HTTPException(403, "Not authorized to edit this test")

        visibility = test.visibility or t.get("visibility", "private")
        row = {
            "name":             test.name,
            "code":             new_code,
            "title":            test.title or "",
            "adaptive":         test.adaptive,
            "untimed":          test.untimed,
            "time_limit_secs":  test.timeLimitSecs,
            "warn_secs":        test.warnSecs,
            "one_attempt":      test.oneAttempt,
            "subject":          test.subject or "math",
            "visibility":       visibility,
            "shared_with":      test.sharedWith or [],
            "admin_scores_only":test.adminScoresOnly or False,
        }
        if test.closeDate is not None:
            row["close_date"] = test.closeDate
        sb.table("saved_tests").update(row).eq("id", tid).execute()
        if test.questions:
            _upsert_test_questions(tid, test.questions)
        _upsert_test_classes(tid, test.classIds or [])
        return {"ok": True, "code": new_code}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.patch("/tests/saved/{tid}/classes")
def set_test_classes(tid: str, body: dict):
    try:
        res = sb.table("saved_tests").select("id").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Not found")
        _upsert_test_classes(tid, body.get("classIds", []))
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/tests/saved/{tid}")
def delete_saved_test(tid: str, teacherId: Optional[str] = None, role: Optional[str] = None, _teacher: str = Depends(require_teacher)):
    try:
        res = sb.table("saved_tests").select("*").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Not found")
        t = res.data[0]
        is_admin = role in ("super_admin", "school_admin")
        cb = t.get("created_by", "")
        if cb and cb != teacherId and not is_admin:
            raise HTTPException(403, "Not authorized to delete this test")
        sb.table("test_questions").delete().eq("test_id", tid).execute()
        sb.table("test_classes").delete().eq("test_id", tid).execute()
        sb.table("saved_tests").delete().eq("id", tid).execute()
        return {"ok": True, "removed": 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Bulk seed ──────────────────────────────────────────────
@app.post("/questions/seed")
def seed_questions(questions_in: List[Any] = fastapi.Body(...), _teacher: str = Depends(require_teacher)):
    try:
        existing_res = sb.table("questions").select("id").execute()
        existing_ids = {r["id"] for r in (existing_res.data or [])}
        added = 0
        skipped_duplicate = []

        for q in questions_in:
            q = dict(q)
            qid = q.get("id", "")
            needs_new_id = (
                not qid or
                not qid.startswith("Q") or
                not qid[1:].isdigit() or
                (qid.startswith("Q") and len(qid) < 6)
            )
            if needs_new_id:
                q["id"] = _next_question_id()
                sb.table("questions").insert(_api_question_to_db(q)).execute()
                existing_ids.add(q["id"])
                added += 1
            elif qid in existing_ids:
                skipped_duplicate.append(qid)
            else:
                sb.table("questions").insert(_api_question_to_db(q)).execute()
                existing_ids.add(qid)
                added += 1

        total_res = sb.table("questions").select("id").execute()
        return {
            "ok": True,
            "added": added,
            "total": len(total_res.data or []),
            "duplicates": skipped_duplicate,
            "duplicate_count": len(skipped_duplicate),
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Roster ─────────────────────────────────────────────────
@app.get("/roster")
def get_roster(classIds: Optional[str] = None):
    if classIds is None:
        return _get_roster()
    if classIds.strip() == "":
        return []
    ids = {i for i in classIds.split(",") if i.strip()}
    if not ids:
        return []
    return _get_roster(ids)


@app.get("/roster/class/{cid}")
def get_class(cid: str):
    try:
        cls_res = sb.table("classes").select("*").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls = cls_res.data[0]
        stu_res = sb.table("students").select("*").eq("class_id", cid).execute()
        students = [_db_student_to_api(s) for s in (stu_res.data or [])]
        return _db_class_to_api(cls, students)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/roster/class")
def create_class(body: NewClass, _teacher: str = Depends(require_teacher)):
    name = body.name.strip()
    try:
        dup_res = sb.table("classes").select("id").ilike("name", name).execute()
        if dup_res.data:
            raise HTTPException(400, f'A class named "{name}" already exists. Use a unique name.')
        cls_id = "c" + uuid.uuid4().hex[:8]
        sb.table("classes").insert({
            "id":           cls_id,
            "name":         name,
            "gc_course_id": body.gcCourseId,
            "hide_timer":   True,
        }).execute()
        if body.teacherId:
            sb.table("teacher_classes").insert({
                "teacher_id": body.teacherId,
                "class_id":   cls_id,
            }).execute()
        return {"ok": True, "id": cls_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.put("/roster/class/{cid}")
def update_class(cid: str, body: UpdateClass, _teacher: str = Depends(require_teacher)):
    try:
        cls_res = sb.table("classes").select("*").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls = cls_res.data[0]

        updates = {}
        if body.name is not None:
            new_name = body.name.strip()
            dup_res = sb.table("classes").select("id").ilike("name", new_name).neq("id", cid).execute()
            if dup_res.data:
                raise HTTPException(400, f'A class named "{new_name}" already exists.')
            updates["name"] = new_name
        if body.gcCourseId is not None:
            updates["gc_course_id"] = body.gcCourseId
        if body.hideTimer is not None:
            updates["hide_timer"] = body.hideTimer
        if body.drillDuration is not None:
            updates["drill_duration"] = body.drillDuration
        if updates:
            sb.table("classes").update(updates).eq("id", cid).execute()

        if body.students is not None:
            stu_res = sb.table("students").select("*").eq("class_id", cid).execute()
            existing = {s["id"]: s for s in (stu_res.data or [])}
            for s in body.students:
                sid = s.get("id")
                if not sid:
                    continue
                upd = {}
                if "extendedTime" in s:
                    upd["extended_time"] = s["extendedTime"]
                if "reduceChoices" in s:
                    upd["reduce_choices"] = s["reduceChoices"]
                if "name" in s:
                    upd["name"] = s["name"]
                if upd and sid in existing:
                    sb.table("students").update(upd).eq("id", sid).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/roster/class/{cid}")
def delete_class(cid: str, _teacher: str = Depends(require_teacher)):
    try:
        sb.table("teacher_classes").delete().eq("class_id", cid).execute()
        sb.table("test_classes").delete().eq("class_id", cid).execute()
        sb.table("students").delete().eq("class_id", cid).execute()
        sb.table("classes").delete().eq("id", cid).execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/roster/class/{cid}/students")
def add_students(cid: str, body: AddStudents, _teacher: str = Depends(require_teacher)):
    try:
        cls_res = sb.table("classes").select("id").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        stu_res = sb.table("students").select("name").eq("class_id", cid).execute()
        existing_names = {s["name"].lower() for s in (stu_res.data or [])}
        added = []
        for item in body.students:
            if isinstance(item, dict):
                name  = (item.get("name") or "").strip()
                email = (item.get("email") or "").strip().lower()
            else:
                name  = str(item).strip()
                email = ""
            if name and name.lower() not in existing_names:
                sid = "s" + uuid.uuid4().hex[:8]
                row = {"id": sid, "class_id": cid, "name": name}
                if email:
                    row["email"] = email
                sb.table("students").insert(row).execute()
                existing_names.add(name.lower())
                student = {"id": sid, "name": name}
                if email:
                    student["email"] = email
                added.append(student)
        return {"ok": True, "added": len(added), "students": added}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/roster/class/{cid}/student/{sid}")
def remove_student(cid: str, sid: str, _teacher: str = Depends(require_teacher)):
    if not sid or sid == "undefined" or sid == "null":
        raise HTTPException(400, "Invalid student ID")
    try:
        cls_res = sb.table("classes").select("id").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        stu_res = sb.table("students").select("id").eq("id", sid).eq("class_id", cid).execute()
        if not stu_res.data:
            raise HTTPException(404, "Student not found")
        sb.table("students").delete().eq("id", sid).eq("class_id", cid).execute()
        return {"ok": True, "removed": 1}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Admin analytics ────────────────────────────────────────
@app.get("/admin/overview")
def admin_overview():
    try:
        roster = _get_roster()
        sess_res = sb.table("test_sessions").select("*").execute()
        all_sessions = [_db_session_to_api(r) for r in (sess_res.data or [])]

        class_stats = []
        for cls in roster:
            cls_sessions = [s for s in all_sessions
                            if s.get("classId") == cls["id"] or s.get("className") == cls["name"]]
            test_sessions_cls = [s for s in cls_sessions if s.get("mode", "test") in ("test", "")]
            drill_sessions    = [s for s in cls_sessions if s.get("mode") == "drill"]
            scores = [s["score"] for s in test_sessions_cls if "score" in s]
            avg = round(sum(scores)/len(scores), 1) if scores else None
            std_map = {}
            for s in test_sessions_cls:
                for r in s.get("results", []):
                    std = r.get("standard", "?")
                    if std not in std_map: std_map[std] = {"correct": 0, "total": 0}
                    std_map[std]["total"]   += 1
                    std_map[std]["correct"] += 1 if r.get("correct") else 0
            standards = [{"standard": k, "pct": round(v["correct"]/v["total"]*100) if v["total"] else 0, "total": v["total"]}
                         for k, v in std_map.items()]
            standards.sort(key=lambda x: x["pct"])
            class_stats.append({
                "id":             cls["id"],
                "name":           cls["name"],
                "studentCount":   len(cls["students"]),
                "sessionCount":   len(test_sessions_cls),
                "drillCount":     len(drill_sessions),
                "avgScore":       avg,
                "standards":      standards,
                "recentActivity": max((s.get("timestamp", "") for s in cls_sessions), default=None),
            })
        all_std = {}
        for s in all_sessions:
            if s.get("mode", "test") not in ("test", ""): continue
            for r in s.get("results", []):
                std = r.get("standard", "?")
                if std not in all_std: all_std[std] = {"correct": 0, "total": 0}
                all_std[std]["total"]   += 1
                all_std[std]["correct"] += 1 if r.get("correct") else 0
        gaps = [{"standard": k, "pct": round(v["correct"]/v["total"]*100) if v["total"] else 0, "total": v["total"]}
                for k, v in all_std.items() if v["total"] >= 5]
        gaps.sort(key=lambda x: x["pct"])
        total_students = sum(len(c["students"]) for c in roster)
        tested_ids = {s.get("studentId") for s in all_sessions if s.get("studentId")}
        return {
            "classes":        class_stats,
            "schoolGaps":     gaps[:10],
            "totalStudents":  total_students,
            "testedStudents": len(tested_ids),
            "totalSessions":  len(all_sessions),
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/admin/fix-fluency-sessions")
def fix_fluency_sessions():
    """One-time migration: re-insert fluency_sessions from fluency_data.json.
    The original migration included a 'submitted_at' column that doesn't exist
    in the table, causing all fluency session rows to be skipped.
    Safe to call multiple times — checks row count first."""
    try:
        # Check if already migrated
        existing = sb.table("fluency_sessions").select("id", count="exact").execute()
        existing_count = existing.count or len(existing.data or [])
        if existing_count > 0:
            return {"ok": True, "skipped": True,
                    "message": f"fluency_sessions already has {existing_count} rows — skipping migration."}

        fluency_path = os.path.join(os.path.dirname(__file__), "fluency_data.json")
        with open(fluency_path) as f:
            fluency = json.load(f)

        fs_rows = []
        for sid, data in fluency.items():
            for sess in data.get("sessions", []):
                fs_rows.append({
                    "student_id":   sid,
                    "student_name": sess.get("name") or sess.get("studentName") or "",
                    "class_id":     sess.get("classId") or "",
                    "class_name":   sess.get("className") or "",
                    "test_code":    sess.get("testCode") or sess.get("code") or "",
                    "submitted":    sess.get("submitted") or "",
                    "level_add":    int(sess.get("levelAdd") or 1),
                    "level_sub":    int(sess.get("levelSub") or 1),
                    "level_mul":    int(sess.get("levelMul") or 1),
                    "level_div":    int(sess.get("levelDiv") or 1),
                    "total":        int(sess.get("total") or 0),
                    "correct":      int(sess.get("correct") or 0),
                    "pct":          int(sess.get("pct") or 0),
                    "ppm":          sess.get("ppm"),
                    "stars":        sess.get("stars"),
                    "ops":          sess.get("ops"),   # dict — stored as JSONB, no json.dumps
                })

        if not fs_rows:
            return {"ok": True, "inserted": 0, "message": "No sessions found in fluency_data.json"}

        # Insert in chunks of 50
        inserted = 0
        for i in range(0, len(fs_rows), 50):
            chunk = fs_rows[i:i+50]
            sb.table("fluency_sessions").insert(chunk).execute()
            inserted += len(chunk)

        return {"ok": True, "inserted": inserted,
                "message": f"Migrated {inserted} fluency sessions from fluency_data.json"}
    except Exception as e:
        raise HTTPException(500, f"Migration error: {e}")


# ── Teacher accounts ───────────────────────────────────────
@app.get("/teachers")
def get_teachers():
    teachers = _get_teachers()
    try:
        cls_res = sb.table("classes").select("id").execute()
        valid_class_ids = {r["id"] for r in (cls_res.data or [])}
    except Exception:
        valid_class_ids = set()
    return [{
        "id":       t["id"],
        "name":     t["name"],
        "classIds": [cid for cid in t.get("classIds", []) if cid in valid_class_ids],
        "email":    t.get("email", ""),
        "role":     t.get("role", "teacher"),
    } for t in teachers]


@app.post("/teachers")
def create_teacher(body: NewTeacher, _teacher: str = Depends(require_teacher)):
    try:
        tid = "t" + uuid.uuid4().hex[:8]
        sb.table("teachers").insert({
            "id":    tid,
            "name":  body.name.strip(),
            "email": (body.email or "").lower().strip(),
            "role":  body.role or "teacher",
        }).execute()
        if body.classIds:
            rows = [{"teacher_id": tid, "class_id": cid} for cid in body.classIds]
            sb.table("teacher_classes").insert(rows).execute()
        return {"ok": True, "id": tid}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.put("/teachers/{tid}")
def update_teacher(tid: str, body: NewTeacher, _teacher: str = Depends(require_teacher)):
    try:
        res = sb.table("teachers").select("id").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Teacher not found")
        upd = {"name": body.name.strip()}
        if body.email is not None:
            upd["email"] = body.email.lower().strip()
        if body.role is not None:
            upd["role"] = body.role
        sb.table("teachers").update(upd).eq("id", tid).execute()
        # Replace class assignments
        sb.table("teacher_classes").delete().eq("teacher_id", tid).execute()
        if body.classIds:
            rows = [{"teacher_id": tid, "class_id": cid} for cid in body.classIds]
            sb.table("teacher_classes").insert(rows).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/teachers/{tid}")
def delete_teacher(tid: str, _teacher: str = Depends(require_teacher)):
    try:
        sb.table("teacher_classes").delete().eq("teacher_id", tid).execute()
        sb.table("teachers").delete().eq("id", tid).execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.put("/teachers/{tid}/classes")
def set_teacher_classes(tid: str, body: AddStudents):
    try:
        res = sb.table("teachers").select("id").eq("id", tid).execute()
        if not res.data:
            raise HTTPException(404, "Teacher not found")
        sb.table("teacher_classes").delete().eq("teacher_id", tid).execute()
        if body.students:
            rows = [{"teacher_id": tid, "class_id": cid} for cid in body.students]
            sb.table("teacher_classes").insert(rows).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Google OAuth ───────────────────────────────────────────
@app.post("/auth/google/teacher")
def google_teacher_verify(body: GoogleVerifyBody):
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
    try:
        t_res = sb.table("teachers").select("*").ilike("email", email).execute()
        if not t_res.data:
            raise HTTPException(403, "Your Google account is not registered as a teacher. Contact your administrator.")
        t = t_res.data[0]
        tc_res = sb.table("teacher_classes").select("class_id").eq("teacher_id", t["id"]).execute()
        raw_ids = [r["class_id"] for r in (tc_res.data or [])]
        # Filter stale class IDs
        cls_res = sb.table("classes").select("id").in_("id", raw_ids).execute() if raw_ids else type('obj', (object,), {'data': []})()
        valid_ids_set = {r["id"] for r in (cls_res.data or [])}
        clean_ids = [cid for cid in raw_ids if cid in valid_ids_set]
        stale = [cid for cid in raw_ids if cid not in valid_ids_set]
        if stale:
            for cid in stale:
                sb.table("teacher_classes").delete().eq("teacher_id", t["id"]).eq("class_id", cid).execute()
        session_token = str(uuid.uuid4())
        teacher_sessions[session_token] = email
        return {
            "role":         "teacher",
            "teacherRole":  t.get("role", "teacher"),
            "teacherId":    t["id"],
            "teacherName":  t["name"],
            "classIds":     clean_ids,
            "sessionToken": session_token,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


def _match_student_db(info: dict, classes_to_search: list):
    """Match a Google token to a roster student. Priority: googleSub -> name."""
    sub     = info.get("sub", "")
    gc_name = (info.get("name") or "").strip().lower()

    # 1. Sub match
    for cls in classes_to_search:
        for s in cls.get("students", []):
            if sub and s.get("googleSub") == sub:
                return s, cls

    # 2. Name match — write sub on first login
    if gc_name:
        for cls in classes_to_search:
            for s in cls.get("students", []):
                if s["name"].strip().lower() == gc_name:
                    if sub and not s.get("googleSub"):
                        try:
                            sb.table("students").update({"google_sub": sub}).eq("id", s["id"]).execute()
                            s["googleSub"] = sub
                        except Exception:
                            pass
                    return s, cls

    return None, None


@app.post("/auth/google/verify")
def google_verify(body: GoogleVerifyBody):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google auth not configured on server.")
    try:
        info = id_token.verify_oauth2_token(
            body.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    try:
        if body.classId:
            classes_to_search = _get_roster({body.classId})
        elif body.code:
            code = body.code.strip().upper()
            t_res = sb.table("saved_tests").select("id").eq("code", code).execute()
            if not t_res.data:
                raise HTTPException(404, "Test code not found.")
            test_id = t_res.data[0]["id"]
            class_ids = _get_test_class_ids(test_id)
            classes_to_search = _get_roster(set(class_ids)) if class_ids else []
        else:
            raise HTTPException(400, "Provide code or classId.")

        student, cls = _match_student_db(info, classes_to_search)
        if student:
            return {"ok": True, "student": student, "cls": {"id": cls["id"], "name": cls["name"]}}
        raise HTTPException(403, "Your Google account is not on the class roster. Check with your teacher.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/auth/google/drill")
def google_drill_auth(body: GoogleVerifyBody):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google auth not configured on server.")
    try:
        info = id_token.verify_oauth2_token(
            body.token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    try:
        roster = _get_roster()
        student, cls = _match_student_db(info, roster)
        if student:
            return {"ok": True, "student": student, "cls": {
                "id": cls["id"], "name": cls["name"],
                "hideTimer": cls.get("hideTimer", True),
                "drillDuration": cls.get("drillDuration", 180),
            }}

        # Allow teachers to drill
        email = (info.get("email") or "").lower().strip()
        t_res = sb.table("teachers").select("*").ilike("email", email).execute()
        if t_res.data:
            t = t_res.data[0]
            fake_student = {"id": t["id"], "name": t["name"]}
            first_cls = roster[0] if roster else {"id": "demo", "name": "Demo"}
            return {"ok": True, "student": fake_student, "cls": {"id": first_cls["id"], "name": first_cls.get("name", "Demo")}}

        raise HTTPException(403, "Your Google account is not on a class roster. Ask your teacher to add you.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Fluency Drills ─────────────────────────────────────────

class FluencySession(BaseModel):
    studentId:     str
    studentName:   str
    classId:       Optional[str] = ""
    className:     Optional[str] = ""
    testCode:      Optional[str] = ""
    levels:        dict
    log:           List[Any]
    submitted:     Optional[str] = ""
    stars:         Optional[int] = 0
    drillDuration: Optional[int] = 180


def _get_fluency_progress(student_id: str) -> dict:
    try:
        res = sb.table("fluency_progress").select("*").eq("student_id", student_id).execute()
        return res.data[0] if res.data else {}
    except Exception:
        return {}


@app.get("/fluency/progress/{student_id}")
def get_fluency_progress(student_id: str):
    try:
        d = _get_fluency_progress(student_id)
        sess_res = sb.table("fluency_sessions").select("*").eq("student_id", student_id).order("created_at", desc=False).execute()
        sess_rows = sess_res.data or []
        sessions_out = [
            {
                "levels":    {"add": r.get("level_add", 1), "sub": r.get("level_sub", 1),
                              "mul": r.get("level_mul", 1), "div": r.get("level_div", 1)},
                "pct":       r.get("pct", 0),
                "ppm":       r.get("ppm"),
                "stars":     r.get("stars"),
                "ops":       r.get("ops"),
                "submitted": r.get("submitted", ""),
            }
            for r in sess_rows[-20:]
        ]
        return {
            "add":          max(1, min(10, d.get("level_add", 1))),
            "sub":          max(1, min(10, d.get("level_sub", 1))),
            "mul":          max(1, min(10, d.get("level_mul", 1))),
            "div":          max(1, min(10, d.get("level_div", 1))),
            "personalBests": {
                "bestAccuracy": d.get("best_accuracy", 0),
                "bestPPM":      d.get("best_ppm", 0),
                "bestStars":    d.get("best_stars", 0),
            },
            "streakDays":    d.get("streak_days", 0),
            "lastDrillDate": d.get("last_drill_date", ""),
            "sessions":      sessions_out,
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.post("/fluency/session")
def save_fluency_session(session: FluencySession):
    sid = session.studentId
    if not sid:
        raise HTTPException(400, "studentId required")

    try:
        d = _get_fluency_progress(sid)
        # Update levels ±1
        new_levels = {}
        for op in ("add", "sub", "mul", "div"):
            db_key = f"level_{op}"
            if op in session.levels:
                stored    = d.get(db_key, 1)
                requested = max(1, min(10, int(session.levels[op])))
                if abs(requested - stored) <= 1:
                    new_levels[db_key] = requested
                else:
                    new_levels[db_key] = min(10, stored + 1) if requested > stored else max(1, stored - 1)
            else:
                new_levels[db_key] = d.get(db_key, 1)

        # Per-op breakdown
        ops = {op: {"total": 0, "correct": 0} for op in ("add", "sub", "mul", "div")}
        for entry in session.log:
            op = entry.get("op", "")
            if op in ops:
                ops[op]["total"] += 1
                if entry.get("correct"):
                    ops[op]["correct"] += 1
        for op, data in ops.items():
            data["pct"] = round(data["correct"] / data["total"] * 100) if data["total"] else None

        total   = len(session.log)
        correct = sum(1 for e in session.log if e.get("correct"))
        pct     = round(correct / total * 100) if total else 0
        drill_mins = max(1, (session.drillDuration or 180)) / 60
        ppm = round(total / drill_mins, 1)
        stars = max(1, min(5, int(session.stars or 0))) if session.stars else (
            5 if pct >= 90 else 4 if pct >= 75 else 3 if pct >= 60 else 2 if pct >= 40 else 1
        )

        import datetime as _dt
        today_str     = _dt.date.today().isoformat()
        yesterday_str = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        last_date     = d.get("last_drill_date", "")
        if last_date == today_str:
            streak = d.get("streak_days", 1)
        elif last_date == yesterday_str:
            streak = d.get("streak_days", 0) + 1
        else:
            streak = 1

        # Personal bests
        new_best_accuracy = pct   > d.get("best_accuracy", 0)
        new_best_ppm      = ppm   > d.get("best_ppm", 0)
        new_best_stars    = stars > d.get("best_stars", 0)

        progress_row = {
            "student_id":      sid,
            "level_add":       new_levels["level_add"],
            "level_sub":       new_levels["level_sub"],
            "level_mul":       new_levels["level_mul"],
            "level_div":       new_levels["level_div"],
            "best_accuracy":   pct   if new_best_accuracy else d.get("best_accuracy", 0),
            "best_ppm":        ppm   if new_best_ppm      else d.get("best_ppm", 0),
            "best_stars":      stars if new_best_stars     else d.get("best_stars", 0),
            "streak_days":     streak,
            "last_drill_date": today_str,
        }
        sb.table("fluency_progress").upsert(progress_row, on_conflict="student_id").execute()

        # Save fluency session row — separate try/except so a column mismatch
        # never silently swallows the progress upsert or fails the whole endpoint
        submitted_str = session.submitted or time.strftime("%b %d, %Y %I:%M %p")
        fs_row = {
            "student_id":   sid,
            "student_name": session.studentName,
            "class_id":     session.classId or "",
            "class_name":   session.className or "",
            "test_code":    session.testCode or "",
            "submitted":    submitted_str,
            "level_add":    new_levels["level_add"],
            "level_sub":    new_levels["level_sub"],
            "level_mul":    new_levels["level_mul"],
            "level_div":    new_levels["level_div"],
            "total":        total,
            "correct":      correct,
            "pct":          pct,
            "ppm":          ppm,
            "stars":        stars,
            "ops":          json.dumps(ops) if ops else None,  # TEXT-safe serialization
        }
        try:
            sb.table("fluency_sessions").insert(fs_row).execute()
        except Exception as fs_err:
            print(f"⚠ fluency_sessions insert failed: {fs_err}")
            # Retry without ops in case the column type is incompatible
            try:
                sb.table("fluency_sessions").insert({k: v for k, v in fs_row.items() if k != "ops"}).execute()
            except Exception as fs_err2:
                print(f"⚠ fluency_sessions insert also failed without ops: {fs_err2}")

        return {
            "ok":             True,
            "newBestAccuracy": new_best_accuracy,
            "newBestPPM":     new_best_ppm,
            "pct":            pct,
            "ppm":            ppm,
            "stars":          stars,
            "streak":         streak,
        }
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/fluency/class/{cid}/report")
def get_fluency_class_report(cid: str):
    try:
        cls_res = sb.table("classes").select("*").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        stu_res = sb.table("students").select("*").eq("class_id", cid).execute()
        students = stu_res.data or []

        result = []
        for student in students:
            s_id = student["id"]
            d    = _get_fluency_progress(s_id)
            sess_res = sb.table("fluency_sessions").select("*").eq("student_id", s_id).order("created_at").execute()
            sess = sess_res.data or []
            pcts = [s.get("pct", 0) for s in sess if s.get("pct") is not None]
            avg_accuracy = round(sum(pcts) / len(pcts)) if pcts else 0
            trend = "stable"
            if len(pcts) >= 6:
                recent = sum(pcts[-3:]) / 3
                prior  = sum(pcts[-6:-3]) / 3
                if recent > prior + 5:   trend = "improving"
                elif recent < prior - 5: trend = "declining"
            elif len(pcts) >= 3:
                recent = sum(pcts[-3:]) / 3
                prior  = sum(pcts[:-3]) / max(1, len(pcts) - 3) if len(pcts) > 3 else pcts[0]
                if recent > prior + 5:   trend = "improving"
                elif recent < prior - 5: trend = "declining"

            op_totals = {op: {"total": 0, "correct": 0} for op in ("add", "sub", "mul", "div")}
            for s in sess:
                raw_ops = _parse_jsonb(s.get("ops"), {})
                for op, data in (raw_ops or {}).items():
                    if op in op_totals:
                        op_totals[op]["total"]   += data.get("total", 0)
                        op_totals[op]["correct"] += data.get("correct", 0)
            op_avgs = {
                op: round(v["correct"] / v["total"] * 100) if v["total"] else None
                for op, v in op_totals.items()
            }
            last_sess = None
            if sess:
                lr = sess[-1]
                last_sess = {
                    "levels":    {"add": lr.get("level_add", 1), "sub": lr.get("level_sub", 1),
                                  "mul": lr.get("level_mul", 1), "div": lr.get("level_div", 1)},
                    "pct":       lr.get("pct", 0),
                    "ppm":       lr.get("ppm"),
                    "stars":     lr.get("stars"),
                    "ops":       lr.get("ops"),
                    "submitted": lr.get("submitted", ""),
                }
            result.append({
                "student":       {"id": s_id, "name": student["name"]},
                "levels": {
                    "add": d.get("level_add", 1), "sub": d.get("level_sub", 1),
                    "mul": d.get("level_mul", 1), "div": d.get("level_div", 1),
                },
                "sessionCount":  len(sess),
                "avgAccuracy":   avg_accuracy,
                "trend":         trend,
                "personalBests": {
                    "bestAccuracy": d.get("best_accuracy", 0),
                    "bestPPM":      d.get("best_ppm", 0),
                    "bestStars":    d.get("best_stars", 0),
                },
                "opAvgs":        op_avgs,
                "streakDays":    d.get("streak_days", 0),
                "lastDrillDate": d.get("last_drill_date", ""),
                "lastSession":   last_sess,
            })
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/fluency/class/{cid}/leaderboard")
def get_fluency_leaderboard(cid: str):
    try:
        cls_res = sb.table("classes").select("id").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        stu_res = sb.table("students").select("id,name").eq("class_id", cid).execute()
        students = stu_res.data or []
        entries = []
        for student in students:
            s_id = student["id"]
            d    = _get_fluency_progress(s_id)
            sess_res = sb.table("fluency_sessions").select("id").eq("student_id", s_id).execute()
            sess_count = len(sess_res.data or [])
            if sess_count > 0:
                best_acc = d.get("best_accuracy", 0)
                best_ppm = d.get("best_ppm", 0)
                levels   = [d.get(f"level_{op}", 1) for op in ("add", "sub", "mul", "div")]
                avg_level = round(sum(levels) / len(levels), 2)
                composite = round(avg_level * best_acc, 1)
                entries.append({
                    "studentName":  student["name"],
                    "bestAccuracy": best_acc,
                    "bestPPM":      best_ppm,
                    "sessionCount": sess_count,
                    "avgLevel":     avg_level,
                    "composite":    composite,
                })
        entries.sort(key=lambda x: x["composite"], reverse=True)
        return entries[:5]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/fluency/student/{student_id}")
def reset_fluency_student(student_id: str):
    try:
        res = sb.table("fluency_progress").select("student_id").eq("student_id", student_id).execute()
        if not res.data:
            raise HTTPException(404, "No fluency data found for this student")
        sb.table("fluency_sessions").delete().eq("student_id", student_id).execute()
        sb.table("fluency_progress").delete().eq("student_id", student_id).execute()
        return {"ok": True, "message": f"Fluency data cleared for student {student_id}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/fluency/class/{cid}")
def reset_fluency_class(cid: str):
    try:
        cls_res = sb.table("classes").select("name").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls_name = cls_res.data[0]["name"]
        stu_res  = sb.table("students").select("id").eq("class_id", cid).execute()
        student_ids = [r["id"] for r in (stu_res.data or [])]
        count = 0
        for s_id in student_ids:
            prog = sb.table("fluency_progress").select("student_id").eq("student_id", s_id).execute()
            if prog.data:
                sb.table("fluency_sessions").delete().eq("student_id", s_id).execute()
                sb.table("fluency_progress").delete().eq("student_id", s_id).execute()
                count += 1
        return {"ok": True, "message": f"Fluency data cleared for {count} students in {cls_name}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/fluency/all")
def reset_fluency_all():
    try:
        count_res = sb.table("fluency_progress").select("student_id").execute()
        count = len(count_res.data or [])
        sb.table("fluency_sessions").delete().neq("id", 0).execute()
        sb.table("fluency_progress").delete().neq("student_id", "").execute()
        return {"ok": True, "message": f"All fluency data cleared ({count} students)"}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Student Diagnostic ─────────────────────────────────────
@app.get("/sessions/student/{student_id}/diagnosis")
def get_student_diagnosis(student_id: str):
    import statistics
    try:
        sess_res = sb.table("test_sessions").select("*").eq("student_id", student_id).eq("mode", "test").execute()
        student_sessions = [_db_session_to_api(r) for r in (sess_res.data or [])]
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    if not student_sessions:
        return {
            "studentId": student_id, "sessionCount": 0, "diagnosis": "no_data",
            "label": "No Data", "skillSignals": {}, "engagementSignals": {},
            "standardMastery": {}, "weakestStandards": [],
            "recommendedAction": "No test sessions found for this student.",
        }

    std_map = {}
    dok_map = {}
    for sess in student_sessions:
        for qt in (sess.get("questionTimes") or []):
            std = qt.get("standard", "")
            dok = qt.get("dok")
            correct = qt.get("correct", False)
            if std:
                if std not in std_map:
                    std_map[std] = {"attempts": 0, "correct": 0}
                std_map[std]["attempts"] += 1
                if correct: std_map[std]["correct"] += 1
            if dok:
                k = str(dok)
                if k not in dok_map:
                    dok_map[k] = {"attempts": 0, "correct": 0}
                dok_map[k]["attempts"] += 1
                if correct: dok_map[k]["correct"] += 1

    standard_mastery = {
        std: {"attempts": v["attempts"], "correct": v["correct"],
              "pct": round(v["correct"] / v["attempts"] * 100) if v["attempts"] else 0}
        for std, v in std_map.items()
    }
    dok_mastery = {
        k: round(v["correct"] / v["attempts"] * 100) if v["attempts"] else 0
        for k, v in dok_map.items()
    }
    weakest = sorted(
        [(std, d) for std, d in standard_mastery.items() if d["attempts"] >= 2],
        key=lambda x: x[1]["pct"]
    )[:5]

    scores = [s.get("pct", 0) for s in student_sessions]
    avg_test_score = round(sum(scores) / len(scores)) if scores else 0
    score_variance = round(statistics.stdev(scores)) if len(scores) >= 2 else 0

    total_wrong = sum(v["attempts"] - v["correct"] for v in standard_mastery.values())
    top3_wrong  = sum((d["attempts"] - d["correct"]) for _, d in weakest[:3]) if weakest else 0
    clustered_pct = round(top3_wrong / total_wrong * 100) if total_wrong > 0 else 0

    dok1_pct = dok_mastery.get("1")
    dok3_pct = dok_mastery.get("3")
    dok_drop = (dok1_pct - dok3_pct) if (dok1_pct is not None and dok3_pct is not None) else None

    all_times = []
    total_violations = 0
    total_skipped = 0
    total_questions = 0
    for sess in student_sessions:
        total_violations += sess.get("violations", 0) or 0
        qt_list = sess.get("questionTimes") or []
        for qt in qt_list:
            total_questions += 1
            t = qt.get("timeSecs", 0) or 0
            all_times.append(t)
            if t == 0 and not qt.get("correct"):
                total_skipped += 1

    avg_time_per_q = round(sum(all_times) / len(all_times), 1) if all_times else None
    fast_pct       = round(sum(1 for t in all_times if t < 5) / len(all_times) * 100) if all_times else 0
    skip_pct       = round(total_skipped / total_questions * 100) if total_questions else 0

    # Fluency data from DB
    avg_fluency = None
    fluency_levels = {"add": 1, "sub": 1, "mul": 1, "div": 1}
    try:
        fp = _get_fluency_progress(student_id)
        if fp:
            fluency_levels = {
                "add": fp.get("level_add", 1), "sub": fp.get("level_sub", 1),
                "mul": fp.get("level_mul", 1), "div": fp.get("level_div", 1),
            }
        fs_res = sb.table("fluency_sessions").select("pct").eq("student_id", student_id).order("created_at", desc=True).limit(10).execute()
        fluency_pcts = [r.get("pct", 0) for r in (fs_res.data or []) if r.get("pct") is not None]
        avg_fluency = round(sum(fluency_pcts) / len(fluency_pcts)) if fluency_pcts else None
    except Exception:
        pass
    fluency_gap = (avg_fluency - avg_test_score) if avg_fluency is not None else None

    skill_score = engagement_score = 0
    if avg_test_score < 60:   skill_score += 2
    elif avg_test_score < 75: skill_score += 1
    if clustered_pct >= 60:   skill_score += 2
    if dok_drop is not None and dok_drop > 25: skill_score += 1

    if avg_time_per_q is not None and avg_time_per_q < 10: engagement_score += 2
    if fast_pct > 30:          engagement_score += 2
    if total_violations > 3:   engagement_score += 1
    if skip_pct > 20:          engagement_score += 1
    if fluency_gap is not None and fluency_gap > 20: engagement_score += 2

    if avg_test_score >= 80:
        diagnosis, label = "on_track", "On Track"
    elif engagement_score >= 4 and skill_score <= 1:
        diagnosis, label = "engagement", "Engagement Concern"
    elif skill_score >= 3 and engagement_score <= 1:
        diagnosis, label = "skill_gap", "Skill Gap"
    elif skill_score >= 2 or engagement_score >= 2:
        diagnosis, label = "mixed", "Mixed — Skill & Engagement"
    else:
        diagnosis, label = "watch", "Monitor"

    if diagnosis == "on_track":
        action = "Student is performing well. Continue current approach."
    elif diagnosis == "engagement":
        action = "Student shows capability (good fluency scores) but is rushing or disengaged during tests. Consider a conversation about effort and test strategy."
    elif diagnosis == "skill_gap":
        top_stds = ", ".join(std for std, _ in weakest[:3]) if weakest else "unknown standards"
        action = f"Student has genuine skill gaps, particularly in: {top_stds}. Reteach these standards with targeted practice."
    elif diagnosis == "mixed":
        action = "Student has both skill gaps and engagement issues. Address both: targeted reteaching for weak standards AND a conversation about effort."
    else:
        action = "Insufficient data to make a strong diagnosis. Continue monitoring."

    return {
        "studentId":       student_id,
        "sessionCount":    len(student_sessions),
        "avgTestScore":    avg_test_score,
        "scoreVariance":   score_variance,
        "diagnosis":       diagnosis,
        "label":           label,
        "skillScore":      skill_score,
        "engagementScore": engagement_score,
        "skillSignals": {
            "clusteredFailurePct": clustered_pct,
            "dokMastery":          dok_mastery,
            "dokDrop":             dok_drop,
            "avgTestScore":        avg_test_score,
        },
        "engagementSignals": {
            "avgTimePerQuestion": avg_time_per_q,
            "fastAnswerPct":      fast_pct,
            "totalViolations":    total_violations,
            "skipPct":            skip_pct,
            "avgFluencyScore":    avg_fluency,
            "fluencyTestGap":     fluency_gap,
        },
        "standardMastery":  standard_mastery,
        "dokMastery":       dok_mastery,
        "weakestStandards": [{"standard": std, **d} for std, d in weakest],
        "sessions": [
            {
                "submitted":  s.get("submitted", ""),
                "testTitle":  s.get("testTitle", s.get("testCode", "")),
                "testCode":   s.get("testCode", ""),
                "pct":        s.get("pct", 0),
                "score":      s.get("score", 0),
                "total":      s.get("total", 0),
                "timeUsed":   s.get("timeUsed", ""),
                "violations": s.get("violations", 0),
            }
            for s in sorted(student_sessions, key=lambda x: x.get("submitted", ""))
        ],
        "fluencyLevels":     fluency_levels,
        "recommendedAction": action,
    }


# ── Parent Report ──────────────────────────────────────────
@app.get("/fluency/report/{student_id}")
def get_parent_report(student_id: str):
    try:
        d = _get_fluency_progress(student_id)
        if not d:
            raise HTTPException(404, "No fluency data found for this student")
        sess_res = sb.table("fluency_sessions").select("*").eq("student_id", student_id).order("created_at").execute()
        sess = sess_res.data or []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")

    pb = {
        "bestAccuracy": d.get("best_accuracy", 0),
        "bestPPM":      d.get("best_ppm", 0),
        "bestStars":    d.get("best_stars", 0),
    }
    recent_sessions = [
        {
            "submitted": s.get("submitted", ""),
            "pct":       s.get("pct", 0),
            "ppm":       s.get("ppm"),
            "stars":     s.get("stars"),
            "levels":    {"add": s.get("level_add", 1), "sub": s.get("level_sub", 1),
                          "mul": s.get("level_mul", 1), "div": s.get("level_div", 1)},
            "ops":       s.get("ops"),
        }
        for s in sess[-10:]
    ]
    pcts     = [s.get("pct", 0) for s in sess if s.get("pct") is not None]
    avg_accuracy = round(sum(pcts) / len(pcts)) if pcts else 0
    ppms     = [s["ppm"] for s in sess if s.get("ppm") is not None]
    avg_ppm  = round(sum(ppms) / len(ppms), 1) if ppms else None
    total_stars = sum(s.get("stars", 0) for s in sess)

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

    import datetime as _dt

    def _parse(raw):
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p", "%b %d, %Y"):
            try:
                return _dt.datetime.strptime(raw.strip(), fmt).date()
            except Exception:
                pass
        for fmt in ("%I:%M %p", "%I:%M%p"):
            try:
                _dt.datetime.strptime(raw.strip(), fmt)
                return _dt.date.today()
            except Exception:
                pass
        return None

    sessions_per_week = None
    dated_sessions = [s for s in sess if s.get("submitted", "")]
    if dated_sessions:
        try:
            four_weeks_ago = _dt.date.today() - _dt.timedelta(weeks=4)
            recent_count = sum(
                1 for s in dated_sessions
                if _parse(s.get("submitted", "")) and _parse(s.get("submitted", "")) >= four_weeks_ago
            )
            sessions_per_week = round(recent_count / 4, 1)
        except Exception:
            pass

    _LEVEL_DESC = {
        "add": ["Add within 5","Add within 10","Add within 20 (single digits)",
                "2-digit + 1-digit, within 100","2-digit + 2-digit, within 100",
                "3-digit + 2-digit, within 1,000","3-digit + 3-digit, within 1,000",
                "4-digit + 3-digit, within 10,000","5-digit + 4-digit, within 100,000",
                "Add through hundred-thousands"],
        "sub": ["Subtract within 5","Subtract within 10","Subtract within 20",
                "2-digit − 1-digit, within 100","2-digit − 2-digit, within 100",
                "3-digit − 2-digit, within 1,000","3-digit − 3-digit, within 1,000",
                "4-digit − 3-digit, within 10,000","5-digit − 4-digit, within 100,000",
                "Subtract through hundred-thousands"],
        "mul": ["Equal groups / arrays to 5×5","× 0 and × 1","× 2, × 5, × 10",
                "× 3 and × 4","× 6 and × 7","× 8 and × 9 (within 100)",
                "× multiples of 10","2-digit × 1-digit","2-digit × 2-digit","3-digit × 2-digit"],
        "div": ["÷ 1 and ÷ 2, within 100","÷ 3 and ÷ 4, within 100","÷ 5 and ÷ 6, within 100",
                "÷ 7, ÷ 8, ÷ 9, within 100","÷ multiples of 10","2-digit ÷ 1-digit",
                "3-digit ÷ 1-digit","4-digit ÷ 1-digit","÷ 2-digit, 2–3-digit dividend",
                "÷ 2-digit, up to 4-digit"],
    }
    current_levels = {
        "add": d.get("level_add", 1), "sub": d.get("level_sub", 1),
        "mul": d.get("level_mul", 1), "div": d.get("level_div", 1),
    }
    grade_context = {}
    for op, lvl in current_levels.items():
        descs = _LEVEL_DESC.get(op, [])
        idx = max(0, min(lvl - 1, len(descs) - 1))
        grade_context[op] = descs[idx] if descs else f"Level {lvl}"

    student_name = ""
    class_name   = ""
    if sess:
        student_name = sess[-1].get("student_name", "")
        class_name   = sess[-1].get("class_name", "")

    days_this_week = 0
    try:
        today = _dt.date.today()
        start_of_week = today - _dt.timedelta(days=today.weekday())
        week_dates = set()
        for s in sess:
            d_parsed = _parse(s.get("submitted", ""))
            if d_parsed and d_parsed >= start_of_week:
                week_dates.add(d_parsed)
        days_this_week = len(week_dates)
    except Exception:
        pass

    action_item = None
    practiced_ops = {op: v for op, v in op_avgs.items() if v is not None}
    if practiced_ops:
        weakest_op = min(practiced_ops, key=lambda k: practiced_ops[k])
        if practiced_ops[weakest_op] < 85:
            op_name = {"add": "addition", "sub": "subtraction", "mul": "multiplication", "div": "division"}[weakest_op]
            action_item = f"Practice {op_name} facts at home — flashcards, games, or another MathReady drill session."
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
        "streakDays":      d.get("streak_days", 0),
        "lastDrillDate":   d.get("last_drill_date", ""),
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
    try:
        cls_res = sb.table("classes").select("id,name").eq("id", cid).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls_name = cls_res.data[0]["name"]
        stu_res  = sb.table("students").select("id").eq("class_id", cid).execute()
        reports  = []
        for st in (stu_res.data or []):
            try:
                report = get_parent_report(st["id"])
                reports.append(report)
            except Exception:
                pass
        return {"className": cls_name, "reports": reports}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


# ── Test Assignments ───────────────────────────────────────
@app.post("/assignments")
def create_assignment(body: TestAssignmentBody):
    try:
        t_res = sb.table("saved_tests").select("id,code,name,title").eq("id", body.testId).execute()
        if not t_res.data:
            raise HTTPException(404, "Saved test not found")
        test = t_res.data[0]

        cls_res = sb.table("classes").select("id,name").eq("id", body.classId).execute()
        if not cls_res.data:
            raise HTTPException(404, "Class not found")
        cls = cls_res.data[0]

        import datetime as _dt
        aid = "a" + uuid.uuid4().hex[:8]
        row = {
            "id":              aid,
            "test_id":         body.testId,
            "test_code":       test.get("code", ""),
            "test_title":      test.get("name", test.get("title", "Test")),
            "class_id":        body.classId,
            "class_name":      cls["name"],
            "created_by":      body.createdBy,
            "created_by_name": body.createdByName,
            "created_at":      _dt.datetime.now().isoformat(),
        }
        sb.table("test_assignments").insert(row).execute()

        if body.studentIds:
            student_rows = [{"assignment_id": aid, "student_id": sid, "completed": False}
                            for sid in body.studentIds]
            sb.table("assignment_students").insert(student_rows).execute()

        return {
            "ok": True,
            "id": aid,
            "assignment": {
                **row,
                "testId":        body.testId,
                "testCode":      test.get("code", ""),
                "testTitle":     test.get("name", test.get("title", "Test")),
                "classId":       body.classId,
                "className":     cls["name"],
                "studentIds":    body.studentIds,
                "completedIds":  [],
                "createdBy":     body.createdBy,
                "createdByName": body.createdByName,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


def _assignment_full(row: dict) -> dict:
    aid = row["id"]
    try:
        as_res = sb.table("assignment_students").select("student_id,completed").eq("assignment_id", aid).execute()
        as_rows = as_res.data or []
        student_ids   = [r["student_id"] for r in as_rows]
        completed_ids = [r["student_id"] for r in as_rows if r.get("completed")]
    except Exception:
        student_ids = completed_ids = []
    return {
        "id":              aid,
        "testId":          row.get("test_id", ""),
        "testCode":        row.get("test_code", ""),
        "testTitle":       row.get("test_title", ""),
        "classId":         row.get("class_id", ""),
        "className":       row.get("class_name", ""),
        "createdBy":       row.get("created_by", ""),
        "createdByName":   row.get("created_by_name", ""),
        "createdAt":       row.get("created_at", ""),
        "studentIds":      student_ids,
        "completedIds":    completed_ids,
        "totalStudents":   len(student_ids),
        "completedCount":  len(completed_ids),
    }


@app.get("/assignments")
def list_assignments(classIds: Optional[str] = None):
    try:
        q = sb.table("test_assignments").select("*")
        if classIds:
            ids = [i.strip() for i in classIds.split(",") if i.strip()]
            if ids:
                q = q.in_("class_id", ids)
        res = q.execute()
        return [_assignment_full(r) for r in (res.data or [])]
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.get("/assignments/student/{student_id}")
def get_student_assignment(student_id: str):
    try:
        as_res = sb.table("assignment_students").select("assignment_id,completed").eq("student_id", student_id).eq("completed", False).execute()
        active = []
        for row in (as_res.data or []):
            aid = row["assignment_id"]
            ta_res = sb.table("test_assignments").select("*").eq("id", aid).execute()
            if not ta_res.data:
                continue
            ta = ta_res.data[0]
            t_res = sb.table("saved_tests").select("*").eq("id", ta["test_id"]).execute()
            if not t_res.data:
                continue
            test = t_res.data[0]
            questions = _get_test_questions(ta["test_id"])
            active.append({
                "assignmentId":  aid,
                "testId":        ta["test_id"],
                "testCode":      ta.get("test_code", ""),
                "testTitle":     ta.get("test_title", "Test"),
                "className":     ta.get("class_name", ""),
                "classId":       ta.get("class_id", ""),
                "questions":     questions,
                "adaptive":      test.get("adaptive", False),
                "untimed":       test.get("untimed", False),
                "timeLimitSecs": test.get("time_limit_secs", 1800),
                "warnSecs":      test.get("warn_secs", 300),
                "oneAttempt":    test.get("one_attempt", False),
            })
        return {"assignments": active}
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.patch("/assignments/{aid}/students")
def update_assignment_students(aid: str, body: dict):
    try:
        res = sb.table("test_assignments").select("id").eq("id", aid).execute()
        if not res.data:
            raise HTTPException(404, "Assignment not found")
        sb.table("assignment_students").delete().eq("assignment_id", aid).execute()
        new_ids = body.get("studentIds", [])
        if new_ids:
            rows = [{"assignment_id": aid, "student_id": sid, "completed": False} for sid in new_ids]
            sb.table("assignment_students").insert(rows).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.patch("/assignments/{aid}/complete")
def complete_assignment(aid: str, body: dict):
    try:
        res = sb.table("test_assignments").select("id").eq("id", aid).execute()
        if not res.data:
            raise HTTPException(404, "Assignment not found")
        sid = body.get("studentId", "")
        if sid:
            sb.table("assignment_students").update({"completed": True}).eq("assignment_id", aid).eq("student_id", sid).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.patch("/assignments/{aid}/reopen")
def reopen_assignment(aid: str, body: dict):
    try:
        res = sb.table("test_assignments").select("id").eq("id", aid).execute()
        if not res.data:
            raise HTTPException(404, "Assignment not found")
        sid = body.get("studentId", "")
        if sid:
            sb.table("assignment_students").update({"completed": False}).eq("assignment_id", aid).eq("student_id", sid).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


@app.delete("/assignments/{aid}")
def delete_assignment(aid: str):
    try:
        res = sb.table("test_assignments").select("id").eq("id", aid).execute()
        if not res.data:
            raise HTTPException(404, "Assignment not found")
        sb.table("assignment_students").delete().eq("assignment_id", aid).execute()
        sb.table("test_assignments").delete().eq("id", aid).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"DB error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    reload = port == 8001  # only reload locally
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload,
                reload_dirs=[os.path.dirname(os.path.abspath(__file__))])
