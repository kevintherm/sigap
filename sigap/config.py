import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Loads KEY=value lines from the project .env without overriding the real environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if not (sep and key) or key.startswith("#"):
            continue
        value = value.strip()
        if value[:1] in ('"', "'") and value[0] in value[1:]:  # quoted: keep what is inside, '#' included
            value = value[1:value.index(value[0], 1)]
        else:  # unquoted: ' # ...' is a comment, as in shells and Docker Compose
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("SIGAP_DATA_DIR", ROOT / "data"))
OUTBOX_DIR = Path(os.environ.get("SIGAP_OUTBOX_DIR", ROOT / "outbox"))


STUDENT_SERVICES_INBOX = os.environ.get("SIGAP_SERVICES_INBOX", "layanan.akademik@und.ac.id")


def now() -> datetime:
    """Current time in WIB. SIGAP_NOW (e.g. 2026-10-04T19:00) pins it for demos and tests."""
    fixed = os.environ.get("SIGAP_NOW")
    if fixed:
        dt = datetime.fromisoformat(fixed)
        return dt if dt.tzinfo else dt.replace(tzinfo=WIB)
    return datetime.now(WIB)
