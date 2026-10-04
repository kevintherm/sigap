"""Sigap MCP gateway: the tools for students who bring their own agent (Bob, Claude Code, …).

    uv run sigap-server              # http://HOST:8000/mcp, bearer token from the bot's /token command
    uv run sigap-mcp                 # stdio for local development (acts as SIGAP_MCP_STUDENT, default 2401001)

Each token belongs to one student: deadlines are filtered to their courses, reminders only for their
items, escalations become handoffs in the admin panel. Logic runs in Langflow; Langflow stays private.
"""
import argparse
import contextvars
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from mcp.server.fastmcp import FastMCP
from sqlmodel import func, select

from .db import session_scope
from .langflow_client import tool_backend
from .models import Handoff, ScheduleItem, Student
from .security import now_utc
from .services import handoffs_for_student, log_message, next_reference, record_handoff, student_courses, student_for_token

ESCALATIONS_PER_DAY = int(os.environ.get("SIGAP_MCP_ESCALATIONS_PER_DAY", "5"))
REQUESTS_PER_MINUTE = int(os.environ.get("SIGAP_MCP_REQUESTS_PER_MINUTE", "60"))


@dataclass
class Caller:
    student_id: int
    name: str
    courses: list[str]


current: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("current_caller", default=None)


def _caller() -> Caller:
    c = current.get()
    if c:
        return c
    with session_scope() as db:  # stdio mode: a fixed local student for development
        st = db.exec(select(Student).where(Student.student_number == os.environ.get("SIGAP_MCP_STUDENT", "2401001"))).first()
        if not st:
            raise RuntimeError("No student for this MCP session")
        return Caller(st.id, st.name, student_courses(db, st.id))


def _log(result: dict, tool: str, question: str = "") -> dict:
    with session_scope() as db:
        log_message(db, channel="mcp", linked=True, language=result.get("language", "id"),
                    result={"tool_calls": [{"tool": tool, "status": result.get("status")}], "blocks": []},
                    question=question, latency_ms=int(result.get("langflow_ms") or 0))
    return result


