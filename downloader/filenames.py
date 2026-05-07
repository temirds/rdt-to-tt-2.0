from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def sanitize_filename(name: str) -> str:
    path = Path(name)
    suffix = path.suffix.lower()
    stem = re.sub(r"\s+", "_", path.stem.strip().lower())
    cleaned = "".join(char for char in stem if char.isalnum() or char == "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return f"{cleaned or 'video'}{suffix}"


def normalize_file_path(path: Path) -> Path:
    source = path.resolve()
    target_name = sanitize_filename(source.name)
    target = _unique_target(source.with_name(target_name), source)
    if target == source:
        return source
    source.rename(target)
    return target.resolve()


def normalize_file_paths(paths: tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for path in paths:
        if path.exists() and path.is_file():
            normalized.append(normalize_file_path(path))
    return tuple(dict.fromkeys(normalized))


def normalize_media_files(roots: tuple[Path, ...]) -> tuple[tuple[Path, Path], ...]:
    renamed: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        candidates = (root.rglob("*") if root.is_dir() else (root,))
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            before = path.resolve()
            after = normalize_file_path(before)
            if after != before:
                renamed.append((before, after))
    return tuple(renamed)


def _unique_target(target: Path, source: Path) -> Path:
    target = target.resolve()
    source = source.resolve()
    if target == source:
        return source
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    for index in range(1, 10000):
        candidate = target.with_name(f"{stem}_{index}{suffix}").resolve()
        if candidate == source:
            return source
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot create unique filename for {source}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Normalize downloaded media filenames.")
    parser.add_argument("paths", nargs="+", help="Files or folders to normalize.")
    args = parser.parse_args()
    renamed = normalize_media_files(tuple(Path(value).resolve() for value in args.paths))
    for before, after in renamed:
        print(f"{before} -> {after}")
    print(f"Renamed: {len(renamed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
