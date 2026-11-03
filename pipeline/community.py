"""Places where people share and discuss videos: Hacker News, Lobsters, Reddit, Mastodon.

Each source returns "mentions": a video id plus where it was mentioned and how
much attention that got. Mentions find new videos, resurface old ones people
are talking about, and feed the community part of the score.
"""
from __future__ import annotations

import os
import time
from defusedxml import ElementTree as ET  # feeds are remote input: no entity expansion
from datetime import datetime, timezone

import requests

from .util import iso, log, now, video_ids_in

UA = "Mozilla/5.0 (compatible; HackHarborBot/1.0)"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def _ts(value) -> str | None:
    try:
        return iso(datetime.fromtimestamp(int(value), tz=timezone.utc))
    except (TypeError, ValueError):
        return None


class HackerNews:
    API = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self):
        self.http = _session()

    def _search(self, params: dict, pages: int = 5) -> list[dict]:
        hits: list[dict] = []
        for page in range(pages):
            r = self.http.get(self.API, params={**params, "page": page, "hitsPerPage": 200}, timeout=25)
            r.raise_for_status()
            data = r.json()
            hits.extend(data.get("hits", []))
            if page + 1 >= data.get("nbPages", 0):
                break
        return hits

    def window(self, start_ts: int, end_ts: int, pages: int = 5) -> list[dict]:
        """Stories and comments linking YouTube between two unix times."""
        out: list[dict] = []
        numeric = f"created_at_i>{start_ts},created_at_i<={end_ts}"
        for q in ("youtube", "youtu.be"):
            for hit in self._search({"query": q, "restrictSearchableAttributes": "url", "tags": "story",
                                     "numericFilters": numeric}, pages):
                for vid in video_ids_in(hit.get("url")):
                    out.append({
                        "video_id": vid, "source": "hn",
                        "ref": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                        "title": hit.get("title"), "points": hit.get("points") or 0,
                        "comments": hit.get("num_comments") or 0,
                        "created_at": _ts(hit.get("created_at_i")),
                    })
        for hit in self._search({"query": "youtube", "tags": "comment", "numericFilters": numeric}, pages):
            for vid in video_ids_in(hit.get("comment_text")):
                out.append({
                    "video_id": vid, "source": "hn_comment",
                    "ref": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "title": hit.get("story_title"), "points": None, "comments": None,
                    "created_at": _ts(hit.get("created_at_i")),
                })
        return out


class Lobsters:
    def __init__(self, tags: list[str]):
        self.tags = tags
        self.http = _session()

    def recent(self) -> list[dict]:
        out = []
        failed = []
        for tag in self.tags:
            r = self.http.get(f"https://lobste.rs/t/{tag}.json", timeout=20)
            if not r.ok:
                failed.append(tag)
                continue
            for story in r.json():
                for vid in video_ids_in(story.get("url")):
                    out.append({
                        "video_id": vid, "source": "lobsters",
                        "ref": story.get("comments_url") or story.get("short_id_url"),
                        "title": story.get("title"), "points": story.get("score") or 0,
                        "comments": story.get("comment_count") or 0,
                        "created_at": story.get("created_at"),
                    })
            time.sleep(0 if os.environ.get("HARBOR_FAST") else 1)
        if failed and len(failed) == len(self.tags):
            raise RuntimeError(f"lobsters refused every tag ({', '.join(failed)})")
        return out


