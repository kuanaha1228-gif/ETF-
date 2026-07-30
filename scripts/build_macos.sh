#!/usr/bin/env bash
set -euo pipefail
python -m pip install --no-build-isolation -e '.[build,market,desktop]'
python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "ETF Plan Assistant" \
  --icon "assets/app-icon.icns" \
  --osx-bundle-identifier "com.etfplanassistant.desktop" \
  --collect-all akshare \
  --collect-all py_mini_racer \
  --add-data "src/etf_assistant/web:etf_assistant/web" \
  src/etf_assistant/gui_entry.py
plutil -replace CFBundleShortVersionString -string "1.7.0" "dist/ETF Plan Assistant.app/Contents/Info.plist"
plutil -replace CFBundleVersion -string "1.7.0" "dist/ETF Plan Assistant.app/Contents/Info.plist"
codesign --force --deep --sign - "dist/ETF Plan Assistant.app"
rm -rf dist/dmg-root
mkdir -p dist/dmg-root
cp -R "dist/ETF Plan Assistant.app" dist/dmg-root/
hdiutil create -volname "ETF Plan Assistant 1.7" -srcfolder dist/dmg-root -ov -format UDZO dist/ETFPlanAssistant-V1.7.dmg
shasum -a 256 dist/ETFPlanAssistant-V1.7.dmg
