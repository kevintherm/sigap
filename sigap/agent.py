"""Conversation layer for the student web chat (FR-11).

Plays the role Bob's Sigap mode plays over MCP, with the same rules: every date and rule
comes from a tool, actions need an explicit yes, replies follow the student's language,
and the source is always shown. Routing is deterministic so the demo never depends on
an LLM quota.
"""
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from . import tools  # routing helpers only (course names, window topics); answers come from tool_backend()
from .langflow_client import LangflowError, backend_name, tool_backend
from .textutil import detect_lang, fmt_dt

YES = re.compile(r"^\s*(ya|iya|iyaa|yes|y|yep|yup|ok|oke|okay|okey|boleh|sure|gas|kirim|kirimkan|lanjut|setuju|mau|please|do it|go ahead|sip)\b", re.I)
NO = re.compile(r"^\s*(tidak|nggak|ngga|gak|ga|no|nope|jangan|batal|cancel|skip|nanti( aja)?|gausah|ga usah|nggak usah)\b", re.I)
GREETING = re.compile(r"^\s*(hai|halo|hallo|hi|hello|hey|help|bantuan|menu|start|mulai|pagi|siang|sore|malam|selamat \w+)\W*$", re.I)
THANKS = re.compile(r"^\s*(makasih|terima kasih|thanks|thank you|thx|tq|mksh)\b", re.I)

DISHONEST = re.compile(
    r"\b(kerjain|kerjakan|kerjakanin|buatin|buatkan|bikinin|tulisin|tuliskan|jawabin|jawabkan|selesaikan)\b.*"
    r"\b(essay|esai|tugas|makalah|laporan|jawaban|kuis|quiz|ujian|soal|skripsi|pr)\b"
    r"|\b(do|write|finish|complete|solve|answer)\b.*\bmy\b.*\b(essay|assignment|homework|report|quiz|exam|paper)\b"
    r"|\bjoki\b",
    re.I,
)

PERIOD = re.compile(
    r"minggu ke|pekan ke|week (is it|are we|number)|which week|what week|current week|periode|study period|"
    r"masa (studi|kuliah)|kalender akademik|academic calendar|\bwindows?\b|lagi (masa|periode)|sekarang (masa|periode)",
    re.I,
)
TIME_Q = re.compile(r"\b(kapan|when|buka|dibuka|open|tutup|ditutup|close|closed|masih|still|batas|until|sampai|"
                    r"deadline|tenggat|periode|jadwal|udah lewat|sudah lewat|lewat)\b", re.I)
HOW_Q = re.compile(r"\b(bagaimana|gimana|cara|caranya|how|syarat|prosedur|procedure|steps|biaya|fee|berapa lama)\b", re.I)

DEADLINE_WORDS = re.compile(r"\b(deadline|deadlines|tenggat|due|tugas|kuis|quiz|quizzes|ujian|exam|exams|uts|uas|midterm|"
                            r"final|jadwal|schedule|assignment|assignments|apa saja|apa aja|what'?s due|dikumpul\w*|ngumpulin)\b", re.I)
WHEN_WORDS = re.compile(r"\b(kapan|when|minggu ini|pekan ini|this week|hari ini|today|tonight|malam ini|besok|tomorrow|"
                        r"minggu depan|next week|bulan ini|this month|\d+ hari|\d+ days|upcoming|apa saja|apa aja|"
                        r"what'?s due|list|daftar)\b", re.I)
REMIND = re.compile(r"\b(ingatkan|ingetin|ingatin|ingetkan|ingatkanku|remind|reminder|reminders|pengingat|alarm)\b", re.I)
POLICY_MARKERS = re.compile(r"\b(telat|terlambat|late|kalau|kalo|if|boleh|bisa|can i|could i|aturan|rule|rules|policy|"
                            r"syarat|prosedur|procedure|bagaimana|gimana|how|minimum|minimal|penalti|penalty|potongan|"
                            r"apa yang terjadi|what happens|resubmit|perpanjang\w*|extension|susulan|make-?up|dinilai|graded)\b", re.I)


