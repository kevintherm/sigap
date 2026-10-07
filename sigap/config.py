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


_tunnel_cache: dict = {}


def public_url() -> str:
    """The address students and staff reach Sigap on (sign-in links, MCP URL). SIGAP_PUBLIC_URL wins; otherwise,
    with SIGAP_PUBLIC_URL_FROM set to a cloudflared metrics URL (http://tunnel:2000/quicktunnel), the current
    quick-tunnel address is asked from cloudflared, since it changes on every start."""
    fixed = os.environ.get("SIGAP_PUBLIC_URL", "").strip()
    if fixed:
        return fixed.rstrip("/")
    source = os.environ.get("SIGAP_PUBLIC_URL_FROM", "").strip()
    if source:
        import json
        import time
        import urllib.request
        if _tunnel_cache.get("url") and time.time() - _tunnel_cache["at"] < 300:
            return _tunnel_cache["url"]
        try:
            with urllib.request.urlopen(source, timeout=2) as r:
                host = json.load(r).get("hostname", "")
            if host:
                _tunnel_cache.update(url=f"https://{host}", at=time.time())
                return _tunnel_cache["url"]
        except (OSError, ValueError):
            pass  # tunnel not up yet: fall back below, ask again next time
        if _tunnel_cache.get("url"):
            return _tunnel_cache["url"]
    return "http://localhost:8000"
