# HackHarbor

Hand-picked security videos. No shorts, no noise.

A YouTube-style site that fills itself. Every two hours a pipeline on GitHub
Actions:

- checks hundreds of channels, plus Hacker News, Reddit, Lobsters and Mastodon
- keeps only the videos worth a security practitioner's time
- publishes the site to GitHub Pages

There's no server and no sign-in, and it costs nothing to run.

How it decides is in [docs/LOGIC.md](docs/LOGIC.md).

## Set it up (about 15 minutes, once)

1. **Make a public repository** and push this folder to it. It has to be
   public for free GitHub Pages and free Actions minutes.
2. **Get a YouTube API key.** It's free.
   1. In [Google Cloud Console](https://console.cloud.google.com/), create a
      project.
   2. Go to **APIs & Services → Library** and enable **YouTube Data API v3**.
   3. Go to **Credentials → Create credentials → API key**, and restrict it
      to the YouTube Data API.
3. **Optional, recommended: get a Gemini API key** for the AI judge. It's
   free from [Google AI Studio](https://aistudio.google.com/apikey). Use a
   project without billing, so it stays on the free tier.
4. **Add the keys to the repository.** Go to **Settings → Secrets and
   variables → Actions → New repository secret** and add:
   - `YOUTUBE_API_KEY` (required)
   - `GEMINI_API_KEY` (optional)
   - `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` (optional; without them
     Reddit is read through its public feeds)
5. **Turn on Pages.** Go to **Settings → Pages → Build and deployment**, and
   set **Source** to **GitHub Actions**.
6. **Start it.** Go to **Actions → pipeline → Run workflow**. After that it
   runs every two hours on its own.

The first day mostly builds the channel list and walks archives. The library
grows over the first weeks as channel archives and old Hacker News links are
worked through.

## A custom address (optional)

1. Add a `CNAME` record for the subdomain you want, pointing at
   `<your-github-user>.github.io`. On Cloudflare, set it to **DNS only**.
2. In **Settings → Pages → Custom domain**, enter the address and save. Once
   the certificate is ready, tick **Enforce HTTPS**.
3. In your account's **Settings → Pages**, verify the parent domain, so no
   other account can claim the subdomain if this repository is ever removed.
4. Update the address in `site/index.html` (`canonical` and `og:url`).

## Security

- The site is static. Everything it shows is set as text, links must be
  plain `https`, and a strict Content Security Policy with Trusted Types
  allows only its own script, YouTube's player and thumbnails, and Google
  Fonts.
- API keys live only in Actions secrets and are sent in headers, never in
  URLs. Anything written to logs or the public run report is scrubbed of
  secrets.
- Workflows have no permissions by default; each job asks only for what it
  needs. Actions are pinned to exact commits and Python packages to hashes.
- Please report a security issue privately through the repository's
  **Security** tab, not in a public issue.

## Change what it picks

| File | What it controls |
| --- | --- |
| `config/channels.yml` | Starting channels and how much each is trusted. Add a line with a handle; the pipeline finds the rest. |
| `config/taxonomy.yml` | Categories, topics, the terms that match them, topic searches, and how old a video may be in each category |
| `config/rules.yml` | Score thresholds, Home order, quotas |
| `config/sources.yml` | Subreddits, Mastodon tags, extra searches |

## Check on it

- **Run log:** the **Actions** tab.
- **Last run's report:** `report.json` on the `state` branch. It shows what
  each source returned, what was published, and any channel that couldn't
  be found.
- **Failures:** after three unhealthy runs in a row, the workflow fails and
  GitHub emails you.

## Run it locally

```bash
pip install --require-hashes -r requirements.txt && pip install pytest
python -m pytest -q tests                                        # tests
python -m pipeline.run --offline fixtures/sample --out _site     # sample run, no network
python -m http.server -d _site 8000                              # open http://localhost:8000
YOUTUBE_API_KEY=... python -m pipeline.run --out _site           # a real run
```

Open the site through a local server, not as a file. YouTube's player only
plays on pages served from an address.

## Layout

```
config/      what to cover and how strictly
pipeline/    the pipeline (Python, no heavy dependencies)
site/        the front end (HTML, CSS, one script; reads data/*.json)
tests/       tests and an offline end-to-end run
fixtures/    sample data for the offline run
.github/     the two-hourly workflow and the tests
```
