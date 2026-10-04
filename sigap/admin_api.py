"""Admin-panel API (/api/admin). Session cookie auth with roles:

    admin     everything, including users, calendar and handbook
    staff     handoff inbox, students and invites, dashboard, test chat; read-only data
    lecturer  schedule of their own courses, test chat

CSRF: the session cookie is SameSite=Strict and every state-changing request must carry the
X-Sigap-Request header, which a cross-site form or image cannot send.
"""
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from . import channels, sync
from .chat import Who, chat_service
from .config import WIB
from .db import get_session
from .models import (ApiToken, CalendarEntry, ChannelAccount, Course, Document, Enrollment, Handoff, HandoffEvent,
                     LecturerCourse, MessageLog, ScheduleItem, Student, User, utcnow)
from .security import ROLES, as_utc, authenticate, create_session, end_session, hash_password, user_for_session
from .services import create_link_code, validate_handbook

router = APIRouter(prefix="/api/admin")
COOKIE = "sigap_session"
_attempts: dict[str, deque] = defaultdict(deque)


# ---------------------------------------------------------------- auth helpers

def current_user(request: Request, db: Session = Depends(get_session)) -> User:
    user = user_for_session(db, request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not signed in")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-sigap-request") != "1":
        raise HTTPException(403, "Missing X-Sigap-Request header")
    return user


def require(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Your role cannot do this")
        return user
    return dep


ANY = require("admin", "staff", "lecturer")
STAFF = require("admin", "staff")
ADMIN = require("admin")


def lecturer_courses(db: Session, user: User) -> set[str] | None:
    """None = all courses (admin/staff)."""
    if user.role != "lecturer":
        return None
    return set(db.exec(select(LecturerCourse.course_code).where(LecturerCourse.user_id == user.id)).all())


def _sync_later(bg: BackgroundTasks):
    bg.add_task(sync.push_reference_data)


def _user_out(db: Session, u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active,
            "courses": sorted(db.exec(select(LecturerCourse.course_code).where(LecturerCourse.user_id == u.id)).all())}


# ---------------------------------------------------------------- session

class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_session)):
    key = f"{request.client.host if request.client else '?'}:{body.email.lower()}"
    window, now = _attempts[key], time.time()
    while window and now - window[0] > 300:
        window.popleft()
    if len(window) >= 5:
        raise HTTPException(429, "Too many attempts; wait 5 minutes")
    user = authenticate(db, body.email, body.password)
    if not user:
        window.append(now)
        raise HTTPException(401, "Wrong email or password")
    token = create_session(db, user)
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=12 * 3600, path="/")
    return _user_out(db, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_session)):
    end_session(db, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(ANY), db: Session = Depends(get_session)):
    return _user_out(db, user)


# ---------------------------------------------------------------- handoffs

def _handoff_out(db: Session, h: Handoff, full: bool = False) -> dict:
    st = db.get(Student, h.student_id) if h.student_id else None
    out = {"id": h.id, "reference": h.reference, "status": h.status, "question": h.question, "category": h.category,
           "office": h.office, "language": h.language, "channel": h.channel, "created_at": h.created_at.isoformat(),
           "updated_at": h.updated_at.isoformat(), "assigned_to": h.assigned_to,
           "student": {"id": st.id, "name": st.name, "number": st.student_number} if st else None,
           "can_reply": bool(h.channel_external_id) and channels.available(h.channel)}
    if full:
        events = db.exec(select(HandoffEvent).where(HandoffEvent.handoff_id == h.id).order_by(HandoffEvent.created_at)).all()
        names = {u.id: u.name for u in db.exec(select(User)).all()}
        out["events"] = [{"kind": e.kind, "body": e.body, "delivered": e.delivered, "by": names.get(e.user_id),
                          "at": e.created_at.isoformat()} for e in events]
    return out


