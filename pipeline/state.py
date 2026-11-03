"""Everything the pipeline remembers between runs, in one SQLite file."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .util import dumps, iso, loads, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,
  channel_id TEXT,
  channel_title TEXT,
  title TEXT,
  description TEXT,
  published_at TEXT,
  duration_s INTEGER,
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  tags TEXT,
  lang TEXT,
  audio_lang TEXT,
  live INTEGER DEFAULT 0,
  embeddable INTEGER DEFAULT 1,
  age_restricted INTEGER DEFAULT 0,
  first_seen TEXT,
  sources TEXT,
  status TEXT,
  reason TEXT,
  cats TEXT,
  subs TEXT,
  relevance REAL,
  type TEXT,
  evergreen INTEGER,
  quality REAL,
  heuristic REAL,
  praise REAL,
  quotes TEXT,
  links TEXT,
  gem INTEGER DEFAULT 0,
  hydrated_at TEXT,
  scored_at TEXT,
  next_check TEXT,
  published_since TEXT,
  home_last_shown TEXT
);
CREATE INDEX IF NOT EXISTS videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS videos_next ON videos(next_check);

CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY,
  handle TEXT,
  title TEXT,
  tier TEXT,
  kind TEXT,
  cats TEXT,
  origin TEXT,
  uploads TEXT,
  subscribers INTEGER,
  video_count INTEGER,
  last_upload TEXT,
  avg_gap_h REAL,
  next_check TEXT,
  rss_fail INTEGER DEFAULT 0,
  backfill_token TEXT,
  backfill_done INTEGER DEFAULT 0,
  eval TEXT,
  evaluated_at TEXT,
  added_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS channels_tier ON channels(tier);

CREATE TABLE IF NOT EXISTS mentions (
  video_id TEXT,
  source TEXT,
  ref TEXT,
  title TEXT,
  points INTEGER,
  comments INTEGER,
  created_at TEXT,
  PRIMARY KEY (video_id, source, ref)
);
CREATE INDEX IF NOT EXISTS mentions_video ON mentions(video_id);
CREATE INDEX IF NOT EXISTS mentions_time ON mentions(created_at);

CREATE TABLE IF NOT EXISTS judge (
  video_id TEXT PRIMARY KEY,
  data TEXT,
  model TEXT,
  at TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
  id TEXT PRIMARY KEY,
  channel_id TEXT,
  channel_title TEXT,
  title TEXT,
  items TEXT,
  fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT
);
"""

JSON_FIELDS = {"tags", "sources", "cats", "subs", "quotes", "links", "eval"}


