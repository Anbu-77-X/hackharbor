"""Signals about a video beyond its topic: language, type, age-proofness,
clickbait, what the comments say, how it performs, and links worth reading."""
from __future__ import annotations

import math
import re
from urllib.parse import urlparse

from .classify import chapter_count
from .util import clamp, sigmoid

# -- language ---------------------------------------------------------------
ARABIC = re.compile(r"[؀-ۿ]")
OTHER_SCRIPTS = re.compile(r"[Ѐ-ӿऀ-ॿ぀-ヿ一-鿿가-힯฀-๿]")
WORD = re.compile(r"[a-zA-Z']+")
STOP = {
    "en": {"the", "and", "how", "what", "with", "for", "this", "you", "your", "from", "into", "that", "are", "is", "to", "of", "in", "on", "a", "an", "my", "i", "it"},
    "es": {"el", "la", "los", "las", "de", "del", "que", "para", "con", "como", "una", "por", "en", "es"},
    "pt": {"o", "os", "as", "de", "do", "da", "que", "para", "com", "como", "uma", "não", "em"},
    "fr": {"le", "la", "les", "des", "du", "que", "pour", "avec", "comment", "une", "est", "dans"},
    "de": {"der", "die", "das", "und", "mit", "wie", "ein", "eine", "ist", "für", "nicht", "auf"},
    "hi": {"kaise", "kya", "hai", "hain", "karo", "kare", "mein", "aur", "ke", "ki", "ko", "se", "sikhe", "bhai"},
    "id": {"yang", "dan", "cara", "untuk", "dengan", "ini", "itu", "dari", "di"},
    "tr": {"ve", "bir", "nasıl", "için", "ile", "bu", "da", "de"},
}


def language(video: dict) -> str:
    declared = (video.get("audio_lang") or "").lower().split("-")[0]
    title = video.get("title") or ""
    text = f"{title} {(video.get('description') or '')[:600]}"
    if ARABIC.search(title):
        return "ar"
    if declared and declared not in ("zxx", "und", "en"):
        return declared
    if OTHER_SCRIPTS.search(title):
        return "other"
    # Channels often declare English for everything, so the text gets a say too.
    for sample, need in ((title, 3), (text, 3)):
        words = [w.lower() for w in WORD.findall(sample)]
        counts = {lang: sum(1 for w in words if w in stop) for lang, stop in STOP.items()}
        best = max(counts, key=counts.get) if counts else "en"
        if best != "en" and counts[best] >= need and counts[best] > 2 * counts.get("en", 0):
            return best
    return "en"


# -- type -------------------------------------------------------------------
TYPE_PATTERNS = [
    ("walkthrough", re.compile(r"\b(walkthrough|walk-through|write-?up|hackthebox\s*[-:|]|htb\s*[-:|]|tryhackme|vulnhub|proving grounds|ctf challenge)\b", re.I)),
    ("lecture", re.compile(r"\b(lecture\s*\d*|lec\s*\d+|course\b.*\bpart\s*\d+|cs\s?\d{3}|6\.(858|566\d)|semester|seminar)\b", re.I)),
    ("talk", re.compile(r"\b(def ?con|black ?hat|bsides|owasp|offensivecon|recon ?\d|\d\dc3|ccc|keynote|nahamcon|sec-t|bluehat|hitb|x33fcon|troopers|usenix|rsac|nullcon|insomni'?hack|ekoparty|romhack|fwd:cloudsec|wwhf|conference|summit|talk)\b", re.I)),
    ("podcast", re.compile(r"\b(podcast|episode\s*\d+|ep\.?\s*\d+|interview with)\b", re.I)),
    ("tutorial", re.compile(r"\b(how to|tutorial|guide|step[- ]by[- ]step|setup|set up|build(ing)? (a|an|your)|hands-on|lab)\b", re.I)),
    ("explainer", re.compile(r"\b(explained|what is|what are|how .{1,40} works?|introduction to|intro to|deep dive|understanding|in \d+ minutes)\b", re.I)),
]
KIND_TYPE = {"conference": "talk", "university": "lecture", "podcast": "podcast"}


