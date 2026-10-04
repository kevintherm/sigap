# Sigap — Smart School Service Center

*Sigap — biar fokusmu tetap belajar.* A chatbot that does three jobs for university students: **remember** their
deadlines and exams, **answer** campus rules from official documents with citations, and **route** to staff when it
isn't sure. Staff run it from an admin panel.

The rule that keeps it honest: **dates come from tables, rules come from documents, judgment comes from people.**

## Architecture

```
  Students                    Staff / lecturers / admins        Power users
  Telegram (WhatsApp later)   Admin panel (web)                 Own agent (Bob, Claude Code)
        │                            │                                 │
        └────────────────────────────┼─────────────────────────────────┘
                                     ▼
        ┌──────────────── Sigap backend (FastAPI + SQLite, no LLM) ────────────────┐
        │  authentication   Telegram invite links · panel logins · MCP tokens      │
        │  authorization    roles (admin/staff/lecturer) · own courses · limits    │
        │  data             source of truth: students, courses, schedule,         │
        │                   calendar, handbook versions, handoffs, reminders       │
        │  consent          only a button press confirms an action                 │
        │  channels         Telegram adapter · reminder delivery · staff replies   │
        └──────────────────────────────────┬────────────────────────────────────────┘
                                           │ service API key · course codes, never identities
                                           ▼
        ┌──────────────── Langflow (private, localhost) ───────────────────────────┐
        │  answer_campus_policy · get_my_deadlines · get_study_period              │
        │  create_study_reminder · escalate_to_student_services                    │
        └───────────────────────────────────────────────────────────────────────────┘
```

- **Langflow is the logic.** Five flows with the PRD's tool names compute every date, cited answer and action.
  When staff edit data, the backend pushes it into the flows. Langflow never sees who a student is: it only gets
  course codes.
- **The backend is the gatekeeper.** It decides who is talking, what they may see, and when an action is allowed.
- **IBM Bob is used to build the project** (competition rule). It can also be a client of the MCP gateway.

## Run it

```bash
docker compose up -d                       # Langflow at http://localhost:7860 (localhost only)
uv sync
uv run sigap-admin seed-demo               # database + synthetic demo data; prints panel passwords ONCE
set -a; . ./.env; set +a
python3 langflow/build_flows.py            # create/update the five flows (needs LANGFLOW_API_KEY)
uv run sigap-server                        # backend: http://127.0.0.1:8000/admin
```

The demo accounts from `seed-demo` are `admin@und.ac.id` (admin), `hendra@und.ac.id` (student services) and
`rina@und.ac.id` (lecturer of IF2101). The demo students are `2401001` Dinda (6 courses), `2401002` Raka (3 courses)
and `2401003` Sinta (3 courses). All data is synthetic.

`.env` (gitignored):

| Variable | Purpose |
|---|---|
| `LANGFLOW_URL`, `LANGFLOW_API_KEY` | The backend's service key for Langflow |
| `TELEGRAM_BOT_TOKEN` | Turns on the bot (from @BotFather). Long polling, no public URL needed |
| `SIGAP_PUBLIC_URL` | Base URL shown in the bot's `/token` message (default `http://localhost:8000`) |
| `SIGAP_DATABASE_URL` | Default `sqlite:///var/sigap.db` |
| `SIGAP_NOW` | Pins the backend clock for demos, e.g. `2026-10-04T19:00` (the flows have their own "Demo time" field) |
| `SIGAP_MCP_ESCALATIONS_PER_DAY`, `SIGAP_MCP_REQUESTS_PER_MINUTE` | Gateway limits (default 5 and 60) |
| `SIGAP_SHOW_TRACE` | `0` hides the "🔧 tool — via langflow" line in Telegram |
| `SIGAP_AGENT` | `langflow` = Gemini agent understands messages; `rules` = keyword router |
| `SIGAP_AGENT_TIMEOUT` | Seconds before falling back to the rules (default 30) |
| `SIGAP_SESSION_IDLE_HOURS` | A chat starts a fresh agent memory after this many idle hours, and at midnight WIB (default 6) |

## Students: Telegram + web sign-in

**Self-service linking (normal path):**
1. An unlinked student sends `/start` or `/login` (or asks for their deadlines). The bot answers with a **🔐 Masuk**
   button: a one-time link to `/student?link=…`, tied to that chat and valid for 10 minutes.
2. On the web, the student enters their student ID or campus email and gets a **6-digit code by email**. The code is
   single-use, valid for 10 minutes and allows 5 attempts. Requests are limited to 3 per ID and 10 per IP per
   15 minutes, and the reply is identical whether or not the ID exists.
