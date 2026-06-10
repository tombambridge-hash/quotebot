# QuoteBot — Bambridge Renewables lead-to-proposal bot

Watches **tombambridge@icloud.com** for Formspree lead emails from the solar
website, prices each job with the agreed **Bambridge pricing formula**
(`data/Bambridge_Pricing_Formula.xlsx`), drives **Google Chrome** to create the
project in **Easy-PV**, and emails you the quote + project link. You can talk
to it **from any device** by emailing it commands.

## What happens when a lead arrives

1. Formspree email lands in your iCloud inbox.
2. Bot parses name / phone / address / postcode / message, and pulls panel &
   battery counts out of the enquiry if they're mentioned.
3. **If it can size the job** → it prices it (formula below), opens Chrome,
   logs into Easy-PV, creates the project pre-filled with the client details,
   adds the services total as a custom cost line, and emails you the quote
   breakdown + Easy-PV link (plus an optional iMessage ping).
4. **If it can't size the job** → it emails you the lead and asks for specs.
   Reply from your iPhone/iPad/any Mac with subject **Bot** and first line:

   ```
   spec 3 panels=12 batteries=1 storeys=2
   ```

   and it finishes the job (prices + Easy-PV proposal) and replies.

## Pricing formula (from the spreadsheet, rates agreed June 2026)

| Component | Rate |
|---|---|
| Panel labour | £80 / panel |
| DC stringing | £25 / panel |
| Battery install | £220 / battery |
| Battery rack & DC bus | £100 / battery |
| Frame/rail | £150 / roof face |
| DC string to inverter | £50 / string |
| Inverter mount & AC connection | £200 |
| Inverter commissioning | £80 |
| AC electrical materials | £400 |
| Scaffolding | £400–£1,400 by property (2-storey = £800) |
| G99 admin + commissioning | £300 (auto-added when system > 3.68 kW) |
| MCS registration | £100 |
| EV charger add-on | £150 |

If rates change, edit `quotebot/pricing.py` (`RATES` / `SCAFFOLD`) to match the
updated spreadsheet.

## Install (on the Mac that will run the bot)

```bash
git clone https://github.com/tombambridge-hash/quotebot.git
cd quotebot
./install.sh          # creates config.yaml first time — edit it, then re-run
```

You need one credential in `config.yaml` (it's gitignored, never pushed):

1. **iCloud app-specific password** — your normal Apple password won't work
   over IMAP. Create one at <https://appleid.apple.com> → *Sign-In and
   Security* → *App-Specific Passwords*.

**No Easy-PV password needed** — the bot keeps its own logged-in Chrome
profile. During calibration (below) a Chrome window opens; log into Easy-PV in
it once and the bot stays logged in from then on, like your normal Chrome.

`install.sh` installs a launchd service so the bot starts at login and
restarts itself if it crashes. The Mac must be on (and not fully asleep) for
the bot to work — in System Settings enable *"Prevent automatic sleeping when
the display is off"* or use `caffeinate`, since a sleeping Mac can't read mail
or drive Chrome.

### First-time Easy-PV calibration

Easy-PV's pages change occasionally, so do one supervised test run:

```bash
./venv/bin/python -m quotebot.easypv
```

A visible Chrome window opens, logs in and creates a test project so you can
confirm the flow works on your account. If a step fails it saves a screenshot
in `logs/`. **Note:** the roof design (drawing the array on the map) needs a
human eye — the bot creates the project, fills the client details and adds
your services price; you finish the design from the emailed link.

## Talking to the bot remotely

Email **tombambridge@icloud.com → itself** (from Mail on any Apple device)
with subject starting **Bot**. First line of the body is the command:

```
status                          recent leads & their state
show 3                          full details for lead #3
price panels=12 batteries=1 storeys=2 ev=1
spec 3 panels=12 batteries=1    set specs for lead #3 → auto price + proposal
run 3                           re-run pricing + Easy-PV for lead #3
ignore 3                        drop lead #3
help                            list commands
```

The bot replies by email within one poll cycle (60s by default). Only senders
listed in `owner.command_senders` are obeyed.

Optional: set `owner.imessage_handle` in config.yaml and the bot also sends
you an iMessage ping when a proposal is ready (requires granting Terminal/
python automation access to Messages the first time macOS asks).

## Files

```
quotebot/main.py            daemon loop (mail → price → Easy-PV → notify)
quotebot/email_monitor.py   iCloud IMAP polling, UID tracking
quotebot/lead_parser.py     Formspree email → structured lead
quotebot/pricing.py         Bambridge formula
quotebot/easypv.py          Chrome/Playwright Easy-PV automation
quotebot/commands.py        email command channel
quotebot/notify.py          email + iMessage notifications
config.example.yaml         all settings, copy to config.yaml
install.sh                  one-shot macOS installer (venv + launchd)
data/Bambridge_Pricing_Formula.xlsx   source spreadsheet
```

## Troubleshooting

- `tail -f logs/quotebot.log` — live activity.
- Login failure on IMAP → regenerate the app-specific password.
- Easy-PV step failing → check `logs/easypv_*_error.png` screenshots, rerun
  the calibration; the bot still emails you the quote so no lead is lost.
- Bot not replying to commands → subject must start with "Bot" and be sent
  from an address in `owner.command_senders`.
