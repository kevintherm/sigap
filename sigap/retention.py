"""Data retention (UU PDP): deletes what Sigap no longer needs, on a schedule the privacy notice states.

Langflow keeps the agent's chat messages (its memory for the current conversation); they are deleted after
SIGAP_CHAT_RETENTION_HOURS. Conversations restart after a few idle hours (chat.IDLE_RESET_HOURS), so the
window never cuts into a live conversation. Langflow's run logs are switched off in compose.yaml.
Every limit can be changed in .env; the defaults are what the privacy notice tells students.
"""
import csv
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlmodel import delete, select

from .config import OUTBOX_DIR
from .db import session_scope
from .models import (AuthSession, Handoff, HandoffEvent, LinkCode, LinkRequest, MessageLog, Reminder, StudentLoginCode,
                     StudentSession)
from .security import now_utc

log = logging.getLogger("sigap.retention")
SIGAP_FLOWS = ("sigap_agent", "answer_campus_policy", "get_my_deadlines", "get_study_period", "create_study_reminder",
               "escalate_to_student_services", "handbook_formatter")


def _num(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def limits() -> dict:
    """The retention limits in force, also shown to students in the privacy notice."""
    return {"chat_hours": _num("SIGAP_CHAT_RETENTION_HOURS", 24),
            "usage_days": _num("SIGAP_USAGE_RETENTION_DAYS", 90),
            "unanswered_days": _num("SIGAP_UNANSWERED_RETENTION_DAYS", 30),
            "reminder_days": _num("SIGAP_REMINDER_RETENTION_DAYS", 7),
            "handoff_days": _num("SIGAP_HANDOFF_RETENTION_DAYS", 180),
            "outbox_days": _num("SIGAP_OUTBOX_RETENTION_DAYS", 30)}


def purge_database(now: datetime | None = None) -> dict:
    """Sigap's own tables. Returns how many rows each step removed."""
    lim, now = limits(), now or now_utc()
    ago = lambda days: now - timedelta(days=days)  # noqa: E731
    done = {}
    with session_scope() as db:
        done["usage_log"] = db.exec(delete(MessageLog).where(MessageLog.created_at < ago(lim["usage_days"]))).rowcount
        old_text = db.exec(select(MessageLog).where(MessageLog.unanswered_question != None,  # noqa: E711
                                                    MessageLog.created_at < ago(lim["unanswered_days"]))).all()
        for row in old_text:  # keep the count for the dashboard, drop the words
            row.unanswered_question = None
        done["unanswered_text"] = len(old_text)
        done["reminders"] = db.exec(delete(Reminder).where(Reminder.sent_at != None,  # noqa: E711
                                                           Reminder.sent_at < ago(lim["reminder_days"]))).rowcount
        closed = db.exec(select(Handoff.id).where(Handoff.status == "closed",
                                                  Handoff.updated_at < ago(lim["handoff_days"]))).all()
        if closed:
            db.exec(delete(HandoffEvent).where(HandoffEvent.handoff_id.in_(closed)))
            db.exec(delete(Handoff).where(Handoff.id.in_(closed)))
        done["closed_handoffs"] = len(closed)
        expired = 0
        for model in (StudentLoginCode, LinkRequest, LinkCode, AuthSession, StudentSession):
            expired += db.exec(delete(model).where(model.expires_at < now)).rowcount
        done["expired_codes_and_sessions"] = expired
        db.commit()
    return done


def purge_outbox(now: float | None = None) -> int:
    """Handoff email copies, calendar files and the outbox logs (tickets.csv holds question text)."""
    cutoff, removed = (now or time.time()) - limits()["outbox_days"] * 86400, 0
    if not OUTBOX_DIR.exists():
        return 0
    for f in OUTBOX_DIR.rglob("*"):
        if f.is_file() and f.suffix in (".eml", ".ics") and f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    for f in OUTBOX_DIR.glob("*.csv"):  # append-only logs (tickets.csv holds question text): drop old rows
        with open(f, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        if len(rows) < 2 or "created_at" not in rows[0]:
            continue
        col = rows[0].index("created_at")
        keep = [rows[0]] + [r for r in rows[1:] if _row_time(r, col) >= cutoff]
        if len(keep) < len(rows):
            with open(f, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh, lineterminator="\n").writerows(keep)
            removed += len(rows) - len(keep)
    return removed


def _row_time(row: list[str], col: int) -> float:
    try:
        return datetime.fromisoformat(row[col]).timestamp()
    except (IndexError, ValueError):
        return float("inf")  # unreadable date: keep the row


def purge_langflow(client: httpx.Client, url: str, older_than_hours: float | None = None) -> dict:
    """Deletes Sigap's chat messages and any run logs in Langflow older than the chat window."""
    hours = limits()["chat_hours"] if older_than_hours is None else older_than_hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    flows = {f["name"]: f["id"] for f in client.get(f"{url}/api/v1/flows/", params={"get_all": "true"}).json()
             if f.get("name") in SIGAP_FLOWS}
    done = {"messages": 0, "traces": 0}
    for fid in flows.values():
        msgs = client.get(f"{url}/api/v1/monitor/messages", params={"flow_id": fid}).json()
        old = [m["id"] for m in msgs if _lf_time(m.get("timestamp")) < cutoff]
        for i in range(0, len(old), 500):
            client.request("DELETE", f"{url}/api/v1/monitor/messages", json=old[i:i + 500]).raise_for_status()
        done["messages"] += len(old)
        page = client.get(f"{url}/api/v1/monitor/traces",
                          params={"flow_id": fid, "end_time": cutoff.isoformat(), "page": 1, "size": 500}).json()
        for tr in page.get("traces") or []:
            if _lf_time(tr.get("startTime")) >= cutoff:
                continue
            client.delete(f"{url}/api/v1/monitor/traces/{tr['id']}")
            done["traces"] += 1
        client.delete(f"{url}/api/v1/monitor/builds", params={"flow_id": fid})
    return done


def _lf_time(value: str | None) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)  # unknown age: keep
    dt = datetime.fromisoformat(value.replace(" UTC", "").replace("Z", ""))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run_once() -> dict:
    """One full pass; called every few minutes by the backend."""
    from .langflow_client import backend_name, tool_backend
    done = {"database": purge_database(), "outbox_rows_or_files": purge_outbox()}
    if backend_name() == "langflow":
        lf = tool_backend()
        try:
            done["langflow"] = purge_langflow(lf.client, lf.url)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            done["langflow"] = f"skipped: {e}"
    return done


def main():
    """sigap-retention [--now]: run one pass and print what was removed. --all-chats also clears every
    stored Langflow chat message regardless of age (one-time cleanup)."""
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="sigap-retention")
    ap.add_argument("--all-chats", action="store_true", help="delete all Sigap chat messages in Langflow, any age")
    a = ap.parse_args()
    if a.all_chats:
        from .langflow_client import tool_backend
        lf = tool_backend()
        print(json.dumps({"langflow": purge_langflow(lf.client, lf.url, older_than_hours=0)}))
    print(json.dumps(run_once(), default=str))


if __name__ == "__main__":
    main()