3. The first time, the student accepts the **privacy notice** (UU PDP; versioned).
4. The page shows **which chat account** will be linked ("Telegram: Dinda (@dinda_p)") and asks for confirmation. This
   stops a victim from being tricked into signing in through someone else's link. The bot then confirms in the chat.

**Fallback:** staff can still send an invite (**Admin → Mahasiswa → Undangan**, `t.me/<bot>?start=<code>`, one use,
7 days).

**Student portal (`/student`):** profile and courses, linked chats (unlink), tickets with staff replies, personal MCP
tokens (create/revoke), and a data-deletion request that lands in the staff inbox.

Email needs SMTP settings in `.env`: `SIGAP_SMTP_HOST`, `SIGAP_SMTP_PORT` (587 STARTTLS / 465 SSL),
`SIGAP_SMTP_USER`, `SIGAP_SMTP_PASSWORD`, `SIGAP_SMTP_FROM`, and optionally `SIGAP_SMTP_SECURITY=starttls|ssl|none`.
Set `SIGAP_PUBLIC_URL` to the public https address (the tunnel), because Telegram only accepts https buttons.

Linked students get their own deadlines, reminders and tickets. Unlinked chats can still ask about rules and the
academic calendar.

Commands: `/start`, `/tiket` (my handoffs and staff replies), `/saya` (linked account), `/token` (personal MCP
token), `/lang id|en`, `/unlink`, and `/demoreminder` (a reminder in 15 s, for recording the demo). The bot only
answers private chats.

## Staff: admin panel (`/admin`)

| Page | Who | What |
|---|---|---|
| Kotak masuk | admin, staff | Handoffs with status (open → in progress → answered → closed), internal notes, replies delivered straight into the student's chat (FR-7) |
| Dasbor | admin, staff | Messages per day, outcomes, tools used, channels and languages, response time, and **unanswered questions**, which show what the handbook lacks (FR-15) |
| Mahasiswa | admin, staff | Students, enrolments, invite links, linked chats, MCP tokens (revoke), tickets |
| Jadwal & tenggat | all; lecturers edit **their own courses** | Deadlines and exams; saved changes sync to Langflow |
| Kalender akademik | admin edits | Term, periods and windows |
| Pedoman | admin uploads/activates | Handbook versions: upload, validate, activate. Old versions are kept but never used (FR-10). Also the format guide, a downloadable template, and the formatting agent (below) |
| Pengguna | admin | Staff and lecturer accounts, roles, lecturer courses, password resets |
| Uji chat | all | Try the bot as any student before students see a change |

Security notes:
- Passwords are hashed with argon2id.
- Sessions are random tokens stored hashed, in an HttpOnly + SameSite=Strict cookie.
- Every state-changing request needs an `X-Sigap-Request` header (CSRF protection).
- Logins are limited to 5 attempts per 5 minutes.
- The server binds to 127.0.0.1 by default. Put it behind HTTPS before exposing it.

### Handbook formatting agent

Campuses hand over their handbook as Word or PDF, not in Sigap's article format. On **Pedoman → Buat draf dengan
agen**, an admin uploads the official document (`.docx`, text-based `.pdf`, `.md` or `.txt`, up to 8 MB) or pastes
the text, and fills in title, version and effective date. The backend extracts the text, drops the table of
contents, splits it at article boundaries, and sends each excerpt to the Langflow flow `handbook_formatter` (same
model as the chat agent, no tools). The agent returns articles as JSON: number, titles, tags, Indonesian and English
text. The backend then checks every draft against the source before anyone sees it:

- numbers are compared article by article with that article's own passage ("75%" changed to "70%" is caught even
  when 70 appears elsewhere in the handbook), and numbers in the source that no article carries are listed;
- source sentences no article covers are listed as "Mungkin terlewat";
- an article whose text is not a real sentence, or whose tags overlap heavily with another article's, is flagged;
- tags are cleaned: single words only, no contact details, no words that appear in nearly every question.

The draft lands in the review box with those warnings, and "Langsung aktifkan" is switched off: nothing goes live
until an admin has checked it and pressed **Validasi & unggah**. Formatting runs leave no messages in Langflow.
The template is at `/admin/static/template-pedoman.md`.

Tested on the demo handbook rebuilt as a raw Word file (cover, foreword, table of contents, Indonesian only): 18 of
18 articles, Indonesian text identical to the original, about 60 seconds. Retrieval with the agent's tags: trick
questions 20/20 on four drafts (no false answers); policy questions 40/40 on two drafts and 37 to 38 of 40 on two,
always between closely related articles (attendance vs exams), which the shared-tag warning points out.

