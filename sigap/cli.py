"""Admin command line.

    uv run sigap-admin migrate
    uv run sigap-admin create-user admin@kampus.ac.id "Nama" admin
    uv run sigap-admin seed-demo            # synthetic courses, schedule, calendar, handbook, students, staff
    uv run sigap-admin invite 2401001       # one-time Telegram link for a student
"""
import argparse
import csv
import getpass
import os
import secrets
import sys
from datetime import date

from sqlmodel import select

from .config import DATA_DIR
from .db import migrate, session_scope
from .models import CalendarEntry, Course, Document, Enrollment, LecturerCourse, ScheduleItem, Student, User
from .security import ROLES, hash_password
from .services import create_link_code, validate_handbook

DEMO_STUDENTS = [
    ("2401001", "Dinda Pratiwi", "dinda@student.und.ac.id", ["IF2101", "SI2203", "SI2105", "MA2102", "SI2207", "UM2001"]),
    ("2401002", "Raka Aditya", "raka@student.und.ac.id", ["IF2101", "SI2203", "MA2102"]),
    ("2401003", "Sinta Maharani", "sinta@student.und.ac.id", ["SI2105", "SI2207", "UM2001"]),
]
DEMO_USERS = [
    ("admin@und.ac.id", "Admin Akademik", "admin", []),
    ("hendra@und.ac.id", "Pak Hendra (Layanan Akademik)", "staff", []),
    ("rina@und.ac.id", "Dr. Rina Kusuma", "lecturer", ["IF2101"]),
]


def bot_link(code: str) -> str:
    username = os.environ.get("TELEGRAM_BOT_USERNAME")
    return f"https://t.me/{username}?start={code}" if username else f"/start {code}  (set TELEGRAM_BOT_USERNAME for a link)"


def _rows(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def seed_demo() -> None:
    migrate()
    with session_scope() as db:
        for r in _rows("courses.csv"):
            if not db.get(Course, r["course_code"]):
                db.add(Course(code=r["course_code"], name_id=r["name_id"], name_en=r["name_en"],
                              lecturer=r["lecturer"], aliases=r["aliases"]))
        db.commit()
        for r in _rows("course_schedule.csv"):
            if not db.get(ScheduleItem, r["item_id"]):
                db.add(ScheduleItem(item_id=r["item_id"], course_code=r["course_code"], title_id=r["title_id"],
                                    title_en=r["title_en"], type=r["type"], due_date=date.fromisoformat(r["due_date"]),
                                    due_time=r["due_time"], end_time=r["end_time"], where=r["where"], source=r["source"]))
        if not db.exec(select(CalendarEntry)).first():
            for r in _rows("academic_calendar.csv"):
                db.add(CalendarEntry(kind=r["kind"], key=r["key"], name_id=r["name_id"], name_en=r["name_en"],
                                     start=date.fromisoformat(r["start"]), end=date.fromisoformat(r["end"]),
                                     note_id=r["note_id"], note_en=r["note_en"]))
        if not db.exec(select(Document)).first():
            content = (DATA_DIR / "handbook" / "pedoman_akademik_v1.2.md").read_text(encoding="utf-8")
            meta = validate_handbook(content)["meta"]
            db.add(Document(title=meta["title_id"], version=meta["version"], effective=date.fromisoformat(meta["effective"]),
                            content=content, active=True))
        for number, name, email, codes in DEMO_STUDENTS:
            if not db.exec(select(Student).where(Student.student_number == number)).first():
                st = Student(student_number=number, name=name, email=email, program="S1 Sistem Informasi (kelas daring)")
                db.add(st)
                db.commit()
                db.refresh(st)
                for c in codes:
                    db.add(Enrollment(student_id=st.id, course_code=c))
        db.commit()
        created = []
        for email, name, role, codes in DEMO_USERS:
            if not db.exec(select(User).where(User.email == email)).first():
                password = secrets.token_urlsafe(9)
                u = User(email=email, name=name, role=role, password_hash=hash_password(password))
                db.add(u)
                db.commit()
                db.refresh(u)
                for c in codes:
                    db.add(LecturerCourse(user_id=u.id, course_code=c))
                db.commit()
                created.append((email, role, password))
    print("Demo data ready (synthetic).")
    if created:
        print("\nAdmin-panel accounts (passwords are shown only once):")
        for email, role, password in created:
            print(f"  {role:<9} {email:<22} {password}")


def create_user(email: str, name: str, role: str, password: str | None, courses: list[str]) -> None:
    if role not in ROLES:
        sys.exit(f"role must be one of {ROLES}")
    migrate()
    password = password or getpass.getpass("Password (min 10 chars): ")
    with session_scope() as db:
        if db.exec(select(User).where(User.email == email.lower())).first():
            sys.exit("A user with that email exists")
        u = User(email=email.lower(), name=name, role=role, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        for c in courses:
            db.add(LecturerCourse(user_id=u.id, course_code=c))
        db.commit()
    print(f"Created {role} {email}")


def invite(student_number: str) -> None:
    with session_scope() as db:
        st = db.exec(select(Student).where(Student.student_number == student_number)).first()
        if not st:
            sys.exit("No such student")
        code, expires = create_link_code(db, st.id)
    print(f"{st.name}: {bot_link(code)}\n(one use, expires {expires:%Y-%m-%d %H:%M} UTC)")


def main():
    ap = argparse.ArgumentParser(prog="sigap-admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("seed-demo")
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("name")
    cu.add_argument("role", choices=ROLES)
    cu.add_argument("--password")
    cu.add_argument("--course", action="append", default=[], help="lecturer course code (repeatable)")
    iv = sub.add_parser("invite")
    iv.add_argument("student_number")
    a = ap.parse_args()
    if a.cmd == "migrate":
        migrate()
        print("Database is up to date.")
    elif a.cmd == "seed-demo":
        seed_demo()
    elif a.cmd == "create-user":
        create_user(a.email, a.name, a.role, a.password, a.course)
    elif a.cmd == "invite":
        invite(a.student_number)


if __name__ == "__main__":
    main()
