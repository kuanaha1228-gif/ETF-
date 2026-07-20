$ErrorActionPreference = "Stop"
python -m pip install ".[build,market,desktop]"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ETFPlanAssistant --collect-all akshare --collect-all keyring src/etf_assistant/gui.py
Get-FileHash -Algorithm SHA256 .\dist\ETFPlanAssistant.exe | Format-List
