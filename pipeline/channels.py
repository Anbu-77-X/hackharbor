"""The channel registry: seeds, polling schedule, discovery and promotion.

Tiers:
  core        trusted; polled most often; new videos skip the waiting period
  watched     polled every few hours; videos judged like any other
  broad       big general channels; only clearly relevant videos pass
  occasional  not polled; its videos get in only when people share them or search finds them
  candidate   discovered, waiting to be evaluated
  rejected    evaluated and not about security (looked at again after a few months)
"""
from __future__ import annotations

from datetime import timedelta

from . import signals
from .classify import classify
from .quota import QuotaExhausted
from .score import gate
from .util import age_days, clamp, iso, log, median, now, parse_time

POLLED = ("core", "watched", "broad")


def sync_seeds(state, cfg, yt, report: dict, resolve_limit: int = 150, name_search_limit: int = 3) -> None:
    unresolved = []
    resolved = 0
    name_searches = 0
    need_details = []
    week = now().strftime("%G-%V")
    for seed in cfg.channels:
        cid = seed.get("id")
        handle = seed.get("handle")
        if not cid and handle:
            cid = state.get(f"handle:{handle.lower()}")
        failed_week = state.get(f"handle_fail:{(handle or '').lower()}")
        if not cid and handle and yt is not None and resolved < resolve_limit and failed_week != week:
            info = None
            try:
                info = yt.channel_by_handle(handle)
                resolved += 1
                if not info and seed.get("name") and name_searches < name_search_limit:
                    name_searches += 1
                    info = yt.find_channel(seed["name"])
            except QuotaExhausted:
                info = None
            if not info:
                state.put(f"handle_fail:{handle.lower()}", week)  # try again next week
            if info:
                cid = info["id"]
                state.put(f"handle:{handle.lower()}", cid)
                existing = state.channel(cid) or {}
                state.save_channel({**existing, **{k: info[k] for k in ("id", "title", "uploads", "subscribers", "video_count")},
                                    "handle": handle, "added_at": existing.get("added_at") or iso(now())})
        if not cid:
            unresolved.append(seed.get("name") or handle)
            continue
        existing = state.channel(cid) or {}
        state.save_channel({
            **existing,
            "id": cid,
            "handle": handle or existing.get("handle"),
            "title": existing.get("title") or seed.get("name"),
            "tier": seed.get("tier", "watched"),
            "kind": seed.get("kind", "creator"),
            "cats": seed.get("cats", []),
            "origin": "seed",
            "added_at": existing.get("added_at") or iso(now()),
        })
        if not existing.get("uploads"):
            need_details.append(cid)
    if need_details and yt is not None:
        try:
            for info in yt.channels(need_details):
                ch = state.channel(info["id"]) or {}
                state.save_channel({**ch, "uploads": info["uploads"], "subscribers": info["subscribers"],
                                    "video_count": info["video_count"], "title": info["title"] or ch.get("title")})
        except QuotaExhausted:
            pass
    state.commit()
    report["seeds_unresolved"] = unresolved


def due(state, limit: int) -> list[dict]:
    stamp = iso(now())
    rows = state.channels_where(
        f"tier IN ({','.join('?' for _ in POLLED)}) AND (next_check IS NULL OR next_check<=?) ORDER BY "
        "CASE tier WHEN 'core' THEN 0 WHEN 'watched' THEN 1 ELSE 2 END, next_check",
        [*POLLED, stamp],
    )
    return rows[:limit]


def after_poll(state, channel: dict, entries: list[dict] | None, rules: dict) -> None:
    ch = dict(channel)
    if entries is None:
        ch["rss_fail"] = (ch.get("rss_fail") or 0) + 1
        ch["next_check"] = iso(now() + timedelta(hours=min(24, 2 * ch["rss_fail"])))
        state.save_channel(ch)
        return
    ch["rss_fail"] = 0
    times = sorted((parse_time(e["published_at"]) for e in entries if e.get("published_at")), reverse=True)
    times = [t for t in times if t]
    if times:
        ch["last_upload"] = iso(times[0])
        if len(times) >= 3:
            span_h = (times[0] - times[-1]).total_seconds() / 3600
            ch["avg_gap_h"] = round(span_h / (len(times) - 1), 1)
    gap = ch.get("avg_gap_h") or 72
    tier = ch.get("tier")
    dormant_days = (rules.get("discovery", {}).get("dormant_after_months", 18)) * 30.44
    if ch.get("last_upload") and age_days(ch["last_upload"]) > dormant_days:
        hours = 24 * 7
    elif tier == "core":
        hours = clamp(gap / 3, 2, 6)
    elif tier == "watched":
        hours = clamp(gap / 3, 2, 24)
    else:
        hours = clamp(gap / 2, 4, 24)
    ch["next_check"] = iso(now() + timedelta(hours=hours))
    state.save_channel(ch)


def note_channel(state, channel_id: str | None, via: str, title: str | None = None) -> None:
    if not channel_id or state.channel(channel_id):
        return
    state.save_channel({"id": channel_id, "title": title, "tier": "candidate", "kind": "creator", "cats": [],
                        "origin": via, "added_at": iso(now()), "eval": {}})


