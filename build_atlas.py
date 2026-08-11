"""Atlas builder: turn ANY sprite sheet into the pet's pre-cut PNG frames.

The pet itself ships with no artwork. Build frames from your own sprite sheet
(or generate a procedural placeholder) once — Pillow is needed only for this
step, never to run the pet.

Usage:
  python build_atlas.py <sprite-sheet.png|webp> [--layout layout.json] [--scale 1.25]
  python build_atlas.py --placeholder [--layout layout.json] [--scale 1.25]

Layout JSON (optional; defaults to a 9-row 192x208 sheet):
  {
    "cell_width": 192,
    "cell_height": 208,
    "rows": {
      "idle":          [0, 6],   // state -> [atlas row, frame count]
      "running-right": [1, 8],
      ...
    }
  }

The state names must match pet_ui.py's STATE_NAMES. Frames are cut with a
strict binary alpha mask (alpha >= 248 stays) and color-keyed to #00FF00.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

TRANSPARENT_KEY = (0, 255, 0)
ALPHA_CUTOFF = 248

# Default 9-row layout (state -> (atlas row, frame count)).
DEFAULT_LAYOUT = {
    "idle": (0, 6),
    "running-right": (1, 8),
    "running-left": (2, 8),
    "waving": (3, 4),
    "jumping": (4, 5),
    "failed": (5, 8),
    "waiting": (6, 6),
    "running": (7, 6),
    "review": (8, 6),
}
DEFAULT_CELL = (192, 208)


def load_layout(path: Path | None) -> tuple[dict, tuple[int, int]]:
    if path is None:
        return dict(DEFAULT_LAYOUT), DEFAULT_CELL
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = {str(k): tuple(v) for k, v in data["rows"].items()}
    cell = (int(data.get("cell_width", DEFAULT_CELL[0])),
            int(data.get("cell_height", DEFAULT_CELL[1])))
    return rows, cell


def prepare_frame(frame: Image.Image, scale: float) -> Image.Image:
    """Strict binary color-key frame: alpha >= cutoff stays, rest becomes key."""
    rgba = frame.convert("RGBA")
    if scale != 1.0:
        target = (round(rgba.width * scale), round(rgba.height * scale))
        rgba = rgba.resize(target, Image.Resampling.NEAREST)
    alpha = rgba.getchannel("A")
    binary_mask = alpha.point([0] * ALPHA_CUTOFF + [255] * (256 - ALPHA_CUTOFF))
    output = Image.new("RGB", rgba.size, TRANSPARENT_KEY)
    output.paste(rgba.convert("RGB"), (0, 0), binary_mask)
    return output


def cut_frames(atlas: Image.Image, rows: dict, cell: tuple[int, int],
               scale: float, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for state, (row, frame_count) in rows.items():
        for column in range(frame_count):
            left = column * cell[0]
            top = row * cell[1]
            frame = atlas.crop((left, top, left + cell[0], top + cell[1]))
            prepared = prepare_frame(frame, scale)
            prepared.save(out_dir / f"{state}-{column}.png", "PNG", optimize=True)
            count += 1
    return count


def draw_placeholder(state: str, index: int, size: tuple[int, int]) -> Image.Image:
    """Draw one procedural placeholder frame (a friendly blob pet).

    The placeholder is deliberately simple: a rounded body, eyes, and a few
    per-state variations (bounce, expression, props). Swap in real artwork by
    running the builder on your own sprite sheet instead.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    body_color = (255, 182, 193, 255)   # soft pink
    eye_color = (90, 60, 70, 255)
    mouth_color = (180, 90, 100, 255)
    body_w, body_h = int(width * 0.62), int(height * 0.5)
    cycles = {
        "idle": 6, "running-right": 8, "running-left": 8, "waving": 4,
        "jumping": 5, "failed": 8, "waiting": 6, "running": 6, "review": 6,
    }
    total = cycles.get(state, 6)
    phase = index / max(1, total - 1)

    # Vertical bounce: movement states hover, idle breathes slightly.
    if state in {"running", "running-right", "running-left", "waving", "jumping"}:
        bounce = -int(height * 0.08 * abs(math.sin(math.pi * phase)))
    elif state == "jumping":
        bounce = -int(height * 0.18 * math.sin(math.pi * phase))
    elif state in {"failed", "waiting", "review"}:
        bounce = -int(height * 0.02 * math.sin(2 * math.pi * phase))
    else:
        bounce = -int(height * 0.012 * math.sin(2 * math.pi * phase))

    cx = width // 2 + (int(width * 0.06) if state == "running-left" else 0)
    cy = height - int(height * 0.28) + bounce
    left, top = cx - body_w // 2, cy - body_h // 2
    right, bottom = cx + body_w // 2, cy + body_h // 2
    draw.ellipse((left, top, right, bottom), fill=body_color)

    # Face.
    eye_dx = int(body_w * 0.14)
    eye_y = cy - int(body_h * 0.10)
    blink = (state == "idle" and index % 3 == 2)
    eyes_open = not blink and state != "failed"
    if eyes_open:
        for side in (-1, 1):
            ex = cx + side * eye_dx
            draw.ellipse((ex - 7, eye_y - 9, ex + 7, eye_y + 9), fill=eye_color)
    else:
        for side in (-1, 1):
            ex = cx + side * eye_dx
            draw.line((ex - 6, eye_y, ex + 6, eye_y), fill=eye_color, width=3)

    # Mouth / expression.
    if state == "failed":
        draw.arc((cx - 12, eye_y + 10, cx + 12, eye_y + 30), 200, 340,
                 fill=mouth_color, width=3)
        tear_x = cx - eye_dx
        draw.ellipse((tear_x - 4, eye_y + 10, tear_x + 4, eye_y + 20), fill=(130, 190, 255, 255))
    elif state in {"waiting", "review"}:
        draw.arc((cx - 10, eye_y + 8, cx + 10, eye_y + 24), 20, 160,
                 fill=mouth_color, width=3)
    else:
        draw.arc((cx - 10, eye_y + 8, cx + 10, eye_y + 26), 20, 160,
                 fill=mouth_color, width=3)

    # Props.
    if state == "waving":
        arm_y = cy - int(body_h * 0.28)
        draw.ellipse((cx + body_w // 2 - 6, arm_y - 22, cx + body_w // 2 + 14, arm_y),
                     fill=body_color)
    elif state == "waiting":
        draw.ellipse((cx - body_w // 2 - 12, cy - body_h // 2 - 6,
                      cx - body_w // 2 + 8, cy - body_h // 2 + 14), fill=(255, 105, 180, 255))
    elif state == "review":
        draw.text((cx + body_w // 2 - 10, cy - body_h // 2 - 22), "?",
                  fill=(255, 255, 255, 255))
    elif state == "jumping":
        draw.ellipse((cx - body_w // 2 - 8, cy - body_h // 2 - 12,
                      cx - body_w // 2 + 12, cy - body_h // 2 + 8), fill=(255, 220, 90, 255))
    return image


def placeholder_atlas(rows: dict, cell: tuple[int, int]) -> Image.Image:
    cols = max(count for _, count in rows.values())
    atlas = Image.new("RGBA", (cols * cell[0], len(rows) * cell[1]), (0, 0, 0, 0))
    for state, (row, frame_count) in rows.items():
        for column in range(frame_count):
            frame = draw_placeholder(state, column, cell)
            atlas.paste(frame, (column * cell[0], row * cell[1]))
    return atlas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build pet frames from any sprite sheet (or a placeholder)"
    )
    parser.add_argument("atlas", nargs="?", type=Path, default=None,
                        help="path to your sprite sheet (png/webp/...); omit with --placeholder")
    parser.add_argument("--placeholder", action="store_true",
                        help="generate a procedural placeholder pet instead of using artwork")
    parser.add_argument("--layout", type=Path, default=None,
                        help="layout JSON (cell size + rows mapping)")
    parser.add_argument("--scale", type=float, default=1.25)
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: assets/frames)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, cell = load_layout(args.layout)
    out_dir = args.out or (Path(__file__).parent / "assets" / "frames")

    if args.placeholder:
        atlas = placeholder_atlas(rows, cell)
        source_name = "placeholder"
    else:
        if args.atlas is None:
            print("error: provide a sprite sheet path, or use --placeholder", file=sys.stderr)
            return 1
        with Image.open(args.atlas) as source:
            atlas = source.convert("RGBA")
        source_name = args.atlas.name

    expected = (max(c for _, c in rows.values()) * cell[0], len(rows) * cell[1])
    if atlas.width < expected[0] or atlas.height < expected[1]:
        print(
            f"error: atlas {atlas.width}x{atlas.height} smaller than layout "
            f"needs {expected[0]}x{expected[1]}",
            file=sys.stderr,
        )
        return 1

    count = cut_frames(atlas, rows, cell, args.scale, out_dir)
    print(f"built {count} frames from {source_name} -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
