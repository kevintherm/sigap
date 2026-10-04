"""Student portal API (/api/student): email-code login, linking a chat account, consent, and self-service.

Linking flow: the bot gives an unlinked chat a one-time "Masuk" link (/student?link=<state>, 10 minutes,
tied to that chat). The student signs in here with a code sent to their campus email, sees which chat
account is being linked, and confirms. Showing the chat account before confirming stops someone from
getting a victim to sign in through the attacker's link.

CSRF: state-changing requests need the X-Sigap-Request header; the cookie is HttpOnly + SameSite=Lax
(Lax so the link opened from Telegram still carries an existing session).
"""
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, func, or_, select

from . import channels, mailer
from .chat import Who, chat_service
from .db import get_session
from .models import (ApiToken, ChannelAccount, Handoff, HandoffEvent, LinkRequest, Student, StudentLoginCode,
                     StudentSession, utcnow)
from .security import as_utc, new_secret, now_utc, sha256
from .services import issue_api_token, next_reference, record_handoff, student_courses

router = APIRouter(prefix="/api/student")
COOKIE = "sigap_student"
CODE_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(days=7)
MAX_CODE_ATTEMPTS = 5
CONSENT_VERSION = "2026-10-v1"
_starts: dict[str, deque] = defaultdict(deque)


def _limited(key: str, limit: int, window: int) -> bool:
    q, now = _starts[key], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def current_student(request: Request, db: Session = Depends(get_session)) -> Student:
    token = request.cookies.get(COOKIE)
    s = db.get(StudentSession, sha256(token)) if token else None
    if not s or as_utc(s.expires_at) < now_utc():
        raise HTTPException(401, "Not signed in")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-sigap-request") != "1":
        raise HTTPException(403, "Missing X-Sigap-Request header")
    st = db.get(Student, s.student_id)
    if not st or not st.active:
        raise HTTPException(401, "Account inactive")
    return st


def _find_student(db: Session, identifier: str) -> Student | None:
    ident = identifier.strip().lower()
    return db.exec(select(Student).where(or_(Student.student_number == ident, func.lower(Student.email) == ident),
                                         Student.active == True)).first()  # noqa: E712


# ---------------------------------------------------------------- login

class StartIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)


@router.post("/login/start")
def login_start(body: StartIn, request: Request, bg: BackgroundTasks, db: Session = Depends(get_session)):
    """Always answers the same way, so it does not reveal which student numbers or emails exist."""
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if _limited(f"ip:{ip}", 10, 900) or _limited(f"id:{body.identifier.strip().lower()}", 3, 900):
        raise HTTPException(429, "Terlalu banyak permintaan. Coba lagi 15 menit lagi.")
    if not mailer.configured():
        raise HTTPException(503, "Pengiriman email belum dikonfigurasi. Hubungi Layanan Akademik.")
    st = _find_student(db, body.identifier)
    if st and st.email:
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(StudentLoginCode(student_id=st.id, code_hash=sha256(f"{st.id}:{code}"), expires_at=now_utc() + CODE_TTL))
        db.commit()
        body_text = (f"Halo {st.name},\n\nKode masuk Sigap kamu: {code}\n\nKode berlaku 10 menit dan hanya bisa dipakai sekali. "
                     "Jangan bagikan kode ini ke siapa pun, termasuk staf.\nKalau kamu tidak meminta kode ini, abaikan email ini.\n\n"
                     f"Your Sigap sign-in code: {code} (valid 10 minutes, single use).\n\n— Sigap, Layanan Akademik")
        bg.add_task(mailer.send, st.email, f"Kode masuk Sigap: {code}", body_text)
    return {"ok": True, "message": "Jika NIM atau email terdaftar, kode masuk sudah dikirim ke email kampus yang terdaftar."}


class VerifyIn(BaseModel):
    identifier: str
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/login/verify")
def login_verify(body: VerifyIn, request: Request, response: Response, db: Session = Depends(get_session)):
    st = _find_student(db, body.identifier)
    lc = db.exec(select(StudentLoginCode).where(StudentLoginCode.student_id == st.id, StudentLoginCode.used_at == None)  # noqa: E711
                 .order_by(StudentLoginCode.created_at.desc())).first() if st else None
    if not lc or as_utc(lc.expires_at) < now_utc() or lc.attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(401, "Kode salah atau kedaluwarsa. Minta kode baru.")
    lc.attempts += 1
    if not hmac.compare_digest(lc.code_hash, sha256(f"{st.id}:{body.code}")):
        db.commit()
        left = MAX_CODE_ATTEMPTS - lc.attempts
        raise HTTPException(401, f"Kode salah. Sisa percobaan: {left}." if left else "Kode salah. Minta kode baru.")
    lc.used_at = utcnow()
    token = new_secret()
    db.add(StudentSession(token_hash=sha256(token), student_id=st.id, expires_at=now_utc() + SESSION_TTL))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https",
                        max_age=int(SESSION_TTL.total_seconds()), path="/")
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_session)):
    token = request.cookies.get(COOKIE)
    if token and (s := db.get(StudentSession, sha256(token))):
        db.delete(s)
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- profile & consent

def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


