"""The five Sigap tools from the PRD. Same names and contracts as the Langflow flows.

Each tool returns a dict: structured fields for programs, plus `text` ready to show a
student. Dates are only read from data tables here; nothing computes a date from model output.
"""
import csv
import json
import os
import re
import smtplib
import uuid
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from urllib.parse import quote

from . import data
from .config import OUTBOX_DIR, STUDENT_SERVICES_INBOX, WIB, now
from .retrieval import is_confident, search, supports
from .textutil import detect_lang, fmt_date, fmt_dt

TYPE_LABEL = {
    "id": {"assignment": "Tugas", "quiz": "Kuis", "project": "Proyek", "exam": "Ujian"},
    "en": {"assignment": "Assignment", "quiz": "Quiz", "project": "Project", "exam": "Exam"},
}


def _lang(lang: str | None, text: str = "") -> str:
    return lang if lang in ("id", "en") else detect_lang(text)


# ---------------------------------------------------------------- shared lookups

def find_course(text: str) -> data.Course | None:
    t = f" {text.lower()} "
    best, best_len = None, 0
    for c in data.courses().values():
        for a in (*c.aliases, c.name_id.lower(), c.name_en.lower(), c.code.lower()):
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t) and len(a) > best_len:
                best, best_len = c, len(a)
    return best


def find_item(item_id: str) -> data.ScheduleItem | None:
    return next((i for i in data.schedule() if i.item_id.lower() == item_id.strip().lower()), None)


def _end_of_day(d) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=WIB)


def parse_range(text: str, n: datetime, open_ended: bool) -> tuple[datetime, datetime, str]:
    """Turns 'this week', 'besok', 'next 14 days'… into a [from, to] window starting now."""
    t = text.lower()
    today = n.date()
    if re.search(r"hari ini|today|tonight|malam ini|nanti malam", t):
        return n, _end_of_day(today), "today"
    if re.search(r"\bbesok\b|tomorrow", t):
        d = today + timedelta(days=1)
        return datetime.combine(d, time(0), tzinfo=WIB), _end_of_day(d), "tomorrow"
    if re.search(r"minggu depan|pekan depan|next week", t):
        a = today + timedelta(days=7)
        return datetime.combine(a, time(0), tzinfo=WIB), _end_of_day(a + timedelta(days=6)), "next_week"
    m = re.search(r"(\d+)\s*(hari|days?)", t)
    if m:
        days = max(1, min(int(m.group(1)), 120))
        return n, _end_of_day(today + timedelta(days=days - 1)), f"{days}_days"
    if re.search(r"bulan ini|this month", t):
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return n, _end_of_day(nxt - timedelta(days=1)), "this_month"
    if re.search(r"minggu ini|pekan ini|this week|seminggu|week", t) or not open_ended:
        return n, _end_of_day(today + timedelta(days=6)), "7_days"
    return n, _end_of_day(today + timedelta(days=180)), "upcoming"


def _range_label(label: str, a: datetime, b: datetime, lang: str) -> str:
    span = f"{fmt_date(a.date(), lang)} – {fmt_date(b.date(), lang)}"
    names = {
        "id": {"today": "hari ini", "tomorrow": "besok", "next_week": "minggu depan", "7_days": "7 hari ke depan",
               "this_month": "bulan ini", "upcoming": "yang akan datang"},
        "en": {"today": "today", "tomorrow": "tomorrow", "next_week": "next week", "7_days": "the next 7 days",
               "this_month": "this month", "upcoming": "upcoming"},
    }[lang]
    if label.endswith("_days") and label not in names:
        name = f"{label.split('_')[0]} hari ke depan" if lang == "id" else f"the next {label.split('_')[0]} days"
    else:
        name = names[label]
    return name if label == "upcoming" else f"{name} ({span})"


def item_dict(i: data.ScheduleItem, lang: str, n: datetime) -> dict:
    hours_left = (i.due - n).total_seconds() / 3600
    return {
        "item_id": i.item_id,
        "course_code": i.course.code,
        "course": i.course.name(lang),
        "item": i.title(lang),
        "type": i.type,
        "type_label": TYPE_LABEL[lang][i.type],
        "due": i.due.isoformat(),
        "end": i.end.isoformat() if i.end else None,
        "due_text": fmt_dt(i.due, lang, i.end),
        "where": i.where,
        "source": i.source,
        "due_today": i.due.date() == n.date(),
        "hours_left": round(hours_left, 1),
    }


