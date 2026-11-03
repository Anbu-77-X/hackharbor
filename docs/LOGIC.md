# How HackHarbor picks videos

The goal: someone who wants security content never needs to leave. Every
corner of security is covered, new videos arrive within hours, older videos
appear only when what they teach still holds, and nothing gets in just
because it exists.

A pipeline runs every two hours on GitHub Actions. It needs no server and no
one to look after it. Everything below happens on its own.

## 1. Where videos come from

| Source | What it finds | Cost |
| --- | --- | --- |
| Channel RSS | Every new upload from watched channels | Free, no quota |
| Hacker News (Algolia) | YouTube links in stories and in comments, where people recommend talks to each other | Free |
| Lobsters, Reddit, Mastodon | Links shared in security communities (r/netsec, r/bugbounty, r/redteamsec, r/blueteamsec, r/ReverseEngineering, infosec.exchange…) | Free |
| YouTube search | Alternates: a topic over the last four days, then a bug class or technique across all time (SSRF, IDOR, request smuggling, Frida, Kerberoasting…), plus Arabic searches | 16 searches a day, never more than half of a run's quota |
| Creator playlists | Every playlist of each trusted channel: IppSec's machine series, Critical Thinking's episodes, conference tracks, courses | Read every 14 days, about 8 channels a run |
| Channel archives | Walks each trusted channel's full upload history once, for classics | Quota left over each day |
| Hacker News history | Walks back a month per run to 2010, for classics people kept recommending | Free |

Channels aren't a fixed list. `config/channels.yml` is only the seed.

- **Discovery.** A channel enters as a *candidate* when one of its videos gets
  published, when a trusted channel features it on its page, or when its
  videos keep turning up in searches and communities.
- **Evaluation.** The pipeline reads a candidate's last 40 uploads. If at
  least 40% are on topic and they score well, the channel becomes *watched*
  and is polled from then on. If only some are, it becomes *occasional*: its
  videos get in only when people share them or search finds them.
- **Promotion.** A watched channel with 10 or more published videos averaging
  8.3 or higher becomes *core*. Channels with no uploads for 18 months are
  checked weekly instead of every few hours.

This is how small channels with few views get found: through what people
share and what trusted channels point to, not through view counts.

## 2. What is collected for each video

Title, description, chapters, tags, duration, views, likes, comment count,
upload date, live-stream status, whether it can be embedded, the 50 most
relevant comments, and links in the description.

## 3. Gates (cheapest first)

1. Seen before: skipped.
2. Shorter than 4 minutes (every Short), longer than 12 hours, not public, not embeddable, age-restricted, or made for kids: rejected.
3. Not English or Arabic: rejected. The title and description are checked too, since many channels declare English for everything.
4. Title matches a scam pattern ("hack instagram", "free robux", "mod apk", "prank", "reaction"…): rejected.
5. Not clearly about security: rejected. Trusted channels get the benefit of the doubt here, since talk titles don't always say what they're about. Big general channels (AWS Events, CS50…) get none: their videos must use security vocabulary. AI security and Cloud need a security angle too, so general AI or AWS product videos stay out.
6. **Waiting period.** New videos wait 72 hours so likes and comments can build up. Core channels skip it.

## 4. Classification

Ten categories, each with topics:

| Category | Topics |
| --- | --- |
| Bug bounty | Web, APIs, Mobile, Recon, Web3, Live hunting, Real reports |
| AI security | Attacking AI, AI tools |
| Red team | Active Directory, C2 tradecraft, Initial access, Evasion, OSINT |
| Cloud | AWS, Azure, Google Cloud, Kubernetes, Supply chain |
| Exploitation | Binary exploitation, Reverse engineering, Malware analysis |
| Blue team | Threat hunting, DFIR, Hardening |
| Cryptography | Crypto basics, Breaking crypto |
| Hardware | IoT, Radio & SDR, ICS / OT, Car hacking |
| CTF | Boxes & labs, Jeopardy |
| Fundamentals | Networking, Linux & Windows, Web basics, Code review, Careers |

Each topic has weighted terms (about 740 in all). The title counts three
times, tags twice, chapter titles one and a half times, and the description
once. A video can belong to two categories. Negative terms keep crypto
trading out of Cryptography, and Fundamentals needs a security angle so
general programming videos stay out.

Each video also gets a **type**: talk, lecture, tutorial, walkthrough,
explainer, live stream or podcast. It also gets an **evergreen** flag: will
it still be right in three years?

## 5. The AI judge (optional, free)

With a Gemini key, the pipeline sends batches of 8 videos (title, channel,
description, top comments) to Gemini's free tier, about 140 calls a day.
The judge returns:

- relevance
- category
- type
- evergreen
- a value score from 0 to 10
- a clickbait flag
- a two-sentence summary
- why the video is worth watching
- key ideas
- tags
- speaker and event

Its answers are checked against the taxonomy. Text from the videos is
treated as data, not instructions. Each answer is cached forever. Without
a key, everything still works on the pipeline's own scoring.

## 6. Score (0 to 10, never shown on the site)

