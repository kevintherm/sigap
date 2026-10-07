"""Telegram adapter (long polling; no public URL needed). It only translates between Telegram and the
channel-independent chat service; identity, consent and bookkeeping live in sigap.chat.

    TELEGRAM_BOT_TOKEN=123:abc uv run sigap-server
"""
import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta

import httpx

from . import channels
from .agent import intro
from .chat import Who, chat_service
from .config import WIB, public_url
from .db import session_scope
from .langflow_client import backend_name
from .services import create_link_request, schedule_reminders

log = logging.getLogger("sigap.telegram")
CHANNEL = "telegram"

EXAMPLES = {
    "id": ["Apa saja yang deadline minggu ini?", "Kapan UTS Struktur Data?",
           "Kalau telat submit tugas, masih dinilai nggak?", "Sekarang minggu ke berapa?"],
    "en": ["What's due this week?", "When is my Data Structures midterm?",
           "What is the minimum attendance?", "Which week are we in?"],
}


def _t(lang: str, id_text: str, en_text: str) -> str:
    return en_text if lang == "en" else id_text


def md_to_html(text: str) -> str:
    """The conversation layer writes light markdown (**bold**, '- ' lists); Telegram wants HTML."""
    out = []
    for line in text.split("\n"):
        line = html.escape(line, quote=False)
        line = re.sub(r"^\s*#{1,6}\s+(.+)$", r"<b>\1</b>", line)  # LLM headings → bold
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", line)
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        out.append(line)
    return "\n".join(out)


def render(result: dict, lang: str) -> list[dict]:
    """Reply blocks → Telegram messages ({text, reply_markup})."""
    messages, buffer = [], []

    def flush(markup=None):
        if buffer or markup:
            messages.append({"text": ("\n\n".join(buffer).strip() or "…")[:4000], "reply_markup": markup})
            buffer.clear()

    pending_id = result.get("pending_id")
    for b in result["blocks"]:
        kind = b["type"]
        if kind == "text":
            buffer.append(md_to_html(b["text"]))
        elif kind == "deadlines":
            lines = [f"📅 <b>{html.escape(b['range'])}</b>"]
            if not b["items"]:
                lines.append(_t(lang, "Tidak ada tenggat.", "Nothing due."))
            for i in b["items"]:
                flag = ("⚠️ <b>" + _t(lang, "HARI INI", "TODAY") + "</b> · ") if i["due_today"] else ""
                lines.append(f"{flag}<b>{html.escape(i['item'])}</b>\n   {html.escape(i['course'])} · {html.escape(i['due_text'])}")
            lines.append(f"<i>{_t(lang, 'Sumber', 'Source')}: {html.escape(b['source'])}</i>")
            buffer.append("\n".join(lines))
        elif kind == "answer":
            buffer.append(f"<blockquote>{html.escape(b['text'])}</blockquote>\n📖 <i>{html.escape(b['citation'])}</i>")
        elif kind == "confirm":
            flush({"inline_keyboard": [[
                {"text": "✅ " + b["yes"], "callback_data": f"c:{pending_id}:y"},
                {"text": b["no"], "callback_data": f"c:{pending_id}:n"},
            ]]})
        elif kind == "links":
            flush()
            buffer.append(_t(lang, "Tambahkan ke Google Calendar (opsional):", "Add to Google Calendar (optional):"))
            flush({"inline_keyboard": [[{"text": l["label"][:60], "url": l["href"]}]
                                       for l in b["links"] if l["href"].startswith("http")]})
        # "reference" is already stated in the text; "events" are turned into reminders by the chat service.
    if result.get("tool_calls") and os.environ.get("SIGAP_SHOW_TRACE", "1") != "0":
        calls = " · ".join(f"{c['tool']}" + (f" ({c['status']})" if c.get("status") else "") for c in result["tool_calls"])
        trace = f"<i>🔧 {html.escape(calls)} — via {backend_name()}</i>"
        if buffer or not messages:
            buffer.append(trace)
        else:  # don't send the trace as a lone message after the buttons
            messages[-1]["text"] += "\n\n" + trace
    flush()
    return messages


