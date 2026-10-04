"""Loads the structured sources. Dates only ever come from these tables."""
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR, WIB


@dataclass(frozen=True)
class Course:
    code: str
    name_id: str
    name_en: str
    lecturer: str
    aliases: tuple[str, ...]

    def name(self, lang: str) -> str:
        return self.name_en if lang == "en" else self.name_id


@dataclass(frozen=True)
class ScheduleItem:
    item_id: str
    course: Course
    title_id: str
    title_en: str
    type: str  # assignment | quiz | project | exam
    due: datetime  # deadline, or exam start
    end: datetime | None  # exam/quiz end
    where: str
    source: str

    def title(self, lang: str) -> str:
        return self.title_en if lang == "en" else self.title_id


@dataclass(frozen=True)
class CalendarEntry:
    kind: str  # term | period | window
    key: str
    name_id: str
    name_en: str
    start: date
    end: date
    note_id: str
    note_en: str

    def name(self, lang: str) -> str:
        return self.name_en if lang == "en" else self.name_id

    def note(self, lang: str) -> str:
        return self.note_en if lang == "en" else self.note_id


@dataclass(frozen=True)
class Section:
    number: str
    title_id: str
    title_en: str
    tags: tuple[str, ...]
    text_id: str
    text_en: str

    def title(self, lang: str) -> str:
        return self.title_en if lang == "en" else self.title_id

    def text(self, lang: str) -> str:
        return self.text_en if lang == "en" else self.text_id


@dataclass(frozen=True)
class Document:
    path: Path
    meta: dict
    sections: tuple[Section, ...]

    def title(self, lang: str) -> str:
        return self.meta.get(f"title_{lang}") or self.meta["title_id"]


def _read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache
def courses() -> dict[str, Course]:
    out = {}
    for r in _read_csv("courses.csv"):
        aliases = tuple(a.strip().lower() for a in r["aliases"].split(";") if a.strip())
        out[r["course_code"]] = Course(r["course_code"], r["name_id"], r["name_en"], r["lecturer"], aliases)
    return out


def _at(d: str, t: str) -> datetime:
    return datetime.combine(date.fromisoformat(d), time.fromisoformat(t), tzinfo=WIB)


@lru_cache
def schedule() -> tuple[ScheduleItem, ...]:
    cs = courses()
    items = []
    for r in _read_csv("course_schedule.csv"):
        items.append(ScheduleItem(
            item_id=r["item_id"],
            course=cs[r["course_code"]],
            title_id=r["title_id"],
            title_en=r["title_en"],
            type=r["type"],
            due=_at(r["due_date"], r["due_time"]),
            end=_at(r["due_date"], r["end_time"]) if r["end_time"] else None,
            where=r["where"],
            source=r["source"],
        ))
    return tuple(sorted(items, key=lambda i: i.due))


@lru_cache
def calendar() -> tuple[CalendarEntry, ...]:
    return tuple(
        CalendarEntry(r["kind"], r["key"], r["name_id"], r["name_en"],
                      date.fromisoformat(r["start"]), date.fromisoformat(r["end"]),
                      r["note_id"], r["note_en"])
        for r in _read_csv("academic_calendar.csv")
    )


_SECTION_RE = re.compile(r"^##\s+(?P<num>[\d.]+)\s*\|\s*(?P<tid>[^|]+?)\s*\|\s*(?P<ten>.+?)\s*$")


def _parse_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    sections, cur = [], None
    for line in body.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            cur = {"num": m["num"], "tid": m["tid"], "ten": m["ten"], "tags": "", "id": "", "en": ""}
            sections.append(cur)
        elif cur is not None:
            for key, prefix in (("tags", "tags:"), ("id", "ID:"), ("en", "EN:")):
                if line.startswith(prefix):
                    cur[key] = line[len(prefix):].strip()
    return Document(path, meta, tuple(
        Section(s["num"], s["tid"], s["ten"],
                tuple(t.strip().lower() for t in s["tags"].split(",") if t.strip()),
                s["id"], s["en"])
        for s in sections
    ))


@lru_cache
def documents() -> tuple[Document, ...]:
    """Current handbook versions. Superseded files (meta 'superseded: true') are excluded (FR-10)."""
    docs = [_parse_document(p) for p in sorted((DATA_DIR / "handbook").glob("*.md"))]
    return tuple(d for d in docs if d.meta.get("superseded", "").lower() != "true")
