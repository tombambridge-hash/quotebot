#!/bin/bash
# QuoteBot v2 installer for the Mac mini. Run from the repo folder:
#   ./install.sh
#
# It uses Homebrew Python 3.11 (NOT the system Python 3.9, which has LibreSSL
# IMAP/SSL problems on macOS 14.x), installs dependencies, and sets up a
# launchd service that starts the bot at login and restarts it if it crashes.
set -e
cd "$(dirname "$0")"
REPO="$(pwd)"

echo "== QuoteBot v2 installer =="

# --- 1. Find Homebrew Python 3.11 (NEVER the system python3.9) -------------
# Prefer the Apple-silicon Homebrew path, then Intel Homebrew, then PATH — and
# confirm each candidate really reports 3.11 before accepting it (a python3.11
# that is somehow 3.9/3.12 is rejected rather than silently used).
PY311=""
for cand in /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 "$(command -v python3.11 2>/dev/null)"; do
  if [ -n "$cand" ] && [ -x "$cand" ] && \
     "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)' 2>/dev/null; then
    PY311="$cand"; break
  fi
done
if [ -z "$PY311" ]; then
  echo "!! Homebrew Python 3.11 not found (looked at /opt/homebrew/bin/python3.11,"
  echo "   /usr/local/bin/python3.11, and PATH). Install it with:"
  echo "     brew install python@3.11"
  exit 1
fi
echo "-> Using $PY311 ($($PY311 --version 2>&1))"

# --- 2. Create the venv with Python 3.11 -----------------------------------
# Always rebuild from scratch: 'python -m venv venv' over an EXISTING venv
# re-uses it, so a stale Python 3.9 venv from an earlier run would survive.
# Removing it first guarantees the venv is really 3.11.
echo "-> Creating Python 3.11 virtual environment (venv/)"
rm -rf venv
"$PY311" -m venv venv
VENV_VER="$(./venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$VENV_VER" != "3.11" ]; then
  echo "!! venv is Python $VENV_VER, expected 3.11 — aborting. Delete venv/ and"
  echo "   check that $PY311 is really Python 3.11."
  exit 1
fi
echo "-> venv Python $VENV_VER confirmed"
./venv/bin/python -m pip install --quiet --upgrade pip
echo "-> Installing dependencies"
./venv/bin/pip install --quiet -r requirements.txt

# --- 3. First-run config ----------------------------------------------------
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo ""
  echo "!! Created config.yaml — EDIT IT NOW with:"
  echo "   1. iCloud app-specific password   (https://appleid.apple.com)"
  echo "   2. Easy-PV login email + password"
  echo "   3. Easy-PV API key (X-API-KEY)"
  echo "   4. Anthropic API key (https://console.anthropic.com)"
  echo "   Then re-run ./install.sh"
  exit 0
fi

# --- 4. Self-tests ----------------------------------------------------------
echo "-> Running self-tests"
./venv/bin/python -m pytest tests/ -q || true

# --- 5. launchd service (references the 3.11 venv) -------------------------
echo "-> Installing launchd service (starts at login, restarts on crash)"
PLIST=~/Library/LaunchAgents/com.bambridge.quotebot.plist
mkdir -p ~/Library/LaunchAgents logs
sed -e "s|__REPO__|$REPO|g" launchd/com.bambridge.quotebot.plist > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

cat <<EOF

== Done. QuoteBot v2 is running. ==

IMPORTANT — grant these macOS permissions or Computer Use fails silently:
  System Settings -> Privacy & Security -> Screen Recording   -> enable Terminal AND Python
  System Settings -> Privacy & Security -> Accessibility       -> enable Terminal AND Python
(After granting, restart the bot:  launchctl unload "$PLIST" && launchctl load "$PLIST")

Other notes:
  - Install Google Chrome if it isn't already at /Applications/Google Chrome.app
  - Keep the Mac awake:  System Settings -> Lock Screen / Energy, or run 'caffeinate -dimsu &'
  - Live logs:   tail -f "$REPO/logs/quotebot.log"
  - Test Easy-PV API (creates a test project):  ./venv/bin/python -m quotebot.easypv
  - Watch a full Computer Use run on a test project:  ./venv/bin/python -m quotebot.easypv --design
  - Stop:        launchctl unload "$PLIST"
EOF
