"""Verified download and (explicitly confirmed) installation of updates.

Safety rules implemented here:

* the update package is only **downloaded**, never executed, by
  :meth:`UpdateService.download`,
* it is verified against the ``SHA256SUMS``/``*.sha256`` file published with the
  release when one exists,
* on Windows the Authenticode signature is checked (``WinVerifyTrust`` through
  PowerShell, no third-party dependency) and the user is *told* when a release
  has no signature or no checksum instead of being reassured falsely,
* installation only ever happens after the user presses **Install**, the
  application closes gracefully, and the installer is launched with the
  parameter list we build ourselves,
* user data (settings, history, models) lives outside the installation folder,
  so an upgrade never removes it.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..app.config import APP_VERSION, GITHUB_URL
from ..app.logging_config import get_logger
from ..app.paths import IS_FROZEN, IS_WINDOWS, paths
from ..utils.humanize import human_size
from .github_client import (
    GitHubClient,
    GitHubError,
    OfflineError,
    ReleaseInfo,
    RateLimitError,
)
from .version_manager import (
    UpdateDecision,
    find_checksum_asset,
    find_installer_asset,
    is_newer,
    parse_checksum_file,
    parse_version,
    release_date,
    release_notes,
    should_update,
)

log = get_logger("tixi.updater")

DownloadProgressCallback = Callable[[int, int], None]


@dataclass
class UpdateCheckResult:
    """Everything the Update view needs to render one check."""

    ok: bool
    decision: UpdateDecision | None = None
    release: ReleaseInfo | None = None
    error: str = ""
    offline: bool = False
    rate_limited: bool = False
    current_version: str = APP_VERSION
    checked_at: str = ""
    release_notes: str = ""
    release_date: str = ""
    installer_asset: dict[str, Any] | None = None
    checksum_asset: dict[str, Any] | None = None
    manual_url: str = GITHUB_URL + "/releases"

    @property
    def update_available(self) -> bool:
        return bool(self.decision and self.decision.update_available)

    def summary(self) -> str:
        if self.error:
            return self.error
        return self.decision.describe() if self.decision else "No update information."


@dataclass
class UpdatePackage:
    """A downloaded, verified installer."""

    path: Path
    version: str
    size_bytes: int
    sha256: str = ""
    checksum_verified: bool = False
    signature_status: str = "unknown"   # valid | unsigned | invalid | unavailable
    signature_subject: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "checksum_verified": self.checksum_verified,
            "signature_status": self.signature_status,
            "signature_subject": self.signature_subject,
            "warnings": list(self.warnings),
        }

    def trust_summary(self) -> str:
        if self.checksum_verified and self.signature_status == "valid":
            return "Verified: SHA-256 matches the release checksum and the Windows signature is valid."
        if self.checksum_verified:
            return (
                "SHA-256 matches the published checksum. "
                + (
                    "This release is not digitally signed, so publisher identity could not be verified."
                    if self.signature_status == "unsigned"
                    else "The digital signature could not be verified on this system."
                )
            )
        return (
            "No published checksum was available for this release, so integrity could only be "
            "checked by download size. Review the release page before installing."
        )


class UpdateService:
    """High-level updater used by the Update view and the startup check."""

    def __init__(
        self,
        current_version: str = APP_VERSION,
        *,
        client: GitHubClient | None = None,
        token: str = "",
        cache_dir: Path | None = None,
    ) -> None:
        self.current_version = current_version
        self.cache_dir = Path(cache_dir) if cache_dir else paths().cache / "updates"
        self.client = client or GitHubClient(token=token, cache_dir=self.cache_dir)
        self._cancel = threading.Event()

    # -- checking -----------------------------------------------------------
    def check(self, *, channel: str = "stable", skipped_version: str = "") -> UpdateCheckResult:
        """Query GitHub for the newest release in the selected channel."""
        checked_at = time.strftime("%Y-%m-%d %H:%M:%S")
        include_prerelease = channel == "beta"
        try:
            releases = self.client.list_releases(limit=20, include_prerelease=include_prerelease)
        except OfflineError as exc:
            return UpdateCheckResult(
                ok=False,
                error=str(exc),
                offline=True,
                current_version=self.current_version,
                checked_at=checked_at,
            )
        except RateLimitError as exc:
            return UpdateCheckResult(
                ok=False,
                error=str(exc),
                rate_limited=True,
                current_version=self.current_version,
                checked_at=checked_at,
            )
        except GitHubError as exc:
            message = str(exc)
            offline = "no such release" not in message.lower()
            return UpdateCheckResult(
                ok=False,
                error=message,
                current_version=self.current_version,
                checked_at=checked_at,
                offline=offline and "no published release" in message.lower() is False and False,
            )

        if not releases:
            return UpdateCheckResult(
                ok=True,
                current_version=self.current_version,
                checked_at=checked_at,
                error="No published release was found yet. Tixi Voice is running the development build.",
            )

        release = _pick_best(releases, channel=channel, current_version=self.current_version)
        decision = should_update(
            self.current_version,
            release.raw,
            channel=channel,
            skipped_version=skipped_version,
        )
        installer = find_installer_asset(release.raw)
        checksum = find_checksum_asset(release.raw)
        published = release_date(release.raw)
        return UpdateCheckResult(
            ok=True,
            decision=decision,
            release=release,
            current_version=self.current_version,
            checked_at=checked_at,
            release_notes=release_notes(release.raw),
            release_date=published.strftime("%Y-%m-%d %H:%M") if published else release.published_at,
            installer_asset=installer,
            checksum_asset=checksum,
            error="",
        )

    # -- downloading --------------------------------------------------------
    def download(
        self,
        result: UpdateCheckResult,
        *,
        progress: DownloadProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> UpdatePackage:
        """Download and verify the installer for a available update."""
        if not result.ok or result.release is None:
            raise GitHubError("There is nothing to download — check for updates first.")
        asset = result.installer_asset or find_installer_asset(result.release.raw)
        if asset is None:
            raise GitHubError(
                "This release has no Windows installer attached. You can download it manually "
                f"from {result.manual_url}."
            )
        self._cancel.clear()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / str(asset.get("name") or "TixiVoice-Setup.exe")
        if target.exists():
            target.unlink()

        expected_digest = ""
        if result.checksum_asset is not None:
            expected_digest = self._expected_digest(result, asset)

        self.client.download_asset(
            asset,
            target,
            progress=progress,
            cancel=cancel or self._cancel,
            resume=True,
        )

        digest = _sha256(target)
        package = UpdatePackage(
            path=target,
            version=result.decision.latest.raw if result.decision else result.release.version,
            size_bytes=target.stat().st_size,
            sha256=digest,
        )
        if expected_digest:
            if digest.lower() == expected_digest.lower():
                package.checksum_verified = True
            else:
                target.unlink(missing_ok=True)
                raise GitHubError(
                    "The downloaded update failed its SHA-256 check and was deleted. "
                    "Please retry, or download the release manually from GitHub."
                )
        else:
            package.warnings.append(
                "This release does not publish a SHA-256 checksum file, so integrity could only be "
                "checked by size."
            )
        package.signature_status, package.signature_subject = verify_signature(target)
        if package.signature_status == "unsigned":
            package.warnings.append(
                "The installer is not digitally signed by a trusted publisher, so Tixi Voice "
                "cannot prove who built it. Only continue if you trust the release page."
            )
        elif package.signature_status == "invalid":
            package.warnings.append(
                "The Windows signature is present but NOT valid. Do not install this file."
            )
        log.info(
            "update downloaded",
            extra={
                "event": "update_downloaded",
                "version": package.version,
                "bytes": package.size_bytes,
                "checksum_verified": package.checksum_verified,
                "signature": package.signature_status,
            },
        )
        return package

    def _expected_digest(self, result: UpdateCheckResult, asset: dict[str, Any]) -> str:
        checksum_asset = result.checksum_asset
        if checksum_asset is None:
            return ""
        try:
            url = str(checksum_asset.get("browser_download_url") or "")
            if checksum_asset.get("url", "").startswith("https://api.github.com"):
                payload = self.client._get(f"/releases/assets/{checksum_asset.get('id')}")  # noqa: SLF001
                url = str(payload.get("browser_download_url") or url)
            text = self.client.fetch_text(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read the checksum file", extra={"event": "checksum_fetch_failed", "error": str(exc)})
            return ""
        table = parse_checksum_file(text)
        name = str(asset.get("name") or "")
        return table.get(name, "") or next(iter(table.values()), "")

    def cancel(self) -> None:
        self._cancel.set()
        self.client  # noqa: B018 - keep the reference (documentation)

    # -- installing ---------------------------------------------------------
    def can_install(self) -> tuple[bool, str]:
        """Is a silent in-place upgrade possible in this installation?"""
        if not IS_WINDOWS:
            return False, (
                "Automatic installation is only available in the Windows build. "
                "Download the release from GitHub instead."
            )
        if not IS_FROZEN:
            return False, (
                "You are running Tixi Voice from source. Update the checkout with git, "
                "or install the packaged release from GitHub."
            )
        return True, ""

    def install(
        self,
        package: UpdatePackage,
        *,
        run_after: bool = True,
        silent: bool = True,
        on_before_exit: Callable[[], None] | None = None,
    ) -> None:
        """Launch the verified installer, then exit so files can be replaced."""
        possible, reason = self.can_install()
        if not possible:
            raise GitHubError(reason)
        if package.signature_status == "invalid":
            raise GitHubError(
                "The installer's digital signature is invalid. Installation was refused for your safety."
            )
        if not package.path.exists():
            raise GitHubError("The downloaded installer is no longer on disk; download it again.")

        args = [str(package.path)]
        if silent:
            args.append("/SILENT")
            args.append("/NORESTART")
            args.append("/CLOSEAPPLICATIONS")
        if run_after:
            args.append("/RESTARTAPP")

        log.info(
            "launching installer",
            extra={"event": "update_launch", "version": package.version, "silent": silent},
        )
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008  # DETACHED_PROCESS
        subprocess.Popen(args, close_fds=True, creationflags=creation)  # noqa: S603 - fixed argument list
        if on_before_exit:
            on_before_exit()

    def open_release_page(self, url: str = "") -> None:
        """Open the release page in the default browser (explicit user action)."""
        target = url or f"{GITHUB_URL}/releases"
        try:
            if IS_WINDOWS:
                os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])  # noqa: S603,S607
            else:
                subprocess.Popen(["xdg-open", target])  # noqa: S603,S607
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open the browser", extra={"event": "open_url_failed", "error": str(exc)})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _pick_best(releases: list[ReleaseInfo], *, channel: str, current_version: str) -> ReleaseInfo:
    """Highest version not older than the installed one; newest stable preferred."""
    usable = [release for release in releases if not release.draft]
    if channel != "beta":
        stable = [release for release in usable if not release.prerelease]
        usable = stable or usable
    def key(release: ReleaseInfo) -> tuple:
        return parse_version(release.version).tuple()

    return max(usable, key=key)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_signature(path: Path) -> tuple[str, str]:
    """Check the Authenticode signature on Windows.

    Returns ``(status, subject)`` where status is one of ``valid``, ``unsigned``,
    ``invalid``, ``unavailable``.  Uses PowerShell's ``Get-AuthenticodeSignature``
    (built into Windows) so no extra dependency is required.
    """
    if not IS_WINDOWS:  # pragma: no cover - platform specific
        return "unavailable", ""
    if not path.exists():
        return "unavailable", ""
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "$s = Get-AuthenticodeSignature -LiteralPath "
            f"'{path}'; "
            '"$s.Status|" + $s.SignerCertificate.Subject'
        ),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("signature check failed", extra={"event": "signature_check_failed", "error": str(exc)})
        return "unavailable", ""
    if "|" not in output:
        return "unavailable", ""
    status, _, subject = output.partition("|")
    status = status.strip().lower()
    mapping = {
        "valid": "valid",
        "notsigned": "unsigned",
        "hashmismatch": "invalid",
        "nottrusted": "invalid",
        "unknownerror": "unavailable",
        "notsupported": "unavailable",
    }
    return mapping.get(status.replace(" ", ""), "unavailable"), subject.strip()


def update_cache_dir() -> Path:
    return paths().cache / "updates"


def clean_update_cache(keep_latest: bool = True) -> int:
    """Delete downloaded installers (after a successful update, for example)."""
    directory = update_cache_dir()
    if not directory.exists():
        return 0
    freed = 0
    files = sorted(directory.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True)
    for index, item in enumerate(files):
        if keep_latest and index == 0 and item.is_file():
            continue
        try:
            if item.is_file():
                freed += item.stat().st_size
                item.unlink()
            elif item.is_dir():
                freed += sum(child.stat().st_size for child in item.rglob("*") if child.is_file())
                shutil.rmtree(item)
        except OSError:  # pragma: no cover
            continue
    return freed


def describe_current_build() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "python": platform.python_version(),
        "frozen": IS_FROZEN,
        "platform": platform.platform(),
        "executable": sys.executable,
        "channel": "beta" if parse_version(APP_VERSION).is_prerelease else "stable",
        "cache_dir": str(update_cache_dir()),
    }
