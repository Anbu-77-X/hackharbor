"""Runs the pipeline without network, from a folder of fixtures. Used by the tests
and to build the sample data for the preview.

Fixture folder:
  channels.json   channels with tier, kind and cats
  videos.json     videos in the shape youtube.normalize_video returns
  judge.json      optional: AI judge answers to use instead of calling Gemini
  comments.json   optional: {video_id: [{text, likes}]}
"""
from __future__ import annotations

import json
from pathlib import Path

from .util import iso, now


def _load(folder: Path, name: str, default):
    path = folder / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class OfflineYouTube:
    def __init__(self, folder: str | Path, quota):
        self.folder = Path(folder)
        self.quota = quota
        self._videos = {v["id"]: v for v in _load(self.folder, "videos.json", [])}
        self._comments = _load(self.folder, "comments.json", {})
        self._channels = {c["id"]: c for c in _load(self.folder, "channels.json", [])}

    def rss(self, channel_id):
        items = [v for v in self._videos.values() if v["channel_id"] == channel_id]
        items.sort(key=lambda v: v["published_at"], reverse=True)
        return [{"id": v["id"], "title": v["title"], "published_at": v["published_at"]} for v in items[:15]]

    def uploads_page(self, playlist_id, token=None):
        cid = playlist_id.replace("UU", "UC", 1)
        items = [(v["id"], v["published_at"]) for v in self._videos.values() if v["channel_id"] == cid]
        return items, None

    def videos(self, ids):
        return {i: dict(self._videos[i]) for i in ids if i in self._videos}

    def comments(self, video_id, n=50):
        return self._comments.get(video_id, [])

    def search(self, *args, **kwargs):
        return []

    def channels(self, ids):
        return [{**c, "uploads": "UU" + c["id"][2:], "subscribers": c.get("subscribers"), "video_count": c.get("video_count")}
                for i, c in self._channels.items() if i in ids]

    def channel_by_handle(self, handle):
        return None

    def find_channel(self, name):
        return None

    def featured_channels(self, channel_id):
        return []


def seed_offline(state, folder: str | Path) -> None:
    folder = Path(folder)
    for c in _load(folder, "channels.json", []):
        if not state.channel(c["id"]):
            state.save_channel({
                "id": c["id"], "title": c["title"], "tier": c.get("tier", "watched"), "kind": c.get("kind", "creator"),
                "cats": c.get("cats", []), "origin": "seed", "uploads": "UU" + c["id"][2:],
                "subscribers": c.get("subscribers"), "added_at": iso(now()), "backfill_done": 1,
            })
    for vid, data in _load(folder, "judge.json", {}).items():
        state.save_judgement(vid, data, "fixture")
    for m in _load(folder, "mentions.json", []):
        state.add_mention(m)
    state.commit()