@dataclass
class Session:
    lang: str = "id"
    pending: dict | None = None  # {"tool", "args", "id"}: an action waiting for the student's yes
    # Who is talking, filled in by the channel from the backend database. Langflow only ever gets `courses`.
    linked: bool = False
    student_name: str | None = None
    courses: list[str] | None = None
    make_reference: Callable[[], str] | None = None
    agent_session: str = field(default_factory=lambda: "s-" + uuid.uuid4().hex)  # Langflow memory key; not an identity
    greeted: bool = False
    turns: list = field(default_factory=list)


def _t(lang: str, id_text: str, en_text: str) -> str:
    return en_text if lang == "en" else id_text


def intro(lang: str = "id") -> str:
    return _t(
        lang,
        "Hai, aku **Sigap**, asisten AI layanan akademik. Aku bisa:\n"
        "- mengingatkan tenggat dan ujianmu (dari jadwal resmi),\n"
        "- menjawab aturan kampus dari dokumen resmi, selalu dengan sumbernya,\n"
        "- meneruskan pertanyaanmu ke staf kalau aku tidak yakin.\n\n"
        "Aku bisa salah, dan keputusan akademik tetap di tangan staf. Data di demo ini sintetis.",
        "Hi, I'm **Sigap**, an AI student-services assistant. I can:\n"
        "- remind you of deadlines and exams (from the official schedule),\n"
        "- answer campus rules from official documents, always with the source,\n"
        "- pass your question to staff when I'm not sure.\n\n"
        "I can be wrong, and academic decisions stay with staff. Data in this demo is synthetic.",
    )


class Reply:
    def __init__(self):
        self.blocks: list[dict] = []
        self.tool_calls: list[dict] = []

    def text(self, text: str):
        self.blocks.append({"type": "text", "text": text})

    def call(self, name: str, args: dict, result: dict):
        self.tool_calls.append({"tool": name, "args": args, "status": result.get("status")})
        return result

    def as_dict(self, session: Session) -> dict:
        return {"blocks": self.blocks, "tool_calls": self.tool_calls, "language": session.lang,
                "pending": session.pending["tool"] if session.pending else None}


def _offer_reminder(r: Reply, session: Session, item_id: str, lead: str = "", remind_at: str | None = None):
    lang = session.lang
    args = {"item_id": item_id, "student_confirmed": False, "language": lang}
    if remind_at:
        args["remind_at"] = remind_at
    preview = r.call("create_study_reminder", args, tool_backend().create_study_reminder(**args))
    if preview.get("status") == "INVALID_TIME":
        r.text((lead + "\n\n" if lead else "") + preview["text"])
        return
    if preview.get("status") != "NEEDS_CONFIRMATION":
        return
    evs = preview["events"]
    lines = []
    for ev in evs:
        a, b = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
        if ev["kind"] == "reminder":
            lines.append(f"- {_t(lang, 'Pengingat', 'Reminder')}: {fmt_dt(a, lang)}")
        else:
            lines.append(f"- {_t(lang, 'Blok belajar 90 menit', '90-minute study block')}: {fmt_dt(a, lang, b)}")
    r.text((lead + "\n\n" if lead else "") + _t(
        lang,
        "Mau kubuatkan ini di Google Calendar-mu?\n",
        "Want me to add these to your Google Calendar?\n",
    ) + "\n".join(lines) + _t(lang, "\n\nTidak ada yang kutulis ke kalender sebelum kamu bilang ya.",
                                "\n\nNothing goes into your calendar until you say yes."))
    r.blocks.append({"type": "confirm", "yes": _t(lang, "Ya, buatkan", "Yes, add them"), "no": _t(lang, "Tidak usah", "No thanks")})
    session.pending = {"tool": "create_study_reminder", "args": _reminder_args(item_id, preview), "id": uuid.uuid4().hex[:8]}


def _reminder_args(item_id: str, preview: dict) -> dict:
    """What the yes button creates: the item, plus the exact time the student saw (not their words again,
    so "in 30 minutes" doesn't drift between the offer and the yes)."""
    return {"item_id": item_id} | ({"remind_at": preview["remind_at"]} if preview.get("remind_at") else {})