mcp = FastMCP(
    "Sigap",
    instructions=(
        "Sigap is a campus student-services assistant acting for one signed-in student. Use a tool for every "
        "date and every campus rule; never answer policy from general knowledge. Ask the student before calling "
        "create_study_reminder or escalate_to_student_services, and pass student_confirmed=true only after they say "
        "yes. Reply in the student's language and always show the source the tool returned."
    ),
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def answer_campus_policy(question: str, language: str | None = None) -> dict:
    """Answers a question about campus rules, procedures or academic policy using only the official documents
    the campus uploaded. Returns the answer with document, version and article, or status NOT_FOUND when no
    passage answers it — then offer escalate_to_student_services instead of guessing. language: 'id' or 'en'."""
    _caller()
    return _log(tool_backend().answer_campus_policy(question, language), "answer_campus_policy", question)


@mcp.tool()
def get_my_deadlines(request: str = "this week", course: str | None = None, item_type: str | None = None,
                     language: str | None = None) -> dict:
    """Lists this student's upcoming assignment deadlines and exams from the official course schedule.
    request: a range in words ('this week', 'minggu ini', 'next 14 days', 'besok') or the student's question.
    course: optional course name or code. item_type: optional 'assignment' | 'quiz' | 'project' | 'exam'.
    Each item has item_id, course, due date and time (WIB) and source. Never compute dates yourself."""
    c = _caller()
    return _log(tool_backend().get_my_deadlines(request, course, item_type, language, c.courses), "get_my_deadlines")


@mcp.tool()
def get_study_period(on_date: str | None = None, topic: str | None = None, language: str | None = None) -> dict:
    """Tells which academic week and period today (or on_date, YYYY-MM-DD) falls in, and which windows
    (registration/KRS, add-drop, leave, withdrawal, tuition payment, exams, grade appeals) are open.
    topic: optional window the student asked about, e.g. 'withdrawal' or 'cuti', to get its open/closed status."""
    _caller()
    return _log(tool_backend().get_study_period(on_date, topic, language), "get_study_period")


@mcp.tool()
def create_study_reminder(item_id: str, student_confirmed: bool = False, language: str | None = None,
                          remind_at: str | None = None) -> dict:
    """Creates calendar events for one of this student's deadlines or exams. item_id comes from get_my_deadlines.
    Without remind_at: a reminder 3 days before and two 90-minute study blocks. With remind_at: one reminder at the
    time the student asked for, in their own words ("jam 8 malam", "tomorrow 7am", "2 hours before"); Sigap works
    out the exact time and returns INVALID_TIME if it is unclear, past or after the deadline. Call first with
    student_confirmed=false to get the plan, show it, and call again with student_confirmed=true ONLY after the
    student says yes, passing back the returned remind_at so the time is exactly the one they saw."""
    c = _caller()
    with session_scope() as db:
        item = db.get(ScheduleItem, item_id)
        if not item or item.course_code not in c.courses:
            return {"tool": "create_study_reminder", "status": "ERROR", "text": f"{item_id} is not one of this student's items."}
    return _log(tool_backend().create_study_reminder(item_id, student_confirmed, language, remind_at), "create_study_reminder")


@mcp.tool()
def escalate_to_student_services(question: str, category: str | None = None, course: str | None = None,
                                 language: str | None = None, student_confirmed: bool = False) -> dict:
    """Sends a question Sigap could not answer to student services and returns a reference number and the next
    step; staff see it in the admin panel and the student can follow it with get_my_tickets. category:
    'akademik' | 'keuangan' | 'ti' | 'umum'. Call ONLY after the student agrees; without student_confirmed=true
    nothing is sent and a preview is returned. Never include student ID numbers or other personal data."""
    c = _caller()
    if not student_confirmed:
        return tool_backend().escalate_to_student_services(question, category, course, None, language, False)
    with session_scope() as db:
        since = now_utc() - timedelta(days=1)
        sent = db.exec(select(func.count()).select_from(Handoff).where(
            Handoff.student_id == c.student_id, Handoff.channel == "mcp", Handoff.created_at >= since)).one()
        if sent >= ESCALATIONS_PER_DAY:
            return {"tool": "escalate_to_student_services", "status": "RATE_LIMITED",
                    "text": f"Daily limit of {ESCALATIONS_PER_DAY} handoffs reached. Try again tomorrow."}
        ref = next_reference(db)
    res = tool_backend().escalate_to_student_services(question, category, course, None, language, True, ref)
    if res.get("status") == "SENT":
        with session_scope() as db:
            record_handoff(db, reference=res["reference"], student_id=c.student_id, channel="mcp", external_id=None,
                           question=question, category=res.get("category", "umum"), office=res.get("office", ""),
                           language=res.get("language", "id"))
    return _log(res, "escalate_to_student_services")


@mcp.tool()
def get_my_tickets() -> dict:
    """Lists this student's recent questions to staff (handoffs) with their status (open, in_progress,
    answered, closed) and the latest staff reply."""
    c = _caller()
    with session_scope() as db:
        rows = handoffs_for_student(db, c.student_id, limit=10)
    return {"tool": "get_my_tickets", "tickets": [
        {"reference": h.reference, "status": h.status, "question": h.question, "created_at": h.created_at.isoformat(),
         "latest_reply": replies[-1].body if replies else None} for h, replies in rows]}


# ---------------------------------------------------------------- HTTP gateway

class GatewayAuth:
    """ASGI middleware: bearer token → student (database), per-token request rate limit."""

    def __init__(self, app):
        self.app = app
        self.hits: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        caller = None
        if token:
            with session_scope() as db:
                st = student_for_token(db, token)
                if st:
                    caller = Caller(st.id, st.name, student_courses(db, st.id))
        if not caller:
            return await _json(send, 401, '{"error": "missing or invalid Sigap token (get one with /token in the bot)"}')
        window, now = self.hits[token], time.time()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= REQUESTS_PER_MINUTE:
            return await _json(send, 429, '{"error": "rate limit exceeded"}')
        window.append(now)
        reset = current.set(caller)
        try:
            await self.app(scope, receive, send)
        finally:
            current.reset(reset)


async def _json(send, status: int, body: str):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
    await send({"type": "http.response.body", "body": body.encode()})


def gateway_app():
    """The streamable-HTTP MCP app wrapped in auth; serves POST /mcp. Run inside mcp.session_manager.run()."""
    mcp.settings.streamable_http_path = "/mcp"
    return GatewayAuth(mcp.streamable_http_app())


def main():
    argparse.ArgumentParser(description="Sigap MCP server over stdio (local development).").parse_args()
    mcp.run()


if __name__ == "__main__":
    main()
