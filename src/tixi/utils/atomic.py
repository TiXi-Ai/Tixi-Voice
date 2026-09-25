"""Atomic file helpers.

Model downloads, settings writes, audio renders and database snapshots must
never leave a half-written file behind if the process dies or the user pulls
the plug.  Everything in the application therefore writes to a temporary file
in the *same* directory and then atomically replaces the target.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file is missing or corrupt."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return default


@contextlib.contextmanager
def atomic_file(path: Path, mode: str = "wb", encoding: str | None = None) -> Iterator[BinaryIO]:
    """Context manager yielding a temp file that is renamed onto ``path``.

    Usage::

        with atomic_file(target) as handle:
            handle.write(chunk)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = "b" in mode
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=str(path.parent))
    tmp = Path(tmp_name)
    handle = os.fdopen(fd, mode, encoding=None if binary else (encoding or "utf-8"))
    try:
        yield handle  # type: ignore[misc]
        handle.flush()
        if binary:
            os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(Exception):
            handle.close()
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def unique_path(path: Path, *, max_attempts: int = 10_000) -> Path:
    """Never overwrite: return ``name (2).ext`` style alternatives when needed."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for index in range(2, max_attempts + 1):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free filename for {path.name}")


def ensure_free_space(path: Path, required_bytes: int) -> None:
    """Raise ``OSError`` with an actionable message when disk space is low."""
    if required_bytes <= 0:
        return
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:  # pragma: no cover
        return
    # Keep a 200 MB safety margin so the OS itself stays healthy.
    if usage.free < required_bytes + 200 * 1024 * 1024:
        free_gb = usage.free / (1024**3)
        need_gb = (required_bytes + 200 * 1024 * 1024) / (1024**3)
        raise OSError(
            f"Not enough free disk space in {probe}: {free_gb:.1f} GB available, "
            f"about {need_gb:.1f} GB required."
        )


def directory_size(path: Path) -> int:
    """Total size in bytes of everything below ``path`` (0 when missing)."""
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def safe_rmtree(path: Path) -> bool:
    """Delete a directory tree, ignoring permission errors."""
    if not path.exists():
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False
