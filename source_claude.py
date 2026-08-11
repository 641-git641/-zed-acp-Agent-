"""Claude Code activity source: read-only tail of session transcripts.

Transcripts live at <claude-home>/projects/<encoded-cwd>/<session>.jsonl and
are appended live while a session runs. Only event metadata is extracted:
event type, timestamp, stop_reason, content block types, and tool names.
Prompt text, model output, and tool content never leave the files.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from pet_core import Event, process_is_running

# Tool names that suspend the agent until the user answers, e.g. a question
# or a permission prompt surfaced through the tool protocol.
WAITING_TOOL_NAMES = {
    "AskUserQuestion",
}


class ClaudeSource:
    name = "claude"
    display_name = "Claude"

    def __init__(
        self,
        claude_home: Path | None = None,
        projects_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        home = claude_home or Path(
            os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        )
        self.projects_dir = projects_dir or home / "projects"
        self.clock = clock
        self.offsets: dict[Path, int] = {}
        self.last_probe = 0.0
        self.probe_result = False

    @staticmethod
    def _parse_timestamp(value: object) -> float | None:
        """Parse an ISO-8601 transcript timestamp into epoch seconds."""
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.timestamp()

    def read(self) -> list[Event]:
        events: list[Event] = []
        try:
            current_files = sorted(self.projects_dir.glob("*/*.jsonl"))
        except OSError:
            return events
        current_paths = set(current_files)
        for path in current_files:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            offset = self.offsets.get(path)
            if offset is None:
                self.offsets[path] = size  # prime to end; no history replay
                continue
            if size <= offset:
                continue
            position = offset
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    for raw in handle:
                        position += len(raw)
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            break  # partial trailing line; next poll retries
                        events.extend(self._translate(event))
            except OSError:
                continue
            self.offsets[path] = position
        for path in list(self.offsets):
            if path not in current_paths:
                del self.offsets[path]
        return events

    def _translate(self, event: dict) -> list[Event]:
        timestamp = self._parse_timestamp(event.get("timestamp"))
        if timestamp is None:
            return []
        event_type = event.get("type")

        if event_type == "assistant":
            message = event.get("message") or {}
            content = message.get("content")
            blocks = (
                [c for c in content if isinstance(c, dict)]
                if isinstance(content, list)
                else []
            )
            tool_names = {
                c.get("name") for c in blocks if c.get("type") == "tool_use"
            }
            result = [Event("activity", timestamp)]
            stop_reason = message.get("stop_reason")
            if stop_reason in {"end_turn", "stop_sequence"} and not any(
                c.get("type") == "tool_use" for c in blocks
            ):
                result.append(Event("final", timestamp))
            if tool_names & WAITING_TOOL_NAMES:
                result.append(Event("waiting", timestamp))
            return result

        if event_type == "user":
            message = event.get("message") or {}
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "text" for c in content
            ):
                return [Event("resolved", timestamp)]
            return []

        if event_type == "queue-operation" and event.get("operation") == "enqueue":
            return [Event("activity", timestamp), Event("resolved", timestamp)]

        if event_type == "system" and event.get("subtype") == "api_error":
            return [Event("failure", timestamp)]
        return []

    def probe_active(self) -> bool:
        now = self.clock()
        if now - self.last_probe >= 4.0:
            running = process_is_running(
                "Get-Process claude -ErrorAction SilentlyContinue", "claude"
            )
            self.probe_result = running or self._transcripts_active(now)
            self.last_probe = now
        return self.probe_result

    def _transcripts_active(self, now: float) -> bool:
        """True when any transcript was written within the last 30 seconds."""
        try:
            files = list(self.projects_dir.glob("*/*.jsonl"))
        except OSError:
            return False
        for path in files:
            try:
                if now - path.stat().st_mtime <= 30.0:
                    return True
            except OSError:
                continue
        return False

    def close(self) -> None:
        self.offsets.clear()