@router.get("/me")
def me(st: Student = Depends(current_student), db: Session = Depends(get_session)):
    chans = db.exec(select(ChannelAccount).where(ChannelAccount.student_id == st.id)).all()
    toks = db.exec(select(ApiToken).where(ApiToken.student_id == st.id).order_by(ApiToken.created_at.desc())).all()
    hs = db.exec(select(Handoff).where(Handoff.student_id == st.id).order_by(Handoff.created_at.desc()).limit(20)).all()
    tickets = []
    for h in hs:
        rep = db.exec(select(HandoffEvent).where(HandoffEvent.handoff_id == h.id, HandoffEvent.kind == "reply")
                      .order_by(HandoffEvent.created_at.desc())).first()
        tickets.append({"reference": h.reference, "status": h.status, "question": h.question,
                        "created_at": h.created_at.isoformat(), "reply": rep.body if rep else None})
    return {
        "name": st.name, "number": st.student_number, "email": _mask(st.email), "program": st.program,
        "courses": student_courses(db, st.id),
        "consent": {"accepted": bool(st.consent_at) and st.consent_version == CONSENT_VERSION,
                    "at": st.consent_at.isoformat() if st.consent_at else None, "version": CONSENT_VERSION},
        "channels": [{"id": c.id, "channel": c.channel, "display_name": c.display_name, "linked_at": c.linked_at.isoformat()} for c in chans],
        "tokens": [{"id": t.id, "prefix": t.prefix, "label": t.label, "created_at": t.created_at.isoformat(),
                    "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None, "revoked": bool(t.revoked_at)} for t in toks],
        "tickets": tickets,
        "mcp_url": os.environ.get("SIGAP_PUBLIC_URL", "http://localhost:8000").rstrip("/") + "/mcp",
        "bot_username": os.environ.get("TELEGRAM_BOT_USERNAME"),
    }


class ConsentIn(BaseModel):
    accept: bool


@router.post("/consent")
def consent(body: ConsentIn, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    if not body.accept:
        raise HTTPException(422, "Persetujuan diperlukan untuk memakai Sigap.")
    st.consent_at, st.consent_version = utcnow(), CONSENT_VERSION
    db.add(st)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- linking a chat account

def _link_request(db: Session, state: str) -> LinkRequest:
    lr = db.get(LinkRequest, sha256(state))
    if not lr or lr.used_at or as_utc(lr.expires_at) < now_utc():
        raise HTTPException(410, "Tautan ini sudah dipakai atau kedaluwarsa. Ketik /start lagi di bot untuk tautan baru.")
    return lr


@router.get("/link/{state}")
def link_info(state: str, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    lr = _link_request(db, state)
    existing = db.exec(select(ChannelAccount).where(ChannelAccount.channel == lr.channel,
                                                   ChannelAccount.external_id == lr.external_id)).first()
    other = db.get(Student, existing.student_id).name if existing and existing.student_id != st.id else None
    return {"channel": lr.channel, "display_name": lr.display_name, "expires_at": lr.expires_at.isoformat(),
            "already_linked_to_you": bool(existing and existing.student_id == st.id), "currently_linked_to_other": bool(other)}


@router.post("/link/{state}/confirm")
async def link_confirm(state: str, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    if not (st.consent_at and st.consent_version == CONSENT_VERSION):
        raise HTTPException(409, "Setujui pemberitahuan privasi dulu.")
    lr = _link_request(db, state)
    acc = db.exec(select(ChannelAccount).where(ChannelAccount.channel == lr.channel,
                                              ChannelAccount.external_id == lr.external_id)).first()
    if acc:
        acc.student_id, acc.display_name, acc.linked_at = st.id, lr.display_name, utcnow()
    else:
        db.add(ChannelAccount(student_id=st.id, channel=lr.channel, external_id=lr.external_id, display_name=lr.display_name))
    lr.used_at = utcnow()
    db.commit()
    chat_service.reset(Who(lr.channel, lr.external_id))
    first = st.name.split()[0]
    await channels.deliver(lr.channel, lr.external_id,
                           f"✅ Akun terhubung: <b>{st.name}</b>. Halo {first}! Sekarang aku bisa menjawab tenggat dan ujianmu sendiri. "
                           "Coba: <i>Apa saja yang deadline minggu ini?</i>")
    return {"ok": True, "channel": lr.channel}


@router.delete("/channels/{cid}")
def unlink(cid: int, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    acc = db.get(ChannelAccount, cid)
    if not acc or acc.student_id != st.id:
        raise HTTPException(404, "Not found")
    chat_service.reset(Who(acc.channel, acc.external_id))
    db.delete(acc)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- MCP tokens

class TokenIn(BaseModel):
    label: str | None = Field(default=None, max_length=60)


@router.post("/tokens")
def new_token(body: TokenIn, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    active = db.exec(select(func.count()).select_from(ApiToken).where(ApiToken.student_id == st.id, ApiToken.revoked_at == None)).one()  # noqa: E711
    if active >= 5:
        raise HTTPException(409, "Maksimal 5 token aktif. Cabut salah satu dulu.")
    return {"token": issue_api_token(db, st.id, label=body.label or "via portal")}


@router.delete("/tokens/{tid}")
def revoke_token(tid: int, st: Student = Depends(current_student), db: Session = Depends(get_session)):
    t = db.get(ApiToken, tid)
    if not t or t.student_id != st.id:
        raise HTTPException(404, "Not found")
    t.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- privacy

@router.post("/deletion-request")
def deletion_request(st: Student = Depends(current_student), db: Session = Depends(get_session)):
    """UU PDP: the student asks for their personal data to be deleted; staff handle it from the inbox."""
    open_req = db.exec(select(Handoff).where(Handoff.student_id == st.id, Handoff.category == "privasi",
                                             Handoff.status.in_(["open", "in_progress"]))).first()
    if open_req:
        return {"ok": True, "reference": open_req.reference, "existing": True}
    h = record_handoff(db, reference=next_reference(db), student_id=st.id, channel="portal", external_id=None,
                       question="Permintaan penghapusan data pribadi (UU PDP No. 27/2022) dari portal mahasiswa.",
                       category="privasi", office="Bagian Layanan Akademik", language=st.language)
    return {"ok": True, "reference": h.reference, "existing": False}
