$ErrorActionPreference = "Stop"
python -m pip install ".[build,market,desktop]"
python -m PyInstaller --noconfirm --clean --onefile --name ETFPlanAssistant-Core --collect-all akshare src/etf_assistant/__main__.py
Get-FileHash -Algorithm SHA256 .\dist\ETFPlanAssistant-Core.exe | Format-List

