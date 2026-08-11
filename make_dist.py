"""Package the pet for sharing — WITHOUT any pet artwork or personal files.

Everything the recipient needs to build their own pet is included: the code,
the launchers, the docs, the atlas builder and a layout example. Excluded:
assets/ (your sprite sheet and generated frames), pet-settings.json (window
position), __pycache__ and build artifacts.

Usage:  python make_dist.py [output.zip]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {
    "__pycache__",
    "assets",
}
EXCLUDE_FILES = {
    "pet-settings.json",
}
INCLUDE_SUFFIXES = {".py", ".md", ".cmd", ".json", ".gitignore"}


def main() -> int:
    root = Path(__file__).parent
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "ivory-lace-zed-pet-dist.zip"
    files = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not any(part in EXCLUDE_DIRS for part in p.relative_to(root).parts)
        and p.name not in EXCLUDE_FILES
        and (p.suffix in INCLUDE_SUFFIXES or p.name == ".gitignore")
    ]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root))
    print(f"wrote {len(files)} files -> {out}")
    for path in files:
        print("  ", path.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
