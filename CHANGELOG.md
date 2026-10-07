# Changelog

Notes for people and agents working on this repo: what changed, why, and what not to undo.
Newest first. Work listed here may still be uncommitted; check `git status`.

## 2026-10-08: Whole stack in Docker Compose (other session)

- `compose.yaml` now runs `langflow`, `backend` (new `Dockerfile`) and `tunnel` (cloudflared quick tunnel), all
  `restart: unless-stopped`, so the stack comes back after a reboot. The backend waits for Langflow to be healthy,
  reaches it at `http://langflow:7860`, mounts `var/` and `outbox/`, runs as UID 1000, and listens on
  `127.0.0.1:8000` (Bob's MCP config at `localhost:8000/mcp` keeps working).
- `sigap/config.py`: `public_url()`: `SIGAP_PUBLIC_URL` if set, otherwise the tunnel's current address from
  cloudflared's metrics endpoint (`SIGAP_PUBLIC_URL_FROM`). Used by the bot's sign-in link, `/token` and the portal.
- `FORWARDED_ALLOW_IPS="*"` for the backend so login cookies stay `Secure` behind the tunnel (verified).
- `.env` gained `SIGAP_NOW=2026-10-04T19:00` (demo clock), previously passed on the command line.
- Do not run `uv run sigap-server` while the compose backend is up: both want port 8000 and the Telegram bot.

## 2026-10-04 (~20:15): README future vision; retention covers the formatter flow

- `README.md`: "Not done yet" replaced by "Future vision" with two directions only: integration with existing
  LMS systems (deadline, enrolment and sign-in sync, reminders that follow LMS changes) and a WhatsApp gateway
  (webhook adapter beside `telegram_bot.py`, reply buttons, template messages outside the 24-hour window).
  The other former items (Gemini free tier, SSO/deployment, in-memory sessions, golden-set caveat) were removed
  at the owner's request.
- `sigap/retention.py`: `SIGAP_FLOWS` now includes `handbook_formatter`, as a backup to the per-run session
  delete in `LangflowTools.run_text()`. Takes effect at the next backend restart.

## 2026-10-04 (~20:00): Handbook format guide, template and formatting agent (other session)

- New Langflow flow `handbook_formatter` (`langflow/build_flows.py`: `FORMATTER_PROMPT`, `build_formatter_flow`;
  model lookup shared with the chat agent via `agent_model_value`). No tools; Chat Input/Output do not store.
- `sigap/handbook_formatter.py` (new): text extraction (Word, PDF, Markdown, text), splitting, checks against the
  source, assembly, background jobs. `sigap/langflow_client.py`: `run_text()` deletes the run's session messages
  afterwards, because the Agent component stores its reply even with storage off. Formatter runs leave nothing in
  Langflow; `retention.py` was not changed.
- `sigap/admin_api.py`: `POST /documents/format`, `GET /documents/format/{job}` (admin only).
- Admin panel Pedoman page: format guide, "Unduh template" (`sigap/static/admin/template-pedoman.md`), "Buat draf
  dengan agen", review box with warnings; drafts are never activated automatically.
- New dependencies: `pypdf`, `python-docx`.

## 2026-10-04 (~19:00): Reminder button fix (other session)

- `langflow/build_flows.py`: action-tool descriptions say to call the tool once per reply, and again so a fresh
  button appears. Message-storage settings unchanged.
- `sigap/tools.py`: `parse_remind_at()` validates stored ISO times directly (`_check_remind_at`); `_BUTTON_NOTE`
  says "in this reply".
- `sigap/agent.py`: `handle_with_agent` keeps the previous offer when a reply makes no tool calls.
- Flows rebuilt and backend restarted (same `SIGAP_NOW` / `SIGAP_PUBLIC_URL`). Agent model: `deepseek-v4-flash`;
  the privacy notice reads it from the flow, so it still matches.

## 2026-10-04: Privacy and data retention (UU PDP)

**Why:** Sigap's own database kept no chat text, but Langflow kept every message in full, forever (4,056
messages, plus run logs with the same text). The privacy notice also named the wrong AI provider (Gemini).
Approved by the owner: plan steps 1 to 4. Step 5 (an admin "delete this student's data" action) was deferred.

### What changed

| File | Change |
|---|---|
| `langflow/build_flows.py` | The 5 per-tool flows set `should_store_message = False` on Chat Input and Chat Output. The `sigap_agent` flow still stores messages (see "Do not undo"). |
| `compose.yaml` | `LANGFLOW_TRANSACTIONS_STORAGE_ENABLED=false`, `LANGFLOW_VERTEX_BUILDS_STORAGE_ENABLED=false`, `LANGFLOW_DEACTIVATE_TRACING=true`. These run logs copied message text and have no delete API. |
| `sigap/retention.py` (new) | `purge_database`, `purge_outbox`, `purge_langflow`, `run_once`, CLI `main`. Limits come from `.env` through `limits()`. |
| `sigap/web.py` | `retention_loop()` runs `retention.run_once` at startup and every 10 minutes. |
| `pyproject.toml` | New command `sigap-retention` (one pass; `--all-chats` deletes every stored Sigap chat message in Langflow). |
| `sigap/student_api.py` | `CONSENT_VERSION = "2026-10-v2"`; `/api/student/me` returns `privacy` from `privacy_facts()`: the AI model read from the running flow (cached 5 minutes), `SIGAP_AI_PROVIDER`, the retention limits, and `SIGAP_PRIVACY_CONTACT`. |
| `sigap/static/student/app.js` | The privacy notice is built by `privacyText(me.privacy)` from those facts: what is stored and for how long, Telegram's own copy, the provider, and a contact marked as a demo address. The portal's Privasi card can show it again. No em dashes in the notice. |
| `README.md` | New section "Privacy and data retention (UU PDP)". |
| `.env` (not in git) | Added `SIGAP_AI_PROVIDER="DeepSeek, melalui SumoPod"`. |

