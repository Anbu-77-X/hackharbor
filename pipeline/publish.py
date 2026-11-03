"""Writes what the site reads: data/taxonomy.json, index.json, feed.json and one file per video."""
from __future__ import annotations

import json
import re
import shutil
from datetime import timedelta
from pathlib import Path

from . import feed, signals
from .util import VIDEO_ID, age_days, iso, log, now, safe_url

SOURCE_LABEL = {"hn": "Hacker News", "hn_comment": "Hacker News comment", "reddit": "Reddit", "lobsters": "Lobsters", "mastodon": "Mastodon"}
NOISE = re.compile(r"(subscribe|patreon|discord|sponsor|merch|follow me|join (my|the|our)|instagram|twitter|linkedin|tiktok|use code|affiliate|#\w+|https?://\S+|▶|►|👉|🔔|timestamps?:?|chapters?:?)", re.I)
SENT = re.compile(r"(?<=[.!?])\s+")


def _fallback_summary(desc: str | None) -> str:
    lines = []
    for line in (desc or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^\(?\d{1,2}:\d{2}", line) or NOISE.search(line) and len(NOISE.sub("", line).strip()) < 25:
            continue
        lines.append(NOISE.sub("", line).strip(" -–—|:•"))
        if sum(len(x) for x in lines) > 400:
            break
    text = " ".join(x for x in lines if x)
    sentences = [s for s in SENT.split(text) if len(s) > 20]
    out = ""
    for s in sentences[:3]:
        if out and len(out) + len(s) > 420:
            break
        out = f"{out} {s}".strip()
    if len(out) > 460:  # one very long sentence: stop at a word boundary
        out = out[:440].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def _fallback_why(v: dict, channel: dict | None, mentions: list[dict]) -> str:
    hn = [m for m in mentions if m["source"] == "hn" and (m.get("points") or 0) >= 20]
    if hn:
        return f"Discussed on Hacker News ({max(m['points'] for m in hn)} points)."
    reddit = [m for m in mentions if m["source"] == "reddit"]
    if reddit:
        sub = (reddit[0].get("title") or "").split(":")[0]
        return f"Shared on {sub or 'Reddit'}, where people pointed others to it."
    if v.get("gem"):
        return "A small audience so far, and the comments rate it highly."
    if (v.get("praise") or 0) >= 0.7:
        return "Viewers in the comments single it out as especially clear and useful."
    if (channel or {}).get("kind") == "conference":
        return f"A conference talk published by {v.get('channel_title')}."
    if (channel or {}).get("tier") == "core":
        return f"From {v.get('channel_title')}, one of the channels HackHarbor trusts most."
    return ""


def _tags(v: dict, judgement: dict | None) -> list[str]:
    if judgement and judgement.get("tags"):
        return judgement["tags"]
    return []


def creator_playlists(state, videos: list[dict]) -> list[dict]:
    """Playlists imported from trusted channels, best first. A playlist's value is the average
    score of its ten strongest videos, plus a little for size; videos keep the creator's order."""
    import math
    pub = {v["id"]: v for v in videos}
    out = []
    for pl in state.playlists_all():
        ids = []
        for vid in pl["items"]:
            if vid in pub and vid not in ids:
                ids.append(vid)
        if len(ids) < 3:
            continue
        top = sorted((pub[i]["quality"] for i in ids), reverse=True)[:10]
        score = sum(top) / len(top) + 0.25 * math.log(len(ids))
        if not re.match(r"^[A-Za-z0-9_-]{10,64}$", pl["id"] or ""):
            continue
        out.append({"key": "yt-" + pl["id"], "name": pl["title"], "by": pl.get("channel_title") or "",
                    "ids": ids, "kind": "creator", "yt": pl["id"], "score": round(score, 3)})
    out.sort(key=lambda p: p["score"], reverse=True)
    return out


def apply_caps(videos: list[dict], cfg, channels: dict, keep_ids: set | None = None) -> list[dict]:
    lib = cfg.rules.get("library", {})
    per_topic = lib.get("max_classics_per_topic", 150)
    per_ch = lib.get("max_classics_per_channel_topic", 15)
    per_ch_core = lib.get("max_classics_per_channel_topic_core", 40)
    kept, topic_count, ch_count = [], {}, {}
    for v in sorted(videos, key=lambda x: x["quality"], reverse=True):
        cat = cfg.taxonomy.category(v["cats"][0])
        if age_days(v["published_at"]) <= cat.fresh_months * 30.44 or v["id"] in (keep_ids or ()):
            kept.append(v)
            continue
        topic = v["subs"][0] if v["subs"] else v["cats"][0]
        if topic_count.get(topic, 0) >= per_topic:
            continue
        tier = (channels.get(v["channel_id"]) or {}).get("tier")
        cap = per_ch_core if tier == "core" else per_ch
        key = (v["channel_id"], topic)
        if ch_count.get(key, 0) >= cap:
            continue
        topic_count[topic] = topic_count.get(topic, 0) + 1
        ch_count[key] = ch_count.get(key, 0) + 1
        kept.append(v)
    return kept


def _bust_cache(out_dir: Path) -> None:
    """Browsers keep app.js and style.css for a while; a content hash in the URL makes a new
    version load as soon as it's deployed."""
    import hashlib
    page = out_dir / "index.html"
    html = page.read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        digest = hashlib.sha1((out_dir / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{name}"', f'"{name}?v={digest}"')
    page.write_text(html, encoding="utf-8")


def build(state, cfg, site_dir: Path, out_dir: Path, report: dict, sample: bool = False) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(site_dir, out_dir)
    _bust_cache(out_dir)
    data = out_dir / "data"
    (data / "v").mkdir(parents=True, exist_ok=True)

    rows = state.videos_where("status='published'")
    channels = {c["id"]: c for c in state.channels_where()}
    recent_mentions: dict[str, str] = {}
    for m in state.mentions_since(iso(now() - timedelta(days=7))):
        if m["created_at"] and m["created_at"] > recent_mentions.get(m["video_id"], ""):
            recent_mentions[m["video_id"]] = m["created_at"]

    videos = []
    for r in rows:
        if not r.get("cats") or not VIDEO_ID.match(r.get("id") or ""):
            continue
        v = dict(r)
        v["buzz_h"] = feed.hours_since(recent_mentions.get(v["id"])) if v["id"] in recent_mentions else None
        v["p"] = feed.priority(v)
        videos.append(v)
    in_playlists = {vid for pl in state.playlists_all() for vid in pl["items"]}
    videos = apply_caps(videos, cfg, channels, in_playlists)

    home = feed.compose_home(videos, cfg.rules)
    stamp = iso(now())
    for vid in home[:24]:
        state.db.execute("UPDATE videos SET home_last_shown=? WHERE id=?", (stamp, vid))
    lists = [dict(pl, kind="auto") for pl in feed.playlists(videos, cfg.taxonomy)]
    lists = creator_playlists(state, videos) + lists

    index = []
    for v in sorted(videos, key=lambda x: x["p"], reverse=True):
        judgement = state.judged(v["id"])
        mentions = state.mentions(v["id"])
        channel = channels.get(v["channel_id"])
        tags = _tags(v, judgement)
        yt_tags = [t for t in (v.get("tags") or []) if isinstance(t, str) and 2 < len(t) <= 30][:8]
        words = cfg.taxonomy.keywords(f"{v['title']} | {' | '.join(yt_tags)}")
        extra = [w for w in words + [t.lower() for t in yt_tags] if w not in v["title"].lower()]
        index.append({
            "i": v["id"], "t": v["title"], "c": v["channel_title"], "d": (v["published_at"] or "")[:10],
            "l": v["duration_s"], "v": v["views"], "k": v["cats"], "s": v["subs"] or [], "y": v["type"],
            "e": 1 if v["evergreen"] else 0, "m": 1 if v["gem"] else 0, "g": tags, "p": v["p"],
            "w": " | ".join(dict.fromkeys(extra))[:240],  # extra search words: subject terms and tags
        })
        detail = {
            "who": (judgement or {}).get("who") or (v["channel_title"] if (channel or {}).get("kind") == "conference" else ""),
            "summary": (judgement or {}).get("summary") or _fallback_summary(v.get("description")),
            "why": (judgement or {}).get("why") or _fallback_why(v, channel, mentions),
            "ideas": (judgement or {}).get("ideas") or [],
            "tags": tags,
            "read": [dict(x, url=safe_url(x.get("url"))) for x in (v.get("links") or signals.reading_links(v.get("description")))
                     if safe_url(x.get("url"))],
            "discussed": [
                {"source": SOURCE_LABEL.get(m["source"], m["source"]), "title": m.get("title"), "url": safe_url(m["ref"]),
                 "points": m.get("points"), "comments": m.get("comments"), "date": (m.get("created_at") or "")[:10]}
                for m in mentions[:5] if safe_url(m.get("ref"))
            ],
            "quotes": v.get("quotes") or [],
        }
        (data / "v" / f"{v['id']}.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

    public = cfg.taxonomy.public()
    public["longs_min_minutes"] = cfg.rule("longs_min_minutes", default=40)
    (data / "taxonomy.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    (data / "index.json").write_text(json.dumps({"generated": stamp, "sample": sample, "videos": index}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (data / "feed.json").write_text(json.dumps({"generated": stamp, "home": home, "playlists": lists}, ensure_ascii=False), encoding="utf-8")
    counts = {c.key: sum(1 for v in videos if c.key in v["cats"]) for c in cfg.taxonomy.categories}
    meta = {"generated": stamp, "videos": len(videos), "by_category": counts, "channels_watched": sum(1 for c in channels.values() if c.get("tier") in ("core", "watched", "broad"))}
    (data / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    state.commit()
    log.info("published %s videos (%s on Home)", len(videos), len(home))
    report["published"] = meta
    return meta
