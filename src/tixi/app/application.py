"""Application entry point.

``tixi-voice`` (or ``python -m tixi``) starts here:

1. parse command-line switches,
2. install a crash handler and the rotating log,
3. make sure only one instance runs (a second launch focuses the first window),
4. build the object graph (see :mod:`tixi.app.bootstrap`),
5. apply the theme, create the main window and enter the Qt event loop.

Every failure that happens before the window exists is written to the log and,
when possible, shown in a message box that explains what to try next.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .config import APP_VERSION
from .logging_config import get_logger, install_excepthook, setup_logging
from .paths import APP_NAME, IS_FROZEN, IS_WINDOWS, paths

log = get_logger("tixi.app")

SINGLE_INSTANCE_NAME = "TixiVoice-SingleInstance-8F4A"
_MUTEX_HANDLE: Any = None
_WINDOW_HANDLE: Any = None


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tixi-voice",
        description="Tixi Voice — offline speech tools with Persian-first text processing.",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--safe-mode", action="store_true", help="start without loading engines or themes from settings")
    parser.add_argument("--offline", action="store_true", help="force Offline Mode for this run")
    parser.add_argument("--no-tray", action="store_true", help="do not create the system-tray icon")
    parser.add_argument("--reset-settings", action="store_true", help="restore the default settings and exit")
    parser.add_argument("--diagnostics", action="store_true", help="print a diagnostics report and exit")
    parser.add_argument("--log-level", default="", help="DEBUG, INFO, WARNING or ERROR")
    parser.add_argument(
        "--open",
        default="",
        choices=["", "tts", "stt", "voice_typing", "models", "library", "history", "settings", "updates", "about"],
        help="open a specific page",
    )
    return parser


# ---------------------------------------------------------------------------
# single instance (Windows named mutex, file lock elsewhere)
# ---------------------------------------------------------------------------
def _acquire_single_instance() -> bool:
    """``True`` when this process is the only instance."""
    global _MUTEX_HANDLE, _WINDOW_HANDLE
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_NAME)
        if not _MUTEX_HANDLE:
            return True
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            existing = user32.FindWindowW(None, f"{APP_NAME} {APP_VERSION}")
            if existing:
                user32.ShowWindow(existing, 9)  # SW_RESTORE
                user32.SetForegroundWindow(existing)
            _WINDOW_HANDLE = existing
            return False
        return True
    lock = paths().lock_file
    try:
        handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # A stale lock from a crashed run must not block forever.
        try:
            with lock.open("r", encoding="utf-8") as stream:
                pid = int(stream.read().strip() or 0)
            if pid and _pid_alive(pid):
                return False
            lock.unlink(missing_ok=True)
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except Exception:  # noqa: BLE001
            return True
    except OSError:
        return True
    os.write(handle, str(os.getpid()).encode("ascii"))
    os.close(handle)
    return True


def _release_single_instance() -> None:
    if IS_WINDOWS:
        return
    try:
        paths().lock_file.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# early modes
# ---------------------------------------------------------------------------
def _print_diagnostics() -> int:
    from .bootstrap import build_context, describe_environment

    print(f"{APP_NAME} {APP_VERSION}")
    print("-" * 40)
    environment = describe_environment()
    print(f"Python       : {environment['python']} ({environment['platform']})")
    print(f"Frozen build : {environment['frozen']}")
    for name, version in environment["packages"].items():
        print(f"  {name:<14}: {version or 'not installed'}")
    print(f"Data folder  : {paths().root}")
    try:
        context = build_context(offline=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\nThe application context could not be built: {type(exc).__name__}: {exc}")
        return 1
    summary = context.models.summary()
    print(f"Models       : {summary['installed_count']} installed ({summary['installed_label']})")
    print(f"Engine packs : {summary['packs_installed']} of {summary['packs_total']}")
    print(f"Diacritizer  : {context.diacritization.describe()}")
    for note in context.notes:
        print(f"Note         : {note}")
    context.shutdown()
    return 0


def _reset_settings() -> int:
    from ..storage.database import Database
    from ..storage.settings_repository import SettingsRepository

    location = paths().ensure()
    database = Database(location.database_file)
    database.initialise()
    repository = SettingsRepository(database, mirror=location.settings_file)
    repository.reset()
    database.close()
    print("Settings were reset to their defaults. History, models and the audio library are untouched.")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    location = paths().ensure()
    setup_logging(
        level=args.log_level or "INFO",
        log_file=location.log_file,
        console=not IS_FROZEN,
    )
    install_excepthook(location.crash_file)
    log.info(
        "starting",
        extra={"event": "startup", "version": APP_VERSION, "frozen": IS_FROZEN, "windows": IS_WINDOWS},
    )

    if args.diagnostics:
        return _print_diagnostics()
    if args.reset_settings:
        return _reset_settings()

    if not _acquire_single_instance():
        log.info("another instance is already running", extra={"event": "second_instance"})
        print(f"{APP_NAME} is already running — the existing window was brought to the front.")
        return 0

    try:
        return _run_gui(args)
    except Exception as exc:  # noqa: BLE001 - last line of defence
        log.exception("the application failed to start", extra={"event": "startup_failed"})
        _show_fatal_error(exc)
        return 2
    finally:
        _release_single_instance()


def _run_gui(args: argparse.Namespace) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..app.bootstrap import build_context
    from ..ui.main_window import MainWindow
    from ..ui.theme.manager import ThemeManager

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    application = QApplication([sys.argv[0]])
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName("TiXi-Ai")
    application.setQuitOnLastWindowClosed(False)

    try:
        context = build_context(offline=bool(args.offline))
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(
            None,
            f"{APP_NAME} could not start",
            "The application data could not be initialised.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"Log file: {paths().log_file}\n"
            "Troubleshooting: docs/TROUBLESHOOTING.md",
        )
        raise

    if args.safe_mode:
        from ..app.config import Settings

        context.settings_store.replace(Settings())

    appearance = context.settings.appearance
    theme = ThemeManager(
        application,
        mode=str(appearance.theme_mode),
        accent_id=str(appearance.accent),
        custom_accent=str(getattr(appearance, "custom_accent", "#0A84FF")),
        radius=int(appearance.corner_radius),
        density=str(appearance.density),
        scale=float(appearance.ui_scale),
        animations=bool(appearance.animations),
        animation_speed=float(getattr(appearance, "animation_speed", 1.0) or 1.0),
        reduce_transparency=bool(appearance.reduce_transparency),
        persian_font=str(getattr(appearance, "persian_font", "") or ""),
        on_change=_persist_appearance(context),
    )
    theme.apply()

    window = MainWindow(context, theme)
    if args.no_tray:
        window.tray.hide()
    application.aboutToQuit.connect(context.shutdown)
    application.aboutToQuit.connect(window._save_window_state)  # noqa: SLF001 - deliberate
    window.closing.connect(_release_single_instance)
    window.show()

    if args.open:
        mapping = {"tts": "text_to_speech", "stt": "speech_to_text"}
        window.goto(mapping.get(args.open, args.open))

    if context.notes:
        log.info("startup notes", extra={"event": "startup_notes", "count": len(context.notes)})
    return application.exec()


def _persist_appearance(context: Any):
    """Write appearance changes back into the settings store."""

    def on_change(changes: dict[str, Any]) -> None:
        if not changes:
            return
        mapping = {
            "theme": "appearance.theme_mode",
            "accent": "appearance.accent",
            "radius": "appearance.corner_radius",
            "density": "appearance.density",
            "scale": "appearance.ui_scale",
            "animations": "appearance.animations",
            "reduce_transparency": "appearance.reduce_transparency",
        }
        values = {
            mapping[key]: value
            for key, value in changes.items()
            if key in mapping and context.settings.get(mapping[key]) != value
        }
        if values:
            context.settings_store.update(values)
            context.save_settings()

    return on_change


def _show_fatal_error(exc: BaseException) -> None:
    message = (
        f"{APP_NAME} could not start.\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"Log file: {paths().log_file}\n"
        "See docs/TROUBLESHOOTING.md for the usual causes."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        QApplication.instance() or QApplication([sys.argv[0]])
        QMessageBox.critical(None, f"{APP_NAME} could not start", message)
    except Exception:  # noqa: BLE001 - headless or Qt missing
        print(message, file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
