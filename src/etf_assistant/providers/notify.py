from __future__ import annotations

import os
import platform
import smtplib
import ssl
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


class NotificationProvider(Protocol):
    name: str

    def send(self, title: str, body: str) -> None: ...


@dataclass(slots=True)
class MacOSDesktopNotifier:
    name: str = "desktop"

    def send(self, title: str, body: str) -> None:
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        subprocess.run(
            ["osascript", "-e", script, title, body],
            check=True,
            capture_output=True,
            text=True,
        )


@dataclass(slots=True)
class WindowsDesktopNotifier:
    name: str = "desktop"

    def send(self, title: str, body: str) -> None:
        script = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$safeTitle = [System.Security.SecurityElement]::Escape($env:ETF_NOTICE_TITLE)
$safeBody = [System.Security.SecurityElement]::Escape($env:ETF_NOTICE_BODY)
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$safeTitle</text><text>$safeBody</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ETF Plan Assistant').Show($toast)
"""
        environment = os.environ.copy()
        environment["ETF_NOTICE_TITLE"] = title
        environment["ETF_NOTICE_BODY"] = body
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )


def desktop_notifier() -> NotificationProvider:
    system = platform.system()
    if system == "Darwin":
        return MacOSDesktopNotifier()
    if system == "Windows":
        return WindowsDesktopNotifier()
    raise RuntimeError(f"desktop notifications are not supported on {system}")


@dataclass(slots=True)
class SmtpEmailNotifier:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_ssl: bool = True
    name: str = "email"

    def send(self, title: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(body)
        context = ssl.create_default_context()
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=15) as client:
                client.login(self.username, self.password)
                client.send_message(message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=15) as client:
                client.starttls(context=context)
                client.login(self.username, self.password)
                client.send_message(message)


@dataclass(slots=True)
class RecordingNotifier:
    name: str
    messages: list[tuple[str, str]]
    should_fail: bool = False

    def send(self, title: str, body: str) -> None:
        if self.should_fail:
            raise RuntimeError("simulated notification failure")
        self.messages.append((title, body))


@dataclass(slots=True)
class ConsoleNotifier:
    name: str = "console"

    def send(self, title: str, body: str) -> None:
        print(f"{title}\n\n{body}")