# ---------------------------------------------------------------- tool 1

def get_my_deadlines(request: str = "this week", course: str | None = None, item_type: str | None = None,
                     language: str | None = None, courses: list[str] | str | None = None) -> dict:
    """Lists the student's upcoming deadlines and exams from the official course schedule.
    courses: the student's enrolled course codes (the backend sends these; never a student identity)."""
    lang = _lang(language, request)
    n = now()
    text = f"{request} {course or ''}".lower()
    c = find_course(course) if course else find_course(request)

    kind = (item_type or "").lower() or None
    subtype = None
    if re.search(r"\b(uts|midterm|mid-term|tengah semester)\b", text):
        kind, subtype = "exam", "UTS"
    elif re.search(r"\b(uas|final exam|finals|akhir semester)\b", text):
        kind, subtype = "exam", "UAS"
    elif re.search(r"\b(ujian|exam|exams|test)\b", text):
        kind = "exam"
    elif re.search(r"\b(kuis|quiz|quizzes)\b", text):
        kind = "quiz"

    if isinstance(courses, str):
        courses = [x.strip() for x in courses.split(",") if x.strip()]
    enrolled = {x.upper() for x in courses} if courses is not None else None
    a, b, label = parse_range(text, n, open_ended=bool(c or kind))
    items = [
        i for i in data.schedule()
        if a <= i.due <= b
        and (enrolled is None or i.course.code in enrolled)
        and (c is None or i.course.code == c.code)
        and (kind is None or i.type == kind)
        and (subtype is None or i.item_id.endswith(subtype))
    ]
    rows = [item_dict(i, lang, n) for i in items]
    rng = _range_label(label, a, b, lang)

    scope = (c.name(lang) + " · ") if c else ""
    if not rows:
        msg = (f"Tidak ada tenggat {scope}untuk {rng} di jadwal resmi." if lang == "id"
               else f"Nothing {scope}is due in {rng} on the official schedule.")
    else:
        head = (f"Tenggat {scope}{rng} — {len(rows)} item:" if lang == "id"
                else f"Due {scope}{rng} — {len(rows)} item(s):")
        lines = [head]
        for r in rows:
            flag = ("⚠️ HARI INI · " if lang == "id" else "⚠️ TODAY · ") if r["due_today"] else ""
            lines.append(f"- {flag}{r['due_text']} — {r['course']}: {r['item']} ({r['type_label']})")
        msg = "\n".join(lines)
    src = "Jadwal mata kuliah resmi (tabel jadwal)" if lang == "id" else "Official course schedule (schedule table)"
    return {
        "tool": "get_my_deadlines",
        "language": lang,
        "now": n.isoformat(),
        "range": {"from": a.isoformat(), "to": b.isoformat(), "label": rng},
        "course_filter": c.code if c else None,
        "type_filter": kind,
        "items": rows,
        "source": src,
        "text": f"{msg}\n\nSumber: {src}" if lang == "id" else f"{msg}\n\nSource: {src}",
    }


# ---------------------------------------------------------------- tool 2

WINDOW_TOPICS = {
    "krs": r"\bkrs\b|registrasi|registration|register|enrol",
    "add-drop": r"add.?drop|perubahan krs|tambah.?batal|ubah krs",
    "cuti": r"\bcuti\b|\bleave\b",
    "pembatalan": r"pembatalan|batal(kan)? (mata kuliah|matkul)|withdraw|\bdrop\b",
    "ukt-2": r"\bukt\b|bayar|pembayaran|tuition|payment|cicilan|installment",
    "uts-susulan": r"susulan|make.?up",
    "banding-nilai": r"banding|appeal",
    "nilai": r"nilai (akhir )?(keluar|publikasi|diumumkan)|publikasi nilai|grades? (out|release|published)",
    "uts": r"\buts\b|midterm",
    "uas": r"\buas\b|final exam|finals",
}


def detect_window_topic(text: str) -> str | None:
    t = text.lower()
    for key, pat in WINDOW_TOPICS.items():
        if re.search(pat, t):
            return key
    return None


