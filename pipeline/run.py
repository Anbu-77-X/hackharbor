"""One pipeline run. GitHub Actions starts this every two hours.

Stages, each isolated so one failing source doesn't stop the rest:
  1. seeds       sync config/channels.yml into the registry
  2. poll        read RSS for channels that are due
  3. community   collect links from Hacker News, Lobsters, Reddit, Mastodon
  4. search      rotate through topic searches for new videos and channels
  5. hydrate     fetch details for every id seen for the first time
  6. judge       gates, classification, comments, AI judge, score, decision
  7. refresh     re-score published videos as their numbers settle
  8. discover    evaluate new channels, check featured channels, promote
  9. backfill    walk channel archives and old Hacker News for classics
 10. publish     write the site data
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from . import channels as reg
from . import community, publish, signals
from .classify import classify, vouched
from .config import load
from .judge import Judge
from .quota import Quota, QuotaExhausted
from .score import assess, gate
from .state import State
from .util import age_days, iso, log, median, now, setup_logging, redact
from .youtube import CommentsDisabled, YouTube


class Run:
    def __init__(self, cfg, state: State, yt, judge: Judge, quota: Quota, budget_s: int, env: dict, offline: bool = False):
        self.cfg = cfg
        self.state = state
        self.yt = yt
        self.judge = judge
        self.quota = quota
        self.env = env
        self.offline = offline
        self.deadline = time.monotonic() + budget_s
        self.report: dict = {"started": iso(now()), "stages": {}, "sources": {}}
        self.ch_cache: dict = {}

    def time_left(self) -> float:
        return self.deadline - time.monotonic()

    def stage(self, name: str, fn, *, optional: bool = True, min_seconds: int = 60) -> None:
        if optional and self.time_left() < min_seconds:
            self.report["stages"][name] = "skipped: out of time"
            return
        t0 = time.monotonic()
        try:
            result = fn()
            self.report["stages"][name] = {"ok": True, "seconds": round(time.monotonic() - t0, 1), **(result or {})}
        except QuotaExhausted:
            self.report["stages"][name] = {"ok": True, "note": "quota used up for this run"}
        except Exception as exc:  # noqa: BLE001 - one stage must not take the run down
            log.exception("stage %s failed", name)
            self.report["stages"][name] = {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:300]}
        self.state.commit()

    def migrate(self) -> dict:
        """One-off fixes to the stored state when the rules change."""
        done = set(self.state.get("migrations", []) or [])
        out = {}
        if "recent-archives-v1" not in done:
            # Recent uploads were wrongly held to the "channel's best" archive cut.
            cutoff = iso(now() - timedelta(days=self.cfg.rule("backfill", "always_judge_days", default=540)))
            cur = self.state.db.execute(
                "UPDATE videos SET status='hydrated', reason=NULL WHERE status='rejected' "
                "AND reason=\"not among the channel's best\" AND published_at>=?", (cutoff,))
            out["requeued_recent"] = cur.rowcount
            done.add("recent-archives-v1")
        if "security-context-v1" not in done:
            # AI and Cloud now need a security angle, and language is checked from the text too.
            cur = self.state.db.execute("UPDATE videos SET reason='recheck' WHERE status='published'")
            out["rechecked"] = cur.rowcount
            done.add("security-context-v1")
        if "vouched-channels-v1" not in done:
            # Only hand-picked and core channels vouch for a security angle now.
            cur = self.state.db.execute("UPDATE videos SET reason='recheck' WHERE status='published'")
            out["rechecked_again"] = cur.rowcount
            done.add("vouched-channels-v1")
        if "says-security-v3" not in done:
            done.discard("says-security-v1")
        if "says-security-v1" not in done:
            # Apply the stricter rule to everything already published, right away.
            dropped = 0
            for v in self.state.videos_where("status='published'"):
                ch = self.state.channel(v["channel_id"]) if v.get("channel_id") else None
                if vouched(ch):
                    continue
                res = classify(v, self.cfg.taxonomy, ch)
                if (ch or {}).get("tier") == "rejected" or not self._says_security(v, res, self.state.mentions(v["id"])):
                    self._reject(v, "off topic (no security angle)", {"rejected": 0})
                    dropped += 1
            out["dropped_off_topic"] = dropped
            done.update({"says-security-v1", "says-security-v3"})
        self.state.put("migrations", sorted(done))
        return out

    # 2 -------------------------------------------------------------------------
    def poll(self) -> dict:
        from concurrent.futures import ThreadPoolExecutor
        limit = self.cfg.rule("youtube", "max_channel_checks_per_run", default=400)
        due = reg.due(self.state, limit)
        workers = self.cfg.rule("youtube", "rss_workers", default=8)
        new = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            feeds = list(pool.map(lambda c: (c, self.yt.rss(c["id"])), due))
        for ch, entries in feeds:
            if entries is None and ch.get("uploads") and self.quota.can(1):
                try:  # RSS failed: the uploads playlist costs one unit and gives the same thing
                    items, _ = self.yt.uploads_page(ch["uploads"])
                    entries = [{"id": vid, "published_at": ts} for vid, ts in items[:15]]
                except QuotaExhausted:
                    entries = None
            if entries is None:
                failed += 1
            reg.after_poll(self.state, ch, entries, self.cfg.rules)
            for e in entries or []:
                if self.state.add_candidate(e["id"], "channel", ch["id"]):
                    new += 1
        self.report["sources"]["channels"] = f"{len(due) - failed}/{len(due)} feeds read"
        self.report["rss_ok_share"] = round((len(due) - failed) / len(due), 2) if due else 1.0
        return {"checked": len(due), "failed": failed, "new_videos": new}

    # 3 -------------------------------------------------------------------------
    def community(self) -> dict:
        mentions, status = community.recent_mentions(self.cfg.sources, self.state, self.env)
        self.report["sources"].update(status)
        new = self._take_mentions(mentions, "community")
        return {"mentions": len(mentions), "new_videos": new}

    def _take_mentions(self, mentions: list[dict], source: str) -> int:
        new = 0
        for m in mentions:
            self.state.add_mention(m)
            if self.state.add_candidate(m["video_id"], m["source"]):
                new += 1
            else:
                row = self.state.video(m["video_id"])
                reason = (row or {}).get("reason") or ""
                if row and row["status"] == "rejected" and (reason.startswith(("score", "classic score", "off topic"))):
                    # people are talking about something we turned down: fetch it again and take another look
                    self.state.set_status(m["video_id"], "new", "mentioned again")
        return new

    # 4 -------------------------------------------------------------------------
    def search(self) -> dict:
        if not self.cfg.sources.get("youtube_search", {}).get("enabled", True):
            return {"note": "disabled"}
        queries = []
        for cat in self.cfg.taxonomy.categories:
            for t in cat.topics:
                queries += [(q, "en") for q in t.search]
        src = self.cfg.sources.get("youtube_search", {})
        queries += [(q, "en") for q in src.get("queries_en", [])]
        queries += [(q, "ar") for q in src.get("queries_ar", [])]
        if not queries:
            return {}
        classics = src.get("classic_queries", [])
        pos = self.state.get("search:pos", 0)
        done, new = 0, 0
        cost = self.cfg.rule("youtube", "search_unit_cost", default=100)
        # Searches may use at most half of this run's units; the rest is for details, comments and archives.
        keep = self.quota.units_left() // 2
        while self.quota.can(cost, 1) and self.quota.units_left() - cost >= keep:
            q, lang = queries[pos % len(queries)]
            pos += 1
            # Searches alternate: a topic over the last few days, then a bug class or technique
            # across all time (the best-known explanations of SSRF, IDOR, Frida...).
            duration = "long" if pos % 4 < 2 else "medium"
            if pos % 2 == 0 and classics:
                q, lang = classics[(pos // 2) % len(classics)], "en"
                after, order = None, "relevance"
            else:
                after, order = iso(now() - timedelta(days=4)), "date"
            for vid, cid in self.yt.search(q, after, order, duration, lang):
                if self.state.add_candidate(vid, "search", cid):
                    new += 1
            done += 1
        self.state.put("search:pos", pos % max(1, len(queries) * max(1, len(classics)) * 2))
        return {"searches": done, "new_videos": new}

    # 5 -------------------------------------------------------------------------
    def hydrate(self, statuses=("new",), limit: int = 2500) -> dict:
        rows = self.state.videos_where(f"status IN ({','.join('?' for _ in statuses)}) ORDER BY first_seen DESC LIMIT ?", [*statuses, limit])
        ids = [r["id"] for r in rows]
        got = 0
        for i in range(0, len(ids), 50):
            part = ids[i:i + 50]
            details = self.yt.videos(part)
            for vid in part:
                d = details.get(vid)
                if not d:
                    self.state.set_status(vid, "gone", "not available")
                    continue
                d = {k: v for k, v in d.items() if k in STORED}
                d.update({"status": "hydrated", "hydrated_at": iso(now())})
                self.state.save_video(d)
                got += 1
        return {"hydrated": got}

    # 6 -------------------------------------------------------------------------
    def baselines(self) -> dict:
        cached = self.report.get("_baselines")
        if cached:
            return cached
        rows = self.state.db.execute("SELECT views, likes, comments FROM videos WHERE status='published' AND views>500").fetchall()
        likes = [(r["likes"] or 0) / r["views"] for r in rows if r["likes"] is not None]
        comms = [(r["comments"] or 0) / r["views"] for r in rows if r["comments"] is not None]
        b = {"like": median(likes) if len(likes) >= 30 else 0.035, "comment": median(comms) if len(comms) >= 30 else 0.0025}
        self.report["_baselines"] = b
        return b

    def judge_pending(self, limit: int = 3000) -> dict:
        stamp = iso(now())
        rows = self.state.videos_where(
            "(status='hydrated') OR (status='review' AND next_check<=?) OR (status='published' AND reason='recheck') "
            "ORDER BY published_at DESC LIMIT ?", [stamp, limit])
        # Comments never take more than 60% of what's left, so archives and re-checks still get a turn.
        comments_left = min(self.cfg.rule("youtube", "comments_per_run", default=80), int(self.quota.units_left() * 0.6))
        maturation_h = self.cfg.rule("maturation_hours", default=72)
        judge_saturated = bool(self.judge and self.judge.key and self.judge.daily_left <= 0)
        ready = []
        counts = {"rejected": 0, "waiting": 0, "published": 0, "review": 0, "deferred": 0}

        for v in rows:
            if self.time_left() < 240:
                break
            ch = self.state.channel(v["channel_id"]) if v.get("channel_id") else None
            why = gate(v, self.cfg)
            if why:
                self._reject(v, why, counts)
                continue
            res = classify(v, self.cfg.taxonomy, ch)
            mentions = self.state.mentions(v["id"])
            judged_before = self.state.judged(v["id"])
            if res.rejected_by:
                self._reject(v, f"title matches '{res.rejected_by}'", counts)
                continue
            trusted = vouched(ch)
            # Cheap cut before spending comments or the judge on it. Trusted channels get the
            # benefit of the doubt: their titles don't always say what the talk is about.
            floor = 0.12 if trusted else 0.3
            if res.relevance < floor and not mentions and not judged_before:
                self._reject(v, "off topic", counts)
                continue
            # Big general channels and channels found automatically post plenty that isn't
            # security; their videos must say it plainly, unless a security community shared them.
            if not trusted and not self._says_security(v, res, mentions):
                self._reject(v, "off topic (no security angle)", counts)
                continue
            primary = self.cfg.taxonomy.category(res.cats[0]) if res.cats else None
            days = age_days(v["published_at"])
            fresh = primary is None or days <= primary.fresh_months * 30.44
            tier = (ch or {}).get("tier")
            if fresh and days * 24 < maturation_h and tier != "core" and v["status"] == "hydrated":
                wake = iso(now() + timedelta(hours=maturation_h - days * 24))
                self.state.save_video({"id": v["id"], "status": "maturing", "next_check": wake})
                counts["waiting"] += 1
                continue
            if not fresh and not res.cats and not judged_before:
                self._reject(v, "off topic", counts)
                continue
            in_playlist = "playlist" in (v.get("sources") or [])
            v["_in_playlist"] = in_playlist
            if not fresh and signals.timely(v.get("title")) and not mentions and not judged_before and not in_playlist:
                self._reject(v, "old and about something that has moved on", counts)
                continue
            live = v["status"] == "published"
            if not fresh and judge_saturated and not judged_before and not live:
                counts["deferred"] += 1  # classics can wait for tomorrow's judge allowance
                continue
            # Comments are read while this run's allowance lasts; after that the video is judged
            # on everything else (the score leaves praise out rather than guessing it), and its
            # comments are read at the 3-day re-score if it gets in.
            comments = None
            if comments_left > 0 and v.get("praise") is None and (v.get("comments") or 0) > 0:
                try:
                    comments = self.yt.comments(v["id"], 50)
                    comments_left -= 1
                except CommentsDisabled:
                    comments = []
                except QuotaExhausted:
                    comments_left = 0
            if comments is not None:
                p, quotes = signals.praise(comments)
                v["praise"], v["quotes"] = p, quotes
            v["_comments"] = comments or []
            v["_cats"], v["_subs"] = res.cats, res.subs
            ready.append((v, ch, res, mentions))

        judged = self.judge.run([r[0] for r in ready]) if self.judge else {}
        for v, ch, res, mentions in ready:
            stats = reg.channel_stats(self.state, v["channel_id"], self.ch_cache) if v.get("channel_id") else {}
            v["age_days"] = age_days(v["published_at"])
            a = assess(v, ch, res, v.get("praise"), mentions,
                       judged.get(v["id"]), self.cfg, {**self.baselines(), "channel": stats})
            self._apply(v, a, counts)
            if a.decision == "publish" and (not ch or ch.get("tier") in ("candidate", None)):
                reg.note_channel(self.state, v["channel_id"], "published video", v.get("channel_title"))
        return {**counts, "judged_by_ai": sum(1 for v, *_ in ready if v["id"] in judged)}

    def _says_security(self, v: dict, res, mentions: list[dict]) -> bool:
        """For channels we don't vouch for: the title or tags must name a security subject, or the
        whole text must be thick with it. A passing "security" in a description isn't enough."""
        if any(m["source"] in ("reddit", "mastodon") for m in mentions):
            return True  # shared in a security subreddit or under a security tag
        # The title has to say it. Tags are often the same long list on every upload, and a long
        # description (a two-hour podcast's show notes) mentions everything at some point.
        head = v.get("title") or ""
        tax = self.cfg.taxonomy
        return tax.security_score(head) >= 1.0 or tax.names_security_topic(head)

    def _reject(self, v: dict, reason: str, counts: dict) -> None:
        # Keep only what's needed to recognise it next time; the state file stays small.
        self.state.save_video({"id": v["id"], "status": "rejected", "reason": reason, "scored_at": iso(now()),
                               "description": None, "tags": None})
        counts["rejected"] += 1

    def _apply(self, v: dict, a, counts: dict) -> None:
        base = {
            "id": v["id"], "cats": a.cats, "subs": a.subs, "type": a.vtype, "evergreen": 1 if a.evergreen else 0,
            "quality": a.quality, "heuristic": a.heuristic, "gem": 1 if a.gem else 0, "relevance": a.relevance,
            "praise": v.get("praise"), "quotes": v.get("quotes") or [], "scored_at": iso(now()),
            "links": signals.reading_links(v.get("description")),
        }
        if a.decision == "publish":
            age = age_days(v["published_at"])
            later = [d for d in self.cfg.rule("refresh", "rescore_at_days", default=[3, 7, 30]) if d > age]
            wait = (later[0] - age) if later else self.cfg.rule("refresh", "stats_every_days", default=7)
            base.update({"status": "published", "reason": None, "next_check": iso(now() + timedelta(days=max(0.5, wait))),
                         "published_since": v.get("published_since") or iso(now())})
            counts["published"] += 1
        elif a.decision == "review":
            base.update({"status": "review", "reason": a.reason, "next_check": iso(now() + timedelta(days=7))})
            counts["review"] += 1
        else:
            base.update({"status": "rejected", "reason": a.reason, "description": None, "tags": None, "quotes": [], "links": []})
            counts["rejected"] += 1
        self.state.save_video(base)

    # 7 -------------------------------------------------------------------------
    def refresh(self, limit: int = 1500) -> dict:
        """Fresh numbers for videos that are due: published ones on their re-score schedule,
        borderline ones after a week, and new ones whose waiting period is over."""
        rows = self.state.videos_where(
            "status IN ('published','review','maturing') AND next_check<=? ORDER BY next_check LIMIT ?", [iso(now()), limit])
        previous = {r["id"]: r["status"] for r in rows}
        ids = list(previous)
        removed = 0
        for i in range(0, len(ids), 50):
            part = ids[i:i + 50]
            details = self.yt.videos(part)
            for vid in part:
                d = details.get(vid)
                if not d or not d.get("public", True) or not d.get("embeddable", 1):
                    self.state.set_status(vid, "removed", "no longer available on YouTube")
                    removed += 1
                    continue
                was = previous[vid]
                # Published videos stay on the site while they're re-scored.
                status = {"maturing": "hydrated", "published": "published"}.get(was, "review")
                self.state.save_video({**{k: v for k, v in d.items() if k in STORED}, "status": status,
                                       "reason": "recheck" if was == "published" else None,
                                       "next_check": iso(now()), "hydrated_at": iso(now())})
        # They go back through judge_pending (judge answers are cached, so it's cheap).
        out = self.judge_pending(limit=len(ids) + 10) if ids else {}
        return {"refreshed": len(ids) - removed, "removed": removed, **{f"after_{k}": v for k, v in out.items()}}

    # 8 -------------------------------------------------------------------------
    def discover(self) -> dict:
        n = self.cfg.rule("discovery", "candidate_evals_per_run", default=12)
        found = reg.evaluate_candidates(self.state, self.cfg, self.yt, n, self.report)
        for vid in found:
            self.state.add_candidate(vid, "channel-eval")
        reg.discover_featured(self.state, self.yt, self.report)
        reg.promote(self.state, self.cfg, self.report)
        return {"evaluated": len(self.report.get("channels_evaluated", [])), "videos_from_new_channels": len(found)}

    # 9 -------------------------------------------------------------------------
    def backfill(self) -> dict:
        n = self.cfg.rule("backfill", "channels_per_run", default=6)
        share = self.cfg.rule("backfill", "top_share", default=0.35)
        recent_days = self.cfg.rule("backfill", "always_judge_days", default=540)
        rows = self.state.channels_where(
            "tier IN ('core','watched') AND backfill_done=0 AND uploads IS NOT NULL "
            "ORDER BY CASE tier WHEN 'core' THEN 0 ELSE 1 END, added_at LIMIT ?", [n])
        queued = 0
        for ch in rows:
            if self.quota.units_left() < 40 or self.time_left() < 300:
                break
            token = ch.get("backfill_token")
            ids: list[str] = []
            for _ in range(4):
                items, token = self.yt.uploads_page(ch["uploads"], token)
                ids += [vid for vid, _ in items]
                if not token:
                    break
            known = self.state.known(ids)
            unknown = [vid for vid in ids if vid not in known]
            details = self.yt.videos(unknown) if unknown else {}
            pool = [d for d in details.values() if not gate(d, self.cfg)]
            med = median([d["views"] for d in pool]) or 1
            ranked = sorted(pool, key=lambda d: (d["views"] / med) * (((d.get("likes") or 0) + 1) / max(1, d["views"])), reverse=True)
            keep = {d["id"] for d in ranked[:max(1, int(len(ranked) * share))]}
            # Recent uploads are always judged; the "best third" cut is only for old archives.
            keep |= {d["id"] for d in pool if age_days(d.get("published_at")) <= recent_days}
            for d in details.values():
                self.state.add_candidate(d["id"], "archive", ch["id"])
                stored = {k: v for k, v in d.items() if k in STORED}
                if d["id"] in keep:
                    self.state.save_video({**stored, "status": "hydrated", "hydrated_at": iso(now())})
                    queued += 1
                else:
                    self.state.save_video({**stored, "status": "rejected", "reason": "not among the channel's best", "hydrated_at": iso(now())})
            self.state.save_channel({**ch, "backfill_token": token, "backfill_done": 0 if token else 1})
            self.state.commit()
        hn = self.cfg.sources.get("hacker_news", {})
        if hn.get("enabled") and hn.get("backfill") and not self.offline:
            try:
                found, note = community.hn_backfill_step(self.state, hn.get("backfill_from_year", 2010))
                # Old Hacker News links are mostly not security; keep the ones whose story says it is.
                found = [m for m in found if self.cfg.taxonomy.security_score(m.get("title") or "") >= 1.0]
                note += f", {len(found)} about security"
                self._take_mentions(found, "hn-archive")
                self.report["sources"]["hn_backfill"] = note
            except Exception as exc:
                self.report["sources"]["hn_backfill"] = f"failed: {exc}"
        return {"queued_from_archives": queued}

    # 9b ------------------------------------------------------------------------
    PLAYLIST_SKIP = re.compile(r"\b(shorts?|clips?|highlights?|trailers?|teasers?|promo|merch|ads?|sponsor(ed)?|"
                               r"live ?streams?|livestreams?|vlogs?|uploads|favorites|liked videos|memes?|reactions?)\b", re.I)

    def playlists(self) -> dict:
        """Imports the playlists of the channels we trust (IppSec's machine series, Critical
        Thinking's episodes, conference tracks, courses). Their videos are judged like any other,
        with older ones allowed in, and the playlists appear on the site in value order."""
        pc = self.cfg.rules.get("playlists", {})
        n = pc.get("channels_per_run", 8)
        every = pc.get("refresh_days", 14)
        min_items, max_lists, max_pages = pc.get("min_items", 4), pc.get("max_per_channel", 30), pc.get("max_pages", 4)
        cutoff = iso(now() - timedelta(days=every))
        due = [c for c in self.state.channels_where("tier IN ('core','watched') ORDER BY CASE tier WHEN 'core' THEN 0 ELSE 1 END, added_at")
               if vouched(c) and (self.state.get(f"pl:{c['id']}") or "") < cutoff]
        requeue = ("not among the channel's best", "classic score", "older than", "old and not evergreen",
                   "old and about something that has moved on")
        imported, videos_new, requeued, channels = 0, 0, 0, 0
        for ch in due[:n]:
            if self.quota.units_left() < 60 or self.time_left() < 300:
                break
            lists = [p for p in self.yt.channel_playlists(ch["id"])
                     if p["count"] >= min_items and not self.PLAYLIST_SKIP.search(p["title"])][:max_lists]
            for pl in lists:
                ids, token = [], None
                for _ in range(max_pages):
                    items, token = self.yt.uploads_page(pl["id"], token)
                    ids += [vid for vid, _ in items]
                    if not token:
                        break
                self.state.save_playlist({"id": pl["id"], "channel_id": ch["id"], "channel_title": ch.get("title"),
                                          "title": pl["title"], "items": ids})
                imported += 1
                for vid in ids:
                    if self.state.add_candidate(vid, "playlist", ch["id"]):
                        videos_new += 1
                        continue
                    row = self.state.db.execute("SELECT status, reason FROM videos WHERE id=?", (vid,)).fetchone()
                    if row and row["status"] == "rejected" and (row["reason"] or "").startswith(requeue):
                        self.state.db.execute("UPDATE videos SET status='new', reason=NULL WHERE id=?", (vid,))
                        requeued += 1
            self.state.put(f"pl:{ch['id']}", iso(now()))
            channels += 1
            self.state.commit()
        return {"channels": channels, "playlists": imported, "new_videos": videos_new, "requeued": requeued}

    # housekeeping ------------------------------------------------------------------
    def forget(self) -> dict:
        days = self.cfg.rule("refresh", "forget_rejected_after_days", default=30)
        cutoff = iso(now() - timedelta(days=days))
        cur = self.state.db.execute(
            "UPDATE videos SET description=NULL, tags=NULL, quotes=NULL, links=NULL, views=NULL, likes=NULL, comments=NULL "
            "WHERE status IN ('rejected','gone','removed') AND hydrated_at<? AND description IS NOT NULL", (cutoff,))
        return {"forgotten": cur.rowcount}