class State:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- generic -----------------------------------------------------------
    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for k in JSON_FIELDS & out.keys():
            out[k] = loads(out[k], [] if k != "eval" else {})
        return out

    @staticmethod
    def _encode(data: dict) -> dict:
        out = {}
        for k, v in data.items():
            out[k] = dumps(v) if k in JSON_FIELDS and v is not None and not isinstance(v, str) else v
        return out

    def _upsert(self, table: str, key: str, data: dict) -> None:
        data = self._encode(data)
        cols = list(data.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != key)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT({key}) DO UPDATE SET {updates}"
        self.db.execute(sql, [data[c] for c in cols])

    # -- key/value ---------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return loads(row["v"], default) if row else default

    def put(self, key: str, value: Any) -> None:
        self.db.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, dumps(value)))

    # -- videos ------------------------------------------------------------
    def video(self, vid: str) -> dict | None:
        return self._decode(self.db.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone())

    def known(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        found: set[str] = set()
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = f"SELECT id FROM videos WHERE id IN ({','.join('?' for _ in part)})"
            found.update(r["id"] for r in self.db.execute(q, part))
        return found

    def add_candidate(self, vid: str, source: str, channel_id: str | None = None) -> bool:
        """Records a video id the first time it's seen. Returns True if it was new."""
        row = self.db.execute("SELECT sources, status FROM videos WHERE id=?", (vid,)).fetchone()
        if row:
            sources = loads(row["sources"], [])
            if source not in sources:
                sources.append(source)
                self.db.execute("UPDATE videos SET sources=? WHERE id=?", (dumps(sources), vid))
            return False
        self._upsert("videos", "id", {
            "id": vid, "channel_id": channel_id, "first_seen": iso(now()), "sources": [source], "status": "new",
        })
        return True

    def save_video(self, data: dict) -> None:
        self._upsert("videos", "id", data)

    def videos_where(self, sql: str = "1=1", params: Iterable = ()) -> list[dict]:
        return [self._decode(r) for r in self.db.execute(f"SELECT * FROM videos WHERE {sql}", list(params))]

    def set_status(self, vid: str, status: str, reason: str | None = None) -> None:
        self.db.execute("UPDATE videos SET status=?, reason=? WHERE id=?", (status, reason, vid))

    def count(self, sql: str = "1=1", params: Iterable = ()) -> int:
        return self.db.execute(f"SELECT COUNT(*) AS n FROM videos WHERE {sql}", list(params)).fetchone()["n"]

    # -- channels ----------------------------------------------------------
    def channel(self, cid: str) -> dict | None:
        return self._decode(self.db.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone())

    def channel_by_handle(self, handle: str) -> dict | None:
        return self._decode(self.db.execute("SELECT * FROM channels WHERE lower(handle)=lower(?)", (handle,)).fetchone())

    def save_channel(self, data: dict) -> None:
        data = dict(data)
        data["updated_at"] = iso(now())
        self._upsert("channels", "id", data)

    def save_playlist(self, data: dict) -> None:
        self.db.execute(
            "INSERT INTO playlists (id, channel_id, channel_title, title, items, fetched_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id, channel_title=excluded.channel_title, "
            "title=excluded.title, items=excluded.items, fetched_at=excluded.fetched_at",
            (data["id"], data.get("channel_id"), data.get("channel_title"), data.get("title"), dumps(data.get("items") or []), iso(now())))

    def playlists_all(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM playlists"):
            d = dict(r)
            d["items"] = loads(d["items"], [])
            out.append(d)
        return out

    def channels_where(self, sql: str = "1=1", params: Iterable = ()) -> list[dict]:
        return [self._decode(r) for r in self.db.execute(f"SELECT * FROM channels WHERE {sql}", list(params))]

    # -- mentions ----------------------------------------------------------
    def add_mention(self, m: dict) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO mentions (video_id, source, ref, title, points, comments, created_at) VALUES (?,?,?,?,?,?,?)",
            (m["video_id"], m["source"], m["ref"], m.get("title"), m.get("points"), m.get("comments"), m.get("created_at")),
        )
        if cur.rowcount == 0 and m.get("points") is not None:
            self.db.execute(
                "UPDATE mentions SET points=MAX(COALESCE(points,0),?), comments=MAX(COALESCE(comments,0),?) WHERE video_id=? AND source=? AND ref=?",
                (m.get("points") or 0, m.get("comments") or 0, m["video_id"], m["source"], m["ref"]),
            )
        return cur.rowcount > 0

    def mentions(self, vid: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM mentions WHERE video_id=? ORDER BY points DESC", (vid,))]

    def mentions_since(self, since_iso: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM mentions WHERE created_at>=?", (since_iso,))]

    # -- judge cache -------------------------------------------------------
    def judged(self, vid: str) -> dict | None:
        row = self.db.execute("SELECT data FROM judge WHERE video_id=?", (vid,)).fetchone()
        return loads(row["data"]) if row else None

    def save_judgement(self, vid: str, data: dict, model: str) -> None:
        self.db.execute(
            "INSERT INTO judge (video_id, data, model, at) VALUES (?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET data=excluded.data, model=excluded.model, at=excluded.at",
            (vid, dumps(data), model, iso(now())),
        )
