# Security

Please report a vulnerability privately through this repository's
**Security → Report a vulnerability** page, not in a public issue.

HackHarbor is a static site built by a scheduled GitHub Actions workflow:

- **Site:** no server, no sign-in, no cookies. What a visitor saves (likes,
  history, playlists) stays in their own browser. All data is shown as text;
  links are limited to `https`; a Content Security Policy with Trusted Types
  allows only the site's own script, YouTube's player and thumbnails, and
  Google Fonts.
- **Pipeline:** API keys are Actions secrets, sent in request headers and
  scrubbed from logs and the public run report. Remote feeds are parsed
  without entity expansion. Video ids, links and playlist ids are validated
  before anything is written.
- **Workflows:** no default permissions, per-job grants, actions pinned to
  commits, dependencies pinned to hashes, and the checkout token isn't kept on
  disk.
