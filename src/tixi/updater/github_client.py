"""GitHub Releases API client.

Only the public REST API of the official repository is used, with a proper
``User-Agent``, conditional requests (``ETag``) and explicit handling of rate
limits — no scraping, no tokens required for public releases, and every failure
mode (offline, 404, 403, malformed JSON) has a human-readable message.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..app.config import GITHUB_API, GITHUB_OWNER, GITHUB_REPO, GITHUB_URL
from ..app.logging_config import get_logger
from ..utils.humanize import human_size

log = get_logger("tixi.updater.github")

USER_AGENT = "TixiVoice/1.0 (+https://github.com/TiXi-Ai/Tixi-Voice)"
API_ROOT = "https://api.github.com"


class GitHubError(RuntimeError):
    """Base error for updater network operations."""


class OfflineError(GitHubError):
    """No usable network connection."""


class RateLimitError(GitHubError):
    """The API rate limit was hit."""

    def __init__(self, message: str, *, reset_at: float = 0.0) -> None:
        super().__init__(message)
        self.reset_at = reset_at

    def wait_seconds(self) -> float:
        return max(0.0, self.reset_at - time.time()) if self.reset_at else 0.0


class ReleaseNotFound(GitHubError):
    """The repository has no (matching) releases."""


@dataclass
class ReleaseInfo:
    """A GitHub release, cleaned up for the UI."""

    tag: str
    name: str
    body: str
    prerelease: bool
    draft: bool
    published_at: str
    html_url: str
    assets: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ReleaseInfo:
        return cls(
            tag=str(payload.get("tag_name", "")),
            name=str(payload.get("name") or payload.get("tag_name") or ""),
            body=str(payload.get("body") or ""),
            prerelease=bool(payload.get("prerelease")),
            draft=bool(payload.get("draft")),
            published_at=str(payload.get("published_at") or ""),
            html_url=str(payload.get("html_url") or GITHUB_URL),
            assets=list(payload.get("assets") or []),
            raw=payload,
        )

    def asset_by_name(self, name: str) -> dict[str, Any] | None:
        for asset in self.assets:
            if str(asset.get("name")) == name:
                return asset
        return None

    def total_download_size(self) -> int:
        return sum(int(asset.get("size", 0) or 0) for asset in self.assets)

    def describe(self) -> str:
        size = human_size(self.total_download_size())
        return f"{self.name or self.tag} · {self.published_at[:10]} · {size} of assets"


class GitHubClient:
    """Small, well-behaved client for the official Tixi Voice repository."""

    def __init__(
        self,
        owner: str = GITHUB_OWNER,
        repo: str = GITHUB_REPO,
        *,
        token: str = "",
        timeout: float = 20.0,
        cache_dir: Path | None = None,
        max_retries: int = 2,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self._etag: str = ""
        self._etag_payload: dict[str, Any] | None = None
        self._session: Any = None

    # -- plumbing -----------------------------------------------------------
    @property
    def api_url(self) -> str:
        return f"{API_ROOT}/repos/{self.owner}/{self.repo}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str, *, allow_not_modified: bool = False) -> Any:
        import requests  # noqa: PLC0415

        url = f"{self.api_url}{path}"
        headers = self._headers()
        if self._etag and allow_not_modified:
            headers["If-None-Match"] = self._etag
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(url, headers=headers, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise OfflineError(
                    "Could not reach GitHub. Check your internet connection"
                    + (f" ({exc})" if exc else "")
                    + ". Tixi Voice keeps working offline — only update checks need a connection."
                ) from exc

            if response.status_code == 304 and allow_not_modified:
                return self._etag_payload or {}
            if response.status_code == 404:
                raise ReleaseNotFound(
                    f"The repository {self.owner}/{self.repo} has no such release yet."
                )
            if response.status_code in (403, 429):
                reset = response.headers.get("X-RateLimit-Reset")
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    raise RateLimitError(
                        f"GitHub asked to wait {retry_after}s before the next request.", 
                        reset_at=time.time() + float(retry_after),
                    )
                if reset and response.headers.get("X-RateLimit-Remaining") == "0":
                    raise RateLimitError(
                        "The GitHub API rate limit has been reached. "
                        "Try again later, or download the release manually from the GitHub page.",
                        reset_at=float(reset),
                    )
                raise GitHubError(
                    "GitHub refused the request (403). If this is a private repository, "
                    "check that your token is still valid."
                )
            if response.status_code >= 500 and attempt < self.max_retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise GitHubError(f"GitHub returned HTTP {response.status_code}: {response.text[:200]}")
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise GitHubError("GitHub returned a response that was not valid JSON.") from exc
            etag = response.headers.get("ETag", "")
            if etag:
                self._etag = etag
                self._etag_payload = payload if isinstance(payload, dict) else None
            return payload
        raise GitHubError(str(last_error) if last_error else "The request failed")

    # -- API ----------------------------------------------------------------
    def latest_release(self, *, include_prerelease: bool = False) -> ReleaseInfo:
        """Newest release; pre-releases only when explicitly requested."""
        releases = self.list_releases(limit=20, include_prerelease=include_prerelease)
        if not releases:
            raise ReleaseNotFound(
                "No published release was found for Tixi Voice. "
                "This is expected while the project prepares its first release."
            )
        return releases[0]

    def list_releases(
        self, *, limit: int = 10, include_prerelease: bool = False
    ) -> list[ReleaseInfo]:
        payload = self._get(f"/releases?per_page={max(1, min(100, limit))}")
        if not isinstance(payload, list):
            raise GitHubError("Unexpected response from the GitHub Releases API.")
        releases = [ReleaseInfo.from_api(item) for item in payload if isinstance(item, dict)]
        if not include_prerelease:
            releases = [release for release in releases if not release.prerelease]
        return [release for release in releases if not release.draft]

    def release_by_tag(self, tag: str) -> ReleaseInfo:
        payload = self._get(f"/releases/tags/{tag}")
        return ReleaseInfo.from_api(payload)

    def repository_info(self) -> dict[str, Any]:
        return self._get("")

    def rate_limit(self) -> dict[str, Any]:
        import requests  # noqa: PLC0415

        try:
            response = requests.get(f"{API_ROOT}/rate_limit", headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            return payload.get("resources", {}).get("core", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read the GitHub rate limit", extra={"event": "rate_limit_failed", "error": str(exc)})
            return {}

    def download_asset(
        self,
        asset: dict[str, Any],
        target: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancel: Any = None,
        resume: bool = True,
    ) -> Path:
        """Download a release asset with resume support and no redirect surprises."""
        import requests  # noqa: PLC0415

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        url = str(asset.get("browser_download_url") or asset.get("url") or "")
        if not url:
            raise GitHubError(f"The release asset '{asset.get('name')}' has no download URL.")
        expected = int(asset.get("size", 0) or 0)
        offset = target.stat().st_size if (resume and target.exists()) else 0
        if expected and offset == expected:
            return target
        headers = self._headers()
        if offset and resume:
            headers["Range"] = f"bytes={offset}-"
        with requests.get(url, headers=headers, stream=True, timeout=self.timeout, allow_redirects=True) as response:
            if response.status_code == 416:
                return target
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0) or 0) + (offset if response.status_code == 206 else 0)
            written = offset if response.status_code == 206 else 0
            mode = "ab" if written else "wb"
            with target.open(mode) as handle:
                for block in response.iter_content(1024 * 256):
                    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                        raise GitHubError("The download was cancelled.")
                    if not block:
                        continue
                    handle.write(block)
                    written += len(block)
                    if progress:
                        progress(written, total or expected)
        if expected and target.stat().st_size != expected:
            raise GitHubError(
                f"The downloaded update is {target.stat().st_size} bytes but {expected} were expected."
            )
        return target

    def fetch_text(self, url: str, *, timeout: float | None = None) -> str:
        import requests  # noqa: PLC0415

        response = requests.get(url, headers=self._headers(), timeout=timeout or self.timeout)
        response.raise_for_status()
        return response.text


def human_rate_limit(reset_at: float) -> str:
    remaining = max(0.0, reset_at - time.time())
    if remaining < 60:
        return f"{int(remaining)} seconds"
    return f"{int(remaining / 60)} minutes"
