"""Database tables. The backend's SQLite database is the source of truth; Langflow gets a synced
copy of the reference data (courses, schedule, calendar, handbook) and never sees identities."""
from datetime import date, datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- people and access

class User(SQLModel, table=True):
    """Admin-panel accounts. role: admin | staff | lecturer."""
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    role: str
    password_hash: str
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class LecturerCourse(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.id", primary_key=True)
    course_code: str = Field(foreign_key="course.code", primary_key=True)


class AuthSession(SQLModel, table=True):
    """Admin-panel login sessions. Only the SHA-256 of the cookie value is stored."""
    token_hash: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class Student(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_number: str = Field(index=True, unique=True)  # NIM; stays in the backend, never sent to Langflow
    name: str
    email: str | None = None
    program: str | None = None
    language: str = "id"
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    consent_at: datetime | None = None  # accepted the privacy notice in the student portal
    consent_version: str | None = None


class StudentLoginCode(SQLModel, table=True):
    """Email login codes for the student portal. Only the hash is stored; single use, short-lived."""
    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    code_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    attempts: int = 0
    used_at: datetime | None = None


class StudentSession(SQLModel, table=True):
    """Student-portal login sessions (hash of the cookie value)."""
    token_hash: str = Field(primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class LinkRequest(SQLModel, table=True):
    """A chat asking to be linked: the bot's "Masuk" button carries this one-time state to the web login."""
    state_hash: str = Field(primary_key=True)
    channel: str
    external_id: str
    display_name: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = None


class Enrollment(SQLModel, table=True):
    student_id: int = Field(foreign_key="student.id", primary_key=True)
    course_code: str = Field(foreign_key="course.code", primary_key=True)


class ChannelAccount(SQLModel, table=True):
    """A chat account linked to a student, e.g. telegram:123456."""
    __table_args__ = (UniqueConstraint("channel", "external_id"),)
    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    channel: str  # telegram | whatsapp
    external_id: str
    display_name: str | None = None
    linked_at: datetime = Field(default_factory=utcnow)


class LinkCode(SQLModel, table=True):
    """One-time invite that links a chat account to a student. Only the hash is stored."""
    code_hash: str = Field(primary_key=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    created_by: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = None


class ApiToken(SQLModel, table=True):
    """Personal MCP-gateway tokens for a student's own agent. Only the hash is stored."""
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    prefix: str  # first characters, to recognise a token in the panel
    student_id: int = Field(foreign_key="student.id", index=True)
    label: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


# ---------------------------------------------------------------- reference data (synced to Langflow)

class Course(SQLModel, table=True):
    code: str = Field(primary_key=True)
    name_id: str
    name_en: str
    lecturer: str = ""
    aliases: str = ""  # ';'-separated


class ScheduleItem(SQLModel, table=True):
    item_id: str = Field(primary_key=True)
    course_code: str = Field(foreign_key="course.code", index=True)
    title_id: str
    title_en: str
    type: str  # assignment | quiz | project | exam
    due_date: date
    due_time: str  # HH:MM WIB
    end_time: str = ""
    where: str = ""
    source: str = ""
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: int | None = Field(default=None, foreign_key="user.id")


class CalendarEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # term | period | window
    key: str = Field(index=True)
    name_id: str
    name_en: str
    start: date
    end: date
    note_id: str = ""
    note_en: str = ""


class Document(SQLModel, table=True):
    """Handbook versions. Exactly one active version is synced; older ones are kept but excluded (FR-10)."""
    id: int | None = Field(default=None, primary_key=True)
    title: str
    version: str
    effective: date
    content: str  # the handbook markdown (see data/handbook/*.md for the format)
    active: bool = False
    uploaded_by: int | None = Field(default=None, foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- operations

class Handoff(SQLModel, table=True):
    """A question escalated to staff. status: open | in_progress | answered | closed."""
    id: int | None = Field(default=None, primary_key=True)
    reference: str = Field(index=True, unique=True)
    student_id: int | None = Field(default=None, foreign_key="student.id", index=True)
    channel: str
    channel_external_id: str | None = None  # where staff replies are delivered (e.g. the Telegram chat)
    question: str
    category: str = "umum"
    office: str = ""
    language: str = "id"
    status: str = "open"
    assigned_to: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class HandoffEvent(SQLModel, table=True):
    """Timeline of a handoff: status changes, internal notes and replies sent to the student."""
    id: int | None = Field(default=None, primary_key=True)
    handoff_id: int = Field(foreign_key="handoff.id", index=True)
    user_id: int | None = Field(default=None, foreign_key="user.id")
    kind: str  # status | note | reply
    body: str = ""
    delivered: bool | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Reminder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    student_id: int | None = Field(default=None, foreign_key="student.id", index=True)
    channel: str
    external_id: str
    send_at: datetime = Field(index=True)
    text: str
    item_id: str | None = None
    sent_at: datetime | None = None


class MessageLog(SQLModel, table=True):
    """One row per student message, for the usage dashboard. No student link and no message text,
    except the question text of unanswered (NOT_FOUND) questions, which shows what the handbook lacks."""
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    channel: str
    linked: bool
    language: str
    tools: str = ""  # e.g. "answer_campus_policy:NOT_FOUND"
    outcome: str = ""  # answered | not_found | offer | action | declined | error | other
    unanswered_question: str | None = None
    latency_ms: int | None = None