## Power users: MCP gateway (`/mcp`)

A linked student sends `/token` to the bot and connects their own agent:

```bash
claude mcp add --transport http sigap http://localhost:8000/mcp --header "Authorization: Bearer <token>"
```

The gateway has six tools: the five PRD tools plus `get_my_tickets`. Each token acts as one student:
- deadlines are filtered to that student's courses
- reminders only work for the student's own items
- escalations become handoffs in the inbox
- limits: 60 requests a minute and 5 escalations a day

Revoking a token in the panel takes effect immediately.

## Understanding messages: Gemini agent (LLM mode)

With `SIGAP_AGENT=langflow` in `.env`, each student message goes to the **`sigap_agent`** flow:
Chat Input → Agent (Gemini, `gemini-3.5-flash-lite` by default, key from the Langflow Global Variable
`GEMINI_API_KEY`) with the five tools → Chat Output. The model only understands and chooses; code enforces the
safety rules:

| Risk | Enforced by code, not the prompt |
|---|---|
| Seeing another student's deadlines | The backend sets the student's course codes on the deadlines tool with a Langflow **tweak** per request, so the model's arguments can't change them (tested with a prompt-injection attempt) |
| Acting without consent | Inside the agent flow the action tools are **preview-only**. The backend reads the proposal from the tool's real output, writes the confirm line itself (exact dates, office) and acts only on a button press |
| An LLM-rewritten query "finding" a rule | The policy tool also gets the student's **original message**; an article counts only if the student's own words match it too |
| Identity in the LLM | Langflow gets a random session alias and course codes, never names, student IDs or chat IDs |
| Agent loops (hits its step limit) | Retried once, then the rule-based router answers |
| Gemini down or rate-limited | The backend falls back to the rule-based router after `SIGAP_AGENT_TIMEOUT` (30 s) |

`uv run sigap-eval --agent --limit 4` grades a sample through Gemini by the tools it actually called. The last run
scored 24/24: Gemini answered 21 (median 3.6 s) and 3 fell back after the free tier's rate limit. **The free tier is
small** (e.g. 20 requests/day per model for `gemini-3.8-flash`; each message uses 2–4 requests), so enable billing on
the Google project before the demo. Set `SIGAP_AGENT_MODEL` and rerun `build_flows.py` to change the model, or
`SIGAP_AGENT=rules` to switch the LLM off.

## Privacy and data retention (UU PDP)

Langflow, the AI provider and Sigap never get a student's name, NIM or chat account; the agent sees only the
message text, course codes and a random session code. What is kept, and for how long:

