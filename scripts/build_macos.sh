#!/usr/bin/env bash
set -euo pipefail
python -m pip install '.[build,market,desktop]'
python -m PyInstaller --noconfirm --clean --onefile --name ETFPlanAssistant-Core --collect-all akshare src/etf_assistant/__main__.py
rm -rf dist/dmg-root
mkdir -p dist/dmg-root
cp dist/ETFPlanAssistant-Core dist/dmg-root/
hdiutil create -volname "ETF Plan Assistant" -srcfolder dist/dmg-root -ov -format UDZO dist/ETFPlanAssistant-Core.dmg
shasum -a 256 dist/ETFPlanAssistant-Core dist/ETFPlanAssistant-Core.dmg

