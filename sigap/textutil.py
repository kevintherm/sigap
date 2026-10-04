"""Language detection, tokenisation and date formatting shared by the tools."""
import re
from datetime import date, datetime

_ID_MARKERS = set("""
apa saja aja yang minggu ini kapan saya aku gue gw nggak ngga gak ga tidak bisa boleh kalau kalo dong sih nih ya iya
harus ke di dan untuk berapa bagaimana gimana mana sudah udah belum masih tugas ujian besok hari dengan itu ada mau
tolong kirim kirimkan siapa jadwal telat dinilai nilai buka tutup dibuka cuti kuliah mata deh lagi jam tanggal terima
kasih halo hai pagi malam siang sore kerjain kerjakan bikin bikinin buatkan buatin esai apakah bulan depan sampai batas
lho kok sekarang keluar caranya cara syarat aturan dikumpulkan pekan kak tenggat pengingat ingatkan oke
""".split())
_EN_MARKERS = set("""
what when is my the are due this week which can i how do does it we in of to for yes no please exam midterm late
still open closed has have will about rule assignment next tomorrow today who should where attendance minimum window
withdrawal leave grade hello hi thanks thank send you me write essay sure okay what's whats did get an a if am there
""".split())


def detect_lang(text: str, default: str = "id") -> str:
    words = re.findall(r"[a-z']+", text.lower())
    id_score = sum(w in _ID_MARKERS for w in words)
    en_score = sum(w in _EN_MARKERS for w in words)
    if id_score == en_score:
        return default
    return "id" if id_score > en_score else "en"


# Phrases folded before tokenising, so English and Indonesian questions meet the same index terms.
_PHRASES = {
    "make-up": "susulan", "make up": "susulan", "makeup exam": "susulan ujian",
    "student services": "layanan", "grade appeal": "banding nilai",
    "add/drop": "tambah batal", "add drop": "tambah batal",
    "ghost writer": "joki", "ghost-writing": "joki",
}

