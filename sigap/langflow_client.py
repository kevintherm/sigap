"""Calls the Sigap tool flows in Langflow through its run API.

Each flow answers with the human text, then '---' and a ```json block holding the structured
result (see langflow/build_flows.py). This client turns that back into the same dict the local
tools return, so callers don't care where a tool ran.
"""
import json
import os
import time
import uuid

import httpx

from . import config  # noqa: F401  (loads .env)


class LangflowError(RuntimeError):
    pass


# Component class of each tool flow, for per-request tweaks (node ids are "<class>-tool" and "<class>-agent").
TOOL_CLASSES = {"answer_campus_policy": "SigapAnswerCampusPolicy", "get_my_deadlines": "SigapGetMyDeadlines",
                "get_study_period": "SigapGetStudyPeriod", "create_study_reminder": "SigapCreateStudyReminder",
                "escalate_to_student_services": "SigapEscalateToStudentServices"}


def _demo_tweak() -> dict:
    """With SIGAP_NOW set (tests, a recorded demo), the Langflow tools use the same pinned time as the backend;
    without it they use the real clock."""
    now = os.environ.get("SIGAP_NOW", "").strip()
    return {"demo_now": now} if now else {}


class LangflowTools:
    def __init__(self, url: str | None = None, api_key: str | None = None, timeout: float = 60):
        self.url = (url or os.environ.get("LANGFLOW_URL", "http://localhost:7860")).rstrip("/")
        key = api_key or os.environ.get("LANGFLOW_API_KEY")
        if not key:
            raise LangflowError("LANGFLOW_API_KEY is not set")
        self.client = httpx.Client(timeout=timeout, headers={"x-api-key": key})
        self.last_ms: float | None = None

    def run(self, flow: str, args: dict, session_id: str | None = None) -> dict:
        body = {"input_value": json.dumps({k: v for k, v in args.items() if v is not None}, ensure_ascii=False),
                "input_type": "chat", "output_type": "chat"}
        if demo := _demo_tweak():
            body["tweaks"] = {f"{TOOL_CLASSES[flow]}-tool": demo}
        if session_id:
            body["session_id"] = session_id
        t0 = time.perf_counter()
        try:
            r = self.client.post(f"{self.url}/api/v1/run/{flow.replace('_', '-')}", params={"stream": "false"}, json=body)
        except httpx.HTTPError as e:
            raise LangflowError(f"Langflow unreachable at {self.url}: {e}") from e
        self.last_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            raise LangflowError(f"{flow}: HTTP {r.status_code} {r.text[:300]}")
        try:
            text = r.json()["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LangflowError(f"{flow}: unexpected response shape") from e
        human, sep, block = text.partition("\n\n---\n")
        if not sep:
            raise LangflowError(f"{flow}: response has no structured block")
        data = json.loads(block.strip().removeprefix("```json").removesuffix("```").strip())
        data["text"] = human
        data["langflow_ms"] = round(self.last_ms, 1)
        return data

    def run_agent(self, text: str, session_id: str, courses: list[str] | None, linked: bool) -> dict:
        """Runs the Sigap Agent flow (Gemini). The student's courses go in as a tweak on the deadlines tool,
        so the model cannot choose them; the session id is a random alias, never a chat or student id.
        Returns {"text", "steps": [{"tool", "input", "result"}], "langflow_ms"}."""
        tweaks = {"SigapGetMyDeadlines-agent": {"enrolled_courses": ",".join(courses or []) or "-"},
                  "SigapAnswerCampusPolicy-agent": {"student_message": text}}
        if demo := _demo_tweak():
            for cls in TOOL_CLASSES.values():
                tweaks.setdefault(f"{cls}-agent", {}).update(demo)
        body = {"input_value": f"[SIGAP CONTEXT linked={'yes' if linked else 'no'}]\n{text}", "input_type": "chat",
                "output_type": "chat", "session_id": session_id, "tweaks": tweaks}
        t0 = time.perf_counter()
        try:
            r = self.client.post(f"{self.url}/api/v1/run/sigap-agent", params={"stream": "false"}, json=body,
                                 timeout=float(os.environ.get("SIGAP_AGENT_TIMEOUT", "30")))
        except httpx.HTTPError as e:
            raise LangflowError(f"agent: {e}") from e
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            raise LangflowError(f"agent: HTTP {r.status_code} {r.text[:300]}")
        try:
            answer = r.json()["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LangflowError("agent: unexpected response shape") from e
        steps, narration = self._agent_steps(session_id)
        for piece in narration:  # the model's "let me check…" notes between tool calls are not part of the reply
            answer = answer.replace(piece, "", 1)
        return {"text": answer.strip(), "steps": steps, "langflow_ms": round(ms, 1)}

    def run_text(self, flow: str, text: str, timeout: float = 180) -> str:
        """Sends one text to a Langflow chat flow and returns its reply text (fresh session, nothing remembered)."""
        session = "fmt-" + uuid.uuid4().hex
        body = {"input_value": text, "input_type": "chat", "output_type": "chat", "session_id": session}
        try:
            r = self.client.post(f"{self.url}/api/v1/run/{flow.replace('_', '-')}", params={"stream": "false"},
                                 json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise LangflowError(f"{flow}: {e}") from e
        finally:  # the Agent component saves its reply even with storage off; this run's copy is not needed
            try:
                self.client.delete(f"{self.url}/api/v1/monitor/messages/session/{session}")
            except httpx.HTTPError:
                pass
        if r.status_code != 200:
            raise LangflowError(f"{flow}: HTTP {r.status_code} {r.text[:300]}")
        try:
            return r.json()["outputs"][0]["outputs"][0]["results"]["message"]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LangflowError(f"{flow}: unexpected response shape") from e

    def agent_model(self) -> str:
        """'provider: model' of the sigap_agent flow, read from Langflow (what actually runs)."""
        try:
            flows = self.client.get(f"{self.url}/api/v1/flows/", params={"get_all": "true"}).json()
            flow = next(f for f in flows if f.get("name") == "sigap_agent")
            agent = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "Agent")
            m = agent["data"]["node"]["template"]["model"]["value"][0]
            return f"{m['provider']}: {m['name']}"
        except (httpx.HTTPError, ValueError, KeyError, IndexError, StopIteration, TypeError):
            return "unknown model"

    def _agent_steps(self, session_id: str) -> tuple[list[dict], list[str]]:
        """The tool calls of the agent's latest reply, read from Langflow's message log, and the text the model
        wrote before its last tool call (narration that Langflow also puts in the reply)."""
        try:
            msgs = self.client.get(f"{self.url}/api/v1/monitor/messages", params={"session_id": session_id}).json()
        except (httpx.HTTPError, ValueError):
            return [], []
        last = next((m for m in reversed(msgs) if m.get("sender") == "Machine"), None)
        steps, texts, narration = [], [], []
        for block in (last or {}).get("content_blocks") or []:
            for c in block.get("contents", []):
                if c.get("type") == "text":
                    texts.append(c.get("text") or "")
                if c.get("type") != "tool_use":
                    continue
                narration += [t.strip() for t in texts if t.strip()]
                texts = []
                out = c.get("output")
                out_text = out.get("content", "") if isinstance(out, dict) else str(out or "")
                result = None
                if "```json" in out_text:
                    raw = out_text.split("```json", 1)[1].split("```", 1)[0]
                    try:
                        result = json.loads(raw)
                    except json.JSONDecodeError:
                        result = None
                steps.append({"tool": c.get("name"), "input": (c.get("tool_input") or {}).get("input_value"), "result": result})
        return steps, narration

    # Same signatures as sigap.tools, so the two are interchangeable.

    def answer_campus_policy(self, question: str, language: str | None = None) -> dict:
        return self.run("answer_campus_policy", {"question": question, "language": language})

    def get_my_deadlines(self, request: str = "this week", course: str | None = None, item_type: str | None = None,
                         language: str | None = None, courses: list[str] | None = None) -> dict:
        return self.run("get_my_deadlines", {"request": request, "course": course, "item_type": item_type,
                                             "language": language, "courses": courses})

    def get_study_period(self, on_date: str | None = None, topic: str | None = None, language: str | None = None) -> dict:
        return self.run("get_study_period", {"on_date": on_date, "topic": topic, "language": language})

    def create_study_reminder(self, item_id: str, student_confirmed: bool = False, language: str | None = None,
                              remind_at: str | None = None) -> dict:
        return self.run("create_study_reminder", {"item_id": item_id, "student_confirmed": student_confirmed,
                                                  "language": language, "remind_at": remind_at})

    def escalate_to_student_services(self, question: str, category: str | None = None, course: str | None = None,
                                     checked_sources: list[str] | None = None, language: str | None = None,
                                     student_confirmed: bool = False, reference: str | None = None) -> dict:
        return self.run("escalate_to_student_services", {
            "question": question, "category": category, "course": course, "checked_sources": checked_sources,
            "language": language, "student_confirmed": student_confirmed, "reference": reference})


_backend = None


def tool_backend():
    """The tools every channel uses: Langflow when configured (SIGAP_TOOLS=langflow, the default when
    LANGFLOW_API_KEY is set), otherwise the local Python tools for offline development."""
    global _backend
    if _backend is None:
        mode = os.environ.get("SIGAP_TOOLS") or ("langflow" if os.environ.get("LANGFLOW_API_KEY") else "local")
        if mode == "langflow":
            _backend = LangflowTools()
        else:
            from . import tools
            _backend = tools
    return _backend


def backend_name() -> str:
    return "langflow" if isinstance(tool_backend(), LangflowTools) else "local"
