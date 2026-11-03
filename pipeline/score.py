"""Turns a classified video and its signals into a score and a decision."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import signals
from .classify import Result
from .config import Config
from .util import age_days, clamp

TIER_BASE = {"core": 0.85, "watched": 0.65, "broad": 0.55, "occasional": 0.5, "candidate": 0.5}


@dataclass
class Assessment:
    decision: str                 # publish | reject | wait | review
    reason: str = ""
    cats: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    vtype: str = "explainer"
    evergreen: bool = False
    quality: float = 0.0
    heuristic: float = 0.0
    gem: bool = False
    relevance: float = 0.0
    classic: bool = False


def reputation(channel: dict | None) -> float:
    if not channel:
        return 0.45
    base = TIER_BASE.get(channel.get("tier") or "", 0.45)
    ev = channel.get("eval") or {}
    n = ev.get("published", 0) or 0
    mean = ev.get("mean_quality")
    if n and mean:
        return round((base * 3 + n * (mean / 10)) / (3 + n), 3)
    return base


def gate(video: dict, cfg: Config) -> str | None:
    """Cheap checks that need no API calls. Returns a reason to reject, or None."""
    langs = set(cfg.rule("languages", default=["en"]))
    if not video.get("public", True):
        return "not public"
    if not video.get("embeddable", 1):
        return "embedding disabled"
    if video.get("age_restricted"):
        return "age restricted"
    if video.get("kids"):
        return "made for kids"
    dur = video.get("duration_s") or 0
    if dur < cfg.rule("min_duration_seconds", default=240):
        return "too short"
    if dur > cfg.rule("max_duration_seconds", default=43200):
        return "too long"
    lang = signals.language(video)
    if lang not in langs:
        return f"language: {lang}"
    return None


def assess(video: dict, channel: dict | None, cls: Result, praise: float | None, mentions: list[dict],
           judgement: dict | None, cfg: Config, baselines: dict) -> Assessment:
    praise_known = praise is not None
    praise = 0.5 if praise is None else praise
    tax = cfg.taxonomy
    q = cfg.rules.get("quality", {})
    tier = (channel or {}).get("tier") or "candidate"

    if cls.rejected_by:
        return Assessment("reject", f"title matches '{cls.rejected_by}'")

    cats, subs, relevance = list(cls.cats), list(cls.subs), cls.relevance
    if judgement:
        if not judgement.get("relevant"):
            return Assessment("reject", "judge: not useful for practitioners", cats=cats)
        cats = judgement.get("cats") or cats
        subs = judgement.get("subs") if judgement.get("subs") is not None else subs
        relevance = max(relevance, 0.75)

    min_rel = q.get("broad_min_relevance", 0.7) if tier == "broad" else q.get("min_relevance", 0.45)
    if not cats or relevance < min_rel:
        return Assessment("reject", "off topic", cats=cats, relevance=relevance)

    vtype = (judgement or {}).get("type") or signals.video_type(video, channel)
    ever = (judgement or {}).get("evergreen") if judgement else signals.evergreen(video, vtype, cats)
    links = signals.reading_links(video.get("description"))
    content = signals.content_quality(video, links, channel)
    eng = signals.engagement(video, baselines.get("channel", {}), baselines.get("like", 0.035), baselines.get("comment", 0.0025))
    comm = signals.community(mentions)
    cb = signals.clickbait(video.get("title") or "")

    w = q.get("weights", {})
    comps = {"relevance": relevance, "engagement": eng, "reputation": reputation(channel), "content": content}
    if praise_known:
        comps["praise"] = praise  # unknown comments shouldn't drag a video toward the middle
    total_w = sum(w.get(k, 0) for k in comps) or 1.0
    base = sum(w.get(k, 0) * v for k, v in comps.items()) / total_w
    heuristic = 2.0 + 8.0 * (base ** 0.8)
    heuristic += q.get("community_bonus_max", 1.0) * comm
    heuristic -= q.get("clickbait_penalty", 2.5) * cb
    heuristic = round(clamp(heuristic, 0, 10), 2)

    quality = heuristic
    if judgement:
        jw = q.get("judge_weight", 0.55)
        if not praise_known:
            jw = min(0.85, jw + 0.2)  # no comments to go on: lean more on the reader who looked at it
        quality = (1 - jw) * heuristic + jw * judgement["value"]
        if judgement.get("clickbait"):
            quality -= 1.0
    quality = round(clamp(quality, 0, 10), 2)

    gem_cfg = cfg.rules.get("gem", {})
    gem = quality >= gem_cfg.get("min_score", 8.3) and (
        (video.get("views") or 0) <= gem_cfg.get("max_views", 15000)
        or ((channel or {}).get("subscribers") or 10**9) <= gem_cfg.get("max_subscribers", 25000)
    )

    result = Assessment("reject", cats=cats, subs=subs, vtype=vtype, evergreen=bool(ever), quality=quality,
                        heuristic=heuristic, gem=gem, relevance=relevance)

    primary = tax.category(cats[0])
    days = age_days(video.get("published_at"))
    fresh = days <= primary.fresh_months * 30.44
    if fresh:
        need = q.get("publish_new", 7.0)
        if tier == "core":
            need -= q.get("core_allowance", 0.5)
        elif tier == "broad":
            need += q.get("broad_extra", 0.7)
        if quality >= need:
            result.decision = "publish"
        elif quality >= need - q.get("borderline_margin", 0.5) and days < 30:
            result.decision, result.reason = "review", f"borderline ({quality} vs {need})"
        else:
            result.reason = f"score {quality} below {need}"
        return result

    result.classic = True
    if video.get("_in_playlist") and tier in ("core", "watched"):
        # In a playlist a trusted channel curated (a machine series, a course, a conference
        # track): what's in it was chosen to keep, so age alone doesn't rule it out.
        need = q.get("playlist_min", 7.5)
        if quality >= need:
            result.decision = "publish"
        else:
            result.reason = f"playlist video score {quality} below {need}"
        return result
    if days > primary.classic_max_years * 365.25:
        result.reason = f"older than {primary.classic_max_years:g} years for {primary.name}"
        return result
    if not result.evergreen:
        result.reason = "old and not evergreen"
        return result
    need = primary.classic_min_score + (q.get("broad_extra", 0.7) if tier == "broad" else 0.0)
    if quality >= need:
        result.decision = "publish"
    else:
        result.reason = f"classic score {quality} below {need}"
    return result