def channel_stats(state, channel_id: str, cache: dict) -> dict:
    if channel_id in cache:
        return cache[channel_id]
    rows = state.db.execute(
        "SELECT views, likes, published_at FROM videos WHERE channel_id=? AND hydrated_at IS NOT NULL AND views>0 "
        "ORDER BY published_at DESC LIMIT 60", (channel_id,)).fetchall()
    older = [r["views"] for r in rows if age_days(r["published_at"]) >= 14]
    ratios = [(r["likes"] or 0) / r["views"] for r in rows if r["likes"] is not None and r["views"] >= 200]
    stats = {
        "median_views": median(older) if len(older) >= 5 else None,
        "like_ratio": median(ratios) if len(ratios) >= 5 else None,
    }
    cache[channel_id] = stats
    return stats


def evaluate_candidates(state, cfg, yt, limit: int, report: dict) -> list[str]:
    """Looks at a few discovered channels' recent uploads and decides whether to watch them.
    Returns ids of recent videos worth processing."""
    rules = cfg.rules.get("discovery", {})
    stale = iso(now() - timedelta(days=rules.get("reevaluate_rejected_days", 120)))
    rows = state.channels_where(
        "(tier='candidate' AND evaluated_at IS NULL) OR (tier='rejected' AND evaluated_at<?) ORDER BY added_at LIMIT ?",
        [stale, limit])
    if not rows:
        return []
    found: list[str] = []
    try:
        infos = {i["id"]: i for i in yt.channels([r["id"] for r in rows])}
    except QuotaExhausted:
        return []
    results = []
    for ch in rows:
        info = infos.get(ch["id"])
        if not info or not info.get("uploads"):
            state.save_channel({**ch, "tier": "rejected", "evaluated_at": iso(now()), "eval": {"note": "channel unavailable"}})
            continue
        try:
            items, _ = yt.uploads_page(info["uploads"])
            vids = yt.videos([vid for vid, _ in items[:40]])
        except QuotaExhausted:
            break
        on_topic, scores, cat_count = 0, [], {}
        considered = 0
        for v in vids.values():
            if gate(v, cfg):
                continue
            considered += 1
            res = classify(v, cfg.taxonomy, None)
            if res.cats and res.relevance >= 0.5:
                on_topic += 1
                for c in res.cats:
                    cat_count[c] = cat_count.get(c, 0) + 1
                views = max(1, v.get("views") or 0)
                like = ((v.get("likes") or 0) + 1) / views
                quick = 2 + 8 * (0.45 * res.relevance + 0.35 * clamp(like / 0.05) + 0.2 * signals.content_quality(v, signals.reading_links(v.get("description")), None))
                scores.append(quick)
                if age_days(v.get("published_at")) <= 120:
                    found.append(v["id"])
        share = on_topic / considered if considered else 0.0
        med = median(scores) or 0.0
        recent = info.get("video_count", 0) and vids and min(age_days(v.get("published_at")) for v in vids.values()) <= 365
        if share >= rules.get("promote_relevance_share", 0.4) and med >= rules.get("promote_median_score", 6.5) and recent:
            tier = "watched"
        elif share >= rules.get("occasional_relevance_share", 0.15):
            tier = "occasional"
        else:
            tier = "rejected"
        cats = [c for c, _ in sorted(cat_count.items(), key=lambda kv: kv[1], reverse=True)[:3]]
        state.save_channel({
            **ch, "title": info["title"], "handle": info.get("handle"), "uploads": info["uploads"],
            "subscribers": info["subscribers"], "video_count": info["video_count"], "tier": tier, "cats": cats,
            "evaluated_at": iso(now()), "eval": {"on_topic_share": round(share, 2), "median_quick": round(med, 2), "sampled": considered},
        })
        results.append(f"{info['title']}: {tier} ({share:.0%} on topic)")
    state.commit()
    report["channels_evaluated"] = results
    return found


def promote(state, cfg, report: dict) -> None:
    rules = cfg.rules.get("discovery", {}).get("core_promotion", {})
    changes = []
    rows = state.db.execute(
        "SELECT channel_id, COUNT(*) AS n, AVG(quality) AS q FROM videos WHERE status='published' GROUP BY channel_id").fetchall()
    for r in rows:
        ch = state.channel(r["channel_id"])
        if not ch:
            continue
        ev = dict(ch.get("eval") or {})
        ev["published"], ev["mean_quality"] = r["n"], round(r["q"] or 0, 2)
        tier = ch.get("tier")
        if tier in ("watched", "occasional", "candidate") and ch.get("origin") != "seed" \
                and r["n"] >= rules.get("min_published", 10) and (r["q"] or 0) >= rules.get("min_mean_score", 8.3):
            tier = "core"
            changes.append(f"{ch.get('title')}: promoted to core")
        elif tier in ("occasional", "candidate") and r["n"] >= 3 and (r["q"] or 0) >= 7.8:
            tier = "watched"
            changes.append(f"{ch.get('title')}: now watched")
        state.save_channel({**ch, "eval": ev, "tier": tier})
    state.commit()
    report["channel_changes"] = changes


def discover_featured(state, yt, report: dict, per_run: int = 4) -> None:
    """Channels that trusted channels feature on their pages become candidates."""
    month = now().strftime("%Y-%m")
    rows = [c for c in state.channels_where("tier='core'") if (c.get("eval") or {}).get("featured_checked") != month][:per_run]
    added = 0
    for ch in rows:
        try:
            ids = yt.featured_channels(ch["id"])
        except QuotaExhausted:
            break
        for cid in ids:
            if not state.channel(cid):
                note_channel(state, cid, f"featured by {ch.get('title')}")
                added += 1
        ev = dict(ch.get("eval") or {})
        ev["featured_checked"] = month
        state.save_channel({**ch, "eval": ev})
    state.commit()
    report["featured_candidates"] = added
