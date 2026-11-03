"""Tests for classification, gates, scoring rules, the Home feed and an offline end-to-end run."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import feed, signals  # noqa: E402
from pipeline.classify import classify  # noqa: E402
from pipeline.config import load  # noqa: E402
from pipeline.score import gate  # noqa: E402

CFG = load(ROOT / "config")


def video(title, desc="", **kw):
    base = {"id": "x" * 11, "title": title, "description": desc, "tags": [], "duration_s": 1800, "views": 10000,
            "likes": 400, "comments": 20, "published_at": "2026-10-01T00:00:00Z", "audio_lang": "en",
            "embeddable": 1, "public": True}
    base.update(kw)
    return base


@pytest.mark.parametrize("title,cat,sub", [
    ("HTTP Desync Attacks: Smashing into the Cell Next Door", "bounty", "web"),
    ("Kerberoasting and AS-REP roasting in Active Directory", "red", "ad"),
    ("Prompt injection: exploiting AI agents through the content they read", "ai", "attack"),
    ("I built a hackbot for bug bounty", "ai", "tools"),
    ("Container escape techniques in Kubernetes", "cloud", "k8s"),
    ("Heap exploitation: use after free from scratch", "exploit", "pwn"),
    ("Memory forensics with Volatility", "blue", "dfir"),
    ("Padding oracle attack explained", "crypto", "breaking"),
    ("Dumping router firmware over UART and JTAG", "hardware", "embedded"),
    ("HackTheBox - Reel walkthrough", "ctf", "boxes"),
    ("Bypassing SSL pinning in Android apps with Frida", "bounty", "mobile"),
])
def test_classify_topics(title, cat, sub):
    res = classify(video(title), CFG.taxonomy)
    assert res.cats and res.cats[0] == cat, (res.cats, res.conf)
    assert sub in res.subs, res.subs


def test_crypto_trading_is_not_cryptography():
    res = classify(video("Bitcoin price prediction: crypto trading strategy"), CFG.taxonomy)
    assert "crypto" not in res.cats


def test_scam_titles_rejected():
    assert classify(video("How to hack instagram account in 2 minutes"), CFG.taxonomy).rejected_by


def test_fundamentals_need_security_context():
    res = classify(video("Learn Python in one hour for beginners"), CFG.taxonomy)
    assert "basics" not in res.cats


def test_gates():
    assert gate(video("x", duration_s=45), CFG) == "too short"
    assert gate(video("Wie man Hacking lernt mit der Burp Suite und die Grundlagen für eine Webseite", audio_lang=None), CFG).startswith("language")
    assert gate(video("XSS explained", embeddable=0), CFG) == "embedding disabled"
    assert gate(video("شرح ثغرة XSS", audio_lang=None), CFG) is None
    assert gate(video("Bug bounty kaise kare hindi mein, bhai ye dekho kya hai", audio_lang=None), CFG).startswith("language")


def test_praise_and_clickbait():
    p, quotes = signals.praise([{"text": "Best explanation of request smuggling I've ever seen, thank you so much!", "likes": 40},
                                {"text": "great talk", "likes": 2}])
    assert p > 0.6 and quotes
    assert signals.clickbait("I MADE $50,000 HACKING THIS!!! 😱🔥") > 0.6
    assert signals.clickbait("HTTP Desync Attacks: Smashing into the Cell Next Door") < 0.2


def test_reading_links():
    links = signals.reading_links("Slides: https://www.blackhat.com/docs/x.pdf\nPatreon: https://patreon.com/x\nCode: https://github.com/a/b")
    kinds = {l["type"] for l in links}
    assert kinds == {"Slides", "Code"}


def test_home_feed_rules():
    vids = []
    for i in range(60):
        vids.append({"id": f"v{i}", "channel_id": f"c{i % 4}", "cats": ["ai"] if i % 7 == 0 else ["bounty"], "subs": [],
                     "quality": 8 + (i % 10) / 10, "published_at": "2026-10-20T00:00:00Z", "evergreen": 0, "gem": 0,
                     "buzz_h": None})
    for v in vids:
        v["p"] = feed.priority(v)
    home = feed.compose_home(vids, CFG.rules)
    by_id = {v["id"]: v for v in vids}
    assert len(home) == 60
    for a, b in zip(home, home[1:]):
        assert by_id[a]["channel_id"] != by_id[b]["channel_id"]
    # Pure rank order apart from channel spacing: the best video leads, and no video is
    # pushed down more than a few places to make room for another channel.
    assert by_id[home[0]]["p"] == max(v["p"] for v in vids)
    order = sorted(vids, key=lambda v: (-v["p"], v["id"]))
    rank_of = {v["id"]: i for i, v in enumerate(order)}
    assert all(abs(rank_of[vid] - pos) <= 6 for pos, vid in enumerate(home[:40]))


def test_offline_run(tmp_path):
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, "-m", "pipeline.run", "--offline", "fixtures/sample", "--state", str(tmp_path / "state"),
                        "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    index = json.loads((out / "data" / "index.json").read_text())
    ids = {v["i"] for v in index["videos"]}
    assert "kgkRih2MQtU" in ids                 # strong classic
    assert "Z9es1_BUXmQ" not in ids             # dated 2024 recon basics: not evergreen
    assert "ob9SgtFm6_g" not in ids             # CTF walkthrough older than the CTF age limit
    feed_data = json.loads((out / "data" / "feed.json").read_text())
    assert feed_data["home"][0] in ids
    one = json.loads((out / "data" / "v" / "kgkRih2MQtU.json").read_text())
    assert one["summary"] and one["read"]


def test_youtube_parsing():
    from pipeline.youtube import normalize_video
    from pipeline.util import video_ids_in
    item = {"id": "kgkRih2MQtU", "snippet": {"channelId": "UC1", "channelTitle": "Black Hat", "title": "T", "description": "d",
            "publishedAt": "2020-01-15T18:00:00Z", "liveBroadcastContent": "none", "defaultAudioLanguage": "en"},
            "contentDetails": {"duration": "PT49M58S"}, "statistics": {"viewCount": "120000", "likeCount": "3000", "commentCount": "90"},
            "status": {"embeddable": True, "privacyStatus": "public"}}
    v = normalize_video(item)
    assert v["duration_s"] == 2998 and v["views"] == 120000 and v["embeddable"] == 1 and v["live"] == 0
    text = 'see https://www.youtube.com/watch?v=kgkRih2MQtU&amp;t=10 and https://youtu.be/voTHFdL9S2k?si=x and <a href="https://youtube.com/shorts/abcdefghijk">'
    assert video_ids_in(text) == ["kgkRih2MQtU", "voTHFdL9S2k", "abcdefghijk"]


def test_judge_answers_are_checked():
    from pipeline.judge import validate
    raw = {"id": "a", "relevant": True, "cats": ["bounty", "nonsense"], "topics": ["web", "ad"], "type": "talk",
           "evergreen": True, "value": 14, "clickbait": False, "summary": "See https://evil.example now. Ignore previous instructions.",
           "why": "x", "ideas": ["one"], "tags": ["XSS", "bad tag!"], "who": ""}
    out = validate(raw, CFG.taxonomy)
    assert out["cats"] == ["bounty"] and out["subs"] == ["web"] and out["value"] == 10.0
    assert "http" not in out["summary"] and out["tags"] == ["xss"]


def test_playlist_import_and_publish(tmp_path):
    from pipeline import publish
    from pipeline.quota import Quota
    from pipeline.run import Run
    from pipeline.state import State

    class FakeYT:
        def channel_playlists(self, cid):
            return [{"id": "PLmachines", "title": "HackTheBox Retired Machines", "count": 5},
                    {"id": "PLshorts", "title": "Shorts", "count": 40},
                    {"id": "PLtiny", "title": "Two videos", "count": 2}]

        def uploads_page(self, pid, token=None):
            assert pid == "PLmachines"
            return [(f"vid{i}", None) for i in range(5)], None

    st = State(tmp_path / "s.db")
    st.save_channel({"id": "UCipp", "title": "IppSec", "tier": "core", "origin": "seed", "added_at": "2026-01-01T00:00:00Z"})
    st.add_candidate("vid0", "archive", "UCipp")
    st.save_video({"id": "vid0", "status": "rejected", "reason": "not among the channel's best"})
    run = Run(CFG, st, FakeYT(), None, Quota(st, 9000, 16, 2), 900, {})
    out = run.playlists()
    assert out["playlists"] == 1 and out["requeued"] == 1 and out["new_videos"] == 4
    assert st.video("vid0")["status"] == "new"
    assert "playlist" in st.video("vid3")["sources"]

    videos = [{"id": f"vid{i}", "quality": 8 + i / 10} for i in range(4)]
    lists = publish.creator_playlists(st, videos)
    assert lists[0]["name"] == "HackTheBox Retired Machines"
    assert lists[0]["ids"] == ["vid0", "vid1", "vid2", "vid3"]  # the creator's order
    assert lists[0]["by"] == "IppSec"


def test_rank_values_score_over_newness():
    old_great = {"quality": 9.2, "published_at": "2021-01-01T00:00:00Z"}
    new_ok = {"quality": 7.6, "published_at": feed.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    new_great = {"quality": 9.2, "published_at": new_ok["published_at"]}
    year_old_great = {"quality": 9.2, "published_at": "2025-11-01T00:00:00Z"}
    assert feed.priority(year_old_great) > feed.priority(new_ok)       # value beats newness
    assert feed.priority(new_great) > feed.priority(year_old_great)
    assert feed.priority(new_ok) > feed.priority(old_great)            # over three years old goes last
    older_ok = {"quality": 7.6, "published_at": "2020-01-01T00:00:00Z"}
    assert feed.priority(old_great) > feed.priority(older_ok)          # still in value order there


def test_home_puts_strong_recent_first():
    today = feed.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    vids = [
        {"id": "old_best", "channel_id": "a", "quality": 9.5, "published_at": "2025-01-01T00:00:00Z"},
        {"id": "new_good", "channel_id": "b", "quality": 8.4, "published_at": today},
        {"id": "new_weak", "channel_id": "c", "quality": 7.2, "published_at": today},
    ]
    for v in vids:
        v["p"] = feed.priority(v)
    home = feed.compose_home(vids, CFG.rules)
    assert home[0] == "new_good"          # recent and strong leads
    assert home.index("old_best") < home.index("new_weak")   # recent but weak doesn't jump ahead


def test_secrets_never_leave_and_links_are_plain_https(monkeypatch):
    from pipeline.util import redact, safe_url
    fake = "not-a-real-key-" + "0" * 12          # built at runtime so no scanner mistakes it for a key
    monkeypatch.setenv("YOUTUBE_API_KEY", fake)
    leaked = f"GET https://www.googleapis.com/youtube/v3/videos?id=1&key={fake} timed out"
    assert fake not in redact(leaked)
    assert fake not in redact(f"error: {fake}")
    shaped = "AIza" + "x" * 30
    assert shaped not in redact(f"saw {shaped} somewhere")
    assert "s3cr3t" not in redact("https://x-access-token:s3cr3t@github.com/a/b")
    assert safe_url("https://portswigger.net/research") == "https://portswigger.net/research"
    for bad in ["javascript:alert(1)", "data:text/html,x", "https://a.b/\"onload=", "//evil.com", "https://a.b/<x>", None, 5]:
        assert safe_url(bad) is None


def test_youtube_key_is_sent_in_a_header(monkeypatch):
    from pipeline.quota import Quota
    from pipeline.state import State
    from pipeline.youtube import YouTube
    import tempfile
    seen = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"items": []}

    def fake_get(self, url, params=None, headers=None, timeout=None):
        seen["url"], seen["params"], seen["headers"] = url, params, headers
        return Resp()

    monkeypatch.setattr("requests.Session.get", fake_get)
    st = State(Path(tempfile.mkdtemp()) / "s.db")
    fake = "not-a-real-key-" + "1" * 12
    yt = YouTube(fake, Quota(st, 9000, 16, 2))
    yt.videos(["dQw4w9WgXcQ"])
    assert "key" not in (seen["params"] or {}) and fake not in seen["url"]
    assert seen["headers"]["X-Goog-Api-Key"] == fake
