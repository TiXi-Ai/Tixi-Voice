"""Formatting helpers shared by every view (sizes, durations, dates, counts)."""

from __future__ import annotations

from datetime import datetime, timedelta

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(size_bytes: float, *, precision: int | None = None) -> str:
    """``1536`` -> ``"1.5 KB"``."""
    value = float(size_bytes or 0)
    negative = value < 0
    value = abs(value)
    index = 0
    while value >= 1024 and index < len(_UNITS) - 1:
        value /= 1024.0
        index += 1
    if precision is None:
        precision = 0 if index == 0 else (1 if value < 100 else 0)
    text = f"{value:.{precision}f} {_UNITS[index]}"
    return f"-{text}" if negative else text


def human_duration(seconds: float, *, short: bool = False) -> str:
    """``95`` -> ``"1:35"`` (or ``"1 min 35 s"`` when not short)."""
    seconds = max(0.0, float(seconds or 0))
    if short:
        minutes, remainder = divmod(int(round(seconds)), 60)
        if minutes >= 60:
            hours, minutes = divmod(minutes, 60)
            return f"{hours}:{minutes:02d}:{remainder:02d}"
        return f"{minutes}:{remainder:02d}"
    if seconds < 1:
        return f"{int(seconds * 1000)} ms"
    if seconds < 60:
        return f"{seconds:.1f} s" if seconds < 10 else f"{int(round(seconds))} s"
    minutes, remainder = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {remainder:02d} s" if remainder else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if minutes else f"{hours} h"


def human_time_ago(value: str | datetime | None) -> str:
    """Relative time for history rows ("3 minutes ago")."""
    moment = _as_datetime(value)
    if moment is None:
        return ""
    delta = datetime.now() - moment
    seconds = delta.total_seconds()
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def human_datetime(value: str | datetime | None, *, with_time: bool = True) -> str:
    moment = _as_datetime(value)
    if moment is None:
        return ""
    if with_time:
        return moment.strftime("%Y-%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d")


def human_count(value: int, noun: str, plural: str = "") -> str:
    text = f"{value:,} {noun if value == 1 else (plural or noun + 's')}"
    return text


def human_rate(bytes_per_second: float) -> str:
    return f"{human_size(bytes_per_second)}/s"


def human_percent(fraction: float, *, digits: int = 0) -> str:
    return f"{max(0.0, min(1.0, fraction)) * 100:.{digits}f}%"


def truncate(text: str, length: int = 80, *, ellipsis: str = "…") -> str:
    text = text or ""
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)].rstrip() + ellipsis


def pluralise(count: int, singular: str, plural: str = "") -> str:
    return singular if count == 1 else (plural or singular + "s")


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(text.replace("Z", ""), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def day_label(value: str | datetime | None) -> str:
    """``"Today"``, ``"Yesterday"`` or a date, for grouping library items."""
    moment = _as_datetime(value)
    if moment is None:
        return "Unknown date"
    today = datetime.now().date()
    if moment.date() == today:
        return "Today"
    if moment.date() == today - timedelta(days=1):
        return "Yesterday"
    if moment.year == today.year:
        return moment.strftime("%d %B")
    return moment.strftime("%d %B %Y")


MONTH_NAMES_FA = (
    "", "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