class TelegramBot:
    def __init__(self, token: str, api_base: str | None = None):
        base = (api_base or os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")).rstrip("/")
        self.api = f"{base}/bot{token}"
        self.http = httpx.AsyncClient(timeout=70)
        self.offset = 0
        self.username: str | None = None

    async def call(self, method: str, **params):
        r = await self.http.post(f"{self.api}/{method}", json={k: v for k, v in params.items() if v is not None})
        data = r.json()
        if not data.get("ok"):
            log.warning("Telegram %s failed: %s", method, data.get("description"))
        return data.get("result")

    async def send_text(self, chat_id, text: str, markup: dict | None = None) -> bool:
        res = await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup,
                              link_preview_options={"is_disabled": True})
        return res is not None

    async def send(self, chat_id, messages: list[dict]):
        for m in messages:
            await self.send_text(chat_id, m["text"], m["reply_markup"])

    async def send_login(self, who: Who, sender: dict, lang: str):
        """The "Masuk" button: a one-time web-login link tied to this chat (10 minutes)."""
        name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or None
        if sender.get("username"):
            name = f"{name or ''} (@{sender['username']})".strip()
        with session_scope() as db:
            state = create_link_request(db, CHANNEL, who.external_id, name)
        url = public_url() + f"/student?link={state}"
        text = _t(lang, "🔐 Hubungkan akun kampusmu: masuk dengan kode yang dikirim ke email kampus. Tautan berlaku 10 menit dan hanya untuk chat ini.",
                  "🔐 Link your campus account: sign in with a code sent to your campus email. The link is valid for 10 minutes and only for this chat.")
        if url.startswith("https://"):
            await self.send_text(int(who.external_id), text, {"inline_keyboard": [[{"text": _t(lang, "🔐 Masuk", "🔐 Sign in"), "url": url}]]})
        else:  # Telegram only accepts public https URLs on buttons
            await self.send_text(int(who.external_id), f"{text}\n{url}")

    async def run(self):
        me = await self.call("getMe") or {}
        self.username = me.get("username")
        if self.username:
            os.environ.setdefault("TELEGRAM_BOT_USERNAME", self.username)
        channels.register(CHANNEL, lambda ext, text: self.send_text(ext, text))
        await self.call("setMyCommands", commands=[
            {"command": "start", "description": "Mulai / Start"},
            {"command": "login", "description": "Hubungkan akun kampus / Link campus account"},
            {"command": "tiket", "description": "Status pertanyaanku ke staf / My tickets"},
            {"command": "saya", "description": "Akun yang terhubung / Linked account"},
            {"command": "token", "description": "Token MCP untuk agen AI-mu / MCP token"},
            {"command": "lang", "description": "Bahasa: /lang id | /lang en"},
            {"command": "unlink", "description": "Putuskan akun / Unlink account"},
        ])
        log.info("Telegram bot @%s polling", self.username)
        while True:
            try:
                updates = await self.call("getUpdates", offset=self.offset, timeout=50,
                                          allowed_updates=["message", "callback_query"]) or []
            except httpx.HTTPError as e:
                log.warning("getUpdates failed: %s", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                self.offset = u["update_id"] + 1
                try:
                    await self.on_update(u)
                except Exception:  # one bad update must not stop the bot
                    log.exception("update %s failed", u.get("update_id"))

    async def on_update(self, u: dict):
        if "callback_query" in u:
            return await self.on_button(u["callback_query"])
        msg = u.get("message") or {}
        text, chat = (msg.get("text") or "").strip(), msg.get("chat") or {}
        if not chat.get("id") or not text:
            return
        if chat.get("type", "private") != "private":  # personal data: private chats only
            return await self.send_text(chat["id"], "Sigap hanya melayani chat pribadi. / Sigap only works in private chats.")
        who = Who(CHANNEL, str(chat["id"]))
        sender = msg.get("from") or {}
        if text.startswith("/"):
            return await self.on_command(who, text, sender)
        typing = asyncio.create_task(self._typing(chat["id"]))  # the LLM can take ~20 s; keep "typing…" visible
        try:
            result = await asyncio.to_thread(chat_service.message, who, text, sender.get("language_code"))
        finally:
            typing.cancel()
        await self.send(chat["id"], render(result, chat_service.language(who)))
        if any(b["type"] == "link_required" for b in result["blocks"]) or (
                not chat_service.whoami(who) and _asks_personal(result)):
            await self.send_login(who, sender, chat_service.language(who))

    async def _typing(self, chat_id):
        while True:
            await self.call("sendChatAction", chat_id=chat_id, action="typing")
            await asyncio.sleep(4)

    async def on_button(self, q: dict):
        chat_id = q["message"]["chat"]["id"]
        _, pending_id, choice = (q.get("data") or "c::n").split(":", 2)
        await self.call("answerCallbackQuery", callback_query_id=q["id"])
        await self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=q["message"]["message_id"],
                        reply_markup={"inline_keyboard": []})
        who = Who(CHANNEL, str(chat_id))
        result = await asyncio.to_thread(chat_service.confirm, who, pending_id, choice == "y")
        await self.send(chat_id, render(result, chat_service.language(who)))

    async def on_command(self, who: Who, text: str, sender: dict):
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.split("@")[0].lower(), arg.strip()
        chat_id = int(who.external_id)
        lang = chat_service.language(who)
        if cmd == "/start" and arg:
            name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or None
            student = await asyncio.to_thread(chat_service.link, who, arg, name)
            if not student:
                return await self.send_text(chat_id, _t(
                    lang, "Tautan undangan tidak valid, sudah dipakai, atau kedaluwarsa. Minta tautan baru ke Layanan Akademik.",
                    "This invite link is invalid, already used or expired. Ask Academic Services for a new one."))
            await self.send_text(chat_id, _t(lang, f"✅ Akun terhubung: <b>{html.escape(student.name)}</b>. Sekarang aku bisa "
                                                   "menjawab tenggat dan ujianmu sendiri.",
                                             f"✅ Account linked: <b>{html.escape(student.name)}</b>. I can now answer "
                                             "about your own deadlines and exams."))
        if cmd == "/lang" and arg in ("id", "en"):
            chat_service.set_language(who, arg)
            lang = arg
        if cmd in ("/login", "/masuk"):
            return await self.send_login(who, sender, lang)
        if cmd in ("/start", "/help", "/lang"):
            student = chat_service.whoami(who)
            status = (_t(lang, f"Terhubung sebagai <b>{html.escape(student.name)}</b>.", f"Linked as <b>{html.escape(student.name)}</b>.")
                      if student else _t(lang, "Belum terhubung: buka tautan undangan dari kampus untuk melihat tenggatmu.",
                                         "Not linked yet: open your campus invite link to see your own deadlines."))
            keyboard = {"keyboard": [[{"text": q}] for q in EXAMPLES[lang]], "resize_keyboard": True,
                        "input_field_placeholder": _t(lang, "Tanya Sigap…", "Ask Sigap…")}
            await self.send_text(chat_id, md_to_html(intro(lang)) + "\n\n" + status + "\n" +
                                 _t(lang, "Perintah: /tiket · /saya · /token · /lang en", "Commands: /tiket · /saya · /token · /lang id"),
                                 keyboard)
            if not student:
                await self.send_login(who, sender, lang)
        elif cmd == "/tiket":
            await self.send_text(chat_id, await asyncio.to_thread(chat_service.tickets_text, who))
        elif cmd == "/saya":
            student = chat_service.whoami(who)
            await self.send_text(chat_id, _t(lang, f"Terhubung sebagai <b>{html.escape(student.name)}</b>." if student else "Belum terhubung.",
                                             f"Linked as <b>{html.escape(student.name)}</b>." if student else "Not linked."))
        elif cmd == "/token":
            token = await asyncio.to_thread(chat_service.new_token, who)
            if not token:
                return await self.send_text(chat_id, _t(lang, "Hubungkan akunmu dulu.", "Link your account first."))
            url = public_url() + "/mcp"
            await self.send_text(chat_id, _t(
                lang, "🔑 Token MCP pribadimu (hanya ditampilkan sekali, jangan dibagikan):",
                "🔑 Your personal MCP token (shown once, don't share it):") +
                f"\n<code>{token}</code>\n\n" + _t(lang, "Hubungkan agen AI-mu (Bob, Claude Code):", "Connect your AI agent (Bob, Claude Code):") +
                f"\n<code>claude mcp add --transport http sigap {url} --header \"Authorization: Bearer {token}\"</code>\n\n" +
                _t(lang, "Staf bisa mencabut token ini kapan saja.", "Staff can revoke this token at any time."))
        elif cmd == "/unlink":
            done = await asyncio.to_thread(chat_service.unlink, who)
            await self.send_text(chat_id, _t(lang, "Akun diputus dari chat ini." if done else "Chat ini belum terhubung.",
                                             "Account unlinked from this chat." if done else "This chat is not linked."))
        elif cmd == "/reset":
            chat_service.reset(who)
            await self.send_text(chat_id, _t(lang, "Percakapan direset.", "Conversation reset."))
        elif cmd == "/demoreminder":  # for recording the demo: a native reminder 15 seconds from now
            at = datetime.now(WIB) + timedelta(seconds=15)
            item = {"course": "Struktur Data", "item": "UTS Struktur Data", "due_text": "Sel, 20 Okt 2026 · 08:00–10:00 WIB"}
            with session_scope() as db:
                schedule_reminders(db, student_id=None, channel=CHANNEL, external_id=who.external_id, item=item,
                                   events=[{"kind": "reminder", "start": at.isoformat()}], lang=lang)
            await self.send_text(chat_id, _t(lang, "Pengingat demo dikirim dalam 15 detik.", "Demo reminder coming in 15 seconds."))
        else:
            await self.send_text(chat_id, "/start · /login · /tiket · /saya · /token · /lang id|en · /unlink")


def _asks_personal(result: dict) -> bool:
    """LLM mode: the agent told an unlinked student to link (its words vary), seen from the tool it called."""
    return any(c["tool"] in ("get_my_deadlines", "create_study_reminder") for c in result.get("tool_calls", []))


def start_if_configured() -> asyncio.Task | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.info("TELEGRAM_BOT_TOKEN not set: Telegram channel disabled")
        return None
    return asyncio.create_task(TelegramBot(token).run())