def get_study_period(on_date: str | None = None, topic: str | None = None, language: str | None = None) -> dict:
    """Tells which academic week and period a date falls in and which windows are open."""
    lang = _lang(language, topic or "")
    n = now()
    d = datetime.fromisoformat(on_date).date() if on_date else n.date()
    cal = data.calendar()
    term = next((e for e in cal if e.kind == "term" and e.start <= d <= e.end), None)
    period = next((e for e in cal if e.kind == "period" and e.start <= d <= e.end), None)
    week = (d - term.start).days // 7 + 1 if term else None
    windows = [e for e in cal if e.kind == "window"]
    open_w = [e for e in windows if e.start <= d <= e.end]
    soon = [e for e in windows if d < e.start <= d + timedelta(days=30)]

    topic_key = (topic if topic in {e.key for e in windows} else detect_window_topic(topic or "")) if topic else None
    topic_entry = None
    if topic_key:
        cands = sorted((e for e in windows if e.key.startswith(topic_key)), key=lambda e: (e.end < d, e.start))
        topic_entry = cands[0] if cands else None

    def wline(e) -> str:
        if e.start == e.end:
            span = fmt_date(e.start, lang)
        else:
            span = f"{fmt_date(e.start, lang)} – {fmt_date(e.end, lang)}"
        return f"{e.name(lang)}: {span}"

    lines = []
    if topic_entry:
        e = topic_entry
        left = (e.end - d).days
        if e.start <= d <= e.end:
            lines.append(f"✅ {e.name(lang)} " + (f"MASIH DIBUKA sampai {fmt_date(e.end, lang)} (sisa {left} hari)."
                                                if lang == "id" else
                                                f"is OPEN until {fmt_date(e.end, lang)} ({left} days left)."))
        elif d < e.start:
            lines.append(f"⏳ {e.name(lang)} " + (f"BELUM DIBUKA — dibuka {fmt_date(e.start, lang)} s.d. {fmt_date(e.end, lang)}."
                                                 if lang == "id" else
                                                 f"is NOT OPEN YET — runs {fmt_date(e.start, lang)} to {fmt_date(e.end, lang)}."))
        else:
            lines.append(f"⛔ {e.name(lang)} " + (f"SUDAH DITUTUP pada {fmt_date(e.end, lang)}."
                                                 if lang == "id" else f"CLOSED on {fmt_date(e.end, lang)}."))
        if e.note(lang):
            lines.append(f"   {e.note(lang)}")
        lines.append("")

    if term:
        if lang == "id":
            lines.append(f"Hari ini {fmt_date(d, lang)}: {term.name(lang)}, minggu ke-{week}"
                         + (f" — {period.name(lang)}." if period else "."))
        else:
            lines.append(f"Today is {fmt_date(d, lang)}: {term.name(lang)}, week {week}"
                         + (f" — {period.name(lang)}." if period else "."))
    else:
        lines.append("Tanggal ini di luar semester berjalan." if lang == "id" else "This date is outside the current term.")
    if open_w:
        lines.append("Sedang dibuka:" if lang == "id" else "Open now:")
        lines += [f"- {wline(e)}" for e in open_w]
    if soon:
        lines.append("Segera (30 hari ke depan):" if lang == "id" else "Coming up (next 30 days):")
        lines += [f"- {wline(e)}" for e in soon]
    src = "Kalender Akademik Ganjil 2026/2027 (tabel kalender)" if lang == "id" else "Academic Calendar Odd 2026/2027 (calendar table)"
    lines.append(f"\n{'Sumber' if lang == 'id' else 'Source'}: {src}")

    def wd(e):
        return {"key": e.key, "name": e.name(lang), "start": e.start.isoformat(), "end": e.end.isoformat(), "note": e.note(lang)}

    return {
        "tool": "get_study_period",
        "language": lang,
        "date": d.isoformat(),
        "term": term.name(lang) if term else None,
        "week": week,
        "period": period.name(lang) if period else None,
        "open_windows": [wd(e) for e in open_w],
        "upcoming_windows": [wd(e) for e in soon],
        "topic": wd(topic_entry) | {"status": "open" if topic_entry.start <= d <= topic_entry.end
                                    else "not_yet" if d < topic_entry.start else "closed"} if topic_entry else None,
        "source": src,
        "text": "\n".join(lines).strip(),
    }


# ---------------------------------------------------------------- tool 3