def _offer_escalation(r: Reply, session: Session, question: str, checked: list[str]):
    lang = session.lang
    args = {"question": question, "checked_sources": checked, "language": lang, "student_confirmed": False}
    preview = r.call("escalate_to_student_services", args, tool_backend().escalate_to_student_services(**args))
    r.text(_t(
        lang,
        f"Aku bisa meneruskan pertanyaanmu ke **{preview['office']}** supaya dijawab staf. Yang dikirim hanya pertanyaanmu, "
        "kategorinya, dan sumber yang sudah kucek — tanpa NIM atau data pribadi. Kirim sekarang?",
        f"I can forward your question to **{preview['office']}** so a staff member answers it. Only your question, its "
        "category and the sources I checked are sent — no student ID or personal data. Send it now?",
    ))
    r.blocks.append({"type": "confirm", "yes": _t(lang, "Ya, kirimkan", "Yes, send it"), "no": _t(lang, "Tidak", "No")})
    session.pending = {"tool": "escalate_to_student_services",
                       "args": {"question": question, "checked_sources": checked, "category": preview["category"]},
                       "id": uuid.uuid4().hex[:8]}


def _run_pending(r: Reply, session: Session):
    p, session.pending = session.pending, None
    args = p["args"] | {"student_confirmed": True, "language": session.lang}
    if p["tool"] == "escalate_to_student_services" and session.make_reference:
        args["reference"] = session.make_reference()
    if p["tool"] == "create_study_reminder":
        res = r.call("create_study_reminder", args, tool_backend().create_study_reminder(**args))
        r.text(res["text"])
        if res.get("status") == "CREATED":
            links = [{"label": ev["title"], "href": ev["google_calendar_link"]} for ev in res["events"]]
            if backend_name() == "local":  # the .ics is only served when the tools ran in this process
                links.append({"label": _t(session.lang, "⬇️ Unduh semua (.ics)", "⬇️ Download all (.ics)"), "href": res["ics_file"]})
            r.blocks.append({"type": "links", "links": links})
            r.blocks.append({"type": "events", "item": res["item"], "events": res["events"]})
    else:
        res = r.call("escalate_to_student_services", args, tool_backend().escalate_to_student_services(**args))
        r.text(res["text"])
        if res.get("reference"):
            r.blocks.append({"type": "reference", "reference": res["reference"], "office": res["office"],
                             "category": res.get("category", "umum"), "question": args["question"]})


def resolve_pending(session: Session, accept: bool, pending_id: str | None = None) -> dict:
    """A button press on a confirm offer. Only this (or a typed yes) can trigger the action."""
    r = Reply()
    if not session.pending or (pending_id and session.pending.get("id") != pending_id):
        r.text(_t(session.lang, "Tawaran itu sudah kedaluwarsa. Tanyakan lagi ya.", "That offer has expired. Please ask again."))
        return r.as_dict(session)
    try:
        if accept:
            _run_pending(r, session)
        else:
            session.pending = None
            r.text(_t(session.lang, "Oke, tidak jadi. Ada lagi yang bisa kubantu?", "Okay, cancelled. Anything else I can help with?"))
    except LangflowError:
        r.blocks, r.tool_calls = [], []
        r.text(_unavailable(session.lang))
    return r.as_dict(session)


def _unavailable(lang: str) -> str:
    return _t(lang, "Maaf, layanan Sigap sedang tidak bisa dihubungi. Coba lagi sebentar lagi.",
              "Sorry, Sigap's services can't be reached right now. Please try again in a moment.")


def handle(session: Session, message: str) -> dict:
    try:
        return _handle(session, message)
    except LangflowError:
        r = Reply()
        r.text(_unavailable(session.lang))
        return r.as_dict(session)


