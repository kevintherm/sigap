"""Backend services over the database: identity, linking, tokens, handoffs, reminders, usage log,
and exporting the reference data that is synced to Langflow."""
import csv
import io
import re
from datetime import date, datetime, timedelta

from sqlmodel import Session, func, select

from .config import WIB
from .models import (ApiToken, CalendarEntry, ChannelAccount, Course, Document, Enrollment, Handoff, HandoffEvent,
                     LinkCode, MessageLog, Reminder, ScheduleItem, Student, utcnow)
from .security import as_utc, new_secret, now_utc, sha256

LINK_CODE_TTL = timedelta(days=7)


# ---------------------------------------------------------------- students and channels

def student_for_channel(db: Session, channel: str, external_id: str) -> Student | None:
    acc = db.exec(select(ChannelAccount).where(ChannelAccount.channel == channel,
                                              ChannelAccount.external_id == str(external_id))).first()
    if not acc:
        return None
    st = db.get(Student, acc.student_id)
    return st if st and st.active else None


def student_courses(db: Session, student_id: int) -> list[str]:
    return sorted(db.exec(select(Enrollment.course_code).where(Enrollment.student_id == student_id)).all())


def create_link_code(db: Session, student_id: int, user_id: int | None = None) -> tuple[str, datetime]:
    code = new_secret("L", 12)  # fits Telegram's 64-char /start payload
    expires = now_utc() + LINK_CODE_TTL
    db.add(LinkCode(code_hash=sha256(code), student_id=student_id, created_by=user_id, expires_at=expires))
    db.commit()
    return code, expires


def redeem_link_code(db: Session, code: str, channel: str, external_id: str, display_name: str | None) -> Student | None:
    """Links the chat account to the student behind a valid, unused, unexpired code. One use only."""
    lc = db.get(LinkCode, sha256(code.strip()))
    if not lc or lc.used_at or as_utc(lc.expires_at) < now_utc():
        return None
    student = db.get(Student, lc.student_id)
    if not student or not student.active:
        return None
    existing = db.exec(select(ChannelAccount).where(ChannelAccount.channel == channel,
                                                   ChannelAccount.external_id == str(external_id))).first()
    if existing:  # re-linking moves this chat account to the new student
        existing.student_id, existing.display_name, existing.linked_at = student.id, display_name, utcnow()
    else:
        db.add(ChannelAccount(student_id=student.id, channel=channel, external_id=str(external_id), display_name=display_name))
    lc.used_at = utcnow()
    db.commit()
    return student


def create_link_request(db: Session, channel: str, external_id: str, display_name: str | None) -> str:
    """One-time state for the web login link (10 minutes, tied to this chat). Only the hash is stored."""
    from .models import LinkRequest
    state = new_secret("W", 18)
    db.add(LinkRequest(state_hash=sha256(state), channel=channel, external_id=str(external_id),
                       display_name=display_name, expires_at=now_utc() + timedelta(minutes=10)))
    db.commit()
    return state


def unlink_channel(db: Session, channel: str, external_id: str) -> bool:
    acc = db.exec(select(ChannelAccount).where(ChannelAccount.channel == channel,
                                              ChannelAccount.external_id == str(external_id))).first()
    if not acc:
        return False
    db.delete(acc)
    db.commit()
    return True


# ---------------------------------------------------------------- MCP gateway tokens

def issue_api_token(db: Session, student_id: int, label: str | None = None) -> str:
    token = new_secret("sgp_", 24)
    db.add(ApiToken(token_hash=sha256(token), prefix=token[:10], student_id=student_id, label=label))
    db.commit()
    return token


def student_for_token(db: Session, token: str) -> Student | None:
    t = db.exec(select(ApiToken).where(ApiToken.token_hash == sha256(token))).first()
    if not t or t.revoked_at:
        return None
    st = db.get(Student, t.student_id)
    if not st or not st.active:
        return None
    t.last_used_at = utcnow()
    db.commit()
    return st


# ---------------------------------------------------------------- handoffs

def next_reference(db: Session) -> str:
    today = datetime.now(WIB)
    prefix = f"SGP-{today:%y%m%d}-"
    count = db.exec(select(func.count()).select_from(Handoff).where(Handoff.reference.startswith(prefix))).one()
    return f"{prefix}{count + 1:04d}"


def record_handoff(db: Session, *, reference: str, student_id: int | None, channel: str, external_id: str | None,
                   question: str, category: str, office: str, language: str) -> Handoff:
    h = Handoff(reference=reference, student_id=student_id, channel=channel, channel_external_id=external_id,
                question=question, category=category, office=office, language=language)
    db.add(h)
    db.commit()
    db.refresh(h)
    db.add(HandoffEvent(handoff_id=h.id, kind="status", body="open"))
    db.commit()
    return h


def handoffs_for_student(db: Session, student_id: int, limit: int = 5) -> list[tuple[Handoff, list[HandoffEvent]]]:
    hs = db.exec(select(Handoff).where(Handoff.student_id == student_id).order_by(Handoff.created_at.desc()).limit(limit)).all()
    return [(h, db.exec(select(HandoffEvent).where(HandoffEvent.handoff_id == h.id, HandoffEvent.kind == "reply")
                        .order_by(HandoffEvent.created_at)).all()) for h in hs]