_CANON = {
    "telat": "terlambat", "late": "terlambat", "lambat": "terlambat", "keterlambatan": "terlambat", "overdue": "terlambat",
    "submit": "kumpul", "submission": "kumpul", "submitted": "kumpul", "dikumpulkan": "kumpul", "mengumpulkan": "kumpul",
    "pengumpulan": "kumpul", "ngumpul": "kumpul", "ngumpulin": "kumpul", "kumpulkan": "kumpul", "resubmit": "kumpul",
    "upload": "unggah", "diunggah": "unggah", "mengunggah": "unggah", "uploaded": "unggah",
    "assignment": "tugas", "homework": "tugas",
    "attendance": "hadir", "kehadiran": "hadir", "absen": "hadir", "absensi": "hadir", "presensi": "hadir", "attend": "hadir",
    "absent": "izin", "absence": "izin", "permission": "izin", "excused": "izin",
    "sick": "sakit", "ill": "sakit", "illness": "sakit", "medical": "sakit",
    "minimum": "minimal", "percent": "persen",
    "graded": "nilai", "grade": "nilai", "dinilai": "nilai", "score": "nilai", "penilaian": "nilai", "mark": "nilai",
    "penalty": "potong", "potongan": "potong", "dipotong": "potong", "deduction": "potong", "penalti": "potong", "penalized": "potong",
    "extension": "perpanjang", "perpanjangan": "perpanjang", "extend": "perpanjang", "diperpanjang": "perpanjang",
    "deadline": "tenggat", "due": "tenggat",
    "exam": "ujian", "test": "ujian", "midterm": "uts", "final": "uas", "finals": "uas",
    "makeup": "susulan", "missed": "susulan", "miss": "susulan",
    "plagiarism": "plagiat", "plagiarisme": "plagiat", "cheat": "curang", "cheating": "curang", "nyontek": "curang",
    "contek": "curang", "mencontek": "curang", "menyontek": "curang", "chatgpt": "ai", "integrity": "integritas",
    "appeal": "banding", "dispute": "banding",
    "leave": "cuti",
    "withdraw": "batal", "withdrawal": "batal", "pembatalan": "batal", "dibatalkan": "batal", "membatalkan": "batal", "drop": "batal",
    "registration": "krs", "register": "krs", "enroll": "krs", "enrol": "krs", "enrollment": "krs", "credits": "sks",
    "credit": "sks", "load": "beban",
    "tuition": "ukt", "fee": "ukt", "fees": "ukt", "pay": "bayar", "payment": "bayar", "pembayaran": "bayar",
    "installment": "cicil", "installments": "cicil", "cicilan": "cicil", "mencicil": "cicil", "nyicil": "cicil",
    "retake": "ulang", "mengulang": "ulang", "repeat": "ulang", "perbaikan": "remedial", "fail": "gagal", "failed": "gagal",
    "counseling": "konseling", "counselling": "konseling", "counselor": "konseling", "psychologist": "psikolog",
    "stress": "stres", "stressed": "stres", "anxious": "cemas", "anxiety": "cemas", "depressed": "depresi",
    "camera": "kamera", "proctoring": "pengawas", "proctor": "pengawas", "connection": "koneksi", "disconnected": "putus",
    "contact": "kontak", "hours": "jam", "office": "kantor",
    "letter": "surat", "doctor": "dokter", "note": "surat",
    "transcript": "transkrip", "scale": "skala", "gpa": "ipk", "pass": "lulus", "passing": "lulus",
    "eligible": "syarat", "eligibility": "syarat", "requirement": "syarat", "requirements": "syarat", "persyaratan": "syarat",
    "mengajukan": "ajukan", "pengajuan": "ajukan", "diajukan": "ajukan", "apply": "ajukan", "request": "ajukan",
    "procedure": "prosedur", "process": "prosedur", "steps": "prosedur", "caranya": "cara",
    "email": "email", "whatsapp": "email", "wa": "email",
}

_STOP = set("""
saya aku gue gw apa yang dan di ke dari untuk ini itu ada kalau kalo masih nggak ngga gak ga tidak bisa boleh dong sih nih
ya harus siapa bagaimana gimana berapa kapan mana apakah atau dengan pada juga akan sudah udah belum lagi jadi kok deh
the a an is are am was were be been can could i my me do does did what how when who which if still will would to of in
on for and or it its with at after before about there any get should we you your our this that than so not no yes may
mata kuliah course courses class mau aja saja kah tolong please kak min sama nya kita kami tentang soal lho
banget bantu help gimana
""".split())


def _fold(text: str) -> str:
    t = text.lower()
    for phrase, repl in _PHRASES.items():
        t = t.replace(phrase, repl)
    return t


def _stem(w: str) -> str:
    for suf in ("nya", "lah", "kah"):
        if w.endswith(suf) and len(w) > len(suf) + 3:
            w = w[: -len(suf)]
            break
    if w.endswith("s") and w[:-1] in _CANON:  # English plurals only; Indonesian words often end in s
        w = w[:-1]
    return w


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[a-z0-9]+", _fold(text)):
        if raw in _STOP:
            continue
        w = _CANON.get(raw) or _CANON.get(_stem(raw)) or _stem(raw)
        if w and w not in _STOP:
            out.append(w)
    return out


_DAYS = {"id": ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
_MONTHS = {
    "id": ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def fmt_date(d: date, lang: str) -> str:
    return f"{_DAYS[lang][d.weekday()]}, {d.day} {_MONTHS[lang][d.month - 1]} {d.year}"


def fmt_dt(dt: datetime, lang: str, end: datetime | None = None) -> str:
    t = dt.strftime("%H:%M")
    if end:
        t += f"–{end.strftime('%H:%M')}"
    return f"{fmt_date(dt.date(), lang)} · {t} WIB"
