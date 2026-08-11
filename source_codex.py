"""Codex activity source: read-only observer of Codex's local event log.

The SQL query extracts only event metadata (names, statuses, item types,
roles, phases). Prompt text, model output, command arguments, and tool
output never leave SQLite.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from pet_core import Event, process_is_running

WAITING_EVENTS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}

WAITING_TOOL_NAMES = {
    "request_user_input",
    "request_permissions",
}

_SELECT_SQL = """
SELECT
    id,
    ts,
    CASE
        WHEN target = 'codex_app_server::outgoing_message' THEN 'app'
        WHEN target = 'codex_api::sse::responses' THEN 'sse'
        WHEN target = 'codex_core::session::turn' THEN 'turn_activity'
    END AS category,
    CASE
        WHEN target = 'codex_app_server::outgoing_message'
        THEN replace(
            substr(
                feedback_log_body,
                19,
                instr(feedback_log_body || ' targeted_connections=', ' targeted_connections=') - 19
            ),
            'app-server event: ',
            ''
        )
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.type')
    END AS event_type,
    CASE
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.item.type')
    END AS item_type,
    CASE
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.item.name')
    END AS item_name,
    CASE
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.item.role')
    END AS item_role,
    CASE
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.item.phase')
    END AS item_phase,
    CASE
        WHEN target = 'codex_api::sse::responses'
             AND feedback_log_body LIKE 'SSE event: {%'
             AND json_valid(substr(feedback_log_body, 12))
        THEN json_extract(substr(feedback_log_body, 12), '$.response.status')
    END AS response_status
FROM logs
WHERE id > ?
  AND target IN (
      'codex_app_server::outgoing_message',
      'codex_api::sse::responses',
      'codex_core::session::turn'
  )
ORDER BY id
LIMIT 2500
"""


class CodexSource:
    name = "codex"
    display_name = "Codex"

    def __init__(
        self,
        codex_home: Path | None = None,
        db_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        home = codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.db_path = db_path or home / "logs_2.sqlite"
        self.clock = clock
        self.connection: sqlite3.Connection | None = None
        self.last_id = 0
        self.last_probe = 0.0
        self.probe_result = False

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connect(self) -> sqlite3.Connection:
        if self.connection is not None:
            return self.connection
        absolute = self.db_path.resolve().as_posix()
        uri = f"file:{quote(absolute, safe='/:')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.15)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=150")
        now = int(self.clock())
        row = connection.execute(
            "SELECT COALESCE(MIN(id), (SELECT COALESCE(MAX(id), 0) + 1 FROM logs)) "
            "FROM logs WHERE ts >= ?",
            (now - 20,),
        ).fetchone()
        self.last_id = max(0, int(row[0]) - 1)
        self.connection = connection
        return connection

    def read(self) -> list[Event]:
        events: list[Event] = []
        rows = self._connect().execute(_SELECT_SQL, (self.last_id,)).fetchall()
        if not rows:
            return events
        self.last_id = int(rows[-1][0])
        for row in rows:
            events.extend(self._translate(row))
        return events

    def _translate(self, row: tuple) -> list[Event]:
        _, timestamp, category, event_type, item_type, item_name, role, phase, response_status = row
        timestamp = float(timestamp)

        if category == "turn_activity":
            return [Event("activity", timestamp)]

        if category == "app":
            if event_type in WAITING_EVENTS:
                return [Event("waiting", timestamp)]
            if event_type == "serverRequest/resolved":
                return [Event("resolved", timestamp)]
            if event_type in {"turn/started", "item/started"}:
                return [Event("activity", timestamp)]
            if event_type == "turn/completed":
                return [Event("final", timestamp)]
            return []

        if category != "sse":
            return []

        if event_type in {"response.created", "response.in_progress"}:
            return [Event("activity", timestamp)]
        if event_type in {"response.failed", "response.incomplete"} or response_status in {
            "failed",
            "incomplete",
        }:
            return [Event("failure", timestamp)]
        if event_type == "response.output_item.done":
            result: list[Event] = []
            if item_name in WAITING_TOOL_NAMES:
                result.append(Event("waiting", timestamp))
            if item_type == "message" and role == "assistant" and phase == "final_answer":
                result.append(Event("final", timestamp))
                result.append(Event("resolved", timestamp))
            return result
        return []

    def probe_active(self) -> bool:
        now = self.clock()
        if now - self.last_probe >= 4.0:
            running = process_is_running(
                "Get-Process codex -ErrorAction SilentlyContinue", "codex"
            )
            self.probe_result = running or self._db_active(now)
            self.last_probe = now
        return self.probe_result

    def _db_active(self, now: float) -> bool:
        try:
            return now - self.db_path.stat().st_mtime <= 30.0
        except OSError:
            return False