def answer_campus_policy(question: str, language: str | None = None, support_text: str | None = None) -> dict:
    """Answers a campus-rule question using only the uploaded official documents, or NOT_FOUND.
    support_text: the student's original message; when given, the article must match it too."""
    lang = _lang(language, support_text or question)
    hits = search(question)
    top = hits[0] if hits else None
    if top and support_text and not supports(support_text, top.section.number):
        top = None  # the (rewritten) query matched, the student's own words do not: don't stretch an article
    checked = [f"{d.title(lang)} v{d.meta.get('version')}" for d in data.documents()]
    if not is_confident(top):
        msg = ("Aku tidak menemukan aturan yang menjawab pertanyaan ini di dokumen resmi kampus, jadi aku tidak akan menebak."
               if lang == "id" else
               "I couldn't find a rule that answers this in the campus's official documents, so I won't guess.")
        return {
            "tool": "answer_campus_policy",
            "status": "NOT_FOUND",
            "language": lang,
            "checked_sources": checked,
            "closest_section": f"§{top.section.number} {top.section.title(lang)}" if top else None,
            "text": msg,
        }
    doc, s = top.doc, top.section
    eff = datetime.fromisoformat(doc.meta["effective"]).date()
    citation = {
        "document": doc.title(lang),
        "version": doc.meta.get("version"),
        "effective": doc.meta["effective"],
        "section": s.number,
        "section_title": s.title(lang),
    }
    cite = (f"{doc.title(lang)} v{citation['version']} (berlaku {fmt_date(eff, lang)}), Pasal {s.number} — {s.title(lang)}"
            if lang == "id" else
            f"{doc.title(lang)} v{citation['version']} (effective {fmt_date(eff, lang)}), Article {s.number} — {s.title(lang)}")
    return {
        "tool": "answer_campus_policy",
        "status": "FOUND",
        "language": lang,
        "answer": s.text(lang),
        "citation": citation,
        "citation_text": cite,
        "checked_sources": checked,
        "text": f"{s.text(lang)}\n\n{'Sumber' if lang == 'id' else 'Source'}: {cite}",
    }


# ---------------------------------------------------------------- tool 4

def plan_reminders(item: data.ScheduleItem, n: datetime) -> dict:
    """Default plan: a reminder 3 days before and two 90-minute evening study blocks.
    Every time is derived from the schedule row, never from the model."""
    due_day = item.due.date()
    reminder = datetime.combine(due_day - timedelta(days=3), time(19, 0), tzinfo=WIB)
    blocks = []
    for back in (2, 1):
        start = datetime.combine(due_day - timedelta(days=back), time(19, 30), tzinfo=WIB)
        blocks.append((start, start + timedelta(minutes=90)))
    if item.type == "exam" and item.due.hour >= 19:  # evening exam: last block the same morning instead
        blocks[-1] = (item.due.replace(hour=9, minute=0), item.due.replace(hour=10, minute=30))
    blocks = [(a, b) for a, b in blocks if a > n and b < item.due]
    if reminder <= n:
        reminder = item.due - timedelta(hours=2)
        if reminder <= n:
            reminder = None
    return {"reminder": reminder, "blocks": blocks}


_WEEKDAYS = {"senin": 0, "monday": 0, "selasa": 1, "tuesday": 1, "rabu": 2, "wednesday": 2, "kamis": 3, "thursday": 3,
             "jumat": 4, "jum'at": 4, "friday": 4, "sabtu": 5, "saturday": 5, "hari minggu": 6, "sunday": 6}
_MONTH_WORDS = {"jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2, "mar": 3, "maret": 3,
                "march": 3, "apr": 4, "april": 4, "mei": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7,
                "july": 7, "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
                "okt": 10, "oktober": 10, "oct": 10, "october": 10, "nov": 11, "november": 11, "des": 12, "desember": 12,
                "dec": 12, "december": 12}
_UNIT = r"(menit|mins?|minutes?|jam|hours?|hrs?|hari|days?)"
_PART_OF_DAY = [(r"\b(pagi|morning)\b", "am", 7), (r"\b(siang|noon|midday)\b", "noon", 12),
                (r"\b(sore|afternoon)\b", "pm", 16), (r"\b(malam|evening|night|tonight)\b", "pm", 20)]


def _delta(num: str, unit: str) -> timedelta:
    k = 1 if num in ("se", "a", "an", "satu", "one") else float(num.replace(",", "."))
    if unit.startswith(("menit", "min")):
        return timedelta(minutes=k)
    if unit.startswith(("jam", "hour", "hr")):
        return timedelta(hours=k)
    return timedelta(days=k)


