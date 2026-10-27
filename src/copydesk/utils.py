"""Small helpers shared across the app. Nothing here touches the database."""

from __future__ import annotations

import math
import re
import secrets
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def utcnow() -> datetime:
    """Current UTC time as a naive datetime. All timestamps are stored this way."""
    return datetime.now(UTC).replace(tzinfo=None)


def get_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def to_local(value: datetime | None, tz_name: str | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(get_timezone(tz_name))


def local_to_utc(value: datetime, tz_name: str | None) -> datetime:
    """Interpret a naive datetime typed in the site's time zone and convert it to naive UTC."""
    aware = value.replace(tzinfo=get_timezone(tz_name))
    return aware.astimezone(UTC).replace(tzinfo=None)


def slugify(text: str, max_length: int = 80, fallback: str = "untitled") -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0] or text[:max_length]
    return text.strip("-") or fallback


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(EMAIL_RE.match(value))


def email_domain(value: str) -> str:
    return value.rsplit("@", 1)[-1].lower() if "@" in value else ""


def domain_matches(email: str, domains: Iterable[str]) -> bool:
    """True if the email's domain equals one of ``domains`` or is a subdomain of one."""
    domain = email_domain(email)
    for allowed in domains:
        allowed = allowed.strip().lower().lstrip("@")
        if allowed and (domain == allowed or domain.endswith("." + allowed)):
            return True
    return False


def random_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def word_count(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text or ""))


def reading_minutes(words: int, wpm: int = 230) -> int:
    return max(1, math.ceil(words / wpm)) if words else 1


def truncate(text: str, length: int = 200, suffix: str = "…") -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0].rstrip(",.;:—-")
    return cut + suffix


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def parse_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_lines(value: str | None) -> list[str]:
    """Split a textarea into non-empty, stripped lines (commas also separate items)."""
    items = re.split(r"[\n,]", value or "")
    seen: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def parse_datetime_local(value: str | None) -> datetime | None:
    """Parse the value of an <input type="datetime-local">."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def is_safe_redirect(target: str | None) -> bool:
    """Only allow redirects to paths on this site (no scheme, no host, no //)."""
    if not target or "\\" in target or any(ord(c) < 32 or ord(c) == 127 for c in target):
        return False
    try:
        parts = urlsplit(target)
    except ValueError:
        return False
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//")


def humanize_delta(delta: timedelta) -> str:
    seconds = int(abs(delta.total_seconds()))
    if seconds < 60:
        return "just now"
    for unit, size in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "just now"


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count:,} {word}"


def join_names(names: Sequence[str]) -> str:
    """["Ana"] -> "Ana"; ["Ana", "Ben"] -> "Ana and Ben"; three or more -> "Ana, Ben and Cy"."""
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


@dataclass
class Pagination:
    items: list[Any]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.per_page)) if self.per_page else 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first_index(self) -> int:
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last_index(self) -> int:
        return min(self.page * self.per_page, self.total)
