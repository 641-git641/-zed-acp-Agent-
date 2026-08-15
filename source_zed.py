"""Zed activity source: zed.exe process probe + read-only tail of Zed.log.

Zed's log records ACP (agent client protocol) session signals, most notably
`agent_client_protocol::util connection; name="zed"` when an agent session
connects, `agent_servers::acp` stderr passthrough, and `[agent]` errors.

Zed.log paths:
  Windows: %LOCALAPPDATA%/Zed/logs/Zed.log
  macOS:   ~/Library/Application Support/Zed/logs/Zed.log
  Linux:   ~/.local/share/zed/logs/Zed.log
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from pet_core import Event, process_is_running

# "2026-08-08T23:51:24+08:00 INFO  [agent_client_protocol::util] connection; name=\"zed\""
_LOG_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)\s+(?P<level>\w+)\s+\[(?P<component>[^\]]+)\]"
)
_ACP_CONNECTION = re.compile(r"connection;\s*name=\"zed\"")
_ACP_STDERR = re.compile(r"agent_servers::acp|agent stderr")


class ZedSource:
    name = "zed"
    display_name = "Zed"

    def __init__(
        self,
        log_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.log_path = log_path or self._default_log_path()
        self.clock = clock
        self.offset = 0
        self.primed = False
        self.last_probe = 0.0
        self.probe_result = False

    @staticmethod
    def _default_log_path() -> Path:
        if os.name == "nt":
            return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Zed" / "logs" / "Zed.log"
        if os.path.exists(Path.home() / "Library" / "Application Support" / "Zed"):
            return Path.home() / "Library" / "Application Support" / "Zed" / "logs" / "Zed.log"
        return Path.home() / ".local" / "share" / "zed" / "logs" / "Zed.log"

    @staticmethod
    def _parse_epoch(value: str) -> float:
        # Zed.log timestamps carry the local offset ("+08:00"); parse with it.
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp()

    def read(self) -> list[Event]:
        events: list[Event] = []
        try:
            size = self.log_path.stat().st_size
        except OSError:
            self.offset = 0
            self.primed = False
            return events
        if size < self.offset:
            self.offset = 0  # log was rotated
        if not self.primed:
            self.offset = size
            self.primed = True
            return events
        if size <= self.offset:
            return events
        try:
            with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                text = handle.read()
        except OSError:
            return events
        self.offset = size
        for line in text.splitlines():
            translated = self._translate(line)
            if translated is not None:
                events.append(translated)
        return events

    def _translate(self, line: str) -> Event | None:
        match = _LOG_PREFIX.match(line)
        if match is None:
            return None
        try:
            ts = self._parse_epoch(match.group("ts"))
        except ValueError:
            return None
        component = match.group("component")
        level = match.group("level")

        if level == "ERROR" and component.lower().startswith("agent"):
            return Event("failure", ts)      
        if _ACP_CONNECTION.search(line) or _ACP_STDERR.search(component + " " + line):
            return Event("activity", ts)
        return None

    def probe_active(self) -> bool:
        now = self.clock()
        if now - self.last_probe >= 4.0:
            self.probe_result = process_is_running(
                "Get-Process zed -ErrorAction SilentlyContinue", "zed"
            )
            self.last_probe = now
        return self.probe_result

    def close(self) -> None:
        self.offset = 0
        self.primed = False
