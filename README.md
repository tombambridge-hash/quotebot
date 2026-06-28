# QuoteBot v2 — Bambridge Renewables lead-to-proposal bot

Watches **tombambridge@icloud.com** for Formspree lead emails from the solar
website and, with **zero human involvement**, creates a full **Easy-PV**
customer proposal: it creates the project via the Easy-PV API, then hands a
dedicated Chrome window to **Claude Computer Use** to log in, finish the roof
design, pick the standard components and generate the customer proposal, then
confirms the proposal PDF via the API and emails you the project link. You can
also talk to it by emailing commands from any device.

If anything goes wrong it **always emails you the project URL to finish
manually** — no lead is ever lost.

## The pipeline

1. Poll iCloud over IMAP every 60s.
2. A Formspree lead arrives from `noreply@formspree.io`.
3. Parse the customer details (handles Formspree's **two-line** field format).
4. `POST /api/v1/projects/create` (header `X-API-KEY`, `owner =
   bambridgeelectrical@icloud.com`, `magicMode: true`) → `projectId`.
5. Launch a dedicated Chrome window (the **QuoteBot** profile, isolated from your
   everyday Chrome) and run Claude Computer Use to log in, open
   `…/project/{projectId}`, complete the design, select the standard components
   and generate the customer proposal.
6. Poll `GET /api/v1/files/list` every 30s for up to 3 minutes for a file with
   id `customerProposal`.
7. On success, email: *"New lead #X processed — Easy-PV proposal generated.
   Project: …"*
8. On any failure or timeout (10 min cap / 50-step cap), email:
   *"Computer Use failed — please complete this proposal manually: …"*

Everything is logged verbosely to `logs/quotebot.log`.

## Requirements (the Mac mini)

- **macOS 14.x**, always on, with a real screen (Chrome is visible, not headless).
- **Homebrew Python 3.11** — do **not** use the system Python 3.9 (LibreSSL/IMAP
  problems). Install it first:
  ```bash
  brew install python@3.11
  python3.11 --version    # confirm before continuing
  ```
- **Google Chrome** installed at `/Applications/Google Chrome.app`.
- **Privacy & Security permissions** (without these Computer Use fails silently):
  - *Screen Recording* → enable **Terminal** and **Python**
  - *Accessibility* → enable **Terminal** and **Python**

## Install

```bash
git clone https://github.com/tombambridge-hash/quotebot.git
cd quotebot
./install.sh        # creates config.yaml the first time — edit it, then re-run
```

`config.yaml` is gitignored. Fill in (see `config.example.yaml` for the full,
commented list):

| Setting | What |
|---|---|
| `icloud.app_password` | iCloud **app-specific** password (appleid.apple.com → Sign-In and Security) |
| `easypv.email` / `easypv.password` | Easy-PV login (Computer Use logs in fresh each run) |
| `easypv.api_key` | Easy-PV REST API key (sent as `X-API-KEY`) |
| `anthropic.api_key` | Anthropic API key (console.anthropic.com) |

`install.sh` then runs the tests and installs the launchd service so the bot
starts at login and restarts on crash. Keep the Mac awake (`caffeinate -dimsu &`
or the Energy settings) — a sleeping Mac can't read mail or drive Chrome.

### Calibrate / watch it work

```bash
./venv/bin/python -m quotebot.easypv            # create a test project via the API
./venv/bin/python -m quotebot.easypv --design   # …and watch a full Computer Use run
tail -f logs/quotebot.log                        # live activity
```

## Computer Use model & cost

The Computer Use model is set by `anthropic.computer_use_model` (default
`claude-sonnet-4-6` — the most cost-effective model that reliably handles a
multi-step roof design). The **beta header and tool version are derived from the
model automatically**, so they can never drift out of sync:

| Model | Beta header | Tool version |
|---|---|---|
| `claude-sonnet-4-6`, `claude-opus-4-8/4.7/4.6/4.5` | `computer-use-2025-11-24` | `computer_20251124` |
| `claude-haiku-4-5`, `claude-sonnet-4-5` | `computer-use-2025-01-24` | `computer_20250124` |

`claude-haiku-4-5` is cheaper but less reliable on complex designs;
`claude-opus-4-8`/`4-7` are the most accurate (and most expensive). Each proposal
is capped at **50 steps** and **10 minutes** (`anthropic.max_steps` /
`anthropic.timeout_seconds`); `anthropic.effort` defaults to `medium`, the
recommended setting for computer use.

> Verified against the Computer use docs (June 2026). The brief's
> `computer-use-2024-10-22` header is from the retired Claude 3.5 era — the
> values above are current.

## Proposal backend (Easy-PV or Pylon)

The proposal backend is pluggable — set `provider: easypv` or `provider: pylon`
in `config.yaml`. Both follow the same flow (API creates the project →
Computer Use finishes the design + generates the proposal → confirm → email);
they differ only in API/auth/UI, which live in each provider's config section:

- **easypv** — `X-API-KEY`, `/api/v1/projects/create`, `magicMode`, confirmed by
  polling `/files/list` for the `customerProposal` file.
- **pylon** ([getpylon.com](https://getpylon.com/developers/)) — `Authorization:
  Bearer`, `/lead_form` to create, proposals signalled by webhook (so
  confirmation is `confirm_mode: trust` by default, or `poll` a configured
  endpoint). Pylon's API reference is gated, so the endpoint/URL/field values in
  the `pylon:` config block are defaults to **confirm against your account** —
  they're config, not hard-coded, so no code change is needed to correct them.

Components, login, and Chrome are shared; the `chrome:` block configures the
dedicated isolated Chrome window used by either provider.

## Talking to the bot

Email **tombambridge@icloud.com → itself** with subject starting **Bot**. The
first line of the body is the command:

```
status                          recent leads & their state
show 3                          full details for lead #3
run 3                           (re)run the Easy-PV pipeline for lead #3
spec 3 panels=12 batteries=1 storeys=2    set specs (then runs the pipeline)
ignore 3                        drop lead #3
help                            list commands
```

Only senders in `owner.command_senders` are obeyed. Forwarding a Formspree email
(subject `Fwd: …`) ingests it as a lead.

## State database (`state.db`)

SQLite, tracks leads and the last processed IMAP UID to prevent duplicate
processing. The `last_uid` is read on startup and **written immediately after
each message is fetched, before processing it**, so a crash mid-pipeline can't
re-process the same email; it is never reset on restart. New columns are added
to an existing v1 database additively (no data loss). Schema (from
`quotebot/state.py`):

```sql
CREATE TABLE leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL,
    name TEXT, email TEXT, phone TEXT,
    address TEXT, postcode TEXT, message TEXT,
    panels INTEGER, batteries INTEGER, scaffold TEXT,
    roof_faces INTEGER, dc_strings INTEGER, ev_charger INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',   -- new | awaiting_info | project_created |
                                 -- proposal_generated | needs_manual | ignored | error
    quote_total INTEGER, quote_text TEXT,
    easypv_project_id TEXT, easypv_url TEXT,
    proposal_confirmed INTEGER DEFAULT 0,
    details TEXT,                -- JSON of the rich form fields (epc, roof, usage…)
    error TEXT, raw TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);   -- holds last_uid
```

## Files

```
quotebot/main.py          daemon loop (mail → project → Computer Use → confirm → notify)
quotebot/email_monitor.py iCloud IMAP polling, UID tracking
quotebot/lead_parser.py   Formspree two-line (and legacy colon/HTML) → structured lead
quotebot/providers.py     provider selection (easypv | pylon)
quotebot/easypv.py        Easy-PV provider (REST API + Computer Use orchestration)
quotebot/pylon.py         Pylon (getpylon.com) provider (Bearer API + Computer Use)
quotebot/browser.py       shared dedicated/isolated Chrome launcher
quotebot/computer_use.py  Claude Computer Use agent (drives the Mac screen)
quotebot/pricing.py       Bambridge services pricing formula (for owner reference)
quotebot/commands.py      email command channel
quotebot/notify.py        email + optional iMessage notifications
quotebot/state.py         SQLite persistence + migrations
config.example.yaml       all settings, copy to config.yaml
install.sh                Python 3.11 venv + launchd installer
```

## Troubleshooting

- **Computer Use does nothing / clicks miss** → grant Screen Recording +
  Accessibility to Terminal *and* Python, then reload the service.
- **IMAP login fails** → regenerate the iCloud app-specific password; make sure
  you're on Homebrew Python 3.11, not system 3.9.
- **`create_project` errors** → check `easypv.api_key`; the full API response is
  logged. Add any account-specific fields under `easypv.extra_project_fields`.
- **Proposal not confirmed** → you still get an email with the project URL to
  finish by hand; widen `easypv.poll_timeout_seconds` if Easy-PV is slow.
- **Bot not replying to commands** → subject must start with `Bot` and come from
  an address in `owner.command_senders`.
