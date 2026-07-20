#!/usr/bin/env bash
set -euo pipefail
python -m pip install '.[build,market,desktop]'
python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "ETF Plan Assistant" \
  --collect-all akshare \
  --collect-all keyring \
  src/etf_assistant/gui_entry.py
rm -rf dist/dmg-root
mkdir -p dist/dmg-root
cp -R "dist/ETF Plan Assistant.app" dist/dmg-root/
hdiutil create -volname "ETF Plan Assistant" -srcfolder dist/dmg-root -ov -format UDZO dist/ETFPlanAssistant-Core.dmg
shasum -a 256 dist/ETFPlanAssistant-Core.dmg