def parse_remind_at(text: str, n: datetime, due: datetime) -> tuple[datetime | None, str | None]:
    """Turns the student's own words ("jam 8 malam", "besok 07.00", "2 jam sebelum deadline", "H-1",
    "in 30 minutes", or an ISO time) into an exact time. Returns (time, None) or (None, reason) where reason is
    'unclear', 'past' or 'after_due'. Done in code so the model never computes a time."""
    t = " " + (text or "").lower().replace("’", "'") + " "
    at = None
    try:  # an exact ISO time (what the backend stores between the preview and the yes)
        iso = datetime.fromisoformat(text.strip())
        at = iso if iso.tzinfo else iso.replace(tzinfo=WIB)
    except ValueError:
        pass
    # explicit clock time: 20:00 / 20.00 / jam 8 / pukul 8 / at 8 / 8pm / 8 am
    hour = minute = None
    m = (re.search(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm|a\.m\.|p\.m\.)?(?!\d)", t)
         or re.search(r"\b(?:jam|pukul|pkl\.?|at|@)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b", t)
         or re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t))
    if m and not re.search(r"^\s*" + _UNIT + r"\s*(sebelum|before|lagi|from now)", t[m.end():]):
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm:
            for pat, kind, _ in _PART_OF_DAY:
                if re.search(pat, t):
                    if kind == "pm" and hour < 12:
                        hour += 12
                    elif kind == "noon" and hour < 6:  # "jam 1 siang" = 13:00
                        hour += 12
                    break
        if hour > 23 or minute > 59:
            return None, "unclear"
    default_hour = next((h for pat, _, h in _PART_OF_DAY if re.search(pat, t)), 19)

    if at is None and (m := re.search(r"\b(\d+(?:[.,]\d+)?|se|satu|one|an?)\s*" + _UNIT + r"\s*(sebelum|sebelumnya|before)", t)) \
            or at is None and (m := re.search(r"\bh\s*-\s*(\d+)\b()", t)) \
            or at is None and (m := re.search(r"\b(the)\s+(day|night|evening) before\b|\b(malam) (sebelum\w*)", t)):
        count, unit = m.group(1), m.group(2) or "hari"
        if count in ("the", None):  # "the day/night before", "malam sebelumnya" = one day before
            count, unit = "1", "hari"
        if unit.startswith(("hari", "day")):  # N days before: that day, at the time given or the evening
            day = due.date() - _delta(count, unit)
            at = datetime.combine(day, time(hour if hour is not None else default_hour, minute or 0), tzinfo=WIB)
        else:
            at = due - _delta(count, unit)
    if at is None and (m := re.search(r"\b(\d+(?:[.,]\d+)?|se|satu|one|an?)\s*" + _UNIT + r"\s*(lagi|dari sekarang|from now)\b", t)
                       or re.search(r"\bin\s+(\d+(?:[.,]\d+)?|an?|one)\s*" + _UNIT + r"\b", t)):
        at = n + _delta(m.group(1), m.group(2))
    if at is None:
        day, explicit_day = n.date(), False
        if re.search(r"\b(hari ini|today|tonight|malam ini|nanti malam|sore ini|pagi ini|siang ini|this (morning|afternoon|evening))\b", t):
            explicit_day = True
        elif re.search(r"\b(lusa|day after tomorrow)\b", t):
            day, explicit_day = n.date() + timedelta(days=2), True
        elif re.search(r"\b(besok|besoknya|tomorrow)\b", t):
            day, explicit_day = n.date() + timedelta(days=1), True
        elif m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
            day, explicit_day = datetime(int(m[1]), int(m[2]), int(m[3])).date(), True
        elif m := re.search(r"\b(\d{1,2})\s+(" + "|".join(sorted(_MONTH_WORDS, key=len, reverse=True)) + r")\b", t):
            day, explicit_day = datetime(n.year, _MONTH_WORDS[m[2]], int(m[1])).date(), True
            if day < n.date():
                day = day.replace(year=n.year + 1)
        elif m := re.search(r"\b(" + "|".join(_WEEKDAYS) + r")\b", t):
            day, explicit_day = n.date() + timedelta(days=(_WEEKDAYS[m[1]] - n.weekday()) % 7), True
        if hour is None and not explicit_day and not any(re.search(p, t) for p, _, _ in _PART_OF_DAY):
            return None, "unclear"
        at = datetime.combine(day, time(hour if hour is not None else default_hour, minute or 0), tzinfo=WIB)
        if at <= n and not explicit_day:
            if hour is not None and hour < 12 and not re.search(r"\b(am|pagi|morning)\b", t) and at + timedelta(hours=12) > n:
                at += timedelta(hours=12)  # "jam 8" said in the evening means 20:00
            else:
                at += timedelta(days=1)  # "jam 7 pagi" said tonight means tomorrow morning
    if at <= n:
        return None, "past"
    if at >= due:
        return None, "after_due"
    return at, None


