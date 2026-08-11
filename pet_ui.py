"""Ivory Lace — Zed 桌宠：透明的 Windows 桌面宠物，只读观察 Zed cli-acp 的 agent 活动。

纯 Python 标准库（tkinter），无需安装任何第三方包。帧从 assets/frames/*.png
加载（构建时已按 1.25x 缩放并色键化）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import time
import tkinter as tk
from pathlib import Path

from pet_core import MonitorStatus, PetCoordinator
from source_claude import ClaudeSource
from source_codex import CodexSource
from source_zed import ZedSource

TRANSPARENT_KEY_HEX = "#00FF00"

STATE_NAMES = {
    "idle": "休息",
    "running-right": "向右散步",
    "running-left": "向左散步",
    "waving": "打招呼",
    "jumping": "完成庆祝",
    "failed": "任务失败",
    "waiting": "等待输入",
    "running": "工作中",
    "review": "完成待查看",
}

ANIMATION_TIMING = {
    "running-right": (220, 300),
    "running-left": (220, 300),
    "waving": (280, 900),
    "jumping": (260, 450),
    "failed": (340, 1200),
    "waiting": (380, 1300),
    "running": (320, 650),
    "review": (400, 1500),
}

IDLE_INITIAL_DELAY_RANGE = (3.0, 5.0)
IDLE_REPEAT_DELAY_RANGE = (5.0, 8.0)
IDLE_FRAME_MS = 230
IDLE_POLL_MS = 250


def enable_windows_dpi_awareness() -> None:
    """Prevent Windows from bitmap-scaling the color-keyed pet window."""
    if os.name != "nt":
        return
    try:
        per_monitor_v2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(per_monitor_v2):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def load_frames(frames_dir: Path) -> dict[str, list[tk.PhotoImage]]:
    """Load pre-cut PNG frames with plain tk.PhotoImage (no Pillow)."""
    result: dict[str, list[tk.PhotoImage]] = {}
    for state in STATE_NAMES:
        frames: list[tk.PhotoImage] = []
        index = 0
        while True:
            candidate = frames_dir / f"{state}-{index}.png"
            if not candidate.is_file():
                break
            frames.append(tk.PhotoImage(file=str(candidate)))
            index += 1
        if frames:
            result[state] = frames
    return result


class IvoryLacePet:
    def __init__(
        self,
        root: tk.Tk,
        frames_dir: Path,
        claude_home: Path | None,
        codex_home: Path | None,
        zed_log: Path | None,
        debug: bool,
    ) -> None:
        self.root = root
        self.debug = debug
        self.settings_path = Path(__file__).with_name("pet-settings.json")
        self.settings = self._load_settings()
        self.frames = load_frames(frames_dir)
        self.width = self.frames["idle"][0].width()
        self.height = self.frames["idle"][0].height()

        self.coordinator = PetCoordinator(
            [
                ClaudeSource(claude_home=claude_home),
                CodexSource(codex_home=codex_home),
                ZedSource(log_path=zed_log),
            ]
        )
        self.monitor_status = MonitorStatus("idle", "正在连接", 0.0)
        self.current_state = "waving"
        self.manual_state: str | None = "waving"
        self.frame_index = 0
        self.paused = False
        self.dragging = False
        self.drag_origin = (0, 0)
        self.window_origin = (0, 0)
        self.idle_motion_step: int | None = None
        self.next_idle_motion_at = time.monotonic() + random.uniform(
            *IDLE_INITIAL_DELAY_RANGE
        )
        self.loop_pause_until = 0.0

        self._configure_window()
        self._create_menu()
        self._bind_events()
        self._restore_position()
        self.root.after(1800, self._end_greeting)
        self.root.after(80, self._animate)
        self.root.after(200, self._poll_monitor)

    def _configure_window(self) -> None:
        self.root.title("Ivory Lace — Zed 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_KEY_HEX)
        if os.name == "nt":
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY_HEX)
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=TRANSPARENT_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack()
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")

    def _create_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=False)
        self.status_menu_index = 0
        self.menu.add_command(label="状态：正在连接", state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="暂停/继续监听", command=self._toggle_pause)
        test_menu = tk.Menu(self.menu, tearoff=False)
        for state in STATE_NAMES:
            test_menu.add_command(
                label=STATE_NAMES[state],
                command=lambda selected=state: self._test_state(selected),
            )
        test_menu.add_separator()
        test_menu.add_command(label="恢复自动", command=self._clear_manual_state)
        self.menu.add_cascade(label="测试动作", menu=test_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.close)

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._stop_drag)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.canvas.bind("<Double-Button-1>", lambda _event: self._test_state("waving"))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        data = {"x": self.root.winfo_x(), "y": self.root.winfo_y()}
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)

    def _restore_position(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        default_x = max(0, screen_w - self.width - 36)
        default_y = max(0, screen_h - self.height - 84)
        x = int(self.settings.get("x", default_x))
        y = int(self.settings.get("y", default_y))
        x = min(max(0, x), max(0, screen_w - self.width))
        y = min(max(0, y), max(0, screen_h - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _effective_state(self) -> str:
        if self.manual_state:
            return self.manual_state
        if self.dragging:
            return "jumping"
        return self.monitor_status.state

    def _animate(self) -> None:
        state = self._effective_state()
        if state != self.current_state:
            self.current_state = state
            self.frame_index = 0
            self.idle_motion_step = None
            self.next_idle_motion_at = time.monotonic() + random.uniform(
                *IDLE_INITIAL_DELAY_RANGE
            )
            self.loop_pause_until = 0.0
            if self.debug:
                print(f"state={state} detail={self.monitor_status.detail}", flush=True)
        frames = self.frames[state]
        if state == "idle":
            now = time.monotonic()
            if self.idle_motion_step is None and now >= self.next_idle_motion_at:
                self.idle_motion_step = 0
            if self.idle_motion_step is None:
                image = frames[0]
                interval = IDLE_POLL_MS
            else:
                idle_pattern = (0, 1, 2, 3, 4, 5, 0)
                image = frames[idle_pattern[self.idle_motion_step]]
                self.idle_motion_step += 1
                interval = IDLE_FRAME_MS
                if self.idle_motion_step >= len(idle_pattern):
                    self.idle_motion_step = None
                    self.next_idle_motion_at = now + random.uniform(
                        *IDLE_REPEAT_DELAY_RANGE
                    )
        else:
            now = time.monotonic()
            frame_ms, loop_pause_ms = ANIMATION_TIMING[state]
            if now < self.loop_pause_until:
                image = frames[0]
                interval = min(300, max(80, int((self.loop_pause_until - now) * 1000)))
            else:
                image = frames[self.frame_index]
                self.frame_index += 1
                interval = frame_ms
                if self.frame_index >= len(frames):
                    self.frame_index = 0
                    self.loop_pause_until = now + loop_pause_ms / 1000
        self.canvas.itemconfigure(self.image_item, image=image)
        self.canvas.image = image
        self.root.after(interval, self._animate)

    def _poll_monitor(self) -> None:
        if not self.paused:
            self.monitor_status = self.coordinator.poll()
        label = f"状态：{STATE_NAMES[self._effective_state()]}"
        if self.paused:
            label += "（已暂停）"
        self.menu.entryconfigure(self.status_menu_index, label=label)
        self.root.title(f"Ivory Lace — {self.monitor_status.detail}")
        self.root.after(650, self._poll_monitor)

    def _start_drag(self, event: tk.Event) -> None:
        self.dragging = True
        self.drag_origin = (event.x_root, event.y_root)
        self.window_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        dx = event.x_root - self.drag_origin[0]
        dy = event.y_root - self.drag_origin[1]
        self.root.geometry(f"+{self.window_origin[0] + dx}+{self.window_origin[1] + dy}")

    def _stop_drag(self, _event: tk.Event) -> None:
        self.dragging = False

    def _show_menu(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _test_state(self, state: str) -> None:
        self.manual_state = state
        self.root.after(5000, self._clear_manual_state)

    def _clear_manual_state(self) -> None:
        self.manual_state = None

    def _end_greeting(self) -> None:
        if self.manual_state == "waving":
            self.manual_state = None

    def _toggle_pause(self) -> None:
        self.paused = not self.paused

    def close(self) -> None:
        self._save_settings()
        self.coordinator.close()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Ivory Lace read-only desktop pet for Zed cli-acp users"
    )
    parser.add_argument("--frames", type=Path, default=directory / "assets" / "frames")
    parser.add_argument("--claude-home", type=Path, default=None)
    parser.add_argument("--codex-home", type=Path, default=None)
    parser.add_argument("--zed-log", type=Path, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enable_windows_dpi_awareness()
    root = tk.Tk()
    pet = IvoryLacePet(
        root, args.frames, args.claude_home, args.codex_home, args.zed_log, args.debug
    )
    if args.smoke_test:
        def finish_smoke_test() -> None:
            print(
                f"ui_smoke=ok geometry={root.winfo_geometry()} state={pet._effective_state()}",
                flush=True,
            )
            pet.close()

        root.after(1200, finish_smoke_test)
    root.mainloop()


if __name__ == "__main__":
    main()
