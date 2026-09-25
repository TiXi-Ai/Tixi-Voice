"""The main window: sidebar navigation, pages, tray and global feedback."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..app.config import APP_VERSION
from ..app.paths import APP_NAME
from ..app.logging_config import get_logger
from ..services.jobs import JobRunner
from ..utils.win_integration import (
    enable_window_blur,
    flash_window,
    notify,
    set_dark_titlebar,
)
from ..voice_typing.controller import VoiceTypingState
from .pages.about import AboutPage
from .pages.base import Page
from .pages.dashboard import DashboardPage
from .pages.history import HistoryPage
from .pages.library import LibraryPage
from .pages.models import ModelsPage
from .pages.settings import SettingsPage
from .pages.speech_to_text import SpeechToTextPage
from .pages.text_to_speech import TextToSpeechPage
from .pages.updates import UpdatesPage
from .pages.voice_typing import VoiceTypingPage
from .theme.icons import icon_pixmap, tray_icon, window_icon
from .theme.manager import ThemeManager
from .widgets.toast import BusyOverlay, ToastManager

log = get_logger("tixi.ui.window")

#: ``(page_id, class)`` in sidebar order.
PAGE_TYPES: tuple[tuple[str, type[Page]], ...] = (
    ("dashboard", DashboardPage),
    ("text_to_speech", TextToSpeechPage),
    ("speech_to_text", SpeechToTextPage),
    ("voice_typing", VoiceTypingPage),
    ("models", ModelsPage),
    ("library", LibraryPage),
    ("history", HistoryPage),
    ("settings", SettingsPage),
    ("updates", UpdatesPage),
    ("about", AboutPage),
)


class MainWindow(QMainWindow):
    """Hosts every page and keeps the tray in sync with the services."""

    closing = Signal()

    def __init__(self, context: Any, theme_manager: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.theme_manager = theme_manager
        self.pages: dict[str, Page] = {}
        self._first_show = True
        #: Connected once, the first time a busy operation needs a Cancel button.
        self._cancel_hook: Callable[[], None] | None = None

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(QSize(1120, 720))
        self.setWindowIcon(window_icon(theme_manager.tokens))

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_stack(), 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.status.setSizeGripEnabled(True)
        self._status_label = QLabel("Ready")
        self.status.addWidget(self._status_label, 1)
        self._job_label = QLabel("")
        self.status.addPermanentWidget(self._job_label)
        self.setStatusBar(self.status)

        self.toasts = ToastManager(self)
        self.busy_overlay = BusyOverlay(self)
        self.busy_overlay.hide()

        self._build_tray()
        self._connect_services()
        self._install_shortcuts()

        theme_manager.tokens_changed.connect(self._on_tokens_changed)
        self._restore_window_state()
        QTimer.singleShot(300, self._startup_tasks)

    # -- construction -------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(232)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(6)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(6, 0, 6, 10)
        brand_layout.setSpacing(9)
        self._logo = QLabel()
        brand_layout.addWidget(self._logo)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setObjectName("H3")
        column.addWidget(name)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("CaptionFaint")
        column.addWidget(version)
        brand_layout.addLayout(column, 1)
        layout.addWidget(brand)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        for page_id, page_type in PAGE_TYPES:
            button = QPushButton(f"  {page_type.title}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("pageId", page_id)
            button.clicked.connect(lambda _=False, key=page_id: self.goto(key))
            self._nav_group.addButton(button)
            self._nav_buttons[page_id] = button
            layout.addWidget(button)
        layout.addStretch(1)

        self._dictation_button = QPushButton("  Dictate now")
        self._dictation_button.setObjectName("PrimaryButton")
        self._dictation_button.setProperty("primary", "true")
        self._dictation_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dictation_button.clicked.connect(self._toggle_dictation)
        layout.addWidget(self._dictation_button)

        self._engine_label = QLabel("")
        self._engine_label.setObjectName("CaptionFaint")
        self._engine_label.setWordWrap(True)
        layout.addWidget(self._engine_label)
        self._nav_bar = bar
        return bar

    def _build_stack(self) -> QWidget:
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        for page_id, page_type in PAGE_TYPES:
            page = page_type(self.context, self.stack)
            self.pages[page_id] = page
            self.stack.addWidget(page)
        return self.stack

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(window_icon(self.theme_manager.tokens), self)
        self.tray.setToolTip(f"{APP_NAME} {APP_VERSION}")
        menu = QMenu(self)
        self._tray_show = QAction("Show Tixi Voice", self)
        self._tray_show.triggered.connect(self.show_window)
        self._tray_dictate = QAction("Start dictation", self)
        self._tray_dictate.triggered.connect(self._toggle_dictation)
        self._tray_tts = QAction("Text to Speech", self)
        self._tray_tts.triggered.connect(lambda: self._tray_goto("text_to_speech"))
        self._tray_stt = QAction("Speech to Text", self)
        self._tray_stt.triggered.connect(lambda: self._tray_goto("speech_to_text"))
        self._tray_models = QAction("AI Models", self)
        self._tray_models.triggered.connect(lambda: self._tray_goto("models"))
        self._tray_updates = QAction("Check for updates", self)
        self._tray_updates.triggered.connect(lambda: self._tray_goto("updates"))
        self._tray_quit = QAction("Quit", self)
        self._tray_quit.triggered.connect(self.quit_application)
        for action in (
            self._tray_show,
            self._tray_dictate,
            self._tray_tts,
            self._tray_stt,
            self._tray_models,
            self._tray_updates,
        ):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self._tray_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _connect_services(self) -> None:
        jobs: JobRunner = self.context.jobs
        jobs.job_started.connect(self._on_job_started)
        jobs.job_progress.connect(self._on_job_progress)
        jobs.job_finished.connect(lambda payload: self._on_job_done(payload, "completed"))
        jobs.job_failed.connect(lambda payload: self._on_job_done(payload, "failed"))
        jobs.job_cancelled.connect(lambda payload: self._on_job_done(payload, "cancelled"))
        jobs.busy_changed.connect(self._on_busy_changed)

        dictation = getattr(self.context, "dictation", None)
        if dictation is not None:
            dictation.state_changed.connect(self._on_dictation_state)
            dictation.transcript_ready.connect(self._on_transcript)
            dictation.error.connect(self._on_dictation_error)
        else:
            self._dictation_button.setEnabled(False)
            self._dictation_button.setToolTip("Voice typing needs Windows.")

        # React to settings changes whatever the state of the updater.
        store = getattr(self.context, "settings_store", None)
        subscribe = getattr(store, "subscribe", None)
        if callable(subscribe):
            subscribe(self._on_settings_changed)

    def _install_shortcuts(self) -> None:
        for index, (page_id, _page) in enumerate(PAGE_TYPES[:9], start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda key=page_id: self.goto(key))
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._toggle_dictation)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.goto("settings"))
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.quit_application)

    # -- navigation ---------------------------------------------------------
    def goto(self, page_id: str) -> None:
        page = self.pages.get(page_id)
        if page is None:
            return
        current = self.stack.currentWidget()
        if current is not None and current is not page:
            leave = getattr(current, "on_leave", None)
            if callable(leave):
                leave()
        self.stack.setCurrentWidget(page)
        button = self._nav_buttons.get(page_id)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        enter = getattr(page, "on_enter", None)
        if callable(enter):
            enter()
        try:
            page.refresh()
        except Exception as exc:  # noqa: BLE001 - one broken page must not kill the window
            log.exception("page refresh failed", extra={"event": "page_refresh_failed", "page": page_id})
            self.notify("This page could not be loaded", str(exc), severity="error")
        store = getattr(self.context, "settings_store", None)
        if store is not None:
            store.set("general.last_view", page_id)

    def page(self, page_id: str) -> Page | None:
        return self.pages.get(page_id)

    def _tray_goto(self, page_id: str) -> None:
        self.show_window()
        self.goto(page_id)

    # -- window behaviour ---------------------------------------------------
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore_window_state(self) -> None:
        settings = self.context.settings.window
        geometry = str(getattr(settings, "geometry", "") or "")
        if geometry:
            try:
                from PySide6.QtCore import QByteArray

                self.restoreGeometry(QByteArray.fromHex(geometry.encode("ascii")))
            except Exception:  # noqa: BLE001
                log.debug("stored geometry could not be restored", exc_info=True)
        last = str(self.context.settings.general.last_view or "dashboard")
        if not bool(self.context.settings.general.restore_last_view):
            last = str(self.context.settings.general.startup_behavior or "dashboard")
        if last == "tray":
            self.hide()
        self.goto(last if last in self.pages else "dashboard")

    def _save_window_state(self) -> None:
        try:
            hex_geometry = bytes(self.saveGeometry().toHex()).decode("ascii")
            self.context.settings_store.set("window.geometry", hex_geometry)
            self.context.settings_store.set("window.state", "maximized" if self.isMaximized() else "normal")
            self.context.save_settings()
        except Exception:  # noqa: BLE001
            log.debug("window state could not be saved", exc_info=True)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            self._apply_window_effects()
            self.goto(self._current_page_id())

    def _current_page_id(self) -> str:
        current = self.stack.currentWidget()
        for page_id, page in self.pages.items():
            if page is current:
                return page_id
        return "dashboard"

    def _apply_window_effects(self) -> None:
        tokens = self.theme_manager.tokens
        set_dark_titlebar(int(self.winId()), dark=tokens.is_dark)
        if not tokens.reduce_transparency and tokens.glass.blur_enabled:
            enable_window_blur(
                int(self.winId()),
                colour=_rgb(tokens.background),
                alpha=150 if tokens.is_dark else 120,
                acrylic=True,
            )

    def changeEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and self.isMinimized():
            if bool(self.context.settings.general.minimize_to_tray) and self.tray.isVisible():
                QTimer.singleShot(0, self.hide)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        if bool(self.context.settings.general.close_to_tray) and not self._quitting and self.tray.isVisible():
            event.ignore()
            self.hide()
            if bool(self.context.settings.general.confirm_on_exit):
                notify(
                    APP_NAME,
                    "Tixi Voice keeps running in the tray. Right-click the icon to quit.",
                    tray=self.tray,
                )
            return
        self._save_window_state()
        self.closing.emit()
        super().closeEvent(event)

    # -- services feedback --------------------------------------------------
    def notify(self, title: str, message: str = "", *, severity: str = "info", detail: str = "") -> None:
        self.toasts.show(title, message, severity=severity, detail=detail)
        if severity in ("error", "warning") and self.isHidden():
            flash_window(int(self.winId()))

    def set_busy(self, message: str, *, cancellable: bool = True) -> None:
        self.busy_overlay.start(message, cancellable=cancellable)
        self._status_label.setText(message)
        if self._cancel_hook is None:
            self._cancel_hook = self.context.jobs.cancel_all
            self.busy_overlay.cancelled.connect(self._cancel_hook)

    def clear_busy(self) -> None:
        self.busy_overlay.stop()
        self._status_label.setText("Ready")

    def _on_job_started(self, payload: dict[str, Any]) -> None:
        self._job_label.setText(str(payload.get("label", "")))
        self._status_label.setText(str(payload.get("label", "")))

    def _on_job_progress(self, payload: dict[str, Any]) -> None:
        fraction = float(payload.get("progress", 0.0))
        label = str(payload.get("label", ""))
        detail = str(payload.get("detail", ""))
        self._job_label.setText(f"{label} {int(fraction * 100)}%")
        if detail:
            self._status_label.setText(f"{label}: {detail}")

    def _on_job_done(self, payload: dict[str, Any], outcome: str) -> None:
        if not self.context.jobs.is_busy:
            self._job_label.setText("")
            self._status_label.setText("Ready")
        if outcome == "failed":
            error = str(payload.get("error", ""))
            if not payload.get("_reported"):
                self.notify("Something went wrong", error, severity="error", detail=error)

    def _on_busy_changed(self, busy: bool) -> None:
        if not busy:
            self._job_label.setText("")
            self._status_label.setText("Ready")

    # -- dictation ----------------------------------------------------------
    def _toggle_dictation(self) -> None:
        dictation = getattr(self.context, "dictation", None)
        if dictation is None:
            self.notify("Voice typing unavailable", "Global dictation requires Windows.", severity="warning")
            return
        state = dictation.state
        if state.is_busy:
            dictation.stop_manual()
        else:
            if not dictation.start_manual():
                self.notify("Could not start dictation", dictation.last_error(), severity="warning")

    def _on_dictation_state(self, state: str, message: str) -> None:
        busy = state in (VoiceTypingState.RECORDING.value, VoiceTypingState.TRANSCRIBING.value)
        self._dictation_button.setText("  Stop dictation" if busy else "  Dictate now")
        self._status_label.setText(message or state.title())
        tokens = self.theme_manager.tokens
        self.tray.setIcon(tray_icon(tokens, recording=busy))
        self._tray_dictate.setText("Stop dictation" if busy else "Start dictation")

    def _on_transcript(self, text: str, target: str) -> None:
        self._status_label.setText(f"Dictated {len(text)} characters" + (f" into {target}" if target else ""))

    def _on_dictation_error(self, message: str) -> None:
        self.notify("Dictation problem", message, severity="warning")

    # -- theming ------------------------------------------------------------
    def _on_tokens_changed(self, tokens: Any) -> None:
        self.setWindowIcon(window_icon(tokens))
        self.tray.setIcon(tray_icon(tokens))
        if hasattr(self, "_logo"):
            self._logo.setPixmap(icon_pixmap("waveform", tokens, 26, colour=tokens.accent_primary))
        for page in self.pages.values():
            try:
                page.apply_tokens(tokens)
            except Exception:  # noqa: BLE001
                log.debug("page theming failed", exc_info=True)
        self._engine_label.setStyleSheet(f"color: {tokens.text_faint};")
        self._apply_window_effects()

    def apply_settings_everywhere(self) -> None:
        """Re-apply settings after an import or a reset."""
        manager = self.theme_manager
        appearance = self.context.settings.appearance
        manager.set_mode(str(appearance.theme_mode), persist=False)
        manager.set_accent(str(appearance.accent), custom=str(getattr(appearance, "custom_accent", "")), persist=False)
        manager.set_radius(int(appearance.corner_radius), persist=False)
        manager.set_density(str(appearance.density), persist=False)
        manager.set_scale(float(appearance.ui_scale), persist=False)
        manager.set_animations(bool(appearance.animations), persist=False)
        manager.set_reduce_transparency(bool(appearance.reduce_transparency), persist=False)
        manager.apply()
        dictation = getattr(self.context, "dictation", None)
        if dictation is not None:
            dictation.apply_settings(self.context.settings)
        for page in self.pages.values():
            try:
                page.refresh()
            except Exception:  # noqa: BLE001
                continue
        self.toast_refresh()

    def toast_refresh(self) -> None:
        tokens = self.theme_manager.tokens
        for page in self.pages.values():
            page.apply_tokens(tokens)

    def _on_settings_changed(self, settings: Any, sections: list[str]) -> None:
        if "general" in sections and "model_dir" in sections:
            pass
        label = self._engine_summary()
        self._engine_label.setText(label)

    def _engine_summary(self) -> str:
        try:
            availability = self.context.engine_manager.availability()
        except Exception:  # noqa: BLE001
            return ""
        ready = [name for name, info in availability.items() if info.get("available")]
        missing = [name for name, info in availability.items() if not info.get("available")]
        if ready and not missing:
            return f"Engines ready: {', '.join(ready)}"
        if ready:
            return f"Engines ready: {', '.join(ready)} · missing: {', '.join(missing)}"
        return "No engine installed yet — open AI Models."

    # -- startup ------------------------------------------------------------
    def _startup_tasks(self) -> None:
        self._engine_label.setText(self._engine_summary())
        dictation = getattr(self.context, "dictation", None)
        if dictation is not None:
            try:
                dictation.apply_settings(self.context.settings)
                dictation.start()
            except Exception as exc:  # noqa: BLE001
                self.notify("Global shortcut unavailable", str(exc), severity="warning")
        if bool(self.context.settings.privacy.history_enabled):
            try:
                days = int(self.context.settings.privacy.retention_days or 0)
                if days > 0:
                    removed = self.context.history.apply_retention(days)
                    if removed:
                        log.info("retention removed entries", extra={"event": "retention", "count": removed})
            except Exception:  # noqa: BLE001
                log.debug("retention failed", exc_info=True)
        for note in self.context.notes:
            self.notify("Heads-up", note, severity="info")
        updates = getattr(self.context, "updates", None)
        if updates is not None and updates.should_check_automatically():
            self._startup_update_check(updates)
        self._on_tokens_changed(self.theme_manager.tokens)

    def _startup_update_check(self, updates: Any) -> None:
        def job(*, progress: Any = None, cancel: Any = None) -> Any:
            return updates.check()

        def done(payload: dict[str, Any]) -> None:
            summary = updates.last_summary
            self.context.settings_store.set("updates.last_check", summary.checked_at)
            self.context.save_settings()
            if summary.update_available and bool(self.context.settings.updates.notify):
                self.notify(
                    "Update available",
                    f"Version {summary.latest_version} can be downloaded from the Application Updates page.",
                    severity="info",
                    detail=summary.notes,
                )

        self.context.jobs.submit(
            "Checking for updates",
            job,
            kind="update-check",
            exclusive=True,
            on_finished=done,
        )

    # -- shutdown -----------------------------------------------------------
    def _on_tray_activated(self, reason: Any) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_window()

    def quit_application(self) -> None:
        self._quitting = True
        self._save_window_state()
        self.closing.emit()
        self.tray.hide()
        self.close()
        from PySide6.QtWidgets import QApplication

        QApplication.instance().quit()

    _quitting = False


def _rgb(colour: str) -> tuple[int, int, int]:
    value = (colour or "#101014").lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return (16, 16, 20)