def handle_with_agent(session: Session, message: str, run_agent: Callable) -> dict:
    """LLM mode: the Langflow agent (Gemini) understands the message and calls the tools. Proposed actions are
    taken from the tools' real outputs, never from the model's text, and still need the student's button press.
    Falls back to the rule-based router if the agent is unavailable."""
    msg = message.strip()
    session.lang = detect_lang(msg, session.lang)
    if session.pending and (YES.match(msg) or NO.match(msg)):
        return resolve_pending(session, bool(YES.match(msg)), session.pending["id"])
    if not msg or GREETING.match(msg):
        return handle(session, msg)
    session.pending = None
    session.turns.append(msg)
    try:
        out = run_agent(msg, session.agent_session, session.courses, session.linked)
    except LangflowError:
        res = handle(session, msg)
        res["tool_calls"].insert(0, {"tool": "agent", "args": {}, "status": "FALLBACK"})
        return res
    r = Reply()
    answer = "\n".join(line for line in out["text"].splitlines() if not line.strip().startswith("[SIGAP CONTEXT")).strip()
    r.text(answer or _unavailable(session.lang))
    for st in out["steps"]:
        res = st["result"] or {}
        r.tool_calls.append({"tool": st["tool"], "args": {"input": st["input"]}, "status": res.get("status")})
    proposal = next((st for st in reversed(out["steps"]) if (st["result"] or {}).get("status") == "NEEDS_CONFIRMATION"), None)
    policy = [(st["result"] or {}).get("status") for st in out["steps"] if st["tool"] == "answer_campus_policy"]
    if not proposal and "NOT_FOUND" in policy and "FOUND" not in policy:
        # The handoff offer after NOT_FOUND is a safety rule (PRD), so it must not depend on the model remembering
        # to call escalate_to_student_services: the backend offers it whenever no rule answered the question.
        cat = tools.guess_category(msg)
        office_id, office_en, _ = tools.OFFICES[cat]
        proposal = {"tool": "escalate_to_student_services", "input": None,
                    "result": {"status": "NEEDS_CONFIRMATION", "category": cat,
                               "office": office_en if session.lang == "en" else office_id}}
        r.tool_calls.append({"tool": "escalate_to_student_services", "args": {"by": "backend"}, "status": "NEEDS_CONFIRMATION"})
    if proposal:
        res, lang = proposal["result"], session.lang
        # The confirm prompt is written here from the tool's own output, never taken from the model's wording,
        # so the button always does exactly what the line above it says.
        if proposal["tool"] == "create_study_reminder":
            item = res.get("item") or {}
            if session.linked and item.get("course_code") in (session.courses or []):
                session.pending = {"tool": "create_study_reminder", "args": _reminder_args(item["item_id"], res),
                                   "id": uuid.uuid4().hex[:8]}
                lines = []
                for ev in res.get("events", []):
                    a_, b_ = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
                    lines.append(f"- {_t(lang, 'Pengingat', 'Reminder')}: {fmt_dt(a_, lang)}" if ev["kind"] == "reminder" else
                                 f"- {_t(lang, 'Blok belajar', 'Study block')}: {fmt_dt(a_, lang, b_)}")
                r.text(_t(lang, f"⏰ Buat pengingat untuk **{item['item']}**?", f"⏰ Set reminders for **{item['item']}**?")
                       + "\n" + "\n".join(lines))
                r.blocks.append({"type": "confirm", "yes": _t(lang, "Ya, buat pengingat", "Yes, set reminders"),
                                 "no": _t(lang, "Tidak usah", "No thanks")})
        elif proposal["tool"] == "escalate_to_student_services":
            question = msg
            try:
                question = json.loads(proposal["input"]).get("question") or msg
            except (TypeError, ValueError, AttributeError):
                pass
            session.pending = {"tool": "escalate_to_student_services", "id": uuid.uuid4().hex[:8],
                               "args": {"question": question, "category": res.get("category")}}
            office = res.get("office") or _t(lang, "layanan mahasiswa", "student services")
            r.text(_t(lang, f"📨 Teruskan pertanyaanmu ke **{office}**? Hanya pertanyaan dan kategorinya yang dikirim, tanpa NIM.",
                      f"📨 Forward your question to **{office}**? Only the question and its category are sent, no student ID."))
            r.blocks.append({"type": "confirm", "yes": _t(lang, "Ya, teruskan ke staf", "Yes, send to staff"),
                             "no": _t(lang, "Tidak", "No")})
    return r.as_dict(session)


