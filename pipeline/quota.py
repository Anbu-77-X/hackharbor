"""Keeps YouTube API use inside the free daily quota, spread across the day's runs."""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from .util import log

PACIFIC = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight Pacific


class QuotaExhausted(Exception):
    pass


class Quota:
    def __init__(self, state, daily_units: int, search_per_day: int, run_every_hours: float):
        self.state = state
        self.daily_units = daily_units
        self.search_per_day = search_per_day
        now_pt = datetime.now(PACIFIC)
        self.day = now_pt.strftime("%Y-%m-%d")
        used = state.get(f"quota:{self.day}", {"units": 0, "search": 0})
        self.used_units = used.get("units", 0)
        self.used_search = used.get("search", 0)
        hours_left = 24 - (now_pt.hour + now_pt.minute / 60)
        runs_left = max(1, math.ceil(hours_left / max(0.5, run_every_hours)))
        left = max(0, daily_units - self.used_units)
        # This run may use its share of what's left today, but never less than a floor
        # so a late run can still check new uploads.
        self.run_units = min(left, max(150, left // runs_left))
        self.run_search = min(max(0, search_per_day - self.used_search), max(1, math.ceil((search_per_day - self.used_search) / runs_left)))
        self.spent_units = 0
        self.spent_search = 0
        self.exhausted = left <= 0
        log.info("quota: %s units left today, %s for this run; %s searches for this run", left, self.run_units, self.run_search)

    def can(self, units: int = 1, search: int = 0) -> bool:
        if self.exhausted:
            return False
        if self.spent_units + units > self.run_units:
            return False
        if search and self.spent_search + search > self.run_search:
            return False
        return True

    def units_left(self) -> int:
        return 0 if self.exhausted else max(0, self.run_units - self.spent_units)

    def spend(self, units: int = 1, search: int = 0) -> None:
        self.spent_units += units
        self.spent_search += search

    def mark_exhausted(self) -> None:
        self.exhausted = True

    def save(self) -> None:
        self.state.put(f"quota:{self.day}", {
            "units": self.used_units + self.spent_units,
            "search": self.used_search + self.spent_search,
        })

    def summary(self) -> dict:
        return {"day": self.day, "units": self.spent_units, "searches": self.spent_search, "exhausted": self.exhausted}
