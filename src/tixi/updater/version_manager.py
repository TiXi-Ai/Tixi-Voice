"""Version parsing, comparison and channel handling for the updater."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
    r"(?:[-._]?(?P<stage>a|alpha|b|beta|rc|pre|preview)\.?(?P<stage_num>\d+)?)?"
    r"(?:\+(?P<build>[0-9A-Za-z.\-]+))?$",
    re.IGNORECASE,
)

STAGE_ORDER = {
    "dev": -3,
    "a": -2,
    "alpha": -2,
    "b": -1,
    "beta": -1,
    "rc": 0,
    "pre": 0,
    "preview": 0,
    "": 1,
    "final": 1,
}


@dataclass(frozen=True, order=False)
class Version:
    """A parsed, comparable version."""

    raw: str
    major: int = 0
    minor: int = 0
    patch: int = 0
    stage: str = ""
    stage_number: int = 0
    build: str = ""

    @property
    def is_prerelease(self) -> bool:
        return bool(self.stage) and self.stage not in ("final", "")

    @property
    def base(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def tuple(self) -> tuple:
        return (
            self.major,
            self.minor,
            self.patch,
            STAGE_ORDER.get(self.stage.lower(), 1),
            self.stage_number,
        )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.raw

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Version({self.raw!r})"

    # -- comparisons --------------------------------------------------------
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Version):
            return self.tuple() == other.tuple()
        if isinstance(other, str):
            return self.tuple() == parse_version(other).tuple()
        return NotImplemented

    def __lt__(self, other: "Version | str") -> bool:
        return self.tuple() < _coerce(other).tuple()

    def __le__(self, other: "Version | str") -> bool:
        return self.tuple() <= _coerce(other).tuple()

    def __gt__(self, other: "Version | str") -> bool:
        return self.tuple() > _coerce(other).tuple()

    def __ge__(self, other: "Version | str") -> bool:
        return self.tuple() >= _coerce(other).tuple()

    def __hash__(self) -> int:
        return hash(self.tuple())


def _coerce(value: "Version | str") -> Version:
    return value if isinstance(value, Version) else parse_version(value)


def parse_version(value: str) -> Version:
    """Parse a version string; unknown tails are ignored rather than fatal."""
    raw = (value or "").strip()
    match = SEMVER_RE.match(raw)
    if not match:
        numbers = re.findall(r"\d+", raw)
        return Version(
            raw=raw,
            major=int(numbers[0]) if numbers else 0,
            minor=int(numbers[1]) if len(numbers) > 1 else 0,
            patch=int(numbers[2]) if len(numbers) > 2 else 0,
        )
    stage = (match.group("stage") or "").lower()
    return Version(
        raw=raw,
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        patch=int(match.group("patch") or 0),
        stage=stage,
        stage_number=int(match.group("stage_num") or 0) if match.group("stage_num") else 0,
        build=match.group("build") or "",
    )


@dataclass
class UpdateDecision:
    """The result of comparing the installed version with a release."""

    update_available: bool
    current: Version
    latest: Version
    reason: str = ""
    channel: str = "stable"

    def describe(self) -> str:
        if self.update_available:
            return f"Version {self.latest.raw} is available (you have {self.current.raw})."
        return self.reason or f"Tixi Voice {self.current.raw} is up to date."


def compare(current: str | Version, latest: str | Version) -> int:
    """``-1`` older, ``0`` equal, ``1`` newer."""
    left, right = _coerce(current), _coerce(latest)
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def is_newer(candidate: str | Version, current: str | Version) -> bool:
    return _coerce(candidate) > _coerce(current)


def should_update(
    current_version: str,
    release: dict[str, Any],
    *,
    channel: str = "stable",
    skipped_version: str = "",
) -> UpdateDecision:
    """Decide whether a GitHub release should be offered to the user."""
    current = parse_version(current_version)
    tag = str(release.get("tag_name") or release.get("name") or "")
    latest = parse_version(tag)
    prerelease = bool(release.get("prerelease")) or latest.is_prerelease
    draft = bool(release.get("draft"))

    if draft:
        return UpdateDecision(False, current, latest, "The newest release is still a draft.", channel)
    if prerelease and channel != "beta":
        return UpdateDecision(
            False,
            current,
            latest,
            f"{latest.raw} is a pre-release; switch to the Beta channel to test it.",
            channel,
        )
    if latest <= current:
        return UpdateDecision(
            False, current, latest, f"Tixi Voice {current.raw} is up to date.", channel
        )
    if skipped_version and parse_version(skipped_version) == latest:
        return UpdateDecision(
            False,
            current,
            latest,
            f"You chose to skip version {latest.raw}. It will not be offered again until a newer "
            "release appears.",
            channel,
        )
    return UpdateDecision(
        True, current, latest, f"Version {latest.raw} is available.", channel
    )


def release_notes(release: dict[str, Any], *, limit: int = 6000) -> str:
    """Cleaned release notes, ready for a read-only text view."""
    body = str(release.get("body") or "").strip()
    notes = body if body else "This release does not include release notes."
    if len(notes) > limit:
        notes = notes[:limit] + "\n…\n(notes truncated — open the release page for the full text)"
    return notes


def release_date(release: dict[str, Any]) -> datetime | None:
    value = release.get("published_at") or release.get("created_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def paged_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    assets = release.get("assets") or []
    ordered = sorted(
        assets,
        key=lambda asset: (
            _asset_rank(str(asset.get("name", ""))),
            -int(asset.get("size", 0) or 0),
        ),
    )
    return ordered


_INSTALLER_PATTERNS = (
    ("setup", 0),
    ("installer", 1),
    ("windows", 2),
    (".exe", 3),
    ("checksum", 9),
    (".sha256", 9),
    ("sha256", 9),
    ("portable", 5),
    (".zip", 6),
)


def _asset_rank(name: str) -> int:
    lowered = name.lower()
    for pattern, rank in _INSTALLER_PATTERNS:
        if pattern in lowered:
            return rank
    return 7


def find_installer_asset(release: dict[str, Any], *, allow_portable: bool = False) -> dict[str, Any] | None:
    """Pick the Windows installer asset (checksum sidecars excluded)."""
    for asset in paged_assets(release):
        name = str(asset.get("name", "")).lower()
        if any(token in name for token in ("sha256", "checksums", ".txt", ".json", ".sig", ".asc")):
            continue
        if name.endswith(".exe"):
            return asset
        if allow_portable and name.endswith(".zip"):
            return asset
    return None


def find_checksum_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    for asset in release.get("assets") or []:
        name = str(asset.get("name", "")).lower()
        if "sha256" in name or "checksum" in name:
            return asset
    return None


def parse_checksum_file(text: str) -> dict[str, str]:
    """Parse ``sha256sum``-style output into ``{filename: digest}``."""
    table: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest = ""
        filename = ""
        match = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", line)
        if match:
            digest, filename = match.group(1), match.group(2).strip()
        else:
            match = re.match(r"^(.+?):\s*([0-9a-fA-F]{64})$", line)
            if match:
                filename, digest = match.group(1).strip(), match.group(2)
        if digest and filename:
            table[filename.lstrip("*").strip()] = digest.lower()
    return table


def channel_for_version(version: str) -> str:
    """Which channel a version belongs to (used when auto-checking)."""
    return "beta" if parse_version(version).is_prerelease else "stable"
