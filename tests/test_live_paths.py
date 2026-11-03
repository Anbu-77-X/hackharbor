"""Runs the online code paths against canned HTTP answers, so the parts the offline run skips
(seeds, RSS, search, communities, discovery, backfill) are exercised too."""
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import run as runmod  # noqa: E402

VID = {"id": "kgkRih2MQtU", "snippet": {"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa", "channelTitle": "Black Hat",
       "title": "HTTP Desync Attacks: Smashing into the Cell Next Door", "description": "Request smuggling talk.\nSlides: https://www.blackhat.com/x.pdf",
       "publishedAt": "2020-01-15T18:00:00Z", "liveBroadcastContent": "none", "defaultAudioLanguage": "en"},
       "contentDetails": {"duration": "PT49M58S"}, "statistics": {"viewCount": "120000", "likeCount": "5000", "commentCount": "300"},
       "status": {"embeddable": True, "privacyStatus": "public"}}
NEW = json.loads(json.dumps(VID))
NEW["id"] = "voTHFdL9S2k"
NEW["snippet"]["title"] = "Kerberoasting and NTLM relay attacks in Active Directory"
NEW["snippet"]["publishedAt"] = "2026-10-20T10:00:00Z"
RSS = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<entry><yt:videoId>voTHFdL9S2k</yt:videoId><title>x</title><published>2026-10-20T10:00:00+00:00</published></entry>
<entry><yt:videoId>kgkRih2MQtU</yt:videoId><title>y</title><published>2020-01-15T18:00:00+00:00</published></entry></feed>"""


class R:
    def __init__(self, data=None, status=200, text=None):
        self.status_code = status
        self._data = data
        self.content = (text or json.dumps(data)).encode()
        self.text = text or json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def fake_get(self, url, params=None, **kw):
    params = params or {}
    if "feeds/videos.xml" in url:
        return R(text=RSS)
    if url.endswith("/channels"):
        if params.get("forHandle") and params["forHandle"] != "@BlackHatOfficialYT":
            return R({"items": []})
        return R({"items": [{"id": "UCaaaaaaaaaaaaaaaaaaaaaa", "snippet": {"title": "Black Hat", "customUrl": "@blackhat"},
                             "contentDetails": {"relatedPlaylists": {"uploads": "UUaaaaaaaaaaaaaaaaaaaaaa"}},
                             "statistics": {"subscriberCount": "300000", "videoCount": "2000"}}]})
    if url.endswith("/videos"):
        ids = params["id"].split(",")
        return R({"items": [x for x in (VID, NEW) if x["id"] in ids]})
    if url.endswith("/commentThreads"):
        return R({"items": [{"snippet": {"topLevelComment": {"snippet": {"textDisplay": "Best explanation of this I've seen, thank you so much!", "likeCount": 30}}, "totalReplyCount": 1}}]})
    if url.endswith("/search"):
        if params.get("type") == "channel":
            return R({"items": []})
        return R({"items": [{"id": {"videoId": "voTHFdL9S2k"}, "snippet": {"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}}]})
    if url.endswith("/playlistItems"):
        return R({"items": [{"contentDetails": {"videoId": "kgkRih2MQtU", "videoPublishedAt": "2020-01-15T18:00:00Z"}}]})
    if url.endswith("/channelSections"):
        return R({"items": [{"contentDetails": {"channels": ["UCbbbbbbbbbbbbbbbbbbbbbb"]}}]})
    if "hn.algolia.com" in url:
        if params.get("tags") == "story":
            return R({"hits": [{"objectID": "1", "url": "https://www.youtube.com/watch?v=kgkRih2MQtU", "title": "Desync talk", "points": 250, "num_comments": 80, "created_at_i": 1790000000}], "nbPages": 1})
        return R({"hits": [{"objectID": "2", "comment_text": "Watch https://youtu.be/kgkRih2MQtU", "story_title": "Ask HN", "created_at_i": 1790000000}], "nbPages": 1})
    if "lobste.rs" in url:
        return R([{"url": "https://www.youtube.com/watch?v=kgkRih2MQtU", "title": "t", "score": 20, "comment_count": 3, "created_at": "2026-11-01T00:00:00Z", "comments_url": "https://lobste.rs/s/x"}])
    if "reddit.com" in url:
        return R(status=403, text="blocked")
    if "/api/v1/timelines/tag/" in url:
        return R([])
    return R(status=404, text="")


def test_live_run_with_canned_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test")
    monkeypatch.setenv("HARBOR_FAST", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with mock.patch("requests.Session.get", fake_get), mock.patch("requests.Session.post", lambda *a, **k: R(status=500, text="")):
        code = runmod.main(["--state", str(tmp_path / "state"), "--out", str(tmp_path / "site"), "--budget-minutes", "20"])
    assert code == 0
    report = json.loads((tmp_path / "state" / "report.json").read_text())
    failed = {k: v for k, v in report["stages"].items() if isinstance(v, dict) and not v.get("ok")}
    assert not failed, failed
    assert report["sources"]["hacker_news"].startswith("ok")
    assert report["sources"]["reddit"].startswith("failed")      # blocked source is reported, run carries on
    index = json.loads((tmp_path / "site" / "data" / "index.json").read_text())
    assert any(v["i"] == "kgkRih2MQtU" for v in index["videos"]), report["totals"]
    detail = json.loads((tmp_path / "site" / "data" / "v" / "kgkRih2MQtU.json").read_text())
    assert detail["discussed"] and detail["quotes"]
