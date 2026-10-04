"""Sigap backend: authentication, authorization, data and channels. No LLM; logic runs in Langflow.

    uv run sigap-server            # http://127.0.0.1:8000
      /admin       admin panel (staff, lecturers, admins)
      /api/admin   admin API (session cookie)
      /student     student portal (email-code sign-in, link Telegram, tokens, tickets, privacy)
      /mcp         MCP gateway for students' own agents (personal token from the bot's /token)
      Telegram     long polling when TELEGRAM_BOT_TOKEN is set
"""
import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import channels, retention, sync, telegram_bot
from .admin_api import router as admin_router
from .student_api import router as student_router
from .db import migrate, session_scope
from .langflow_client import backend_name
from .mcp_server import gateway_app, mcp
from .models import utcnow
from .services import due_reminders

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("sigap")


async def reminder_loop():
    """Delivers due reminders on the channel each student used."""
    while True:
        try:
            with session_scope() as db:
                for r in due_reminders(db):
                    if not channels.available(r.channel):
                        continue
                    if await channels.deliver(r.channel, r.external_id, r.text):
                        r.sent_at = utcnow()
                        db.commit()
        except Exception:
            log.exception("reminder loop")
        await asyncio.sleep(5)


async def retention_loop():
    """Deletes chat text and records past their retention limits (UU PDP), every 10 minutes."""
    while True:
        try:
            done = await asyncio.get_running_loop().run_in_executor(None, retention.run_once)
            log.info("retention: %s", done)
        except Exception:
            log.exception("retention loop")
        await asyncio.sleep(600)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    log.info("Sigap backend · tools via %s", backend_name())
    asyncio.get_running_loop().run_in_executor(None, sync.push_reference_data)
    async with mcp.session_manager.run():
        tasks = [asyncio.create_task(reminder_loop()), asyncio.create_task(retention_loop())]
        if bot := telegram_bot.start_if_configured():
            tasks.append(bot)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()


app = FastAPI(title="Sigap", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin_router)
app.include_router(student_router)
app.mount("/admin/static", StaticFiles(directory=STATIC / "admin"), name="admin-static")
app.mount("/student/static", StaticFiles(directory=STATIC / "student"), name="student-static")


@app.get("/")
def root():
    return RedirectResponse("/student")  # students are the public users; staff use /admin


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/student")
def student_page():
    return FileResponse(STATIC / "student" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True, "tools": backend_name(), "sync": sync.status, "channels": sorted(channels._senders)}


# Mounted last: it only serves /mcp; everything above matches first.
app.mount("/", gateway_app())


def main():
    import uvicorn
    ap = argparse.ArgumentParser(description="Sigap backend (admin panel, MCP gateway, Telegram)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
