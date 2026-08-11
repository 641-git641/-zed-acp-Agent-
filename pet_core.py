"""Decoupled core for the Ivory Lace desktop pet.

Data sources (source_*.py) translate agent activity into a small normalized
event vocabulary; this module owns the state machine and the coordinator that
decides which sources are connected. It never touches agent data directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class MonitorStatus:
    state: str
    detail: str
    observed_at: float


@dataclass(frozen=True)
class Event:
    """One normalized observation from an activity source.

    kind: activity (agent is doing work)
          final (a turn/answer completed)
          failure (an API/agent error)
          waiting (the agent asked the user something)
          resolved (the user provided input)
    ts:   epoch seconds when it happened
    """

    kind: str
    ts: float


class ActivitySource(Protocol):
    """A read-only observer of one agent's local activity."""

    name: str
    display_name: str

    def read(self) -> list[Event]:
        """Return newly observed events since the last call."""

    def probe_active(self) -> bool:
        """True when this agent appears to be running right now."""

    def close(self) -> None:
        """Release resources."""


def process_is_running(powershell_query: str, hint: str) -> bool:
    """Windows process probe via PowerShell (sees elevated processes too)."""
    import os
    import subprocess

    if os.name != "nt":
        return False
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", powershell_query],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=creation_flags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return hint in result.stdout.lower()


class PetStateMachine:
    """Single source of truth for the pet's state transitions."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        process_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.clock = clock
        self.process_probe = process_probe or (lambda: False)
        self.label = ""  # display name of the most active source
        self.last_turn_activity = 0.0
        self.last_response_started = 0.0
        self.last_response_finished = 0.0
        self.last_final = 0.0
        self.last_failure = 0.0
        self.waiting_since = 0.0
        self.waiting_resolved = 0.0
        self.last_error: str | None = None
        self.process_running = False
        self.last_process_check = 0.0
        self.last_event_source: str | None = None

    def apply(self, event: Event, source_name: str | None = None) -> None:
        ts = event.ts
        if source_name is not None:
            self.last_event_source = source_name
        if event.kind == "activity":
            self.last_response_started = max(self.last_response_started, ts)
            self.last_turn_activity = max(self.last_turn_activity, ts)
        elif event.kind == "final":
            self.last_final = max(self.last_final, ts)
            self.last_response_finished = max(self.last_response_finished, ts)
        elif event.kind == "failure":
            self.last_failure = max(self.last_failure, ts)
            self.last_response_finished = max(self.last_response_finished, ts)
        elif event.kind == "waiting":
            self.waiting_since = max(self.waiting_since, ts)
        elif event.kind == "resolved":
            self.waiting_resolved = max(self.waiting_resolved, ts)
            self.last_turn_activity = max(self.last_turn_activity, ts)

    def poll(
        self,
        connected_names: list[str] | None = None,
        active_names: set[str] | None = None,
    ) -> MonitorStatus:
        now = self.clock()
        if now - self.last_process_check >= 4.0:
            self.process_running = self.process_probe()
            self.last_process_check = now

        connected = bool(connected_names)
        active = active_names or set()
        # A pending response or question only counts while the source that
        # produced it is still active; a dead agent would otherwise leave the
        # pet "working" or "waiting" forever.
        source_alive = self.last_event_source in active
        waiting_active = self.waiting_since > self.waiting_resolved and source_alive
        response_active = (
            self.last_response_started > self.last_response_finished and source_alive
        )
        turn_is_fresh = now - self.last_turn_activity <= 4.0
        label = self.label or (connected_names[0] if connected_names else "")

        if now - self.last_failure <= 20.0:
            return MonitorStatus("failed", f"{label} 运行失败", now)
        if waiting_active:
            return MonitorStatus("waiting", f"{label} 正在等待你的输入或回答", now)
        if now - self.last_final <= 2.2:
            return MonitorStatus("jumping", f"{label} 已完成", now)
        if response_active or turn_is_fresh:
            return MonitorStatus("running", f"{label} 正在工作", now)
        if now - self.last_final <= 45.0:
            return MonitorStatus("review", f"{label} 已完成，等待查看", now)
        if self.last_error:
            return MonitorStatus("idle", "只读事件源暂时不可用", now)
        if connected:
            return MonitorStatus("idle", f"{'、'.join(connected_names)} 已连接", now)
        return MonitorStatus("idle", "未检测到 Zed", now)


class PetCoordinator:
    """Feeds every source into one machine and decides what is connected.

    The label shown in the status detail follows the source with the most
    recent event; otherwise the first active source. Zed itself counts as a
    connection even when its agent is idle.
    """

    def __init__(
        self,
        sources: list[ActivitySource],
        clock: Callable[[], float] = time.time,
        process_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.sources = sources
        self.machine = PetStateMachine(clock=clock, process_probe=process_probe)
        self.clock = clock

    def poll(self) -> MonitorStatus:
        newest_by_source: dict[str, float] = {}
        for source in self.sources:
            try:
                events = source.read()
            except (OSError, ValueError) as exc:
                self.machine.last_error = str(exc)
                continue
            for event in events:
                self.machine.apply(event, source.name)
                newest_by_source[source.name] = max(
                    newest_by_source.get(source.name, 0.0), event.ts
                )
        self.machine.last_error = None

        connected_names: list[str] = []
        active_names: set[str] = set()
        for source in self.sources:
            try:
                if source.probe_active():
                    connected_names.append(source.display_name)
                    active_names.add(source.name)
            except (OSError, ValueError):
                continue

        now = self.clock()
        label_source = max(
            newest_by_source,
            key=lambda name: newest_by_source[name],
            default=None,
        )
        if label_source is not None and now - newest_by_source[label_source] <= 60.0:
            self.machine.label = next(
                (s.display_name for s in self.sources if s.name == label_source), ""
            )
        elif connected_names:
            self.machine.label = connected_names[0]
        else:
            self.machine.label = ""

        return self.machine.poll(connected_names, active_names)

    def close(self) -> None:
        for source in self.sources:
            try:
                source.close()
            except (OSError, ValueError):
                pass