def _remind_at_error(reason: str, item: data.ScheduleItem, n: datetime, lang: str) -> str:
    due = fmt_dt(item.due, lang)
    if reason == "past":
        return (f"Waktu itu sudah lewat (sekarang {fmt_dt(n, lang)}). Sebutkan waktu lain sebelum tenggat {due}."
                if lang == "id" else f"That time has already passed (it's now {fmt_dt(n, lang)}). Pick a time before the deadline, {due}.")
    if reason == "after_due":
        return (f"Waktu itu sudah melewati tenggatnya ({due}). Sebutkan waktu sebelum itu."
                if lang == "id" else f"That's after the deadline ({due}). Pick an earlier time.")
    return ("Aku belum paham waktunya. Coba tulis seperti \"jam 8 malam\", \"besok 07.00\" atau \"2 jam sebelum tenggat\"."
            if lang == "id" else "I couldn't work out the time. Try \"8pm tonight\", \"tomorrow 07:00\" or \"2 hours before the deadline\".")


def _gcal_link(title: str, start: datetime, end: datetime, details: str) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    from datetime import timezone
    s, e = start.astimezone(timezone.utc).strftime(fmt), end.astimezone(timezone.utc).strftime(fmt)
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={quote(title)}&dates={s}/{e}&details={quote(details)}&ctz=Asia/Jakarta")


