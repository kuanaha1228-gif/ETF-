$ErrorActionPreference = "Stop"
python -m pip install ".[build,market,desktop]"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ETFPlanAssistant --collect-all akshare --collect-all py_mini_racer --add-data "src/etf_assistant/web;etf_assistant/web" src/etf_assistant/gui_entry.py
Get-FileHash -Algorithm SHA256 .\dist\ETFPlanAssistant.exe | Format-List