@router.get("/handoffs")
def list_handoffs(status: str | None = None, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    q = select(Handoff).order_by(Handoff.created_at.desc())
    if status:
        q = q.where(Handoff.status == status)
    counts = dict(db.exec(select(Handoff.status, func.count()).group_by(Handoff.status)).all())
    return {"items": [_handoff_out(db, h) for h in db.exec(q.limit(200)).all()], "counts": counts}


@router.get("/handoffs/{hid}")
def get_handoff(hid: int, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    h = db.get(Handoff, hid) or _404()
    return _handoff_out(db, h, full=True)


class HandoffPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|in_progress|answered|closed)$")
    assign_to_me: bool | None = None


@router.patch("/handoffs/{hid}")
def patch_handoff(hid: int, body: HandoffPatch, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    h = db.get(Handoff, hid) or _404()
    if body.status and body.status != h.status:
        h.status = body.status
        db.add(HandoffEvent(handoff_id=h.id, user_id=user.id, kind="status", body=body.status))
    if body.assign_to_me:
        h.assigned_to = user.id
    h.updated_at = utcnow()
    db.commit()
    return _handoff_out(db, h, full=True)


class TextIn(BaseModel):
    body: str = Field(min_length=1, max_length=3000)


@router.post("/handoffs/{hid}/notes")
def add_note(hid: int, body: TextIn, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    h = db.get(Handoff, hid) or _404()
    db.add(HandoffEvent(handoff_id=h.id, user_id=user.id, kind="note", body=body.body))
    h.updated_at = utcnow()
    db.commit()
    return _handoff_out(db, h, full=True)


@router.post("/handoffs/{hid}/reply")
async def reply(hid: int, body: TextIn, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    """Sends the reply to the student on the channel they asked from, and marks the handoff answered."""
    h = db.get(Handoff, hid) or _404()
    esc = body.body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    head = (f"💬 Reply from {h.office or 'student services'} about <code>{h.reference}</code>:" if h.language == "en" else
            f"💬 Balasan dari {h.office or 'layanan mahasiswa'} untuk <code>{h.reference}</code>:")
    delivered = bool(h.channel_external_id) and await channels.deliver(h.channel, h.channel_external_id, f"{head}\n\n{esc}")
    db.add(HandoffEvent(handoff_id=h.id, user_id=user.id, kind="reply", body=body.body, delivered=delivered))
    if h.status in ("open", "in_progress"):
        h.status = "answered"
        db.add(HandoffEvent(handoff_id=h.id, user_id=user.id, kind="status", body="answered"))
    h.updated_at = utcnow()
    db.commit()
    return _handoff_out(db, h, full=True) | {"delivered": delivered}


# ---------------------------------------------------------------- students, invites, tokens

def _student_out(db: Session, s: Student, full: bool = False) -> dict:
    accounts = db.exec(select(ChannelAccount).where(ChannelAccount.student_id == s.id)).all()
    out = {"id": s.id, "number": s.student_number, "name": s.name, "email": s.email, "program": s.program,
           "active": s.active, "courses": sorted(db.exec(select(Enrollment.course_code).where(Enrollment.student_id == s.id)).all()),
           "channels": [{"id": a.id, "channel": a.channel, "display_name": a.display_name,
                         "linked_at": a.linked_at.isoformat()} for a in accounts]}
    if full:
        out["tokens"] = [{"id": t.id, "prefix": t.prefix, "label": t.label, "created_at": t.created_at.isoformat(),
                          "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                          "revoked": bool(t.revoked_at)}
                         for t in db.exec(select(ApiToken).where(ApiToken.student_id == s.id).order_by(ApiToken.created_at.desc())).all()]
        out["handoffs"] = [_handoff_out(db, h) for h in db.exec(
            select(Handoff).where(Handoff.student_id == s.id).order_by(Handoff.created_at.desc()).limit(20)).all()]
    return out


@router.get("/students")
def list_students(user: User = Depends(STAFF), db: Session = Depends(get_session)):
    return [_student_out(db, s) for s in db.exec(select(Student).order_by(Student.student_number)).all()]


@router.get("/students/{sid}")
def get_student(sid: int, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    return _student_out(db, db.get(Student, sid) or _404(), full=True)


class StudentIn(BaseModel):
    number: str = Field(min_length=3, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    program: str | None = None
    courses: list[str] = []
    active: bool = True


def _set_courses(db: Session, sid: int, codes: list[str]):
    known = set(db.exec(select(Course.code)).all())
    bad = [c for c in codes if c not in known]
    if bad:
        raise HTTPException(422, f"Unknown course(s): {', '.join(bad)}")
    for e in db.exec(select(Enrollment).where(Enrollment.student_id == sid)).all():
        db.delete(e)
    for c in sorted(set(codes)):
        db.add(Enrollment(student_id=sid, course_code=c))


@router.post("/students")
def create_student(body: StudentIn, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    if db.exec(select(Student).where(Student.student_number == body.number)).first():
        raise HTTPException(409, "Student number already exists")
    s = Student(student_number=body.number, name=body.name, email=body.email, program=body.program, active=body.active)
    db.add(s)
    db.commit()
    db.refresh(s)
    _set_courses(db, s.id, body.courses)
    db.commit()
    return _student_out(db, s, full=True)


@router.patch("/students/{sid}")
def update_student(sid: int, body: StudentIn, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    s = db.get(Student, sid) or _404()
    s.student_number, s.name, s.email, s.program, s.active = body.number, body.name, body.email, body.program, body.active
    _set_courses(db, s.id, body.courses)
    db.commit()
    return _student_out(db, s, full=True)


@router.post("/students/{sid}/invite")
def invite(sid: int, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    import os
    s = db.get(Student, sid) or _404()
    code, expires = create_link_code(db, s.id, user.id)
    username = os.environ.get("TELEGRAM_BOT_USERNAME")
    return {"code": code, "expires_at": expires.isoformat(),
            "telegram_link": f"https://t.me/{username}?start={code}" if username else None,
            "instructions": f"Send this to {s.name}. One use, valid 7 days."}


@router.delete("/students/{sid}/channels/{cid}")
def remove_channel(sid: int, cid: int, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    acc = db.get(ChannelAccount, cid)
    if not acc or acc.student_id != sid:
        _404()
    chat_service.reset(Who(acc.channel, acc.external_id))
    db.delete(acc)
    db.commit()
    return {"ok": True}


@router.delete("/tokens/{tid}")
def revoke_token(tid: int, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    t = db.get(ApiToken, tid) or _404()
    t.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- courses and schedule

class CourseIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z]{2}\d{4}$")
    name_id: str
    name_en: str
    lecturer: str = ""
    aliases: str = ""


@router.get("/courses")
def list_courses(user: User = Depends(ANY), db: Session = Depends(get_session)):
    mine = lecturer_courses(db, user)
    return [c.model_dump() | {"editable": mine is None or c.code in mine}
            for c in db.exec(select(Course).order_by(Course.code)).all()]


@router.post("/courses")
def upsert_course(body: CourseIn, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    c = db.get(Course, body.code) or Course(code=body.code, name_id="", name_en="")
    c.name_id, c.name_en, c.lecturer, c.aliases = body.name_id, body.name_en, body.lecturer, body.aliases
    db.add(c)
    db.commit()
    _sync_later(bg)
    return c


TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class ItemIn(BaseModel):
    item_id: str = Field(pattern=r"^[A-Za-z0-9-]{2,30}$")
    course_code: str
    title_id: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    type: str = Field(pattern="^(assignment|quiz|project|exam)$")
    due_date: date
    due_time: str = Field(pattern=TIME_RE)
    end_time: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    where: str = ""
    source: str = ""


def _check_course(db: Session, user: User, code: str):
    if not db.get(Course, code):
        raise HTTPException(422, "Unknown course")
    mine = lecturer_courses(db, user)
    if mine is not None and code not in mine:
        raise HTTPException(403, "You can only edit your own courses")


@router.get("/schedule")
def list_schedule(course: str | None = None, user: User = Depends(ANY), db: Session = Depends(get_session)):
    q = select(ScheduleItem).order_by(ScheduleItem.due_date, ScheduleItem.due_time)
    mine = lecturer_courses(db, user)
    if course:
        q = q.where(ScheduleItem.course_code == course)
    if mine is not None:
        q = q.where(ScheduleItem.course_code.in_(mine))
    return db.exec(q).all()


@router.post("/schedule")
def create_item(body: ItemIn, bg: BackgroundTasks, user: User = Depends(ANY), db: Session = Depends(get_session)):
    _check_course(db, user, body.course_code)
    if db.get(ScheduleItem, body.item_id):
        raise HTTPException(409, "item_id already exists")
    item = ScheduleItem(**body.model_dump(), updated_by=user.id)
    db.add(item)
    db.commit()
    _sync_later(bg)
    return item


@router.patch("/schedule/{item_id}")
def update_item(item_id: str, body: ItemIn, bg: BackgroundTasks, user: User = Depends(ANY), db: Session = Depends(get_session)):
    item = db.get(ScheduleItem, item_id) or _404()
    _check_course(db, user, item.course_code)
    _check_course(db, user, body.course_code)
    for k, v in body.model_dump(exclude={"item_id"}).items():
        setattr(item, k, v)
    item.updated_at, item.updated_by = utcnow(), user.id
    db.commit()
    _sync_later(bg)
    return item


@router.delete("/schedule/{item_id}")
def delete_item(item_id: str, bg: BackgroundTasks, user: User = Depends(ANY), db: Session = Depends(get_session)):
    item = db.get(ScheduleItem, item_id) or _404()
    _check_course(db, user, item.course_code)
    db.delete(item)
    db.commit()
    _sync_later(bg)
    return {"ok": True}


# ---------------------------------------------------------------- academic calendar

class CalendarIn(BaseModel):
    kind: str = Field(pattern="^(term|period|window)$")
    key: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    name_id: str
    name_en: str
    start: date
    end: date
    note_id: str = ""
    note_en: str = ""


@router.get("/calendar")
def list_calendar(user: User = Depends(ANY), db: Session = Depends(get_session)):
    return db.exec(select(CalendarEntry).order_by(CalendarEntry.start, CalendarEntry.id)).all()


def _check_dates(body: CalendarIn):
    if body.end < body.start:
        raise HTTPException(422, "End date is before start date")


@router.post("/calendar")
def create_calendar(body: CalendarIn, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    _check_dates(body)
    e = CalendarEntry(**body.model_dump())
    db.add(e)
    db.commit()
    _sync_later(bg)
    return e


@router.patch("/calendar/{eid}")
def update_calendar(eid: int, body: CalendarIn, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    _check_dates(body)
    e = db.get(CalendarEntry, eid) or _404()
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    db.commit()
    _sync_later(bg)
    return e


@router.delete("/calendar/{eid}")
def delete_calendar(eid: int, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    db.delete(db.get(CalendarEntry, eid) or _404())
    db.commit()
    _sync_later(bg)
    return {"ok": True}


# ---------------------------------------------------------------- handbook versions

@router.get("/documents")
def list_documents(user: User = Depends(ANY), db: Session = Depends(get_session)):
    docs = db.exec(select(Document).order_by(Document.uploaded_at.desc())).all()
    return [{"id": d.id, "title": d.title, "version": d.version, "effective": d.effective.isoformat(), "active": d.active,
             "uploaded_at": d.uploaded_at.isoformat(), "sections": len(re.findall(r"^## ", d.content, re.M))} for d in docs]


@router.get("/documents/{did}")
def get_document(did: int, user: User = Depends(ANY), db: Session = Depends(get_session)):
    d = db.get(Document, did) or _404()
    return d


class DocumentIn(BaseModel):
    content: str = Field(min_length=20, max_length=500_000)
    activate: bool = True


@router.post("/documents")
def upload_document(body: DocumentIn, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    try:
        info = validate_handbook(body.content)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    meta = info["meta"]
    d = Document(title=meta["title_id"], version=meta["version"], effective=date.fromisoformat(meta["effective"]),
                 content=body.content, uploaded_by=user.id)
    db.add(d)
    db.commit()
    if body.activate:
        _activate(db, d)
        _sync_later(bg)
    return {"id": d.id, "sections": info["sections"], "active": d.active}


def _activate(db: Session, d: Document):
    for other in db.exec(select(Document).where(Document.active == True)).all():  # noqa: E712
        other.active = False
    d.active = True
    db.commit()


@router.post("/documents/{did}/activate")
def activate_document(did: int, bg: BackgroundTasks, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    _activate(db, db.get(Document, did) or _404())
    _sync_later(bg)
    return {"ok": True}


# ---------------------------------------------------------------- users

class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    name: str
    role: str
    courses: list[str] = []
    active: bool = True


@router.get("/users")
def list_users(user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    return [_user_out(db, u) for u in db.exec(select(User).order_by(User.role, User.email)).all()]


def _set_lecturer_courses(db: Session, uid: int, codes: list[str]):
    for lc in db.exec(select(LecturerCourse).where(LecturerCourse.user_id == uid)).all():
        db.delete(lc)
    for c in sorted(set(codes)):
        if not db.get(Course, c):
            raise HTTPException(422, f"Unknown course {c}")
        db.add(LecturerCourse(user_id=uid, course_code=c))


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    if body.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if db.exec(select(User).where(User.email == body.email.lower())).first():
        raise HTTPException(409, "Email already exists")
    password = secrets.token_urlsafe(9)
    u = User(email=body.email.lower(), name=body.name, role=body.role, active=body.active, password_hash=hash_password(password))
    db.add(u)
    db.commit()
    db.refresh(u)
    _set_lecturer_courses(db, u.id, body.courses)
    db.commit()
    return _user_out(db, u) | {"temporary_password": password}


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserIn, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    u = db.get(User, uid) or _404()
    if u.id == user.id and (body.role != "admin" or not body.active):
        raise HTTPException(422, "You cannot remove your own admin access")
    u.email, u.name, u.role, u.active = body.email.lower(), body.name, body.role, body.active
    _set_lecturer_courses(db, u.id, body.courses)
    db.commit()
    return _user_out(db, u)


@router.post("/users/{uid}/reset-password")
def reset_password(uid: int, user: User = Depends(ADMIN), db: Session = Depends(get_session)):
    u = db.get(User, uid) or _404()
    password = secrets.token_urlsafe(9)
    u.password_hash = hash_password(password)
    db.commit()
    return {"temporary_password": password}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=10)


@router.post("/auth/password")
def change_password(body: PasswordIn, user: User = Depends(ANY), db: Session = Depends(get_session)):
    if not authenticate(db, user.email, body.current):
        raise HTTPException(401, "Current password is wrong")
    user.password_hash = hash_password(body.new)
    db.add(user)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- sync, dashboard, test chat

@router.get("/sync")
def sync_status(user: User = Depends(ANY)):
    return sync.status


@router.post("/sync")
def run_sync(user: User = Depends(STAFF)):
    return sync.push_reference_data()


@router.get("/dashboard")
def dashboard(days: int = 14, user: User = Depends(STAFF), db: Session = Depends(get_session)):
    days = max(1, min(days, 90))
    start = (datetime.now(WIB) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # SQLite keeps datetimes as naive UTC, so compare in UTC.
    rows = db.exec(select(MessageLog).where(MessageLog.created_at >= start.astimezone(timezone.utc))).all()
    per_day = {(start + timedelta(days=i)).date().isoformat(): 0 for i in range(days)}
    tools, outcomes, langs, chans, latencies = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int), []
    for m in rows:
        d = as_utc(m.created_at).astimezone(WIB).date().isoformat()
        if d in per_day:
            per_day[d] += 1
        for t in filter(None, m.tools.split(",")):
            tools[t.split(":")[0]] += 1
        outcomes[m.outcome] += 1
        langs[m.language] += 1
        chans[m.channel] += 1
        if m.latency_ms is not None:
            latencies.append(m.latency_ms)
    unanswered = [{"question": m.unanswered_question, "at": m.created_at.isoformat(), "language": m.language}
                  for m in sorted(rows, key=lambda m: m.created_at, reverse=True) if m.unanswered_question][:30]
    linked_students = db.exec(select(func.count(func.distinct(ChannelAccount.student_id)))).one()
    handoff_counts = dict(db.exec(select(Handoff.status, func.count()).group_by(Handoff.status)).all())
    latencies.sort()
    return {
        "days": days, "total": len(rows), "per_day": [{"date": k, "count": v} for k, v in per_day.items()],
        "tools": dict(tools), "outcomes": dict(outcomes), "languages": dict(langs), "channels": dict(chans),
        "unanswered": unanswered, "linked_students": linked_students,
        "students": db.exec(select(func.count()).select_from(Student)).one(),
        "handoffs": handoff_counts,
        "p50_ms": latencies[len(latencies) // 2] if latencies else None,
        "p95_ms": latencies[int(len(latencies) * 0.95) - 1] if len(latencies) >= 2 else (latencies[0] if latencies else None),
        "sync": sync.status,
    }


class TestChatIn(BaseModel):
    message: str | None = None
    confirm: bool | None = None
    pending_id: str | None = None
    student_id: int | None = None
    language: str | None = None


@router.post("/testchat")
def test_chat(body: TestChatIn, user: User = Depends(ANY)):
    who = Who("panel", f"{user.id}:{body.student_id or 'anon'}", body.student_id)
    if body.language in ("id", "en"):
        chat_service.set_language(who, body.language)
    if body.confirm is not None:
        return chat_service.confirm(who, body.pending_id, body.confirm)
    return chat_service.message(who, body.message or "")


@router.post("/testchat/reset")
def test_chat_reset(body: TestChatIn, user: User = Depends(ANY)):
    chat_service.reset(Who("panel", f"{user.id}:{body.student_id or 'anon'}"))
    return {"ok": True}


def _404():
    raise HTTPException(404, "Not found")