| Data | Where | Kept |
|---|---|---|
| Chat messages (the agent's memory) | Langflow | 24 h, then deleted by the backend (`SIGAP_CHAT_RETENTION_HOURS`) |
| Tool-flow inputs and outputs | Langflow | not stored (`should_store_message` off) |
| Run logs (transactions, builds, traces) | Langflow | not stored (`compose.yaml` turns them off) |
| Usage log, no message text | Sigap DB | 90 days (`SIGAP_USAGE_RETENTION_DAYS`) |
| Unanswered questions, no identity | Sigap DB | text cleared after 30 days (`SIGAP_UNANSWERED_RETENTION_DAYS`) |
| Reminders | Sigap DB | 7 days after sending (`SIGAP_REMINDER_RETENTION_DAYS`) |
| Handoffs and staff replies | Sigap DB | 180 days after the ticket is closed (`SIGAP_HANDOFF_RETENTION_DAYS`) |
| Handoff email copies, calendar files, outbox logs | `outbox/` | 30 days (`SIGAP_OUTBOX_RETENTION_DAYS`) |
| Expired sign-in codes, invites and sessions | Sigap DB | deleted once expired |

The backend runs the cleanup every 10 minutes (`sigap/retention.py`); `uv run sigap-retention` runs one pass by
hand, and `--all-chats` clears every stored Langflow chat message. Conversations restart after 6 idle hours or at
midnight, so the 24-hour window never cuts into a live conversation.

The privacy notice in the student portal reads the AI model from the running flow, the provider name from
`SIGAP_AI_PROVIDER`, the limits from the settings above and the contact from `SIGAP_PRIVACY_CONTACT`. Raise
`CONSENT_VERSION` in `sigap/student_api.py` whenever its text changes, so students agree to the new version.

Outside Sigap's control: Telegram keeps its own copy of each chat, and the AI provider handles messages under its
own privacy policy.

## Langflow

```bash
set -a; . ./.env; set +a; python3 langflow/build_flows.py
```

`build_flows.py` generates a self-contained custom component per tool from `sigap/*.py` and the data. It then
creates or updates the flows in the **Sigap** project with MCP turned on, and exports them to `langflow/flows/*.json`
(importable into any Langflow 1.11+). The backend's startup sync and each admin edit then replace the data fields
with the database's current content. Handoff emails and `.ics` files are written in the Langflow volume under
`/app/langflow/sigap-outbox/`; set `SIGAP_SMTP_*` in the container to send real email.

## Tests

```bash
uv run sigap-eval                          # 48-question golden set × 2 languages, through Langflow
SIGAP_TOOLS=local uv run sigap-eval        # same, against the local Python tools
```

## Demo scenarios (PRD)

| # | Student says (Telegram) | Flows called | Proves |
|---|---|---|---|
| 1 | "Apa saja yang deadline minggu ini?" | `get_my_deadlines` | One list across *their* courses, tonight's item flagged |
| 2 | "When is my Data Structures midterm?" → ✅ | `get_my_deadlines` → `create_study_reminder` | Dates from the schedule; nothing created before the button; reminders arrive in the chat |
| 3 | "Kalau telat submit tugas, masih dinilai nggak?" | `answer_campus_policy` | Quotes Handbook §4.2 with version and date |
| 4 | "Nilai saya belum keluar 3 minggu, saya harus ke siapa?" → ✅ | `answer_campus_policy` (NOT_FOUND) → `escalate_to_student_services` | Admits the gap, asks first, gives a reference; staff reply from the panel lands in the chat |
| 5 | "Kerjain essay saya dong" | `get_my_deadlines` → `create_study_reminder` (preview) | Declines, offers a reminder and study plan instead |
| 6 | "Ingatkan aku jam 9 malam buat Tugas 3 Basis Data" → ✅ | `get_my_deadlines` → `create_study_reminder` (`remind_at`) | One reminder at 21:00; the time is worked out in code from the student's words and refused if it is past or after the deadline |

## Layout

```
sigap/
  models.py, db.py         SQLite tables (SQLModel), WAL, Alembic migrations (migrations/)
  security.py              argon2 passwords, hashed tokens, sessions
  services.py              linking, tokens, handoffs, reminders, usage log, data export
  sync.py                  database → Langflow data push
  chat.py                  channel-independent chat service (identity + bookkeeping)
  agent.py                 conversation rules: routing, consent offers, language (no LLM)
  telegram_bot.py          Telegram adapter
  channels.py              outbound delivery registry (staff replies, reminders)
  mcp_server.py            MCP gateway (per-student tokens)
  admin_api.py, static/admin/   admin panel
  web.py                   the server; cli.py: sigap-admin
  tools.py, retrieval.py, textutil.py, data.py   tool logic (bundled into the Langflow components)
  langflow_client.py       calls the flows
langflow/                  build_flows.py, generated components, exported flows
data/                      synthetic seed data (courses, schedule, calendar, handbook)
eval/golden_set.csv        20 policy + 10 dates + 10 trap questions
bob/                       Bob mode instructions + MCP config example
```

## Future vision

### Integration with existing LMS systems

Today deadlines and enrolments come from the admin panel or CSV files, even though each schedule row already
records its LMS location (`LMS › Struktur Data › Tugas 3`). The next step is to read them from the campus LMS
directly, so lecturers keep working where they already do:

- **Deadlines and exams synced from the LMS** (for example Moodle web services, Canvas or Google Classroom APIs)
  into the existing `ScheduleItem` table, then pushed to Langflow by the sync that already runs after every change.
  The admin panel stays for items the LMS does not hold, such as exam rooms.
- **Enrolments synced from the LMS roster**, so each student's courses are always current and the deadline tool
  stays limited to their own classes.
- **Sign-in through the LMS** (LTI 1.3 launch or the campus SSO behind it, FR-14), replacing invite links and
  emailed codes for students who are already logged in to the LMS.
- **Reminders that follow LMS changes:** when a lecturer moves a deadline, scheduled reminders move with it and the
  student is told.

### WhatsApp gateway

A WhatsApp channel beside Telegram, built on the WhatsApp Business Platform. The chat service, account linking,
reminders and staff replies are already channel-independent, so it is a new adapter next to `telegram_bot.py`:

- **Inbound by webhook**, which needs a permanent public HTTPS address (Telegram's long polling does not).
- **Confirm buttons** map to WhatsApp reply buttons, so nothing is created or sent without the student's tap.
- **Reminders and staff replies** sent outside WhatsApp's 24-hour conversation window must use pre-approved
  message templates.
- **Linking** reuses the existing web sign-in: the bot sends the same one-time link it sends on Telegram.