def _handle(session: Session, message: str) -> dict:
    r = Reply()
    msg = message.strip()
    session.lang = detect_lang(msg, session.lang)
    lang = session.lang
    session.turns.append(msg)

    if not msg:
        r.text(intro(lang))
        return r.as_dict(session)

    if session.pending:
        if YES.match(msg):
            _run_pending(r, session)
            return r.as_dict(session)
        if NO.match(msg):
            session.pending = None
            r.text(_t(lang, "Oke, tidak jadi. Ada lagi yang bisa kubantu?", "Okay, cancelled. Anything else I can help with?"))
            return r.as_dict(session)
        session.pending = None  # new question: drop the stale offer

    if GREETING.match(msg):
        r.text(intro(lang))
        return r.as_dict(session)
    if THANKS.match(msg):
        r.text(_t(lang, "Sama-sama! Semangat belajarnya 💪", "You're welcome! Good luck with your studies 💪"))
        return r.as_dict(session)

    if DISHONEST.search(msg):
        _handle_dishonest(r, session, msg)
    elif REMIND.search(msg) and not POLICY_MARKERS.search(msg):
        _handle_remind(r, session, msg)
    elif _is_period_question(msg):
        args = {"topic": msg, "language": lang}
        res = r.call("get_study_period", args, tool_backend().get_study_period(**args))
        r.text(res["text"])
    elif _is_deadline_question(msg):
        _handle_deadlines(r, session, msg)
    else:
        _handle_policy(r, session, msg)
    return r.as_dict(session)


def _is_period_question(msg: str) -> bool:
    if PERIOD.search(msg):
        return True
    topic = tools.detect_window_topic(msg)
    if topic in (None, "uts", "uas"):  # exam dates are per course: the deadline tool answers those
        return False
    return bool(TIME_Q.search(msg)) and not HOW_Q.search(msg) and tools.find_course(msg) is None


def _is_deadline_question(msg: str) -> bool:
    if POLICY_MARKERS.search(msg):
        return False
    has_course = tools.find_course(msg) is not None
    asks_deadline, asks_when = bool(DEADLINE_WORDS.search(msg)), bool(WHEN_WORDS.search(msg))
    return (asks_deadline and (asks_when or has_course)) or (has_course and asks_when)


def _needs_link(r: Reply, lang: str):
    r.text(_t(lang,
              "Untuk melihat tenggat dan ujianmu, hubungkan dulu akun kampusmu lewat tombol **Masuk** di bawah "
              "(atau ketik /login). Aturan kampus dan kalender akademik tetap bisa kamu tanyakan sekarang.",
              "To see your own deadlines and exams, link your campus account first with the **Sign in** button below "
              "(or type /login). You can still ask about campus rules and the academic calendar now."))
    r.blocks.append({"type": "link_required"})


def _handle_deadlines(r: Reply, session: Session, msg: str):
    lang = session.lang
    if not session.linked:
        return _needs_link(r, lang)
    args = {"request": msg, "language": lang, "courses": session.courses or []}
    res = r.call("get_my_deadlines", args, tool_backend().get_my_deadlines(**args))
    r.blocks.append({"type": "deadlines", "range": res["range"]["label"], "items": res["items"], "source": res["source"]})
    if not res["items"]:
        r.text(res["text"])
        return
    today = [i for i in res["items"] if i["due_today"]]
    if today:
        i = today[0]
        r.text(_t(lang, f"⚠️ **{i['course']}: {i['item']}** tenggatnya malam ini. Jangan lupa unggah ke LMS.",
                  f"⚠️ **{i['course']}: {i['item']}** is due tonight. Remember to upload it to the LMS."))
    exams = [i for i in res["items"] if i["type"] == "exam"]
    if res["course_filter"] and len(exams) >= 1 and res["type_filter"] == "exam":
        e = exams[0]
        _offer_reminder(r, session, e["item_id"], _t(
            lang, f"**{e['item']}**: {e['due_text']}, {e['where']}.", f"**{e['item']}**: {e['due_text']}, {e['where']}."))


