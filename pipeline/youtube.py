"""YouTube Data API v3 and channel RSS feeds."""
from __future__ import annotations

import time
from defusedxml import ElementTree as ET  # feeds are remote input: no entity expansion

import requests

from .quota import Quota, QuotaExhausted
from .util import chunks, log, parse_duration

API = "https://www.googleapis.com/youtube/v3/"
RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
UA = "Mozilla/5.0 (compatible; HackHarborBot/1.0; +https://github.com)"


class CommentsDisabled(Exception):
    pass


def _int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def normalize_video(item: dict) -> dict:
    sn = item.get("snippet", {})
    cd = item.get("contentDetails", {})
    st = item.get("statistics", {})
    status = item.get("status", {})
    live = item.get("liveStreamingDetails") or {}
    rating = (cd.get("contentRating") or {}).get("ytRating")
    return {
        "id": item["id"],
        "channel_id": sn.get("channelId"),
        "channel_title": sn.get("channelTitle"),
        "title": sn.get("title") or "",
        "description": (sn.get("description") or "")[:6000],
        "published_at": sn.get("publishedAt"),
        "duration_s": parse_duration(cd.get("duration")),
        "views": _int(st.get("viewCount")) or 0,
        "likes": _int(st.get("likeCount")),
        "comments": _int(st.get("commentCount")),
        "tags": sn.get("tags") or [],
        "audio_lang": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
        "live": 1 if (live.get("actualStartTime") or sn.get("liveBroadcastContent") == "live") else 0,
        "upcoming": sn.get("liveBroadcastContent") == "upcoming",
        "live_now": sn.get("liveBroadcastContent") == "live",
        "embeddable": 1 if status.get("embeddable", True) else 0,
        "public": status.get("privacyStatus", "public") == "public",
        "age_restricted": 1 if rating == "ytAgeRestricted" else 0,
        "kids": bool(status.get("madeForKids")),
    }