def video_type(video: dict, channel: dict | None) -> str:
    title = video.get("title") or ""
    if video.get("live") and (video.get("duration_s") or 0) >= 3600:
        return "live"
    if re.search(r"\b(live|stream|livestream)\b", title, re.I) and (video.get("duration_s") or 0) >= 5400:
        return "live"
    kind = (channel or {}).get("kind")
    for name, rx in TYPE_PATTERNS[:2]:  # walkthroughs and lectures are recognisable whoever posts them
        if rx.search(title):
            return name
    if kind in KIND_TYPE:
        return KIND_TYPE[kind]
    for name, rx in TYPE_PATTERNS[2:]:
        if rx.search(title):
            return name
    return "explainer"


# -- evergreen ----------------------------------------------------------------
TIMELY = re.compile(r"\b(news|this week|weekly|recap|roundup|update[sd]?|announc\w*|breaking|just (dropped|released)|new in|what'?s new|trending now|20\d\d (roadmap|tools|basics)|best .* 20\d\d|top \d+ .* 20\d\d|live now)\b", re.I)


def timely(title: str | None) -> bool:
    return bool(TIMELY.search(title or ""))
LASTING = re.compile(r"\b(explained|fundamentals?|introduction|intro to|how .{1,40} works?|deep dive|lecture|theory|principles|from scratch|internals|methodology|basics)\b", re.I)


def evergreen(video: dict, vtype: str, cats: list[str]) -> bool:
    title = video.get("title") or ""
    if TIMELY.search(title) or vtype in ("podcast",):
        return False
    if vtype in ("lecture",) or LASTING.search(title):
        return True
    if "ai" in cats:
        return False  # AI moves too fast to assume anything lasts
    return vtype in ("talk", "walkthrough", "explainer", "tutorial")


# -- clickbait ----------------------------------------------------------------
ACRONYM_OK = {"XSS", "SSRF", "SQLI", "IDOR", "CSRF", "API", "AWS", "GCP", "RCE", "LFI", "XXE", "SSTI", "JWT", "CTF", "HTB", "OSCP", "DEF", "CON", "CVE", "TLS", "SSL", "DNS", "HTTP", "HTTPS", "TCP", "UDP", "AI", "LLM", "LLMS", "MCP", "EDR", "AMSI", "ADCS", "NTLM", "IAM", "IOT", "UART", "JTAG", "SDR", "RFID", "NFC", "BLE", "OSINT", "DFIR", "SOC", "SIEM", "KQL", "OWASP", "USA", "EU", "BBV", "AD", "C2", "OAUTH", "SAML", "CORS", "DOM", "JS", "VPN", "SSH", "RDP", "SMB", "WAF", "CI", "CD", "K8S", "ROP", "UAF", "GPU", "CPU", "RAM", "PDF", "MSRC", "SANS", "HITB", "CCC", "RTV"}
HYPE = re.compile(r"(\$\s?\d[\d,.]*k?|\bmade \$|\bearn(ed)?\b|\binsane\b|\bshocking\b|\byou won'?t believe\b|\bin \d+ seconds\b|\beasy money\b|\b100%\b|\bnever do this\b|\bsecret\b|\bhack anything\b|\bgone wrong\b|\bexposed\b|😱|🔥|🤯|💰|🚨)", re.I)
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")


def clickbait(title: str) -> float:
    words = re.findall(r"[A-Za-z]{4,}", title or "")
    caps = [w for w in words if w.isupper() and w.upper() not in ACRONYM_OK]
    score = 0.0
    if words and len(caps) / len(words) > 0.4:
        score += 0.4
    if "!!" in title or "??" in title:
        score += 0.2
    if len(EMOJI.findall(title)) >= 2:
        score += 0.2
    score += 0.2 * len(HYPE.findall(title))
    return clamp(score)