def _handle_remind(r: Reply, session: Session, msg: str):
    """"Ingatkan aku jam 8 malam soal Tugas 3 Basis Data": find the item, then offer a reminder at that time
    (the tool parses the time from the message) or the default plan when no time is given."""
    lang = session.lang
    if not session.linked:
        return _needs_link(r, lang)
    course = tools.find_course(msg)
    args = {"request": "next 45 days", "course": course.code if course else None, "language": lang,
            "courses": session.courses or []}
    res = r.call("get_my_deadlines", args, tool_backend().get_my_deadlines(**args))
    item = _pick_item(msg, res["items"])
    if not item:
        r.text(_t(lang, "Pengingat untuk tugas atau ujian yang mana? Sebutkan mata kuliahnya, misalnya "
                        "\"ingatkan Tugas 4 Struktur Data jam 8 malam\".",
                  "Which deadline or exam is the reminder for? Name the course, e.g. "
                  "\"remind me about Data Structures assignment 4 at 8pm\"."))
        return
    has_time = tools.parse_remind_at(msg, tools.now(), datetime.max.replace(tzinfo=tools.WIB))[1] != "unclear"
    _offer_reminder(r, session, item["item_id"], f"**{item['course']}: {item['item']}** — {item['due_text']}.",
                    remind_at=msg if has_time else None)


def _pick_item(msg: str, items: list[dict]) -> dict | None:
    """The schedule item the message names: its number ("Tugas 3"), its kind (UTS, quiz) and title words."""
    if not items:
        return None
    words = set(re.findall(r"[a-z]+|\d+", msg.lower()))
    kinds = {"exam": {"uts", "uas", "ujian", "exam", "midterm", "final"}, "quiz": {"kuis", "quiz"},
             "project": {"proyek", "project", "laporan", "report"}}

    def score(i):
        title = set(re.findall(r"[a-z]+|\d+", i["item"].lower()))
        sc = 3 * len({w for w in words & title if w.isdigit()}) + len({w for w in words & title if not w.isdigit()})
        sc += 2 * bool(words & kinds.get(i["type"], {"tugas", "assignment", "essay", "esai"}))
        return sc
    best = max(items, key=score)  # ties keep the soonest (items are sorted by due date)
    return best if score(best) > 0 or len({i["course"] for i in items}) == 1 else None


def _handle_policy(r: Reply, session: Session, msg: str):
    lang = session.lang
    args = {"question": msg, "language": lang}
    res = r.call("answer_campus_policy", args, tool_backend().answer_campus_policy(**args))
    if res["status"] == "FOUND":
        r.blocks.append({"type": "answer", "text": res["answer"], "citation": res["citation_text"]})
        return
    r.text(res["text"])
    _offer_escalation(r, session, msg, res["checked_sources"] + [
        _t(lang, "Kalender Akademik Ganjil 2026/2027", "Academic Calendar Odd 2026/2027"),
        _t(lang, "Jadwal mata kuliah resmi", "Official course schedule"),
    ])


def _handle_dishonest(r: Reply, session: Session, msg: str):
    lang = session.lang
    r.text(_t(
        lang,
        "Maaf, aku tidak bisa mengerjakan tugas untukmu — itu termasuk pelanggaran integritas akademik "
        "(Pedoman Akademik v1.2, Pasal 4.4), dan karyamu harus tetap karyamu. Tapi aku bisa bantu kamu menyelesaikannya tepat waktu.",
        "Sorry, I can't do the assignment for you — that would break academic integrity "
        "(Academic Handbook v1.2, Article 4.4), and your work needs to stay yours. But I can help you get it done on time.",
    ))
    if not session.linked:
        return
    # Find the assignment they mean through the schedule tool, so the offer has a real date.
    course = tools.find_course(msg)
    args = {"request": "next 45 days", "course": course.code if course else None, "language": lang,
            "courses": session.courses or []}
    res = r.call("get_my_deadlines", args, tool_backend().get_my_deadlines(**args))
    upcoming = [i for i in res["items"] if i["type"] != "exam"]
    if re.search(r"essay|esai", msg.lower()):
        upcoming = [i for i in upcoming if re.search(r"essay|esai", i["item"].lower())] or upcoming
    if upcoming:
        item = upcoming[0]
        _offer_reminder(r, session, item["item_id"], _t(
            lang,
            f"**{item['course']}: {item['item']}** tenggatnya {item['due_text']}. "
            "Rencana belajar: pecah jadi kerangka → draf → revisi, satu blok per sesi.",
            f"**{item['course']}: {item['item']}** is due {item['due_text']}. "
            "Study plan: split it into outline → draft → revision, one block per session.",
        ))
