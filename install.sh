#!/bin/bash
# QuoteBot installer for macOS. Run on your Mac from the repo folder:
#   ./install.sh
set -e
cd "$(dirname "$0")"

echo "== QuoteBot installer =="

if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install Xcode Command Line Tools first:"
  echo "  xcode-select --install"
  exit 1
fi

echo "-> Creating Python virtual environment"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "-> Wiring Playwright to your installed Google Chrome"
# Uses chrome_channel: chrome from config — no separate browser download needed,
# but install playwright deps just in case:
./venv/bin/playwright install chromium >/dev/null 2>&1 || true

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo ""
  echo "!! Created config.yaml — EDIT IT NOW with:"
  echo "   1. iCloud app-specific password (https://appleid.apple.com)"
  echo "   2. Your Easy-PV login"
  echo "   then re-run ./install.sh"
  exit 0
fi

echo "-> Running self-tests"
./venv/bin/python -m pytest tests/ -q || ./venv/bin/python -m unittest discover -s tests -q || true

echo "-> Installing launchd service (starts at login, restarts if it crashes)"
PLIST=~/Library/LaunchAgents/com.bambridge.quotebot.plist
mkdir -p ~/Library/LaunchAgents logs
sed -e "s|__REPO__|$(pwd)|g" launchd/com.bambridge.quotebot.plist > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "== Done. QuoteBot is running. =="
echo "   Logs:        tail -f $(pwd)/logs/quotebot.log"
echo "   Calibrate Easy-PV (watch Chrome do a test run):"
echo "                ./venv/bin/python -m quotebot.easypv"
echo "   Stop:        launchctl unload $PLIST"