# -- comments -----------------------------------------------------------------
PRAISE = re.compile(
    r"(best (explanation|video|talk|tutorial|content)|great (explanation|talk|video|content)|clearest|so clear|explained (so|really|very) well|well explained|"
    r"finally (understand|understood|get it|got it)|underrated|goldmine|\bgold\b|\bgem\b|masterpiece|amazing (talk|video|explanation|content)|"
    r"excellent|brilliant|incredible|eye[- ]opening|thank you so much|thanks a lot|so helpful|very helpful|super helpful|"
    r"learn[te]d (so much|a lot)|must[- ]watch|high[- ]quality|quality content|deserves more|criminally underrated|so few views|top[- ]notch|"
    r"in[- ]depth|helped me|found my first bug|first bounty|life ?saver|mind[- ]?blow|fantastic|"
    r"أفضل شرح|احسن شرح|أحسن شرح|شرح ممتاز|شرح رائع|شرح جميل|شرح عظيم|جزاك الله|ربنا يبارك|استفدت|مفيد جدا|عظيم|رائع)",
    re.I,
)
COMPLAINT = re.compile(
    r"(click ?bait|misleading|outdated|doesn'?t work|does not work|didn'?t work|not working|\bscam\b|\bfake\b|waste of time|useless|"
    r"\bincorrect\b|boring|bad audio|audio is (bad|terrible|low)|can'?t hear|music (is )?too loud|just an ad|reupload|stolen|copied)",
    re.I,
)


def praise(comments: list[dict] | None) -> tuple[float, list[dict]]:
    """0..1 with 0.5 meaning nothing either way, plus up to two quotable comments."""
    if not comments:
        return 0.5, []
    pos = neg = total = 0.0
    quotes = []
    for c in comments:
        text = c.get("text") or ""
        w = 1.0 + math.log1p(max(0, c.get("likes") or 0))
        total += w
        if PRAISE.search(text):
            pos += w
            clean = " ".join(text.split())
            if 40 <= len(clean) <= 280 and "http" not in clean and "@" not in clean[:2]:
                quotes.append({"text": clean, "likes": c.get("likes") or 0})
        if COMPLAINT.search(text):
            neg += w
    rate = (pos - 1.5 * neg) / (total + 5.0)
    quotes.sort(key=lambda q: q["likes"], reverse=True)
    return round(clamp(sigmoid(9.0 * (rate - 0.02))), 3), quotes[:2]


# -- performance --------------------------------------------------------------
def engagement(video: dict, channel_stats: dict, global_like: float, global_comment: float) -> float:
    views = max(1, video.get("views") or 0)
    likes = video.get("likes")
    comments = video.get("comments")
    trust = views / (views + 800.0)  # few views: ratios are noise, stay near neutral
    parts, weights = [], []
    if likes is not None:
        ratio = (likes + 1) / views
        base = channel_stats.get("like_ratio") or global_like
        ref = math.sqrt(base * global_like)
        parts.append(sigmoid(1.6 * math.log(ratio / ref)))
        weights.append(0.6)
    if comments is not None:
        ratio = (comments + 1) / views
        parts.append(sigmoid(1.2 * math.log(ratio / global_comment)))
        weights.append(0.15)
    med = channel_stats.get("median_views")
    if med and video.get("age_days", 0) >= 14:
        parts.append(sigmoid(0.8 * math.log(views / max(1.0, med))))
        weights.append(0.25)
    if not parts:
        return 0.5
    raw = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
    return round(0.5 + (raw - 0.5) * trust, 3)


def community(mentions: list[dict]) -> float:
    if not mentions:
        return 0.0
    best = 0.0
    sources = set()
    recommendations = 0
    for m in mentions:
        sources.add(m["source"])
        pts = m.get("points") or 0
        if m["source"] == "hn":
            best = max(best, math.log1p(pts) / math.log1p(300))
        elif m["source"] == "reddit":
            best = max(best, math.log1p(pts) / math.log1p(500) if pts else 0.25)
        elif m["source"] == "lobsters":
            best = max(best, math.log1p(pts) / math.log1p(40))
        elif m["source"] == "mastodon":
            best = max(best, math.log1p(pts) / math.log1p(100))
        elif m["source"] == "hn_comment":
            recommendations += 1
    score = 0.8 * min(1.0, best) + 0.1 * (len(sources) - 1) + 0.15 * min(4, recommendations)
    return round(clamp(score), 3)


