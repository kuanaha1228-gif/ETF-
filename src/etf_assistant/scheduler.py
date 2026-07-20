from __future__ import annotations

import html
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path

from .config import app_data_dir, default_log_dir


MAC_LABEL = "com.etfplanassistant.daily-check"
WINDOWS_TASK_NAME = "ETF Plan Assistant Daily Check"


def macos_plist(command: list[str]) -> bytes:
    log_dir = default_log_dir()
    payload = {
        "Label": MAC_LABEL,
        "ProgramArguments": command,
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": 14, "Minute": 50} for weekday in range(2, 7)
        ],
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "scheduler.out.log"),
        "StandardErrorPath": str(log_dir / "scheduler.err.log"),
    }
    return plistlib.dumps(payload, sort_keys=False)


def windows_task_xml(command: list[str]) -> str:
    executable = html.escape(command[0])
    arguments = html.escape(subprocess.list2cmdline(command[1:]))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>ETF Plan Assistant 14:50 daily market check</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T14:50:00</StartBoundary><Enabled>true</Enabled>
    <ScheduleByWeek><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>
  </CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable><WakeToRun>true</WakeToRun><ExecutionTimeLimit>PT5M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{executable}</Command><Arguments>{arguments}</Arguments></Exec></Actions>
</Task>"""


def install_scheduler(command: list[str]) -> Path:
    system = platform.system()
    default_log_dir().mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        target = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(macos_plist(command))
        subprocess.run(["launchctl", "unload", str(target)], check=False, capture_output=True)
        subprocess.run(["launchctl", "load", str(target)], check=True)
        return target
    if system == "Windows":
        xml = windows_task_xml(command)
        with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as stream:
            stream.write(xml)
            temporary = Path(stream.name)
        try:
            subprocess.run(
                ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(temporary), "/F"],
                check=True,
            )
        finally:
            temporary.unlink(missing_ok=True)
        return app_data_dir() / "scheduler" / "windows-task-installed"
    raise RuntimeError(f"scheduler installation is unsupported on {system}")


def remove_scheduler() -> None:
    system = platform.system()
    if system == "Darwin":
        target = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        subprocess.run(["launchctl", "unload", str(target)], check=False)
        target.unlink(missing_ok=True)
        return
    if system == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], check=False)
        return
    raise RuntimeError(f"scheduler removal is unsupported on {system}")