class YouTube:
    def __init__(self, api_key: str, quota: Quota, search_unit_cost: int = 100):
        self.key = api_key
        self.quota = quota
        self.search_cost = search_unit_cost
        self.http = requests.Session()
        self.http.headers["User-Agent"] = UA

    # -- API plumbing ------------------------------------------------------
    def _get(self, endpoint: str, params: dict, units: int = 1, search: int = 0) -> dict | None:
        if not self.quota.can(units, search):
            raise QuotaExhausted(endpoint)
        for attempt in range(3):
            try:
                # The key goes in a header, never the URL, so it can't end up in an error message.
                r = self.http.get(API + endpoint, params=params, headers={"X-Goog-Api-Key": self.key}, timeout=25)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise
                log.warning("youtube %s: %s, retrying", endpoint, exc)
                time.sleep(2 * (attempt + 1))
                continue
            self.quota.spend(units, search)
            if r.status_code == 200:
                return r.json()
            reason = ""
            try:
                reason = r.json()["error"]["errors"][0]["reason"]
            except Exception:
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                self.quota.mark_exhausted()
                raise QuotaExhausted(reason)
            if reason == "commentsDisabled":
                raise CommentsDisabled()
            if r.status_code in (404, 400) or reason in ("videoNotFound", "channelNotFound", "playlistNotFound", "forbidden"):
                return None
            if r.status_code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            log.warning("youtube %s failed: %s %s", endpoint, r.status_code, reason)
            return None
        return None

    # -- channels ----------------------------------------------------------
    @staticmethod
    def _channel(item: dict) -> dict:
        sn = item.get("snippet", {})
        st = item.get("statistics", {})
        return {
            "id": item["id"],
            "title": sn.get("title"),
            "handle": sn.get("customUrl"),
            "uploads": (item.get("contentDetails", {}).get("relatedPlaylists") or {}).get("uploads"),
            "subscribers": _int(st.get("subscriberCount")),
            "video_count": _int(st.get("videoCount")),
            "description": sn.get("description") or "",
            "country": sn.get("country"),
            "lang": sn.get("defaultLanguage"),
        }

    def channel_by_handle(self, handle: str) -> dict | None:
        data = self._get("channels", {"part": "snippet,contentDetails,statistics", "forHandle": handle})
        items = (data or {}).get("items") or []
        return self._channel(items[0]) if items else None

    def channels(self, ids: list[str]) -> list[dict]:
        out = []
        for part in chunks(ids, 50):
            data = self._get("channels", {"part": "snippet,contentDetails,statistics", "id": ",".join(part), "maxResults": 50})
            out.extend(self._channel(i) for i in (data or {}).get("items", []))
        return out

    def find_channel(self, name: str) -> dict | None:
        """Search fallback for a seed whose handle didn't resolve."""
        data = self._get("search", {"part": "snippet", "type": "channel", "q": name, "maxResults": 5},
                         units=self.search_cost, search=1)
        items = (data or {}).get("items") or []
        wanted = name.lower().strip()
        for it in items:
            if (it["snippet"].get("title") or "").lower().strip() == wanted:
                cid = it["snippet"]["channelId"]
                found = self.channels([cid])
                return found[0] if found else None
        return None

    def featured_channels(self, channel_id: str) -> list[str]:
        data = self._get("channelSections", {"part": "contentDetails", "channelId": channel_id})
        out: list[str] = []
        for sec in (data or {}).get("items", []):
            for cid in (sec.get("contentDetails") or {}).get("channels", []) or []:
                if cid not in out and cid != channel_id:
                    out.append(cid)
        return out

    # -- videos ------------------------------------------------------------
    def videos(self, ids: list[str]) -> dict[str, dict]:
        """Full details for up to any number of ids, 50 per call (1 unit each)."""
        out: dict[str, dict] = {}
        for part in chunks(ids, 50):
            data = self._get("videos", {
                "part": "snippet,contentDetails,statistics,status,liveStreamingDetails",
                "id": ",".join(part), "maxResults": 50,
            })
            for item in (data or {}).get("items", []):
                out[item["id"]] = normalize_video(item)
        return out

    def uploads_page(self, playlist_id: str, token: str | None = None) -> tuple[list[tuple[str, str]], str | None]:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = self._get("playlistItems", params)
        if not data:
            return [], None
        items = [
            (it["contentDetails"]["videoId"], it["contentDetails"].get("videoPublishedAt"))
            for it in data.get("items", []) if it.get("contentDetails", {}).get("videoId")
        ]
        return items, data.get("nextPageToken")

    def channel_playlists(self, channel_id: str, pages: int = 2) -> list[dict]:
        """A channel's public playlists, newest first: id, title and how many videos."""
        out, token = [], None
        for _ in range(pages):
            params = {"part": "snippet,contentDetails", "channelId": channel_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._get("playlists", params)
            for it in (data or {}).get("items", []):
                out.append({"id": it["id"], "title": it.get("snippet", {}).get("title") or "",
                            "count": it.get("contentDetails", {}).get("itemCount", 0)})
            token = (data or {}).get("nextPageToken")
            if not token:
                break
        return out

    def comments(self, video_id: str, n: int = 50) -> list[dict]:
        data = self._get("commentThreads", {
            "part": "snippet", "videoId": video_id, "order": "relevance",
            "maxResults": min(100, n), "textFormat": "plainText",
        })
        out = []
        for it in (data or {}).get("items", []):
            top = it["snippet"]["topLevelComment"]["snippet"]
            out.append({
                "text": top.get("textDisplay") or top.get("textOriginal") or "",
                "likes": top.get("likeCount", 0),
                "author": top.get("authorDisplayName"),
                "replies": it["snippet"].get("totalReplyCount", 0),
            })
        return out

    def search(self, query: str, published_after: str | None = None, order: str = "date",
               duration: str | None = None, language: str | None = None) -> list[tuple[str, str]]:
        params = {"part": "snippet", "type": "video", "q": query, "maxResults": 50, "order": order,
                  "safeSearch": "none"}
        if published_after:
            params["publishedAfter"] = published_after
        if duration:
            params["videoDuration"] = duration
        if language:
            params["relevanceLanguage"] = language
        data = self._get("search", params, units=self.search_cost, search=1)
        return [
            (it["id"]["videoId"], it["snippet"]["channelId"])
            for it in (data or {}).get("items", []) if it.get("id", {}).get("videoId")
        ]

    # -- RSS (free, no quota) ------------------------------------------------
    def rss(self, channel_id: str) -> list[dict] | None:
        """Latest ~15 uploads. None if the feed couldn't be read."""
        try:
            r = self.http.get(RSS.format(channel_id), timeout=20)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        try:
            root = ET.fromstring(r.content)
        except (ET.ParseError, ValueError):  # malformed, or a feed that tries entity tricks
            return None
        out = []
        for entry in root.findall("a:entry", NS):
            vid = entry.findtext("yt:videoId", namespaces=NS)
            if not vid:
                continue
            out.append({
                "id": vid,
                "title": entry.findtext("a:title", default="", namespaces=NS),
                "published_at": entry.findtext("a:published", namespaces=NS),
            })
        return out