# -- links worth reading --------------------------------------------------------
LINK_TYPES = [
    (re.compile(r"(^|\.)portswigger\.net$"), lambda p: "Labs" if p.startswith("/web-security") else "Research"),
    (re.compile(r"(^|\.)github\.com$"), lambda p: "Code"),
    (re.compile(r"(^|\.)gist\.github\.com$"), lambda p: "Code"),
    (re.compile(r"(^|\.)(arxiv\.org|usenix\.org|dl\.acm\.org|ieeexplore\.ieee\.org|eprint\.iacr\.org|ndss-symposium\.org)$"), lambda p: "Paper"),
    (re.compile(r"(^|\.)(blackhat\.com|media\.defcon\.org|defcon\.org|speakerdeck\.com|slideshare\.net)$"), lambda p: "Slides"),
    (re.compile(r"^docs\.google\.com$"), lambda p: "Slides" if "/presentation" in p else None),
    (re.compile(r"(^|\.)(hackerone\.com)$"), lambda p: "Report" if p.startswith("/reports") else None),
    (re.compile(r"(^|\.)(projectzero\.google|googleprojectzero\.blogspot\.com|embracethered\.com|blog\.assetnote\.io|assetnote\.io|research\.nccgroup\.com|specterops\.io|posts\.specterops\.io|trustedsec\.com|blog\.orange\.tw|devco\.re|labs\.watchtowr\.com|samcurry\.net|0xdf\.gitlab\.io|blog\.trailofbits\.com|wiz\.io|unit42\.paloaltonetworks\.com|securelist\.com|msrc\.microsoft\.com|thedfirreport\.com)$"), lambda p: "Research"),
    (re.compile(r"(^|\.)(attack\.mitre\.org|owasp\.org|genai\.owasp\.org|nvd\.nist\.gov|cve\.org|cve\.mitre\.org)$"), lambda p: "Reference"),
    (re.compile(r"(^|\.)(cryptopals\.com|pwn\.college|tryhackme\.com|app\.hackthebox\.com|hackthebox\.com|picoctf\.org|root-me\.org)$"), lambda p: "Labs"),
]
LINK_SKIP = re.compile(r"(patreon|discord|twitter\.com|x\.com|instagram|tiktok|facebook|linkedin|amzn|amazon\.|udemy|teespring|merch|spotify|apple\.com/podcast|bit\.ly|tinyurl|ko-fi|buymeacoffee|/ref=|affiliate|coupon|youtube\.com|youtu\.be)", re.I)
LINE_URL = re.compile(r"(.*?)(https?://[^\s<>\"')\]]+)")


GENERIC_LABELS = {"research", "slides", "slide", "deck", "code", "source", "source code", "github", "repo", "labs", "lab",
                  "paper", "papers", "blog", "blog post", "post", "notes", "resources", "resource", "links", "link", "follow-up",
                  "followup", "talk page", "website", "site", "more", "more info", "writeup", "write-up", "docs",
                  "documentation", "read more", "article", "pdf", "tool", "tools", "report", "references", "reference"}


def _title_from(url: str, label: str, kind: str) -> str:
    label = re.sub(r"[\s:–—|>\-•*]+$", "", label.strip()).strip(" -•*:")
    if 3 <= len(label) <= 90 and not label.lower().startswith(("http", "www")) and label.lower() not in GENERIC_LABELS:
        return label
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if p.netloc.endswith("github.com") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    if parts:
        slug = re.sub(r"\.(html?|pdf|php)$", "", parts[-1])
        slug = re.sub(r"[-_]+", " ", slug).strip()
        if slug and not slug.isdigit():
            return slug[:1].upper() + slug[1:]
    return p.netloc.replace("www.", "")


def reading_links(description: str | None, limit: int = 4) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for line in (description or "").splitlines():
        for label, url in LINE_URL.findall(line):
            url = url.rstrip(".,;")
            if LINK_SKIP.search(url) or url in seen:
                continue
            host = urlparse(url).netloc.lower().removeprefix("www.")
            path = urlparse(url).path
            for rx, kind_of in LINK_TYPES:
                if rx.search(host):
                    kind = kind_of(path)
                    if kind:
                        seen.add(url)
                        out.append({"type": kind, "title": _title_from(url, label, kind), "url": url})
                    break
            if len(out) >= limit:
                return out
    return out


def content_quality(video: dict, links: list[dict], channel: dict | None) -> float:
    score = 0.0
    if chapter_count(video.get("description")) >= 3:
        score += 0.35
    score += min(0.4, 0.2 * len(links))
    if (channel or {}).get("kind") in ("conference", "university"):
        score += 0.35
    if len(video.get("description") or "") >= 300:
        score += 0.1
    if (video.get("duration_s") or 0) >= 900:
        score += 0.1
    return round(clamp(score), 3)