# ---------------------------------------------------------------- reminders

def schedule_reminders(db: Session, *, student_id: int | None, channel: str, external_id: str, item: dict,
                       events: list[dict], lang: str) -> int:
    added = 0
    for ev in events:
        at = datetime.fromisoformat(ev["start"])
        if at <= datetime.now(WIB):
            continue
        if ev["kind"] == "reminder":
            text = (f"⏰ <b>{'Reminder' if lang == 'en' else 'Pengingat'}</b>: {item['course']} — {item['item']}\n"
                    f"{'Due' if lang == 'en' else 'Tenggat'}: {item['due_text']}")
        else:
            text = (f"📚 Time for your 90-minute study block for <b>{item['item']}</b> ({item['course']}). You've got this!"
                    if lang == "en" else
                    f"📚 Waktunya blok belajar 90 menit untuk <b>{item['item']}</b> ({item['course']}). Semangat!")
        db.add(Reminder(student_id=student_id, channel=channel, external_id=str(external_id), send_at=at,
                        text=text, item_id=item.get("item_id")))
        added += 1
    db.commit()
    return added


def due_reminders(db: Session) -> list[Reminder]:
    return [r for r in db.exec(select(Reminder).where(Reminder.sent_at == None)).all()  # noqa: E711
            if as_utc(r.send_at) <= now_utc()]


# ---------------------------------------------------------------- usage log

def log_message(db: Session, *, channel: str, linked: bool, language: str, result: dict, question: str,
                latency_ms: int | None) -> None:
    calls = result.get("tool_calls", [])
    tools = ",".join(f"{c['tool']}:{c.get('status') or 'OK'}" for c in calls)
    statuses = {c.get("status") for c in calls}
    if any(b["type"] == "text" and "tidak bisa dihubungi" in b.get("text", "") or "can't be reached" in b.get("text", "")
           for b in result.get("blocks", [])):
        outcome = "error"
    elif "NOT_FOUND" in statuses:
        outcome = "not_found"
    elif statuses & {"CREATED", "SENT"}:
        outcome = "action"
    elif "NEEDS_CONFIRMATION" in statuses:
        outcome = "offer"
    elif calls:
        outcome = "answered"
    else:
        outcome = "other"
    db.add(MessageLog(channel=channel, linked=linked, language=language, tools=tools, outcome=outcome,
                      unanswered_question=question[:500] if outcome == "not_found" else None, latency_ms=latency_ms))
    db.commit()


# ---------------------------------------------------------------- reference data export (→ Langflow, local tools)

def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def export_reference_data(db: Session) -> dict[str, str]:
    """The four data fields of the Langflow components, rebuilt from the database."""
    courses = db.exec(select(Course).order_by(Course.code)).all()
    items = db.exec(select(ScheduleItem).order_by(ScheduleItem.due_date, ScheduleItem.due_time)).all()
    cal = db.exec(select(CalendarEntry).order_by(CalendarEntry.start, CalendarEntry.id)).all()
    doc = db.exec(select(Document).where(Document.active == True)).first()  # noqa: E712
    return {
        "courses_csv": _csv(["course_code", "name_id", "name_en", "lecturer", "aliases"],
                            [[c.code, c.name_id, c.name_en, c.lecturer, c.aliases] for c in courses]),
        "schedule_csv": _csv(["item_id", "course_code", "title_id", "title_en", "type", "due_date", "due_time",
                              "end_time", "where", "source"],
                             [[i.item_id, i.course_code, i.title_id, i.title_en, i.type, i.due_date.isoformat(),
                               i.due_time, i.end_time, i.where, i.source] for i in items]),
        "calendar_csv": _csv(["kind", "key", "name_id", "name_en", "start", "end", "note_id", "note_en"],
                             [[e.kind, e.key, e.name_id, e.name_en, e.start.isoformat(), e.end.isoformat(),
                               e.note_id, e.note_en] for e in cal]),
        "handbook_md": doc.content if doc else "",
    }


SECTION_RE = re.compile(r"^##\s+[\d.]+\s*\|[^|]+\|.+$", re.M)


def validate_handbook(content: str) -> dict:
    """Checks an uploaded handbook follows the section format the policy flow reads."""
    if not content.startswith("---"):
        raise ValueError("Missing front matter (--- title_id/title_en/version/effective ---)")
    try:
        _, front, body = content.split("---", 2)
    except ValueError:
        raise ValueError("Front matter is not closed with ---") from None
    meta = {k.strip(): v.strip() for k, _, v in (l.partition(":") for l in front.strip().splitlines()) if k.strip()}
    for key in ("title_id", "version", "effective"):
        if not meta.get(key):
            raise ValueError(f"Front matter needs '{key}'")
    date.fromisoformat(meta["effective"])
    sections = SECTION_RE.findall(body)
    if not sections:
        raise ValueError("No sections found; each starts with '## 4.2 | Judul | Title'")
    missing = [s for s in sections if not re.search(re.escape(s) + r"\s*\ntags:.*\nID:.*\nEN:", body)]
    if missing:
        raise ValueError(f"Section needs 'tags:', 'ID:' and 'EN:' lines right after its heading: {missing[0][:60]}")
    return {"meta": meta, "sections": len(sections)}