| Part | Weight | Measures |
| --- | --- | --- |
| Relevance | 28% | How sure the classifier is |
| Comment praise | 22% | "Best explanation", "underrated", "finally understood", "أحسن شرح"… against "clickbait", "outdated", "doesn't work" (weighted by comment likes) |
| Engagement | 16% | Likes and comments per view against the channel's own normal, plus how the video did against the channel's median, so small channels compete fairly |
| Reputation | 16% | Channel tier and the average score of its published videos |
| Content | 18% | Chapters, links to research, slides or code, conference or university source |

On top of that:

- **Community bonus, up to +1.** Hacker News points, Reddit and Lobsters votes, Mastodon boosts, and comments recommending the video.
- **Clickbait penalty, up to −2.5.** All caps, "!!!", money in the title, "in 60 seconds", 😱🔥.
- **Judge blend.** With the judge, the final score is 45% the above and 55% the judge's value. If there are no comments to read, it's 25% and 75%.

## 7. Decision

**New videos** (inside the category's fresh window) need 7.0. Core
channels need 6.5, and broad channels like MIT OpenCourseWare need 7.7.
Videos just below the line are looked at again a week later, with fresh
numbers.

**Older videos** must be evergreen, within the category's age limit, and
score at least the category's bar:

| Category | New for | Older videos allowed up to | Bar for older videos |
| --- | --- | --- | --- |
| AI security | 12 months | 3 years | 8.8 |
| Cloud | 18 months | 4 years | 8.8 |
| Red team, Blue team | 18 months | 6 years | 8.5 |
| CTF | 24 months | 7 years | 8.6 |
| Bug bounty | 18 months | 10 years | 8.5 |
| Hardware | 18 months | 10 years | 8.5 |
| Exploitation | 18 months | 12 years | 8.5 |
| Cryptography, Fundamentals | 24 to 36 months | 30 years | 8.5 |

From each channel's archive, every upload from the last 18 months is
judged, and of older uploads only the best-performing third. At most 40 older videos per channel per topic are kept (250 for core
channels), and at most 800 per topic, so one prolific channel can't flood a
topic. Videos in a trusted creator's playlist are never capped.

## 8. Keeping it fresh

- **Re-scoring.** Published videos are re-scored at 3, 7 and 30 days, then their numbers are refreshed weekly.
- **Removals.** Videos that disappear from YouTube or stop allowing embedding are removed.
- **Second looks.** If people start discussing a video that was turned down, it gets another look.
- **Clean-up.** Rejected videos keep only their id and reason after a week. YouTube's API terms ask that stored data be refreshed or deleted within 30 days.

## 9. What the site shows

Everything is ordered by one rank, highest first: on Home, in every category and topic, under
every filter, in Longs, search results and shelves. Views never count, and nothing is random.

```
rank = value score (0-10)
     + up to 0.8 for being new (fades over a few months)
     + up to 0.5 while people are discussing it
```

The value score does almost all the work, so a strong older video outranks a weak new one, and
between two equally strong videos the newer one comes first. Videos more than three years old
come after everything newer, in value order among themselves.

- **Home** is the whole library in rank order. The only change: a channel never appears twice in
  a row or more than twice in any 12.
- **Categories** lists each category's topics with counts. Above them: Recently recommended (the
  last month), Longs, Hidden gems and All videos.
- **Longs** is everything 40 minutes or longer.
- **Chips above the videos** start with All and Bug bounty; **+** lets each person choose which
  categories show there (kept in their browser).
- **Filters > Sort by**: Best first (the rank, default), New, or Most viewed.
- **Search** finds the closest videos, not only exact matches: a typo or swapped letters, half a
  word, a plural, a category or topic name, or a word that's only in the video's tags or subject
  terms. Videos matching more of the query come first, then closer matches and more valuable
  videos. An exact word always beats a near miss.
- **Playlists**: creator playlists first, ordered by the average score of their ten best videos
  (videos inside keep the creator's order), then New this week, Hidden gems and Best of each
  topic. Older videos in a trusted creator's playlist need a score of 7.5 and aren't held to the
  category's age limit.
- **Settings** has one switch: show a frame from the middle of each video instead of its
  thumbnail. It's off until you turn it on.
- Liked videos, history, watch later and your playlists stay in your browser, with a copy of each
  video's details, so they're still there even if a video leaves the library. The address bar
  keeps your place, so reloading or going back returns to the same page.
- **Each video page** has the player, speaker and event, a summary, key ideas, research and code
  linked in the description, where it's discussed, and two comments praising it.

## 10. Running unattended

- **Quota.** The daily YouTube quota is split across the day's runs, so no run uses it all.
- **Isolated stages.** A source that fails or gets blocked is skipped and noted in the run report. The rest of the run carries on.
- **Memory.** The pipeline's memory (one SQLite file) lives on the `state` branch, overwritten each run, so the repository doesn't grow.
- **Schedule.** The workflow re-enables its own schedule each run. Otherwise GitHub would pause it after 60 days without a commit.
- **Health.** After three unhealthy runs in a row (for example, RSS blocked or publishing failing), the workflow fails and GitHub emails you. Each run's report is in `report.json` on the `state` branch.
