"""Ordering: what goes on Home, how categories sort, and the automatic playlists."""
from __future__ import annotations

import math

from .util import age_days, now, parse_time


def hours_since(ts: str | None) -> float | None:
    dt = parse_time(ts)
    return (now() - dt).total_seconds() / 3600 if dt else None


def priority(v: dict) -> float:
    """The rank every list sorts by, highest first.

    The value score (0-10: how on-topic it is, what viewers say in the comments, likes and
    comments against the channel's own normal, the channel's track record, chapters and linked
    research, and community shares) decides almost everything. Newer is better, but only by up
    to +0.8 fading over a few months, plus up to +0.5 while people are discussing it. Videos more
    than three years old come after all newer ones. Views never count, and nothing is random."""
    age = age_days(v["published_at"])
    p = v["quality"] + 0.8 * math.exp(-age / 90)
    if age > 3 * 365.25:
        p -= 10.0  # older than three years: after everything newer, still in value order among themselves
    if v.get("buzz_h") is not None and v["buzz_h"] <= 168:
        p += 0.5 * math.exp(-v["buzz_h"] / 48)
    return round(p, 3)


def compose_home(videos: list[dict], rules: dict) -> list[str]:
    """Home is the whole library in rank order, with strong videos from the last six months
    (score 8 or more) first. The only other change: a channel never shows twice in a row or more
    than twice in any 12, so one prolific channel can't fill the top. A skipped video keeps its
    place at the front of the queue."""
    home = rules.get("home", {})
    window = home.get("channel_window", 12)
    per_channel = home.get("max_per_channel_in_window", 2)
    recent_days = home.get("recent_days", 182)
    recent_min = home.get("recent_min_score", 8.0)

    def tier(v: dict) -> int:
        # Strong videos from the last six months lead Home; a recent but ordinary one doesn't
        # jump the queue, it takes its normal place by rank.
        return 0 if age_days(v["published_at"]) <= recent_days and v["quality"] >= recent_min else 1

    queue = sorted(videos, key=lambda v: (tier(v), -v["p"], v["id"]))
    out: list[dict] = []
    while queue:
        recent = out[-(window - 1):] if window > 1 else []
        last = out[-1]["channel_id"] if out else None
        pick = next((i for i, v in enumerate(queue[:200]) if v["channel_id"] != last
                     and sum(1 for x in recent if x["channel_id"] == v["channel_id"]) < per_channel), None)
        if pick is None:  # few channels left: at least avoid the same one twice in a row
            pick = next((i for i, v in enumerate(queue[:200]) if v["channel_id"] != last), 0)
        out.append(queue.pop(pick))
    return [v["id"] for v in out]


def playlists(videos: list[dict], taxonomy) -> list[dict]:
    lists = []
    week = sorted([v for v in videos if age_days(v["published_at"]) <= 7], key=lambda v: v["p"], reverse=True)
    if len(week) >= 3:
        lists.append({"key": "new-this-week", "name": "New this week", "ids": [v["id"] for v in week[:30]]})
    gems = sorted([v for v in videos if v.get("gem")], key=lambda v: v["quality"], reverse=True)
    if len(gems) >= 4:
        lists.append({"key": "hidden-gems", "name": "Hidden gems", "ids": [v["id"] for v in gems[:30]]})
    for cat in taxonomy.categories:
        groups = [(t.key, t.name, [v for v in videos if t.key in v["subs"]]) for t in cat.topics]
        if not cat.topics:
            groups = [(cat.key, cat.name, [v for v in videos if cat.key in v["cats"]])]
        for key, name, items in groups:
            if len(items) >= 6:
                best = sorted(items, key=lambda v: v["quality"], reverse=True)[:15]
                lists.append({"key": f"best-{key}", "name": f"Best of {name}", "ids": [v["id"] for v in best]})
    return lists
