"""Matches a video to categories and topics.

Text is weighted by where it appears: the title counts three times, tags
twice, chapter titles one and a half, the description once. Each phrase counts
at most three times in the description, so long keyword-stuffed descriptions
don't win. A channel's usual categories give a small nudge.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .config import Category, Taxonomy, Terms

FIELD_WEIGHT = {"title": 3.0, "tags": 2.0, "chapters": 1.5, "desc": 1.0}
CHAPTER = re.compile(r"^\s*\(?(\d{1,2}:)?\d{1,2}:\d{2}\)?\s*[-–—:|]?\s*(.+)$")
URL = re.compile(r"https?://\S+")


@dataclass
class Fields:
    title: str
    tags: str
    chapters: str
    desc: str

    @classmethod
    def of(cls, video: dict) -> "Fields":
        desc = video.get("description") or ""
        chapters, body = [], []
        for line in desc.splitlines():
            m = CHAPTER.match(line)
            if m:
                chapters.append(m.group(2))
            else:
                body.append(line)
        body_text = URL.sub(" ", "\n".join(body))[:2500]
        return cls(
            title=video.get("title") or "",
            tags=" ; ".join(video.get("tags") or []),
            chapters=" ; ".join(chapters),
            desc=body_text,
        )


def chapter_count(description: str | None) -> int:
    return sum(1 for line in (description or "").splitlines() if CHAPTER.match(line))


def score_terms(terms: Terms, f: Fields) -> tuple[float, list[str]]:
    total = 0.0
    hits: list[str] = []
    for rx, weight, name in terms.items:
        matched = False
        for fname, fw in FIELD_WEIGHT.items():
            text = getattr(f, fname)
            if not text:
                continue
            n = len(rx.findall(text))
            if n:
                cap = 1 if fname == "title" else 3
                total += weight * fw * min(n, cap) * (1.0 if fname == "title" else 0.6 if n > 1 else 1.0)
                matched = True
        if matched and weight > 0:
            hits.append(name)
    return total, hits


@dataclass
class Result:
    cats: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    conf: dict[str, float] = field(default_factory=dict)
    security: float = 0.0
    relevance: float = 0.0
    hits: dict[str, list[str]] = field(default_factory=dict)
    rejected_by: str | None = None


def confidence(score: float, scale: float = 9.0) -> float:
    return 1.0 - math.exp(-max(0.0, score) / scale)


def rejected_title(tax: Taxonomy, title: str) -> str | None:
    for rx in tax.reject:
        if rx.search(title or ""):
            return rx.pattern
    return None


def vouched(channel: dict | None) -> bool:
    """Channels whose word we take that a video is about security: the hand-picked seeds and
    anything that earned core. Channels found automatically (often general AI or dev channels
    with a few security videos) and big general channels have to show it in each video."""
    ch = channel or {}
    # Only channels picked by hand. A discovered channel can earn core (polled often, no waiting
    # period) but never this: a general dev conference with a few security talks once did.
    return ch.get("origin") == "seed" and ch.get("tier") in ("core", "watched")


def classify(video: dict, tax: Taxonomy, channel: dict | None = None) -> Result:
    res = Result()
    bad = rejected_title(tax, video.get("title", ""))
    if bad:
        res.rejected_by = bad
        return res
    f = Fields.of(video)
    sec, sec_hits = score_terms(tax.security, f)
    res.security = sec
    res.hits["security"] = sec_hits
    channel_cats = set((channel or {}).get("cats") or [])
    # Big general channels (AWS Events, CS50...) don't vouch for a security angle on their own.
    vouches = vouched(channel)

    cat_scores: dict[str, float] = {}
    topic_scores: dict[str, dict[str, float]] = {}
    for cat in tax.categories:
        base, hits = score_terms(cat.terms, f)
        subs: dict[str, float] = {}
        for t in cat.topics:
            s, th = score_terms(t.terms, f)
            if s > 0:
                subs[t.key] = s
                hits += th
        top = max(subs.values(), default=0.0)
        rest = sum(subs.values()) - top
        score = base + top + 0.3 * rest
        if cat.requires_security_context and not (sec >= 2.0 or (vouches and cat.key in channel_cats)):
            score *= 0.25
        if score > 0:
            # A security-minded channel or a security-flavoured video makes every category a bit more likely.
            score += min(sec, 6.0) * 0.25
        cat_scores[cat.key] = score
        topic_scores[cat.key] = subs
        if hits:
            res.hits[cat.key] = hits

    for key, score in cat_scores.items():
        c = confidence(score)
        if key in channel_cats and score > 0:
            c = min(1.0, c + 0.15)
        res.conf[key] = round(c, 3)

    ranked = sorted(res.conf.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return res
    best = ranked[0][1]
    res.relevance = best
    for key, c in ranked[:2]:
        if c >= 0.35 and c >= 0.6 * best:
            res.cats.append(key)
    for key in res.cats:
        subs = topic_scores.get(key, {})
        if not subs:
            continue
        top = max(subs.values())
        for sk, sv in sorted(subs.items(), key=lambda kv: kv[1], reverse=True)[:2]:
            if sv >= 0.5 * top and sv >= 3.0:
                res.subs.append(sk)
    return res
