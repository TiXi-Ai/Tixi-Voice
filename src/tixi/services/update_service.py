"""Application update centre.

This is a thin, settings-aware layer on top of :class:`tixi.updater.UpdateService`.
It answers the questions the *Application Updates* page asks:

* is an update available, and am I allowed to care (skipped versions, channel)?
* when did I last check — should I check automatically at startup?
* what is the download progress, and has the package been verified?

An update is **never** downloaded or executed implicitly.  The centre only
performs a check when the user (or the startup timer, which can be switched off)
asks for one, and installation always requires an explicit confirmation from the
dialog.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..app.config import APP_VERSION, GITHUB_URL
from ..app.logging_config import get_logger
from ..updater.github_client import GitHubError, OfflineError, RateLimitError
from ..updater.update_installer import (
    UpdateCheckResult,
    UpdatePackage,
    UpdateService,
    clean_update_cache,
    describe_current_build,
    update_cache_dir,
)
from ..updater.version_manager import Version, is_newer, parse_version
from ..utils.humanize import human_size

log = get_logger("tixi.services.updates")

ProgressReporter = Callable[[float, str], None]


@dataclass
class UpdateSummary:
    """Flattened view of a check result for the UI table/labels."""

    status: str = "unknown"            # unknown | up-to-date | available | error | offline | rate-limited
    detail: str = ""
    current_version: str = APP_VERSION
    latest_version: str = ""
    channel: str = "stable"
    checked_at: str = ""
    published_at: str = ""
    notes: str = ""
    download_size: int = 0
    download_label: str = ""
    manual_url: str = GITHUB_URL + "/releases"
    update_available: bool = False
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "channel": self.channel,
            "checked_at": self.checked_at,
            "published_at": self.published_at,
            "download_size": self.download_size,
            "download_label": self.download_label,
            "manual_url": self.manual_url,
            "update_available": self.update_available,
            "skipped": self.skipped,
        }


class UpdateCenter:
    """Settings-aware wrapper around the updater, safe to call from the UI thread."""

    def __init__(
        self,
        settings_provider: Callable[[Any], Any] | None = None,
        *,
        offline: bool = False,
        service: UpdateService | None = None,
    ) -> None:
        self.settings_provider = settings_provider or (lambda: None)
        self.offline = offline
        self.service = service or UpdateService(APP_VERSION)
        self.last_result: UpdateCheckResult | None = None
        self.last_summary = UpdateSummary(current_version=APP_VERSION)
        self.last_package: UpdatePackage | None = None
        self._lock = threading.RLock()

    # -- settings -----------------------------------------------------------
    def _update_settings(self) -> Any:
        settings = self.settings_provider()
        return getattr(settings, "updates", settings)

    @property
    def channel(self) -> str:
        return str(getattr(self._update_settings(), "channel", "stable") or "stable")

    @property
    def skipped_version(self) -> str:
        return str(getattr(self._update_settings(), "skipped_version", "") or "")

    @property
    def current_version(self) -> str:
        return APP_VERSION

    def network_allowed(self) -> bool:
        if self.offline:
            return False
        privacy = getattr(self.settings_provider(), "privacy", None)
        if privacy is not None and getattr(privacy, "offline_mode", False):
            return False
        if privacy is not None and hasattr(privacy, "allow_network") and not privacy.allow_network:
            return False
        return True

    # -- scheduling ---------------------------------------------------------
    def should_check_automatically(self) -> bool:
        """``True`` when the interval has elapsed and auto-check is enabled."""
        if not self.network_allowed():
            return False
        settings = self._update_settings()
        if not bool(getattr(settings, "auto_check", True)):
            return False
        last = str(getattr(settings, "last_check", "") or "")
        hours = int(getattr(settings, "check_interval_hours", 24) or 24)
        if not last or hours <= 0:
            return True
        try:
            stamp = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return True
        return datetime.now() - stamp >= timedelta(hours=hours)

    def record_check_time(self, when: str | None = None) -> str:
        stamp = when or time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_summary.checked_at = stamp
        return stamp

    # -- operations ---------------------------------------------------------
    def check(self, *, channel: str = "", skipped_version: str = "") -> UpdateCheckResult:
        """Query GitHub.  Never raises: failures come back inside the result."""
        channel = channel or self.channel
        skipped_version = skipped_version or self.skipped_version
        if not self.network_allowed():
            summary = UpdateSummary(
                status="offline",
                detail=(
                    "Offline Mode is on, so Tixi Voice did not contact GitHub. "
                    "Download the newest release manually from the releases page."
                ),
                current_version=APP_VERSION,
                channel=channel,
                manual_url=GITHUB_URL + "/releases",
            )
            self.last_summary = summary
            return UpdateCheckResult(
                ok=False,
                error=summary.detail,
                offline=True,
                current_version=APP_VERSION,
                checked_at=self.record_check_time(),
                manual_url=summary.manual_url,
            )
        try:
            result = self.service.check(channel=channel, skipped_version=skipped_version)
        except (OfflineError, RateLimitError, GitHubError) as exc:  # pragma: no cover - defensive
            result = UpdateCheckResult(
                ok=False,
                error=str(exc),
                current_version=APP_VERSION,
                checked_at=self.record_check_time(),
            )
        except Exception as exc:  # noqa: BLE001 - the UI must always get an answer
            log.exception("update check failed", extra={"event": "update_check_failed"})
            result = UpdateCheckResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                current_version=APP_VERSION,
                checked_at=self.record_check_time(),
            )
        with self._lock:
            self.last_result = result
            self.last_summary = self._summarise(result, channel=channel)
        return result

    def download(self, *, progress: ProgressReporter | None = None) -> UpdatePackage:
        """Download + verify the installer for the last check result."""
        result = self.last_result
        if result is None or not result.update_available:
            raise GitHubError("Check for updates again — there is nothing verified to download.")

        def on_progress(item: Any) -> None:
            if progress is None:
                return
            fraction = float(getattr(item, "fraction", 0.0) or 0.0)
            describe = getattr(item, "describe", None)
            text = ""
            if callable(describe):
                try:
                    text = str(describe())
                except Exception:  # noqa: BLE001
                    text = ""
            if not text:
                text = str(getattr(item, "message", "") or getattr(item, "status", ""))
            progress(fraction, text)

        cancel = self.service._cancel  # noqa: SLF001 - shared cancel flag for the UI
        package = self.service.download(result, progress=on_progress if progress else None, cancel=cancel)
        self.last_package = package
        return package

    def cancel(self) -> None:
        self.service.cancel()

    def can_install(self) -> tuple[bool, str]:
        return self.service.can_install()

    def install(self, package: UpdatePackage | None = None, *, on_before_exit: Callable[[], None] | None = None) -> None:
        target = package or self.last_package
        if target is None:
            raise GitHubError("Download the update first.")
        self.service.install(target, on_before_exit=on_before_exit)

    def open_release_page(self, url: str = "") -> None:
        self.service.open_release_page(url or self.last_summary.manual_url)

    def clean_cache(self, *, keep_latest: bool = True) -> int:
        return clean_update_cache(keep_latest=keep_latest)

    @property
    def cache_dir(self) -> Path:
        return update_cache_dir()

    # -- reporting ----------------------------------------------------------
    def build_info(self) -> dict[str, Any]:
        info = describe_current_build()
        info.update(
            {
                "channel": self.channel,
                "cache_dir": str(self.cache_dir),
                "cache_size": human_size(_dir_size(self.cache_dir)),
                "network_allowed": self.network_allowed(),
            }
        )
        return info

    def describe(self) -> dict[str, Any]:
        return self.last_summary.as_dict()

    def _summarise(self, result: UpdateCheckResult, *, channel: str) -> UpdateSummary:
        summary = UpdateSummary(
            current_version=APP_VERSION,
            channel=channel,
            checked_at=result.checked_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            notes=result.release_notes,
            manual_url=result.manual_url or (GITHUB_URL + "/releases"),
        )
        release = result.release
        if release is not None:
            summary.latest_version = release.version or str(release.tag or "")
            summary.published_at = str(getattr(release, "published_at", "") or "")
            summary.download_size = int(getattr(release, "total_download_size", lambda: 0)() or 0)
            summary.download_label = human_size(summary.download_size) if summary.download_size else ""
        if result.offline:
            summary.status = "offline"
            summary.detail = result.error or "Tixi Voice is offline."
        elif result.rate_limited:
            summary.status = "rate-limited"
            summary.detail = result.error or "GitHub's rate limit was reached; try again later."
        elif not result.ok:
            summary.status = "error"
            summary.detail = result.error or "The update check failed."
        elif result.update_available:
            summary.status = "available"
            summary.update_available = True
            summary.detail = (
                f"Version {summary.latest_version} is available "
                f"(you have {APP_VERSION})."
            )
        else:
            decision = result.decision
            summary.detail = decision.describe() if decision is not None else "You are up to date."
            summary.status = "up-to-date"
            if decision is not None and getattr(decision, "reason", "") == "skipped":
                summary.skipped = True
                summary.status = "skipped"
        return summary


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def version_gap(current: str, latest: str) -> str:
    """Human phrase describing how far behind the running build is."""
    try:
        running: Version = parse_version(current)
        available: Version = parse_version(latest)
    except Exception:  # noqa: BLE001
        return ""
    if not is_newer(available, running):
        return ""
    if running.base and available.base and running.base != available.base:
        return f"{running.base} → {available.base} (major/minor update)"
    return "Small update"