STORED = {"id", "channel_id", "channel_title", "title", "description", "published_at", "duration_s", "views", "likes",
          "comments", "tags", "audio_lang", "live", "embeddable", "age_restricted"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="One HackHarbor pipeline run")
    ap.add_argument("--config", default="config")
    ap.add_argument("--state", default="state")
    ap.add_argument("--site", default="site")
    ap.add_argument("--out", default="_site")
    ap.add_argument("--budget-minutes", type=float, default=32)
    ap.add_argument("--offline", help="folder with fixtures; runs without network")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)

    cfg = load(args.config)
    state = State(Path(args.state) / "harbor.db")
    env = dict(os.environ)
    quota = Quota(state, cfg.rule("youtube", "daily_units", default=9000),
                  cfg.rule("youtube", "search_calls_per_day", default=80), cfg.rule("run_every_hours", default=2))

    if args.offline:
        from .offline import OfflineYouTube, seed_offline
        yt = OfflineYouTube(args.offline, quota)
        seed_offline(state, args.offline)
        cfg.sources = {k: {**v, "enabled": False} if isinstance(v, dict) else v for k, v in cfg.sources.items()}
    else:
        key = env.get("YOUTUBE_API_KEY")
        if not key:
            log.error("YOUTUBE_API_KEY is not set")
            return 2
        yt = YouTube(key, quota, cfg.rule("youtube", "search_unit_cost", default=100))

    judge = Judge(env.get("GEMINI_API_KEY"), cfg.taxonomy, state, cfg.rules.get("judge", {}))
    run = Run(cfg, state, yt, judge, quota, int(args.budget_minutes * 60), env, offline=bool(args.offline))

    run.stage("migrate", run.migrate, optional=False)
    if not args.offline:
        run.stage("seeds", lambda: reg.sync_seeds(state, cfg, yt, run.report))
    run.stage("poll", run.poll)
    run.stage("community", run.community)
    run.stage("search", run.search)
    run.stage("hydrate", run.hydrate)
    run.stage("judge", run.judge_pending, min_seconds=300)
    run.stage("refresh", run.refresh, min_seconds=300)
    run.stage("discover", run.discover)
    run.stage("backfill", run.backfill, min_seconds=420)
    run.stage("playlists", run.playlists, min_seconds=360)
    run.stage("hydrate_more", lambda: run.hydrate())
    run.stage("judge_more", run.judge_pending, min_seconds=240)
    run.stage("forget", run.forget)
    run.stage("publish", lambda: publish.build(state, cfg, Path(args.site), Path(args.out), run.report, sample=bool(args.offline)),
              optional=False)

    quota.save()
    run.report.pop("_baselines", None)
    run.report["quota"] = quota.summary()
    run.report["judge_calls"] = judge.calls
    run.report["finished"] = iso(now())
    run.report["totals"] = {s: state.count("status=?", [s]) for s in ("published", "rejected", "maturing", "review", "hydrated", "new")}

    bad = (not run.report["stages"].get("publish", {}).get("ok")) or (run.report.get("rss_ok_share", 1) < 0.2)
    streak = state.get("health:bad_runs", 0) + 1 if bad else 0
    state.put("health:bad_runs", streak)
    run.report["health"] = {"bad_run": bad, "bad_runs_in_a_row": streak}
    state.commit()
    state.db.execute("VACUUM")
    state.close()

    Path(args.state).mkdir(parents=True, exist_ok=True)
    (Path(args.state) / "report.json").write_text(redact(json.dumps(run.report, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    log.info("run finished: %s", json.dumps(run.report["totals"]))
    if streak >= cfg.rule("health", "fail_after_bad_runs", default=3):
        log.error("%s bad runs in a row; see state/report.json", streak)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