def _ics(events: list[dict]) -> str:
    from datetime import timezone
    fmt = "%Y%m%dT%H%M%SZ"
    stamp = datetime.now(timezone.utc).strftime(fmt)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sigap//Smart School Service Center//ID", "CALSCALE:GREGORIAN"]
    for ev in events:
        desc = ev["description"].replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
        out += [
            "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@sigap", f"DTSTAMP:{stamp}",
            f"DTSTART:{ev['start'].astimezone(timezone.utc).strftime(fmt)}",
            f"DTEND:{ev['end'].astimezone(timezone.utc).strftime(fmt)}",
            f"SUMMARY:{ev['title']}", f"DESCRIPTION:{desc}",
            "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY", f"DESCRIPTION:{ev['title']}", "END:VALARM",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def create_study_reminder(item_id: str, student_confirmed: bool = False, language: str | None = None,
                          remind_at: str | None = None) -> dict:
    """Creates calendar events for a deadline or exam: by default a reminder + study blocks, or, with remind_at
    (the student's own words, e.g. "jam 8 malam"), one reminder at that time.
    Writes nothing unless student_confirmed is True: without it, returns the proposed plan."""
    lang = _lang(language)
    n = now()
    item = find_item(item_id)
    if not item:
        return {"tool": "create_study_reminder", "status": "ERROR",
                "text": f"Item '{item_id}' tidak ada di jadwal resmi." if lang == "id" else f"Item '{item_id}' is not in the official schedule."}
    custom = None
    if (remind_at or "").strip():
        custom, reason = parse_remind_at(remind_at, n, item.due)
        if reason:
            return {"tool": "create_study_reminder", "status": "INVALID_TIME", "reason": reason, "language": lang,
                    "item": item_dict(item, lang, n), "text": _remind_at_error(reason, item, n, lang)}
    plan = {"reminder": custom, "blocks": []} if custom else plan_reminders(item, n)
    title = f"{item.course.name(lang)}: {item.title(lang)}"
    src_line = (f"Sumber: {item.source} · tenggat {fmt_dt(item.due, lang, item.end)} · dibuat oleh Sigap atas persetujuan mahasiswa"
                if lang == "id" else
                f"Source: {item.source} · due {fmt_dt(item.due, lang, item.end)} · created by Sigap with the student's consent")
    events = []
    if plan["reminder"]:
        r = plan["reminder"]
        events.append({"kind": "reminder", "title": f"⏰ {'Pengingat' if lang == 'id' else 'Reminder'} — {title}",
                       "start": r, "end": r + timedelta(minutes=15), "description": src_line})
    for a, b in plan["blocks"]:
        events.append({"kind": "study_block", "title": f"📚 {'Belajar' if lang == 'id' else 'Study'} — {title}",
                       "start": a, "end": b, "description": src_line})

    def ev_text(ev):
        label = ("Pengingat" if lang == "id" else "Reminder") if ev["kind"] == "reminder" else ("Blok belajar" if lang == "id" else "Study block")
        when = fmt_dt(ev["start"], lang) if ev["kind"] == "reminder" else fmt_dt(ev["start"], lang, ev["end"])
        return f"- {label}: {when}"

    plan_lines = [ev_text(ev) for ev in events]
    base = {"tool": "create_study_reminder", "language": lang, "item": item_dict(item, lang, n),
            "events": [{"kind": e["kind"], "title": e["title"], "start": e["start"].isoformat(), "end": e["end"].isoformat()} for e in events]}
    if custom:
        base["remind_at"] = custom.isoformat()
    if not events:
        return base | {"status": "NOTHING_TO_SCHEDULE",
                       "text": "Tenggatnya terlalu dekat untuk dijadwalkan pengingat." if lang == "id" else "The deadline is too close to schedule a reminder."}
    if not student_confirmed:
        q = ("Rencana ini BELUM dibuat. Tanyakan dulu ke mahasiswa; panggil lagi dengan student_confirmed=true hanya setelah ia menjawab ya."
             if lang == "id" else
             "This plan has NOT been created. Ask the student first; call again with student_confirmed=true only after they say yes.")
        return base | {"status": "NEEDS_CONFIRMATION", "text": "\n".join([title, *plan_lines, "", q])}

    folder = OUTBOX_DIR / "calendar"
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{item.item_id}-{uuid.uuid4().hex[:6]}.ics"
    (folder / fname).write_text(_ics(events), encoding="utf-8")
    for ev in events:
        ev["google_calendar_link"] = _gcal_link(ev["title"], ev["start"], ev["end"], ev["description"])
    _append_csv(OUTBOX_DIR / "calendar_log.csv", {
        "created_at": n.isoformat(), "item_id": item.item_id,
        "events": len(events), "ics_file": fname,
    })
    done = (f"Sudah dibuat {len(events)} acara kalender untuk {title}:" if lang == "id"
            else f"Created {len(events)} calendar events for {title}:")
    return base | {
        "status": "CREATED",
        "ics_file": f"/outbox/calendar/{fname}",
        "events": [{"kind": e["kind"], "title": e["title"], "start": e["start"].isoformat(), "end": e["end"].isoformat(),
                    "google_calendar_link": e["google_calendar_link"]} for e in events],
        "text": "\n".join([done, *plan_lines, "", src_line]),
    }


# ---------------------------------------------------------------- tool 5

OFFICES = {
    "akademik": ("Bagian Layanan Akademik", "Academic Services", STUDENT_SERVICES_INBOX),
    "keuangan": ("Biro Keuangan", "Finance Bureau", "keuangan@und.ac.id"),
    "ti": ("Helpdesk TI", "IT Helpdesk", "helpdesk@und.ac.id"),
    "umum": ("Bagian Layanan Akademik", "Academic Services", STUDENT_SERVICES_INBOX),
}


def guess_category(question: str) -> str:
    q = question.lower()
    if re.search(r"ukt|bayar|biaya|tuition|fee|payment|refund|beasiswa|scholarship|keuangan|finance", q):
        return "keuangan"
    if re.search(r"login|password|wifi|wi-fi|akun|account|lms (error|down)|email kampus|sso", q):
        return "ti"
    if re.search(r"nilai|grade|krs|ujian|exam|cuti|leave|dosen|lecturer|kuliah|course|transkrip|transcript|skripsi|thesis|magang|internship|jurusan|major", q):
        return "akademik"
    return "umum"


def _append_csv(path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def _next_reference(n: datetime) -> str:
    log = OUTBOX_DIR / "tickets.csv"
    count = 0
    if log.exists():
        with open(log, encoding="utf-8") as f:
            count = sum(1 for _ in f) - 1
    return f"SGP-{n:%y%m%d}-{count + 1:04d}"


def escalate_to_student_services(question: str, category: str | None = None, course: str | None = None,
                                 checked_sources: list[str] | None = None, language: str | None = None,
                                 student_confirmed: bool = False, reference: str | None = None) -> dict:
    """Sends a question Sigap could not answer to student services with a case summary.
    Sends nothing unless student_confirmed is True: without it, returns a preview of what would be sent."""
    lang = _lang(language, question)
    n = now()
    cat = category if category in OFFICES else guess_category(question)
    office_id, office_en, inbox = OFFICES[cat]
    office = office_en if lang == "en" else office_id
    checked = checked_sources or [f"{d.title(lang)} v{d.meta.get('version')}" for d in data.documents()] + [
        "Kalender Akademik Ganjil 2026/2027", "Jadwal mata kuliah resmi"]
    c = find_course(course or question)
    base = {"tool": "escalate_to_student_services", "language": lang, "office": office, "inbox": inbox, "category": cat}
    if not student_confirmed:
        preview = (f"Akan dikirim ke {office} ({inbox}): pertanyaan, kategori '{cat}'"
                   + (f", mata kuliah {c.name(lang)}" if c else "")
                   + ", dan daftar sumber yang sudah dicek. Tanpa NIM atau data pribadi lain. BELUM dikirim — tunggu persetujuan mahasiswa."
                   if lang == "id" else
                   f"Will send to {office} ({inbox}): the question, category '{cat}'"
                   + (f", course {c.name(lang)}" if c else "")
                   + ", and the sources already checked. No student ID or other personal data. NOT sent yet — wait for the student's yes.")
        return base | {"status": "NEEDS_CONFIRMATION", "text": preview}

    ref = reference or _next_reference(n)
    body = "\n".join([
        f"Reference: {ref}",
        f"Received: {n:%Y-%m-%d %H:%M} WIB",
        f"Category: {cat}",
        f"Course: {c.code + ' ' + c.name_id if c else '-'}",
        f"Student language: {'Bahasa Indonesia' if lang == 'id' else 'English'}",
        "Student: identity is kept in the Sigap admin panel; open this reference there to reply.",
        "",
        "Question (verbatim):",
        question,
        "",
        "What Sigap already checked (no answer found):",
        *[f"- {s}" for s in checked],
        "",
        "Please reply to the student through the portal ticket. If the answer is general, mark it for the knowledge base (FR-8).",
    ])
    msg = EmailMessage()
    msg["From"] = os.environ.get("SIGAP_SMTP_FROM", "sigap-bot@und.ac.id")
    msg["To"] = inbox
    msg["Subject"] = f"[Sigap {ref}] {cat.title()}: {question[:60]}"
    msg.set_content(body)
    folder = OUTBOX_DIR / "emails"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ref}.eml").write_bytes(bytes(msg))
    delivered = "outbox"
    if os.environ.get("SIGAP_SMTP_HOST"):
        with smtplib.SMTP(os.environ["SIGAP_SMTP_HOST"], int(os.environ.get("SIGAP_SMTP_PORT", "587"))) as s:
            s.starttls()
            if os.environ.get("SIGAP_SMTP_USER"):
                s.login(os.environ["SIGAP_SMTP_USER"], os.environ["SIGAP_SMTP_PASSWORD"])
            s.send_message(msg)
        delivered = "smtp"
    _append_csv(OUTBOX_DIR / "tickets.csv", {
        "reference": ref, "created_at": n.isoformat(), "status": "open", "category": cat, "office": office_id,
        "course": c.code if c else "", "language": lang, "question": question,
        "checked_sources": json.dumps(checked, ensure_ascii=False), "delivered_via": delivered,
    })
    nxt = (f"{office} akan membalas dalam 2 hari kerja (Sen–Jum 08.00–16.00 WIB); balasannya dikirim ke tempat kamu bertanya. "
           "Simpan nomor referensi ini untuk mengecek statusnya."
           if lang == "id" else
           f"{office} will reply within 2 working days (Mon–Fri 08:00–16:00 WIB), right where you asked. "
           "Keep this reference number to check its status.")
    head = f"Terkirim ke {office}. Nomor referensi: {ref}" if lang == "id" else f"Sent to {office}. Reference number: {ref}"
    return base | {"status": "SENT", "reference": ref, "next_step": nxt, "text": f"{head}\n{nxt}"}