### Retention limits (defaults; all overridable in `.env`)

| Data | Where | Kept | Setting |
|---|---|---|---|
| Agent chat messages | Langflow | 24 h | `SIGAP_CHAT_RETENTION_HOURS` |
| Tool-flow messages, run logs | Langflow | not stored | build_flows / compose.yaml |
| Usage log (no message text) | Sigap DB | 90 days | `SIGAP_USAGE_RETENTION_DAYS` |
| Unanswered question text | Sigap DB | 30 days, then the text is cleared and the row kept | `SIGAP_UNANSWERED_RETENTION_DAYS` |
| Sent reminders | Sigap DB | 7 days after sending | `SIGAP_REMINDER_RETENTION_DAYS` |
| Closed handoffs and their events | Sigap DB | 180 days after the last update | `SIGAP_HANDOFF_RETENTION_DAYS` |
| `.eml`/`.ics` files and rows of `outbox/*.csv` | `outbox/` | 30 days | `SIGAP_OUTBOX_RETENTION_DAYS` |
| Expired login codes, link codes, link requests, admin and student sessions | Sigap DB | deleted once expired | none |

Open and answered handoffs are never deleted automatically; only closed ones age out.

### Do not undo

- **Keep message storage ON in the `sigap_agent` flow.** The Agent reads its last 20 messages as conversation
  memory, and `LangflowTools._agent_steps()` (`sigap/langflow_client.py`) reads which tools were called from the
  stored reply. Turning it off breaks multi-turn answers and the confirm buttons. Old messages are purged instead.
- **The 24 h purge relies on the session reset in `sigap/chat.py`** (a new random `s-…` code after
  `SIGAP_SESSION_IDLE_HOURS`, default 6, or at midnight WIB). If you lengthen sessions, keep them shorter than
  `SIGAP_CHAT_RETENTION_HOURS`, or live conversations lose their memory mid-way.
- **Raise `CONSENT_VERSION` whenever the privacy notice text changes**, so students agree to the new text.
- **SQLModel here requires timezone-aware datetimes** in queries (`now_utc()`); naive ones raise
  "Datetime values must have timezone information".
- Langflow transaction records cannot be deleted through its API. If run logs are ever re-enabled, clearing them
  needs direct SQL in the container's `/app/langflow/langflow.db`.

### Already done on the running system

- Flows rebuilt; the Langflow container recreated with the new environment.
- One-time cleanup: all 4,056 Langflow messages; for Sigap's 6 flows, 3,000 transaction records, 3,000 builds, 2,028 traces and 13,363 spans (direct SQL); 11 expired codes and sessions in `var/sigap.db`.
- Backend restarted (same `SIGAP_NOW` and `SIGAP_PUBLIC_URL`); the startup pass is confirmed to run.

### How it was tested

- Backdated rows in a throwaway database and outbox: 8/8 rules delete exactly the old rows (including a
  multi-line, comma-containing question in `tickets.csv`).
- Live Langflow: 2 tool-flow calls stored 0 messages; 0 transaction, build, trace or span rows written; a
  2-turn agent conversation still resolved "it" from turn 1 (SD-UTS reminder at 19 Oct 20:00), and the 24 h purge
  kept it.
- Planted an expired link code in the live DB; the startup pass deleted it.
- Agent eval sample (`--limit 2`): 15/16. The miss (P02/en cited §5.1) is DeepSeek variance: the policy tool
  returns §3.2 for that question directly.

### Known gaps

- Application log lines (logger `sigap`) never reach `var/sigap-live.log` (pre-existing; uvicorn's logging setup),
  so retention passes are not visible in the log.
- No tool to delete one student's data on request (plan step 5, deferred). A deletion request still only creates
  a "privasi" ticket for staff.
- Two em dashes remain in the student portal outside the notice (`student/app.js`, the "Terhubung!" screen and
  the new-token message); audit finding #9 in `anti-slop/audit-001-2026-10-04.md` is still open.

## 2026-10-04: Earlier the same day (also uncommitted)

- **Custom reminder times:** `create_study_reminder` takes `remind_at` in the student's own words; `parse_remind_at`
  in `sigap/tools.py` computes the time in code and returns `INVALID_TIME` (unclear, past, after the deadline).
  The yes button stores the exact ISO time. Golden set gained 8 reminder questions (`R01`–`R08`).
- **Agent narration stripped:** text the model writes before its last tool call ("Let me check…") is removed from
  replies (`run_agent` / `_agent_steps` in `sigap/langflow_client.py`); the agent prompt also says to call tools
  silently.
- **DeepSeek loop fix:** prompt rule "call answer_campus_policy once per question"; the step limit stays at 8.
