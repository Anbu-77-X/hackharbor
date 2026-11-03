"""Small helpers shared by the pipeline."""
from __future__ import annotations

import html
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, TypeVar

log = logging.getLogger("harbor")
T = TypeVar("T")

YT_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^\s\"'<>]*?&)?v=|live/|shorts/|embed/|v/)|youtu\.be/|youtube-nocookie\.com/embed/)"
    r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])"
)
URL = re.compile(r"https?://[^\s<>\"')\]]+")


SECRET_ENV = ("YOUTUBE_API_KEY", "GEMINI_API_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "GITHUB_TOKEN", "GH_TOKEN")
SECRET_SHAPES = re.compile(r"(AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,}|"
                           r"(?<=[?&]key=)[^&\s\"']+|(?<=x-access-token:)[^@\s]+)")


def redact(text: str) -> str:
    """Removes secrets from text that may be logged or published (the run report is public)."""
    import os
    out = str(text)
    for name in SECRET_ENV:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            out = out.replace(value, "[redacted]")
    return SECRET_SHAPES.sub("[redacted]", out)


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - logging must never fail
            pass
        return True


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_Redact())


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def safe_url(url) -> str | None:
    """Only plain http(s) links reach the site."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    u = url.strip()
    if not re.match(r"^https?://[^\s/?#]+", u, re.I) or any(ch in u for ch in "\x00\r\n\t<>\"'`\\"):
        return None
    return u


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_days(published: str | None, ref: datetime | None = None) -> float:
    dt = parse_time(published)
    if not dt:
        return 9999.0
    return max(0.0, ((ref or now()) - dt).total_seconds() / 86400)


_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(value: str | None) -> int:
    """ISO 8601 duration (PT1H2M3S) to seconds."""
    if not value:
        return 0
    m = _DUR.fullmatch(value)
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def video_ids_in(text: str | None) -> list[str]:
    if not text:
        return []
    text = html.unescape(text)
    seen: list[str] = []
    for vid in YT_ID.findall(text):
        if vid not in seen:
            seen.append(vid)
    return seen


def chunks(items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def median(values: Iterable[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def hours_from_now(hours: float) -> str:
    return iso(now() + timedelta(hours=hours))
