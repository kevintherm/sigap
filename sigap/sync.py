"""Pushes the reference data (courses, schedule, calendar, handbook) from the database into the
Langflow flows, so the flows always compute on what staff last saved. Only the data fields of the
Sigap components are changed; the flow structure and code are left alone."""
import logging
import os
from datetime import datetime

import httpx

from .config import WIB
from .db import session_scope
from .services import export_reference_data

log = logging.getLogger("sigap.sync")
PROJECT = os.environ.get("SIGAP_LANGFLOW_PROJECT", "Sigap")
status: dict = {"last_sync": None, "ok": None, "detail": "never synced"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=os.environ.get("LANGFLOW_URL", "http://localhost:7860").rstrip("/"), timeout=60,
                        headers={"x-api-key": os.environ.get("LANGFLOW_API_KEY", "")})


def push_reference_data() -> dict:
    """Returns {"ok", "flows_updated", "detail"} and records it in `status`."""
    if not os.environ.get("LANGFLOW_API_KEY"):
        return _done(False, 0, "LANGFLOW_API_KEY not set")
    with session_scope() as db:
        fields = export_reference_data(db)
    if not fields["handbook_md"]:
        return _done(False, 0, "no active handbook in the database")
    updated = 0
    try:
        with _client() as lf:
            projects = lf.get("/api/v1/projects/").raise_for_status().json()
            project = next((p for p in projects if p["name"] == PROJECT), None)
            if not project:
                return _done(False, 0, f"Langflow project '{PROJECT}' not found; run langflow/build_flows.py")
            for summary in lf.get(f"/api/v1/projects/{project['id']}").raise_for_status().json()["flows"]:
                flow = lf.get(f"/api/v1/flows/{summary['id']}").raise_for_status().json()
                changed = False
                for node in (flow.get("data") or {}).get("nodes", []):
                    template = node.get("data", {}).get("node", {}).get("template", {})
                    if not node["data"].get("type", "").startswith("Sigap"):
                        continue
                    for name, value in fields.items():
                        if name in template and template[name].get("value") != value:
                            template[name]["value"] = value
                            changed = True
                if changed:
                    lf.patch(f"/api/v1/flows/{flow['id']}", json={"data": flow["data"]}).raise_for_status()
                    updated += 1
    except httpx.HTTPError as e:
        return _done(False, updated, f"Langflow error: {e}")
    return _done(True, updated, f"{updated} flow(s) updated" if updated else "already up to date")


def _done(ok: bool, n: int, detail: str) -> dict:
    status.update(last_sync=datetime.now(WIB).isoformat(timespec="seconds"), ok=ok, detail=detail)
    (log.info if ok else log.warning)("Langflow sync: %s", detail)
    return {"ok": ok, "flows_updated": n, "detail": detail}
