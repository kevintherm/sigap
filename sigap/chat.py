"""Channel-independent chat service: identity, linking, tokens, and the bookkeeping around each
reply (handoff records, reminders, usage log). Channel adapters only translate messages."""
import os
import time
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import select

from . import agent, channels
from .config import WIB
from .db import session_scope
from .models import Student
from .services import (handoffs_for_student, issue_api_token, log_message, next_reference, record_handoff,
                       redeem_link_code, schedule_reminders, student_courses, student_for_channel, unlink_channel)


def use_llm_agent() -> bool:
    """SIGAP_AGENT=langflow: Gemini (Langflow "sigap_agent" flow) understands messages; otherwise keyword rules."""
    from .langflow_client import backend_name
    return os.environ.get("SIGAP_AGENT", "rules") == "langflow" and backend_name() == "langflow"


# A conversation starts fresh after this much silence, and at midnight WIB, so "that assignment" never reaches back
# into an old conversation. Real wall-clock time, not the pinned demo clock.
IDLE_RESET_HOURS = float(os.environ.get("SIGAP_SESSION_IDLE_HOURS", "6"))


def _stale(last_active: float, now: float) -> bool:
    day = lambda ts: datetime.fromtimestamp(ts, WIB).date()  # noqa: E731
    return now - last_active > IDLE_RESET_HOURS * 3600 or day(last_active) != day(now)


def _t(lang: str, id_text: str, en_text: str) -> str:
    return en_text if lang == "en" else id_text


@dataclass
class Who:
    channel: str
    external_id: str
    student_id: int | None = None  # set for the admin test chat ("act as" a student)


class ChatService:
    def __init__(self):
        self.sessions: dict[tuple[str, str], agent.Session] = {}

    # ------------------------------------------------------------ conversation

    def _session(self, who: Who, lang_hint: str | None = None) -> tuple[agent.Session, Student | None]:
        key = (who.channel, who.external_id)
        s = self.sessions.get(key)
        if s is None:
            s = self.sessions[key] = agent.Session(lang="en" if (lang_hint or "").startswith("en") else "id")
        with session_scope() as db:
            student = db.get(Student, who.student_id) if who.student_id else student_for_channel(db, who.channel, who.external_id)
            s.linked = bool(student)
            s.student_name = student.name if student else None
            s.courses = student_courses(db, student.id) if student else None
        s.make_reference = _reference
        return s, student

    def message(self, who: Who, text: str, lang_hint: str | None = None) -> dict:
        key, now = (who.channel, who.external_id), time.time()
        old = self.sessions.get(key)
        if old and _stale(old.last_active, now):  # new agent memory; the language choice carries over
            self.sessions[key] = agent.Session(lang=old.lang)
        s, student = self._session(who, lang_hint)
        s.last_active = now
        t0 = time.perf_counter()
        if use_llm_agent():
            from .langflow_client import tool_backend
            result = agent.handle_with_agent(s, text[:1000], tool_backend().run_agent)
        else:
            result = agent.handle(s, text[:1000])
        return self._after(who, s, student, result, text, t0)

    def confirm(self, who: Who, pending_id: str | None, accept: bool) -> dict:
        s, student = self._session(who)
        question = s.pending["args"].get("question", "") if s.pending else ""
        t0 = time.perf_counter()
        result = agent.resolve_pending(s, accept, pending_id)
        return self._after(who, s, student, result, question, t0)

    def _after(self, who: Who, s: agent.Session, student: Student | None, result: dict, text: str, t0: float) -> dict:
        latency = int((time.perf_counter() - t0) * 1000)
        extra = []
        with session_scope() as db:
            for b in result["blocks"]:
                if b["type"] == "reference":
                    record_handoff(db, reference=b["reference"], student_id=student.id if student else None,
                                   channel=who.channel, external_id=who.external_id, question=b.get("question") or text,
                                   category=b.get("category", "umum"), office=b["office"], language=s.lang)
                elif b["type"] == "events" and channels.available(who.channel):
                    n = schedule_reminders(db, student_id=student.id if student else None, channel=who.channel,
                                           external_id=who.external_id, item=b["item"], events=b["events"], lang=s.lang)
                    if n:
                        extra.append({"type": "text", "text": _t(
                            s.lang, f"🔔 Aku juga akan mengirim {n} pengingat di chat ini.",
                            f"🔔 I'll also send you {n} reminder(s) right here in this chat.")})
            if who.channel != "panel":  # staff test chats are not usage
                log_message(db, channel=who.channel, linked=s.linked, language=s.lang, result=result,
                            question=text, latency_ms=latency)
        result["blocks"] += extra
        result["pending_id"] = s.pending["id"] if s.pending else None
        return result

    def reset(self, who: Who) -> None:
        self.sessions.pop((who.channel, who.external_id), None)

    def set_language(self, who: Who, lang: str) -> None:
        self._session(who)[0].lang = lang

    def language(self, who: Who) -> str:
        return self._session(who)[0].lang

    # ------------------------------------------------------------ account commands

    def link(self, who: Who, code: str, display_name: str | None) -> Student | None:
        with session_scope() as db:
            student = redeem_link_code(db, code, who.channel, who.external_id, display_name)
        if student:
            self.reset(who)
        return student

    def unlink(self, who: Who) -> bool:
        with session_scope() as db:
            done = unlink_channel(db, who.channel, who.external_id)
        self.reset(who)
        return done

    def whoami(self, who: Who) -> Student | None:
        return self._session(who)[1]

    def new_token(self, who: Who) -> str | None:
        with session_scope() as db:
            student = student_for_channel(db, who.channel, who.external_id)
            return issue_api_token(db, student.id, label=f"via {who.channel}") if student else None

    def tickets_text(self, who: Who) -> str:
        lang = self.language(who)
        with session_scope() as db:
            student = student_for_channel(db, who.channel, who.external_id)
            if not student:
                return _t(lang, "Hubungkan akunmu dulu untuk melihat tiket.", "Link your account first to see your tickets.")
            rows = handoffs_for_student(db, student.id)
        if not rows:
            return _t(lang, "Kamu belum punya tiket.", "You have no tickets yet.")
        status_name = {"open": ("Terbuka", "Open"), "in_progress": ("Diproses", "In progress"),
                       "answered": ("Dijawab", "Answered"), "closed": ("Selesai", "Closed")}
        lines = []
        for h, replies in rows:
            st = status_name.get(h.status, (h.status, h.status))
            lines.append(f"🎫 <code>{h.reference}</code> · <b>{st[1] if lang == 'en' else st[0]}</b>\n   {_esc(h.question[:120])}")
            for rep in replies[-1:]:
                lines.append(f"   💬 {_esc(rep.body[:300])}")
        return "\n".join(lines)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _reference() -> str:
    with session_scope() as db:
        return next_reference(db)


chat_service = ChatService()