class Reddit:
    def __init__(self, subreddits: list[str], client_id: str | None = None, client_secret: str | None = None):
        self.subs = subreddits
        self.http = _session()
        self.token = None
        if client_id and client_secret:
            try:
                r = self.http.post("https://www.reddit.com/api/v1/access_token",
                                   data={"grant_type": "client_credentials"},
                                   auth=(client_id, client_secret), timeout=20)
                r.raise_for_status()
                self.token = r.json().get("access_token")
            except Exception as exc:
                log.warning("reddit login failed, using public feeds: %s", exc)

    def _api(self, sub: str, listing: str) -> list[dict]:
        url = f"https://oauth.reddit.com/r/{sub}/{listing}"
        params = {"limit": 100}
        if listing == "top":
            params["t"] = "week"
        r = self.http.get(url, params=params, headers={"Authorization": f"bearer {self.token}"}, timeout=20)
        r.raise_for_status()
        out = []
        for child in r.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            for vid in video_ids_in(f"{d.get('url', '')} {d.get('selftext', '')}"):
                out.append({
                    "video_id": vid, "source": "reddit",
                    "ref": "https://www.reddit.com" + d.get("permalink", ""),
                    "title": f"r/{sub}: {d.get('title', '')}", "points": d.get("score") or 0,
                    "comments": d.get("num_comments") or 0, "created_at": _ts(d.get("created_utc")),
                })
        return out

    def _rss(self, sub: str, listing: str) -> list[dict]:
        suffix = "top/.rss?t=week" if listing == "top" else "new/.rss"
        r = self.http.get(f"https://www.reddit.com/r/{sub}/{suffix}", timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in root.findall("a:entry", ns):
            content = entry.findtext("a:content", default="", namespaces=ns)
            link = entry.find("a:link", ns)
            for vid in video_ids_in(content):
                out.append({
                    "video_id": vid, "source": "reddit",
                    "ref": link.get("href") if link is not None else None,
                    "title": f"r/{sub}: {entry.findtext('a:title', default='', namespaces=ns)}",
                    "points": None, "comments": None,
                    "created_at": entry.findtext("a:updated", namespaces=ns),
                })
        return out

    def recent(self) -> list[dict]:
        out: list[dict] = []
        failures = 0
        for sub in self.subs:
            for listing in ("new", "top"):
                try:
                    out.extend(self._api(sub, listing) if self.token else self._rss(sub, listing))
                except Exception as exc:
                    failures += 1
                    log.debug("reddit r/%s %s: %s", sub, listing, exc)
                time.sleep(0 if os.environ.get("HARBOR_FAST") else 1.2)
            if failures >= 6 and not out:
                raise RuntimeError("reddit is refusing requests from this runner")
        return out


class Mastodon:
    def __init__(self, instances: list[dict]):
        self.instances = instances
        self.http = _session()

    def recent(self) -> list[dict]:
        out = []
        closed = []
        for inst in self.instances:
            host = inst["host"]
            for tag in inst.get("tags", []):
                r = self.http.get(f"https://{host}/api/v1/timelines/tag/{tag}", params={"limit": 40}, timeout=20)
                if r.status_code in (401, 403, 422):
                    closed.append(host)  # this instance wants a login for tag timelines
                    break
                if not r.ok:
                    continue
                for status in r.json():
                    text = (status.get("content") or "") + " " + ((status.get("card") or {}).get("url") or "")
                    for vid in video_ids_in(text):
                        out.append({
                            "video_id": vid, "source": "mastodon", "ref": status.get("url"),
                            "title": f"#{tag} on {host}",
                            "points": (status.get("favourites_count") or 0) + 2 * (status.get("reblogs_count") or 0),
                            "comments": status.get("replies_count") or 0,
                            "created_at": status.get("created_at"),
                        })
                time.sleep(0 if os.environ.get("HARBOR_FAST") else 0.5)
        if closed and len(closed) == len(self.instances):
            raise RuntimeError(f"tag timelines need a login on {', '.join(closed)}")
        return out


def recent_mentions(sources: dict, state, env: dict) -> tuple[list[dict], dict]:
    """New mentions since the last run from every enabled source, plus a per-source status."""
    mentions: list[dict] = []
    status: dict[str, str] = {}
    end = int(now().timestamp())

    if sources.get("hacker_news", {}).get("enabled"):
        start = state.get("hn:last", end - 6 * 3600)
        try:
            found = HackerNews().window(start - 3600, end)  # an hour of overlap so nothing slips between runs
            mentions.extend(found)
            state.put("hn:last", end)
            status["hacker_news"] = f"ok ({len(found)})"
        except Exception as exc:
            status["hacker_news"] = f"failed: {exc}"

    lob = sources.get("lobsters", {})
    if lob.get("enabled"):
        try:
            found = Lobsters(lob.get("tags", ["security"])).recent()
            mentions.extend(found)
            status["lobsters"] = f"ok ({len(found)})"
        except Exception as exc:
            status["lobsters"] = f"failed: {exc}"

    red = sources.get("reddit", {})
    if red.get("enabled"):
        try:
            found = Reddit(red.get("subreddits", []), env.get("REDDIT_CLIENT_ID"), env.get("REDDIT_CLIENT_SECRET")).recent()
            mentions.extend(found)
            status["reddit"] = f"ok ({len(found)})"
        except Exception as exc:
            status["reddit"] = f"failed: {exc}"

    mas = sources.get("mastodon", {})
    if mas.get("enabled"):
        try:
            found = Mastodon(mas.get("instances", [])).recent()
            mentions.extend(found)
            status["mastodon"] = f"ok ({len(found)})"
        except Exception as exc:
            status["mastodon"] = f"failed: {exc}"

    return mentions, status


def hn_backfill_step(state, from_year: int) -> tuple[list[dict], str]:
    """One month of old Hacker News links per run, walking back in time."""
    cursor = state.get("hn:backfill")
    today = now()
    if cursor is None:
        year, month = today.year, today.month - 1
        if month == 0:
            year, month = year - 1, 12
    else:
        year, month = cursor
    if year < from_year:
        return [], "done"
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    found = HackerNews().window(int(start.timestamp()), int(end.timestamp()), pages=5)
    prev = (year, month - 1) if month > 1 else (year - 1, 12)
    state.put("hn:backfill", list(prev))
    return found, f"{year}-{month:02d} ({len(found)})"
