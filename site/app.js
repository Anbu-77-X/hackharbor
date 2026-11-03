/* HackHarbor front end. Reads data/*.json written by the pipeline; no server, no sign-in.
   Liked videos, history, watch later and playlists live in this browser's localStorage. */
(function () {
  "use strict";
  if (window.top !== window.self) {
    try { window.top.location = window.location.href; } catch (e) { document.documentElement.textContent = ""; return; }
  }

  const CFG = Object.assign({ embed: true, data: "data/" }, window.HARBOR || {});
  const NS = "http://www.w3.org/2000/svg";

  const ICONS = {
    home: "M4 10.5 12 4l8 6.5V20h-5.5v-6h-5v6H4z",
    longs: "M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5zM10 9.3v5.4l4.6-2.7z",
    liked: "M7 11v9H4.5A1.5 1.5 0 0 1 3 18.5v-6A1.5 1.5 0 0 1 4.5 11zM7 11l3.6-7a1.9 1.9 0 0 1 3.4 1.6L13 9.5h5.3a2 2 0 0 1 2 2.4l-1.3 6.5A2 2 0 0 1 17 20H7",
    history: "M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3.5 4.2v4.5H8M12 7.5V12l3 2",
    later: "M12 3.5a8.5 8.5 0 1 1 0 17 8.5 8.5 0 0 1 0-17zM12 7.5V12l3 2",
    categories: "M4 4h6.5v6.5H4zM13.5 4H20v6.5h-6.5zM4 13.5h6.5V20H4zM13.5 13.5H20V20h-6.5z",
    playlists: "M4 6h12M4 11h12M4 16h7M15 13.5v7l5.5-3.5z",
    settings: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
    filters: "M4 7h16M7 12h10M10 17h4",
    menu: "M4 6h16M4 12h16M4 18h16",
    search: "M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.2 15.2 20 20",
    expand: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
    hash: "M9.5 4 7.5 20M16.5 4l-2 16M4.5 9h15.5M4 15h15.5",
    save: "M4 6h12M4 11h12M4 16h8M18 13v7M14.5 16.5h7",
    external: "M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
    check: "M5 12.5l4.5 4.5L19 7.5",
    play: "M8.5 6.5v11l9-5.5z"
  };

  function icon(name, cls) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "i" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", ICONS[name]);
    if (name === "play") {
      svg.setAttribute("fill", "currentColor");
    } else {
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "1.8");
      svg.setAttribute("stroke-linecap", "round");
      svg.setAttribute("stroke-linejoin", "round");
    }
    svg.appendChild(p);
    return svg;
  }

  function h(tag, props) {
    const n = document.createElement(tag);
    if (props) {
      for (const k in props) {
        const v = props[k];
        if (v == null || v === false) continue;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (let i = 2; i < arguments.length; i++) {
      const c = arguments[i];
      if (c == null || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x != null && x !== false) n.append(x); });
      else n.append(c);
    }
    return n;
  }

  /* ---------- Safety ----------
     Everything shown comes from YouTube and other sites, so it's only ever set as text, links
     must be plain http(s), and ids must look like YouTube ids. Saved data from this browser is
     checked the same way before use. */
  const ID_RE = /^[A-Za-z0-9_-]{11}$/;
  function isId(x) { return typeof x === "string" && ID_RE.test(x); }
  function safeHref(u) {
    if (typeof u !== "string" || u.length > 2048) return null;
    return /^https?:\/\/[^\s/?#]+/i.test(u) && !/[\s<>"'`\\]/.test(u) ? u : null;
  }
  function str(x, max) { return typeof x === "string" ? x.slice(0, max || 300) : ""; }
  function num(x) { return typeof x === "number" && isFinite(x) ? x : 0; }
  function strs(x, max) { return Array.isArray(x) ? x.filter(function (y) { return typeof y === "string"; }).slice(0, max || 20) : []; }

  /* ---------- Data ---------- */
  let CATS = {}, CAT_LIST = [], SUBS = {}, SUB_NAME = {}, SUB_OF = {}, TYPE_NAME = {}, TYPE_LIST = [];
  let VIDEOS = [], BY_ID = Object.create(null), HOME = [], PLAYLISTS = [], GENERATED = null, LONGS_MIN = 40, SAMPLE = false;
  const DETAILS = Object.create(null);

  function getJSON(path) {
    return fetch(CFG.data + path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(path + " " + r.status);
      return r.json();
    });
  }

  function setup(tax, index, feed) {
    CAT_LIST = tax.categories || [];
    SX = null;
    CAT_LIST.forEach(function (c) {
      CATS[c.key] = c.name;
      if (c.topics && c.topics.length) SUBS[c.key] = c.topics.map(function (t) { return [t.key, t.name]; });
      (c.topics || []).forEach(function (t) { SUB_NAME[t.key] = t.name; SUB_OF[t.key] = c.key; });
    });
    TYPE_LIST = tax.types || [];
    TYPE_LIST.forEach(function (t) { TYPE_NAME[t.key] = t.name; });
    LONGS_MIN = tax.longs_min_minutes || 40;
    VIDEOS = (Array.isArray(index.videos) ? index.videos : []).filter(function (r) { return r && isId(r.i); }).map(function (r) {
      return { id: r.i, title: str(r.t, 300), channel: str(r.c, 120), date: /^\d{4}-\d{2}-\d{2}$/.test(r.d) ? r.d : "2000-01-01",
               len: num(r.l), views: num(r.v), cats: strs(r.k, 4), subs: strs(r.s, 6), type: str(r.y, 20), ever: !!r.e, gem: !!r.m,
               tags: strs(r.g, 8), p: num(r.p), words: str(r.w, 400) };
    });
    VIDEOS.forEach(function (v) { BY_ID[v.id] = v; });
    HOME = (feed.home || []).filter(function (id) { return BY_ID[id]; });
    PLAYLISTS = (Array.isArray(feed.playlists) ? feed.playlists : []).filter(function (p) {
      return p && typeof p.key === "string" && typeof p.name === "string" && Array.isArray(p.ids);
    }).map(function (p) {
      return { key: p.key.slice(0, 80), name: p.name.slice(0, 160), by: str(p.by, 120), kind: p.kind === "creator" ? "creator" : "auto",
               yt: typeof p.yt === "string" && /^[A-Za-z0-9_-]{10,64}$/.test(p.yt) ? p.yt : null, score: num(p.score), ids: p.ids.filter(isId) };
    });
    PX = null;
    GENERATED = index.generated || feed.generated;
    SAMPLE = !!index.sample;
  }

  /* Before the first real run the site shows a hand-made sample: say so, and don't show its stand-in view counts. */
  function sampleNote() {
    return SAMPLE ? h("p", { class: "sample-note", text: "HackHarbor is starting up. This is a small hand-picked sample; the full library arrives with the first real run." }) : null;
  }
  function statLine(v) {
    return SAMPLE ? fmtAgo(v.date) : fmtViews(v.views) + " • " + fmtAgo(v.date);
  }

  function cleanDetail(d) {
    if (!d || typeof d !== "object") return {};
    function list(x, f, max) { return Array.isArray(x) ? x.filter(function (y) { return y && typeof y === "object"; }).map(f).slice(0, max) : []; }
    return {
      who: str(d.who, 160), summary: str(d.summary, 1200), why: str(d.why, 600), ideas: strs(d.ideas, 6).map(function (x) { return x.slice(0, 300); }),
      tags: strs(d.tags, 8).map(function (x) { return x.slice(0, 40); }),
      read: list(d.read, function (r) { return { type: str(r.type, 20), title: str(r.title, 200), url: safeHref(r.url) }; }, 6).filter(function (r) { return r.url; }),
      discussed: list(d.discussed, function (m) { return { source: str(m.source, 40), title: str(m.title, 300), url: safeHref(m.url), points: num(m.points), comments: num(m.comments), date: str(m.date, 10) }; }, 6).filter(function (m) { return m.url; }),
      quotes: list(d.quotes, function (q) { return { text: str(q.text, 600), likes: num(q.likes) }; }, 3).filter(function (q) { return q.text; })
    };
  }
  function detail(id) {
    if (!isId(id)) return Promise.resolve({});
    if (DETAILS[id]) return Promise.resolve(DETAILS[id]);
    return getJSON("v/" + encodeURIComponent(id) + ".json")
      .then(function (d) { DETAILS[id] = cleanDetail(d); return DETAILS[id]; })
      .catch(function () { DETAILS[id] = {}; return DETAILS[id]; });
  }

  /* ---------- Per-browser library ---------- */
  const store = loadStore();
  function loadStore() {
    let data = {};
    try { data = JSON.parse(localStorage.getItem("harbor") || "{}") || {}; } catch (e) { data = {}; }
    if (!data || typeof data !== "object" || Array.isArray(data)) data = {};
    function ids(x) { return Array.isArray(x) ? x.filter(isId).slice(0, 2000) : []; }
    const p = data.prefs && typeof data.prefs === "object" ? data.prefs : {};
    // Only known settings are kept; frames are opt-in (an earlier version had them on for everyone).
    const prefs = {
      hideWatched: p.hideWatched === true, pauseHistory: p.pauseHistory === true, showFrames: p.showFrames === true,
      chips: Array.isArray(p.chips) ? p.chips.filter(function (k) { return typeof k === "string" && /^[a-z]{1,20}$/.test(k); }) : ["bounty"]
    };
    const saved = Object.create(null);
    if (data.saved && typeof data.saved === "object") {
      Object.keys(data.saved).forEach(function (id) {
        const v = data.saved[id];
        if (isId(id) && v && typeof v === "object") {
          saved[id] = { id: id, title: str(v.title, 300), channel: str(v.channel, 120), date: /^\d{4}-\d{2}-\d{2}$/.test(v.date) ? v.date : "2000-01-01",
                        len: num(v.len), views: num(v.views), cats: strs(v.cats, 4), subs: strs(v.subs, 6), type: str(v.type, 20), tags: [], p: num(v.p) };
        }
      });
    }
    const history = Array.isArray(data.history) ? data.history.filter(function (x) { return x && isId(x.id) && typeof x.t === "number"; })
      .map(function (x) { return { id: x.id, t: x.t }; }).slice(0, 300) : [];
    const playlists = Array.isArray(data.playlists) ? data.playlists.filter(function (pl) { return pl && typeof pl.key === "string" && typeof pl.name === "string"; })
      .map(function (pl) { return { key: pl.key.slice(0, 40), name: pl.name.slice(0, 60), ids: ids(pl.ids) }; }).slice(0, 100) : [];
    return {
      liked: ids(data.liked), disliked: ids(data.disliked), later: ids(data.later), history: history,
      playlists: playlists, saved: saved, prefs: prefs
    };
  }
  /* A copy of each saved video's card details, so Liked, History, Watch later and your playlists
     keep showing it even if it later leaves the library. */
  function snapshot(v) { return { id: v.id, title: v.title, channel: v.channel, date: v.date, len: v.len, views: v.views, cats: v.cats, subs: v.subs, type: v.type, tags: [], p: v.p || 0 }; }
  function keepSnapshots() {
    const want = new Set(store.liked.concat(store.later, store.history.map(function (x) { return x.id; })));
    store.playlists.forEach(function (pl) { pl.ids.forEach(function (id) { want.add(id); }); });
    const next = Object.create(null);
    want.forEach(function (id) {
      if (BY_ID[id]) next[id] = snapshot(BY_ID[id]);
      else if (store.saved[id]) next[id] = store.saved[id];
    });
    store.saved = next;
  }
  function saveStore() {
    PX = null;
    keepSnapshots();
    try { localStorage.setItem("harbor", JSON.stringify(store)); } catch (e) { /* private window */ }
  }
  function known(id) { return BY_ID[id] || store.saved[id] || null; }
  function toggleIn(list, id) {
    const i = list.indexOf(id);
    if (i >= 0) list.splice(i, 1); else list.unshift(id);
    saveStore();
    return i < 0;
  }
  function drop(list, id) {
    const i = list.indexOf(id);
    if (i >= 0) { list.splice(i, 1); saveStore(); }
  }

  const state = {
    view: "home", arg: null, query: "", cat: null, sub: null,
    plq: "", showFilters: false, filters: { date: "any", len: "any", kind: "any", sort: "rank" },
    context: null
  };

  /* ---------- Formatting ---------- */
  function fmtLen(s) {
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
    return hh ? hh + ":" + String(mm).padStart(2, "0") + ":" + ss : mm + ":" + ss;
  }
  function fmtViews(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M views";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "K views";
    return n + (n === 1 ? " view" : " views");
  }
  function daysSince(iso) { return Math.max(0, Math.floor((Date.now() - new Date(iso + "T12:00:00Z").getTime()) / 86400000)); }
  function unit(n, w) { return n + " " + w + (n === 1 ? "" : "s") + " ago"; }
  function fmtAgo(iso) {
    const d = daysSince(iso);
    if (d >= 365) return unit(Math.floor(d / 365), "year");
    if (d >= 30) return unit(Math.floor(d / 30), "month");
    if (d >= 7) return unit(Math.floor(d / 7), "week");
    if (d >= 1) return unit(d, "day");
    return "today";
  }
  function agoFromMs(t) {
    const m = Math.floor((Date.now() - t) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return unit(m, "minute");
    const hr = Math.floor(m / 60);
    if (hr < 24) return unit(hr, "hour");
    return unit(Math.floor(hr / 24), "day");
  }
  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }
  function ytLink(v) { return "https://www.youtube.com/watch?v=" + encodeURIComponent(v.id); }

  /* ---------- Video pieces ---------- */
  function thumbFace(v, big) {
    const frame = h("div", { class: "frame c-" + (CATS[v.cats[0]] ? v.cats[0] : "default"), "aria-hidden": "true" },
      h("span", { class: "f-ch", text: v.channel }),
      h("span", { class: "f-title", text: v.title })
    );
    const base = "https://i.ytimg.com/vi/" + encodeURIComponent(v.id);
    // YouTube keeps three frames from inside every video; hq2 is the one from the middle.
    const frames = store.prefs.showFrames;
    const first = frames ? (big ? "/sd2.jpg" : "/hq2.jpg") : (big ? "/maxresdefault.jpg" : "/hqdefault.jpg");
    const fallback = frames ? "/hq2.jpg" : "/hqdefault.jpg";
    const img = h("img", { src: base + first, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", function () {
      if (img.src.indexOf(fallback) < 0) img.src = base + fallback; else img.remove();
    });
    img.addEventListener("load", function () {
      if (img.naturalWidth <= 120) { if (img.src.indexOf(fallback) < 0) img.src = base + fallback; else img.remove(); }
    });
    return [frame, img];
  }

  function laterButton(v) {
    const on = store.later.indexOf(v.id) >= 0;
    const b = h("button", {
      class: "later-btn", type: "button", "aria-pressed": String(on),
      "aria-label": on ? "Remove from Watch later" : "Save to Watch later", title: on ? "In Watch later" : "Watch later"
    }, icon(on ? "check" : "later"));
    b.addEventListener("click", function () {
      const now = toggleIn(store.later, v.id);
      b.setAttribute("aria-pressed", String(now));
      b.setAttribute("aria-label", now ? "Remove from Watch later" : "Save to Watch later");
      b.replaceChildren(icon(now ? "check" : "later"));
      if (state.view === "later") render();
    });
    return b;
  }

  function videoCard(v) {
    return h("article", { class: "card" },
      h("div", { class: "thumb-wrap" },
        h("button", { class: "thumb", type: "button", "aria-label": "Play " + v.title, onclick: function () { openWatch(v.id); } },
          thumbFace(v, false), h("span", { class: "dur", text: fmtLen(v.len) })),
        laterButton(v)
      ),
      h("div", { class: "card-text" },
        h("button", { class: "card-title", type: "button", title: v.title, onclick: function () { openWatch(v.id); }, text: v.title }),
        h("div", { class: "meta" }, h("button", { class: "ch-link", type: "button", onclick: function () { go("channel", v.channel); }, text: v.channel })),
        h("div", { class: "meta", text: statLine(v) })
      )
    );
  }

  function videoRow(v, big, note) {
    return h("div", { class: "row" + (big ? " big" : "") },
      h("button", { class: "thumb", type: "button", "aria-label": "Play " + v.title, onclick: function () { openWatch(v.id); } },
        thumbFace(v, false), h("span", { class: "dur", text: fmtLen(v.len) })),
      h("div", { class: "card-text" },
        h("button", { class: "card-title", type: "button", title: v.title, onclick: function () { openWatch(v.id); }, text: v.title }),
        h("div", { class: "meta" }, h("button", { class: "ch-link", type: "button", onclick: function () { go("channel", v.channel); }, text: v.channel })),
        h("div", { class: "meta", text: note || statLine(v) })
      )
    );
  }

  /* ---------- Sidebar ---------- */
  const NAV = [["home", "Home"], ["longs", "Longs"], ["liked", "Liked"], ["history", "History"],
               ["later", "Watch later"], ["categories", "Categories"], ["playlists", "Playlists"], ["settings", "Settings"]];

  function renderSide() {
    const side = document.getElementById("side");
    const before = side.querySelector(".side-scroll");
    const keep = before ? before.scrollTop : 0;
    side.replaceChildren();
    // On phones the menu covers the top bar, so it has its own header (pinned, it never scrolls).
    const logo = document.querySelector(".brand svg");
    side.appendChild(h("div", { class: "side-head" },
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close menu", onclick: closeDrawer }, icon("menu")),
      h("button", { class: "brand side-brand", type: "button", onclick: function () { state.cat = null; state.sub = null; go("home"); } },
        logo ? logo.cloneNode(true) : null, h("span", null, "Hack", h("b", null, "Harbor")))));
    const list = h("div", { class: "side-scroll" });
    NAV.forEach(function (n) {
      const current = state.view === n[0] || (n[0] === "playlists" && state.view === "playlist") || (n[0] === "categories" && ["recent", "gems", "all"].indexOf(state.view) >= 0);
      list.appendChild(h("button", {
        class: "nav-item", type: "button", title: n[1], "aria-current": current ? "page" : null,
        onclick: function () { state.cat = null; state.sub = null; state.plq = ""; go(n[0]); }
      }, icon(n[0], "sm"), h("span", { class: "lbl", text: n[1] })));
    });
    const extra = h("div", { class: "side-extra" }, h("hr"), h("div", { class: "side-label", text: "Explore" }));
    CAT_LIST.forEach(function (c) {
      extra.appendChild(h("button", {
        class: "nav-item", type: "button", "aria-current": state.view === "home" && state.cat === c.key ? "page" : null,
        onclick: function () { setCat(c.key); }
      }, icon("hash", "sm"), h("span", { class: "lbl", text: c.name })));
    });
    list.appendChild(extra);
    list.addEventListener("scroll", shadeSide, { passive: true });
    side.appendChild(list);
    list.scrollTop = keep;
    requestAnimationFrame(shadeSide);
  }

  /* ---------- Lists, filters ---------- */
  function applyFilters(list) {
    const f = state.filters;
    let out = list.filter(function (v) {
      const d = daysSince(v.date);
      if (f.date === "week" && d > 7) return false;
      if (f.date === "month" && d > 31) return false;
      if (f.date === "year" && d > 365) return false;
      if (f.len === "short" && v.len >= 1200) return false;
      if (f.len === "mid" && (v.len < 1200 || v.len > 3600)) return false;
      if (f.len === "long" && v.len <= 3600) return false;
      if (f.kind !== "any" && v.type !== f.kind) return false;
      return true;
    });
    if (store.prefs.hideWatched) {
      const seen = new Set(store.history.map(function (x) { return x.id; }));
      out = out.filter(function (v) { return !seen.has(v.id); });
    }
    if (store.disliked.length) {
      const no = new Set(store.disliked);
      out = out.filter(function (v) { return !no.has(v.id); });
    }
    // Value order unless you ask for Most viewed; equal view counts keep value order.
    if (f.sort === "views") out = out.slice().sort(function (a, b) { return b.views - a.views || b.p - a.p; });
    else if (f.sort === "new") out = out.slice().sort(function (a, b) { return b.date.localeCompare(a.date) || b.p - a.p; });
    return out;
  }

  function filterGroups() {
    return [
      ["date", "Upload date", [["any", "Any time"], ["week", "This week"], ["month", "This month"], ["year", "This year"]]],
      ["len", "Duration", [["any", "Any length"], ["short", "Under 20 minutes"], ["mid", "20 to 60 minutes"], ["long", "Over an hour"]]],
      ["kind", "Type", [["any", "Any type"]].concat(TYPE_LIST.map(function (t) { return [t.key, t.name]; }))],
      ["sort", "Sort by", [["rank", "Best first"], ["new", "New"], ["views", "Most viewed"]]]
    ];
  }

  function filtersUI() {
    const wrap = h("div");
    const f = state.filters;
    const active = f.date !== "any" || f.len !== "any" || f.kind !== "any" || f.sort !== "rank";
    const scroller = h("div", { class: "chip-scroll", id: "chip-scroll", role: "group", "aria-label": "Categories" });
    const shown = CAT_LIST.filter(function (c) { return store.prefs.chips.indexOf(c.key) >= 0 || c.key === state.cat; });
    [[null, "All"]].concat(shown.map(function (c) { return [c.key, c.name]; })).forEach(function (c) {
      scroller.appendChild(h("button", {
        class: "cat-chip", type: "button", "aria-pressed": String(state.cat === c[0]),
        onclick: function () { state.cat = c[0]; state.sub = null; refresh(); }
      }, c[1]));
    });
    scroller.appendChild(h("button", {
      class: "cat-chip add-chip", type: "button", "aria-expanded": String(!!state.pickChips),
      "aria-label": "Choose categories", title: "Choose categories",
      onclick: function () { state.pickChips = !state.pickChips; render(); }
    }, "+"));
    wrap.appendChild(h("div", { class: "toolbar" }, scroller,
      h("button", {
        class: "pill-btn" + (active ? " is-on" : ""), type: "button", "aria-expanded": String(state.showFilters), "aria-label": "Filters",
        onclick: function () { state.showFilters = !state.showFilters; render(); }
      }, icon("filters", "sm"), h("span", { class: "pb-label", text: "Filters" + (active ? " · on" : "") }))));
    if (state.pickChips) {
      const pick = h("div", { class: "chip-pick", role: "group", "aria-label": "Categories to show" },
        h("p", { class: "cp-head", text: "Show these categories here" }));
      CAT_LIST.forEach(function (c) {
        const id = "chip-" + c.key;
        const cb = h("input", { type: "checkbox", id: id });
        cb.checked = store.prefs.chips.indexOf(c.key) >= 0;
        cb.addEventListener("change", function () {
          const on = new Set(store.prefs.chips);
          if (cb.checked) on.add(c.key); else on.delete(c.key);
          store.prefs.chips = CAT_LIST.map(function (x) { return x.key; }).filter(function (k) { return on.has(k); });
          saveStore();
          render();
        });
        pick.appendChild(h("label", { class: "check-row", for: id }, cb, c.name));
      });
      pick.appendChild(h("button", { class: "ghost-btn", type: "button", onclick: function () { state.pickChips = false; render(); } }, "Done"));
      wrap.appendChild(pick);
    }
    if (state.cat && SUBS[state.cat]) {
      const subs = h("div", { class: "sub-row", role: "group", "aria-label": CATS[state.cat] + " topics" });
      [[null, "All"]].concat(SUBS[state.cat]).forEach(function (s) {
        subs.appendChild(h("button", {
          class: "sub-chip", type: "button", "aria-pressed": String(state.sub === s[0]),
          onclick: function () { state.sub = s[0]; refresh(); }
        }, s[1]));
      });
      wrap.appendChild(subs);
    }
    if (state.showFilters) {
      const panel = h("div", { class: "filters" });
      filterGroups().forEach(function (g) {
        const box = h("div", { class: "f-group" }, h("h2", { text: g[1] }));
        g[2].forEach(function (o) {
          box.appendChild(h("button", {
            class: "f-opt", type: "button", "aria-pressed": String(f[g[0]] === o[0]),
            onclick: function () { f[g[0]] = o[0]; render(); }
          }, o[1]));
        });
        panel.appendChild(box);
      });
      wrap.appendChild(panel);
    }
    return wrap;
  }

  /* Lists render a screenful at a time and grow as you scroll, so long pages open instantly. */
  const PAGE = 16;
  const PAUSE_MS = 650;
  let observers = [];
  function progressive(container, list, make) {
    let i = 0, busy = false;
    const sentinel = h("div", { class: "more", role: "status" });
    container.appendChild(sentinel);
    function add(animate) {
      const end = Math.min(list.length, i + PAGE);
      const frag = document.createDocumentFragment();
      for (; i < end; i++) {
        const node = make(list[i]);
        if (animate) node.classList.add("enter");
        frag.appendChild(node);
      }
      container.insertBefore(frag, sentinel);
      sentinel.replaceChildren();
      if (i >= list.length) { if (io) io.disconnect(); sentinel.remove(); }
    }
    function more() {
      if (busy || i >= list.length) return;
      busy = true;
      sentinel.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), h("span", { class: "sr-only", text: "Loading more videos" }));
      setTimeout(function () {
        busy = false;
        if (!container.isConnected) return;
        add(true);
        // Still at the bottom (a tall screen)? Keep going, one calm step at a time.
        if (i < list.length && sentinel.getBoundingClientRect().top < window.innerHeight) more();
      }, PAUSE_MS);
    }
    const io = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) more();
    }, { rootMargin: "0px 0px 80px 0px" }) : null;
    i = 0;
    const first = Math.min(list.length, 24);
    const frag = document.createDocumentFragment();
    for (; i < first; i++) frag.appendChild(make(list[i]));
    container.insertBefore(frag, sentinel);
    if (i < list.length) {
      if (io) { io.observe(sentinel); observers.push(io); }
      else while (i < list.length) add(false);
    } else sentinel.remove();
    return container;
  }
  function gridOf(list) { return progressive(h("div", { class: "grid" }), list, videoCard); }
  function rowsOf(list, make) { return progressive(h("div", { class: "rows" }), list, make); }
  function emptyState(iconName, text) { return h("div", { class: "empty" }, icon(iconName), h("p", { text: text })); }
  function head(title, sub, extra) {
    return h("div", { class: "page-head" },
      h("div", null, h("h1", { text: title }), sub ? h("div", { class: "sub", text: sub }) : null), extra || null);
  }
  function byP(list) { return list.slice().sort(function (a, b) { return b.p - a.p; }); }

  function filteredView(main, title, base) {
    if (state.cat) base = byP(base.filter(function (v) { return v.cats.indexOf(state.cat) >= 0; }));
    if (state.sub) base = base.filter(function (v) { return v.subs.indexOf(state.sub) >= 0; });
    if (title) main.appendChild(head(title, plural(base.length, "video")));
    main.appendChild(filtersUI());
    const list = applyFilters(base);
    state.context = list.map(function (v) { return v.id; });
    let emptyText = "Nothing matches these filters. Try a wider time range or another length.";
    if (!base.length && state.sub) emptyText = "No " + SUB_NAME[state.sub] + " videos yet. New picks land here as the pipeline finds them.";
    else if (!base.length && state.cat) emptyText = "No " + CATS[state.cat] + " videos yet. New picks land here as the pipeline finds them.";
    main.appendChild(list.length ? gridOf(list) : emptyState("filters", emptyText));
  }

  /* ---------- Views ---------- */
  function viewHome(main) {
    const note = sampleNote();
    if (note) main.appendChild(note);
    let base;
    if (state.cat) base = VIDEOS;
    else {
      const top = HOME.map(function (id) { return BY_ID[id]; });
      const seen = new Set(HOME);
      base = top.concat(byP(VIDEOS).filter(function (v) { return !seen.has(v.id); }));
    }
    filteredView(main, null, base);
  }
  function viewLongs(main) {
    filteredView(main, "Longs", byP(VIDEOS.filter(function (v) { return v.len >= LONGS_MIN * 60; })));
  }
  function viewChannel(main) {
    const list = byP(VIDEOS.filter(function (v) { return v.channel === state.arg; }));
    main.appendChild(head(state.arg, plural(list.length, "video") + " on HackHarbor"));
    state.context = list.map(function (v) { return v.id; });
    main.appendChild(gridOf(list));
  }
  /* ---------- Search ----------
     Finds the closest videos, not only exact matches: a missing or swapped letter
     ("kerberosting"), half a word ("deserializ"), a plural, a category or topic name ("red team",
     "mobile"), or a word that's only in the video's tags or subject terms. Videos matching more of the query come first;
     among those, closer matches and more valuable videos lead. */
  const STOPWORDS = new Set(["the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "how", "what", "is", "are",
    "my", "your", "i", "we", "you", "it", "this", "that", "video", "videos", "about", "from", "by"]);
  let SX = null;
  function norm(str) {
    return (str || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  }
  function toks(str) { const n = norm(str); return n ? n.split(" ") : []; }
  function buildSearch() {
    const vocab = new Map();
    function add(tok, i, w) {
      if (tok.length < 2) return;
      let m = vocab.get(tok);
      if (!m) { m = new Map(); vocab.set(tok, m); }
      if ((m.get(i) || 0) < w) m.set(i, w);
    }
    VIDEOS.forEach(function (v, i) {
      toks(v.title).forEach(function (t) { add(t, i, 3); });
      toks(v.channel).forEach(function (t) { add(t, i, 2.5); });
      toks(v.words).forEach(function (t) { add(t, i, 1.6); });
      v.tags.forEach(function (tag) { toks(tag).forEach(function (t) { add(t, i, 1.6); }); });
      v.subs.forEach(function (x) { toks(SUB_NAME[x]).forEach(function (t) { add(t, i, 1.2); }); });
      v.cats.forEach(function (x) { toks(CATS[x]).forEach(function (t) { add(t, i, 1); }); });
      v._title = norm(v.title);
    });
    const subjects = [];
    // A category or topic named in the query ("red team", "mobile", "active directory") brings in
    // its videos. Subject words inside titles and tags are already in each video's words.
    CAT_LIST.forEach(function (c) {
      subjects.push({ toks: toks(c.name), cat: c.key });
      (c.topics || []).forEach(function (tp) { subjects.push({ toks: toks(tp.name), cat: c.key, sub: tp.key }); });
    });
    SX = { vocab: vocab, keys: Array.from(vocab.keys()), subjects: subjects.filter(function (x) { return x.toks.length; }) };
  }
  function editDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    let prev = [], cur = [], prevPrev = [];
    for (let j = 0; j <= b.length; j++) prev[j] = j;
    for (let i = 1; i <= a.length; i++) {
      cur = [i];
      let best = i;
      for (let j = 1; j <= b.length; j++) {
        const cost = a[i - 1] === b[j - 1] ? 0 : 1;
        let d = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d = Math.min(d, (prevPrev[j - 2] || 0) + 1);
        cur[j] = d;
        if (d < best) best = d;
      }
      if (best > max) return max + 1;
      prevPrev = prev; prev = cur;
    }
    return prev[b.length];
  }
  function similar(q, t) {
    if (q === t) return 1;
    if (q.length >= 3 && t.indexOf(q) === 0) return 0.85;                                    // still typing
    if (t.length >= 4 && q.indexOf(t) === 0 && q.length - t.length <= 3) return 0.8;         // plural, -ing, -ed
    const max = q.length >= 8 ? 2 : q.length >= 5 ? 1 : 0;
    if (!max) return 0;
    const d = editDistance(q, t, max);
    if (d > max) return 0;
    return d === 1 ? 0.75 : 0.6;
  }
  function phraseAt(sub, qt) {
    // Where a subject's words appear in a row in the query (each allowing a typo).
    for (let start = 0; start + sub.length <= qt.length; start++) {
      let ok = true;
      for (let k = 0; k < sub.length && ok; k++) ok = similar(qt[start + k], sub[k]) >= 0.75;
      if (ok) { const at = []; for (let k = 0; k < sub.length; k++) at.push(start + k); return at; }
    }
    return null;
  }
  function searchVideos(query) {
    if (!SX) buildSearch();
    let qt = toks(query);
    const meaningful = qt.filter(function (t) { return !STOPWORDS.has(t); });
    if (meaningful.length) qt = meaningful;
    if (!qt.length) return [];
    const per = qt.map(function () { return new Map(); });
    function hit(qi, i, sc) { if ((per[qi].get(i) || 0) < sc) per[qi].set(i, sc); }
    qt.forEach(function (q, qi) {
      for (let k = 0; k < SX.keys.length; k++) {
        const sim = similar(q, SX.keys[k]);
        // Squared, so an exact word always beats a near miss: "ippsec" finds IppSec before AppSec.
        if (sim) SX.vocab.get(SX.keys[k]).forEach(function (w, i) { hit(qi, i, sim * sim * w); });
      }
    });
    SX.subjects.forEach(function (sj) {
      const at = phraseAt(sj.toks, qt);
      if (!at) return;
      VIDEOS.forEach(function (v, i) {
        if (sj.sub ? v.subs.indexOf(sj.sub) >= 0 : v.cats.indexOf(sj.cat) >= 0) at.forEach(function (qi) { hit(qi, i, sj.sub ? 1.3 : 1.0); });
      });
    });
    const n = qt.length, need = n <= 2 ? 1 : Math.ceil(n * 0.6), phrase = qt.join(" ");
    const acc = new Map();
    per.forEach(function (m) { m.forEach(function (sc, i) { const e = acc.get(i) || { sum: 0, n: 0 }; e.sum += sc; e.n++; acc.set(i, e); }); });
    const out = [];
    acc.forEach(function (e, i) {
      if (e.n < need) return;
      const v = VIDEOS[i];
      let rel = (e.sum / n) * (e.n / n);
      if (n > 1 && v._title.indexOf(phrase) >= 0) rel += 1.5;
      out.push([v, e.n, rel + 0.12 * v.p]);
    });
    out.sort(function (a, b) { return b[1] - a[1] || b[2] - a[2]; });
    return out.map(function (x) { return x[0]; });
  }
  /* Playlists are searched the same way as videos (typos, half words, plurals, closest match
     first). A playlist is found by its name, its channel, and what its videos are about, so
     "kerberoasting" finds an Active Directory series even if its name doesn't say it. */
  let PX = null;
  function allPlaylists() {
    return PLAYLISTS.filter(function (p) { return p.ids.some(function (id) { return BY_ID[id]; }); })
      .concat(store.playlists.map(function (p) { return { key: p.key, name: p.name, by: "Yours", kind: "mine", ids: p.ids, score: 0 }; }));
  }
  function buildPlaylistSearch() {
    const items = allPlaylists();
    const vocab = new Map();
    function add(tok, i, w) {
      if (tok.length < 2) return;
      let m = vocab.get(tok);
      if (!m) { m = new Map(); vocab.set(tok, m); }
      if ((m.get(i) || 0) < w) m.set(i, w);
    }
    items.forEach(function (pl, i) {
      toks(pl.name).forEach(function (t) { add(t, i, 3); });
      toks(pl.by).forEach(function (t) { add(t, i, 2.5); });
      let value = 0, n = 0;
      pl.ids.slice(0, 80).forEach(function (id) {
        const v = known(id);
        if (!v) return;
        value += v.p; n++;
        toks(v.title).forEach(function (t) { add(t, i, 0.7); });
        v.subs.forEach(function (x) { toks(SUB_NAME[x]).forEach(function (t) { add(t, i, 0.9); }); });
        v.cats.forEach(function (x) { toks(CATS[x]).forEach(function (t) { add(t, i, 0.6); }); });
      });
      pl._value = pl.score || (n ? value / n : 0);
      pl._name = norm(pl.name);
    });
    PX = { items: items, vocab: vocab, keys: Array.from(vocab.keys()) };
  }
  function searchPlaylists(query) {
    if (!PX) buildPlaylistSearch();
    let qt = toks(query);
    const meaningful = qt.filter(function (t) { return !STOPWORDS.has(t) && t !== "playlist" && t !== "playlists"; });
    if (meaningful.length) qt = meaningful;
    if (!qt.length) return [];
    const acc = new Map();
    qt.forEach(function (q) {
      const best = new Map();
      for (let k = 0; k < PX.keys.length; k++) {
        const sim = similar(q, PX.keys[k]);
        if (sim) PX.vocab.get(PX.keys[k]).forEach(function (w, i) { const sc = sim * sim * w; if ((best.get(i) || 0) < sc) best.set(i, sc); });
      }
      best.forEach(function (sc, i) { const e = acc.get(i) || { sum: 0, n: 0 }; e.sum += sc; e.n++; acc.set(i, e); });
    });
    const n = qt.length, need = n <= 2 ? 1 : Math.ceil(n * 0.6), phrase = qt.join(" ");
    const out = [];
    acc.forEach(function (e, i) {
      if (e.n < need) return;
      const pl = PX.items[i];
      let rel = (e.sum / n) * (e.n / n);
      if (pl._name.indexOf(phrase) >= 0) rel += 2;
      out.push([pl, e.n, rel + 0.12 * pl._value]);
    });
    out.sort(function (a, b) { return b[1] - a[1] || b[2] - a[2]; });
    // When enough playlists match every word, partial matches are just noise.
    const full = out.filter(function (x) { return x[1] === n; });
    return (n > 1 && full.length >= 3 ? full : out).map(function (x) { return x[0]; });
  }
  function ownerOf(pl) { return pl.kind === "creator" ? pl.by : pl.kind === "mine" ? "Private" : "Updated automatically"; }

  function viewSearch(main) {
    const found = searchVideos(state.query);
    const title = "Results for “" + state.query + "”";
    if (!found.length) {
      main.appendChild(head(title, "0 videos"));
      main.appendChild(emptyState("filters", "Nothing close to that yet. Try a channel name or a topic like SSRF, Kerberos or prompt injection."));
      return;
    }
    filteredView(main, title, found);
    const pls = searchPlaylists(state.query);
    if (pls.length) {
      const strip = h("section", { class: "pl-strip", "aria-label": "Matching playlists" },
        h("div", { class: "pl-strip-head" }, h("h2", { class: "sec-title", text: "Playlists" }),
          pls.length > 4 ? h("button", { class: "ghost-btn", type: "button", onclick: function () { state.plq = state.query; go("playlists"); } }, "See all " + pls.length) : null),
        (function () { const g = h("div", { class: "grid" }); pls.slice(0, 4).forEach(function (pl) { g.appendChild(playlistCard(pl, ownerOf(pl))); }); return g; })());
      const headEl = main.querySelector(".page-head");
      if (headEl) headEl.after(strip); else main.prepend(strip);
    }
  }
  function viewSaved(main, key, title, iconName, emptyText) {
    const list = store[key].map(known).filter(Boolean);
    main.appendChild(head(title, plural(list.length, "video")));
    state.context = list.map(function (v) { return v.id; });
    main.appendChild(list.length ? gridOf(list) : emptyState(iconName, emptyText));
  }
  function viewHistory(main) {
    const items = store.history.filter(function (x) { return known(x.id); });
    const clear = items.length ? h("button", { class: "ghost-btn", type: "button", onclick: function () { store.history = []; saveStore(); render(); } }, "Clear history") : null;
    main.appendChild(head("History", store.prefs.pauseHistory ? "History is paused" : plural(items.length, "video"), clear));
    if (!items.length) { main.appendChild(emptyState("history", "Videos you open show up here, newest first.")); return; }
    state.context = items.map(function (x) { return x.id; });
    main.appendChild(rowsOf(items, function (x) { return videoRow(known(x.id), true, "Watched " + agoFromMs(x.t)); }));
  }
  const SHELVES = [["recent", "Recently recommended"], ["longs", "Longs"], ["gems", "Hidden gems"], ["all", "All videos"]];
  function viewCategories(main) {
    main.appendChild(head("Categories", null));
    main.appendChild(h("div", { class: "shelf-chips", role: "group", "aria-label": "Collections" },
      SHELVES.map(function (x) {
        return h("button", { class: "shelf-chip", type: "button", onclick: function () { state.cat = null; state.sub = null; go(x[0]); } }, x[1]);
      })));
    const cards = h("div", { class: "cat-cards" });
    CAT_LIST.forEach(function (c) {
      const n = VIDEOS.filter(function (v) { return v.cats.indexOf(c.key) >= 0; }).length;
      const topics = (SUBS[c.key] || []).map(function (s) {
        return [s[0], s[1], VIDEOS.filter(function (v) { return v.subs.indexOf(s[0]) >= 0; }).length];
      }).filter(function (t) { return t[2] > 0; }).sort(function (a, b) { return b[2] - a[2]; });
      cards.appendChild(h("section", { class: "cat-card c-" + c.key },
        h("button", { class: "cc-head", type: "button", onclick: function () { setCat(c.key); } },
          h("span", { class: "cc-name", text: c.name }), h("span", { class: "cc-n", text: String(n) })),
        topics.length ? h("ul", { class: "cc-topics" }, topics.map(function (t) {
          return h("li", null, h("button", { type: "button", onclick: function () { setCat(c.key, t[0]); } },
            h("span", { text: t[1] }), h("span", { class: "cc-n", text: String(t[2]) })));
        })) : null));
    });
    main.appendChild(cards);
  }
  function viewShelf(main, title, list) {
    main.appendChild(head(title, plural(list.length, "video")));
    state.context = list.map(function (v) { return v.id; });
    main.appendChild(list.length ? gridOf(list) : emptyState("categories", "Nothing here yet. The pipeline adds videos every two hours."));
  }
  function viewRecent(main) {
    viewShelf(main, "Recently recommended", byP(VIDEOS.filter(function (v) { return daysSince(v.date) <= 30; })));
  }
  function viewGems(main) {
    viewShelf(main, "Hidden gems", byP(VIDEOS.filter(function (v) { return v.gem; })));
  }
  function viewAll(main) {
    filteredView(main, "All videos", byP(VIDEOS));
  }
  function playlistCard(pl, owner) {
    const ids = pl.ids.filter(function (id) { return known(id); });
    const first = known(ids[0]);
    const open = function () { go("playlist", pl.key); };
    return h("article", { class: "card" },
      h("div", { class: "thumb-wrap" },
        h("button", { class: "thumb", type: "button", "aria-label": "Open playlist " + pl.name, onclick: open },
          first ? thumbFace(first, false) : h("div", { class: "frame c-default" }),
          h("span", { class: "count-badge" }, icon("playlists"), plural(ids.length, "video")))),
      h("div", { class: "card-text" },
        h("button", { class: "card-title", type: "button", onclick: open, text: pl.name }),
        h("div", { class: "meta", text: owner })));
  }
  function viewPlaylists(main) {
    main.appendChild(head("Playlists", null));
    const input = h("input", { type: "search", class: "pl-search-input", placeholder: "Search playlists", "aria-label": "Search playlists", autocomplete: "off", maxlength: "100" });
    input.value = state.plq || "";
    const body = h("div", { class: "pl-body" });
    let timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        state.plq = input.value.trim();
        history.replaceState(null, "", hashFor());
        fillPlaylists(body);
      }, 160);
    });
    main.appendChild(h("form", { class: "pl-search", role: "search", onsubmit: function (e) { e.preventDefault(); } }, icon("search", "sm"), input));
    main.appendChild(body);
    fillPlaylists(body);
  }
  function fillPlaylists(body) {
    body.replaceChildren();
    if (state.plq) {
      const found = searchPlaylists(state.plq);
      body.appendChild(h("p", { class: "pl-count", text: plural(found.length, "playlist") }));
      if (!found.length) { body.appendChild(emptyState("playlists", "No playlist close to that. Try a channel name, a box series, or a topic like Active Directory.")); return; }
      body.appendChild(progressive(h("div", { class: "grid" }), found, function (pl) { return playlistCard(pl, ownerOf(pl)); }));
      return;
    }
    const live = PLAYLISTS.filter(function (p) { return p.ids.some(function (id) { return BY_ID[id]; }); });
    const creators = live.filter(function (p) { return p.kind === "creator"; });
    const auto = live.filter(function (p) { return p.kind !== "creator"; });
    if (creators.length) {
      body.appendChild(h("h2", { class: "sec-title", text: "From creators" }));
      const g0 = h("div", { class: "grid" });
      creators.forEach(function (pl) { g0.appendChild(playlistCard(pl, pl.by)); });
      body.appendChild(g0);
    }
    body.appendChild(h("h2", { class: "sec-title", text: "From HackHarbor" }));
    if (auto.length) {
      const g1 = h("div", { class: "grid" });
      auto.forEach(function (pl) { g1.appendChild(playlistCard(pl, "Updated automatically")); });
      body.appendChild(g1);
    } else {
      body.appendChild(emptyState("playlists", "Playlists appear here once a topic has enough strong videos."));
    }
    body.appendChild(h("h2", { class: "sec-title", text: "Yours" }));
    if (!store.playlists.length) {
      body.appendChild(emptyState("save", "Make a playlist from Save under any video. Playlists stay in this browser."));
    } else {
      const g2 = h("div", { class: "grid" });
      store.playlists.forEach(function (pl) { g2.appendChild(playlistCard(pl, "Private")); });
      body.appendChild(g2);
    }
  }
  function findPlaylist(key) {
    return PLAYLISTS.find(function (p) { return p.key === key; }) || store.playlists.find(function (p) { return p.key === key; });
  }
  function viewPlaylist(main) {
    const pl = findPlaylist(state.arg);
    if (!pl) { go("playlists"); return; }
    const mine = store.playlists.indexOf(pl) >= 0;
    const list = pl.ids.map(function (id) { return BY_ID[id] || (mine ? known(id) : null); }).filter(Boolean);
    const del = mine ? h("button", { class: "ghost-btn", type: "button", onclick: function () { store.playlists.splice(store.playlists.indexOf(pl), 1); saveStore(); go("playlists"); } }, "Delete playlist") : null;
    const yt = pl.yt ? h("a", { class: "ghost-btn link-btn", href: "https://www.youtube.com/playlist?list=" + encodeURIComponent(pl.yt), target: "_blank", rel: "noopener" }, "Open on YouTube") : null;
    main.appendChild(head(pl.name, (mine ? "Private" : (pl.by || "HackHarbor")) + " · " + plural(list.length, "video"), del || yt));
    state.context = list.map(function (v) { return v.id; });
    main.appendChild(list.length ? gridOf(list) : emptyState("save", "This playlist is empty. Add videos with Save under any video."));
  }
  function viewSettings(main) {
    main.appendChild(head("Settings", null));
    const input = h("input", { id: "pref-frames", type: "checkbox", role: "switch", "aria-describedby": "pref-frames-hint" });
    input.checked = !!store.prefs.showFrames;
    input.addEventListener("change", function () { store.prefs.showFrames = input.checked; saveStore(); });
    main.appendChild(h("div", { class: "settings" },
      h("div", { class: "set-row" },
        h("div", { class: "s-text" },
          h("label", { class: "s-name", for: "pref-frames", text: "Show video frames instead of thumbnails" }),
          h("span", { class: "s-hint", id: "pref-frames-hint", text: "YouTube thumbnails are designed by uploaders to grab your attention: big faces, arrows, shouting text. With this on, every video is shown with a frame from the middle of the video instead, so you see what it actually looks like. Turn it off to see the original thumbnails." })),
        h("span", { class: "switch" }, input, h("span")))));
  }
  function viewAbout(main) {
    main.appendChild(head("About HackHarbor", null));
    const updated = GENERATED ? "Updated " + agoFromMs(new Date(GENERATED).getTime()) + " · " + plural(VIDEOS.length, "video") : "";
    main.appendChild(h("div", { class: "about" },
      h("p", { text: "Hand-picked security videos. No shorts, no noise." }),
      h("p", { text: "Every two hours a pipeline checks hundreds of YouTube channels, plus Hacker News, Reddit, Lobsters and Mastodon, for new videos and the ones people are talking about. It keeps only those worth a practitioner's time: new videos once they've proven themselves, and older ones only when what they teach still holds." }),
      h("p", { text: "There's no sign-in. Liked videos, history, watch later and playlists stay in your browser." }),
      updated ? h("p", { class: "stat", text: updated }) : null));
  }

  /* ---------- Watch page ---------- */
  function openWatch(id) {
    if (!store.prefs.pauseHistory) {
      store.history = store.history.filter(function (x) { return x.id !== id; });
      store.history.unshift({ id: id, t: Date.now() });
      store.history = store.history.slice(0, 300);
      saveStore();
    }
    go("watch", id);
  }

  function actButton(iconName, label, pressed, onClick, extra) {
    const b = h("button", Object.assign({ class: "act", type: "button", "aria-pressed": pressed == null ? null : String(pressed) }, extra || {}),
      icon(iconName), label);
    b.addEventListener("click", function () { onClick(b); });
    return b;
  }

  function savePanel(v) {
    const panel = h("div", { class: "save-panel" }, h("h2", { text: "Save to" }));
    function box(id, label, checked, onChange) {
      const cb = h("input", { type: "checkbox", id: id });
      cb.checked = checked;
      cb.addEventListener("change", onChange);
      panel.appendChild(h("label", { class: "check-row", for: id }, cb, label));
    }
    box("save-later", "Watch later", store.later.indexOf(v.id) >= 0, function () { toggleIn(store.later, v.id); });
    store.playlists.forEach(function (pl, i) {
      box("save-pl-" + i, pl.name, pl.ids.indexOf(v.id) >= 0, function () { toggleIn(pl.ids, v.id); });
    });
    const input = h("input", { id: "new-playlist", type: "text", placeholder: "New playlist name", maxlength: "60", "aria-label": "New playlist name" });
    const err = h("p", { class: "err", hidden: true });
    input.addEventListener("input", function () { err.hidden = true; });
    const form = h("form", { class: "new-pl" }, input, h("button", { class: "ghost-btn", type: "submit" }, "Create"));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const name = input.value.trim();
      if (!name) { err.textContent = "Enter a name for the playlist."; err.hidden = false; return; }
      if (store.playlists.some(function (p) { return p.name.toLowerCase() === name.toLowerCase(); })) {
        err.textContent = "You already have a playlist with that name."; err.hidden = false; return;
      }
      store.playlists.push({ key: "u" + Date.now().toString(36), name: name, ids: [v.id] });
      saveStore();
      panel.replaceWith(savePanel(v));
    });
    panel.appendChild(form);
    panel.appendChild(err);
    return panel;
  }

  function section(title, body) { return h("section", { class: "w-sec" }, h("h2", { class: "w-h2", text: title }), body); }

  function fillDetails(box, v, d) {
    const meta = [SAMPLE ? null : fmtViews(v.views), fmtAgo(v.date), (TYPE_NAME[v.type] || v.type || "").replace(/s$/, "").toLowerCase(), v.ever ? "evergreen" : null, v.gem ? "hidden gem" : null]
      .filter(Boolean).join(" · ");
    const chips = h("div", { class: "chips" },
      v.cats.map(function (c) { return h("button", { class: "chip", type: "button", onclick: function () { setCat(c); } }, CATS[c] || c); }),
      v.subs.map(function (s) { return h("button", { class: "chip", type: "button", onclick: function () { setCat(SUB_OF[s], s); } }, SUB_NAME[s] || s); }),
      (d.tags && d.tags.length ? d.tags : v.tags).map(function (t) { return h("button", { class: "chip tag", type: "button", onclick: function () { searchFor(t); } }, "#" + t); }));
    const body = h("div", { class: "desc-body" },
      d.summary ? h("p", { class: "summary", text: d.summary }) : null,
      d.why ? h("p", { class: "why", text: d.why }) : null,
      chips);
    const toggle = h("button", { class: "desc-more", type: "button", "aria-expanded": "false" }, "more");
    const descBox = h("div", { class: "desc folded" }, h("div", { class: "meta-strong", text: meta }), body, toggle);
    toggle.addEventListener("click", function () {
      const open = descBox.classList.toggle("folded") === false;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Show less" : "more";
    });
    // Only fold when there's more than a few lines to hide.
    requestAnimationFrame(function () { if (body.scrollHeight <= body.clientHeight + 4) { descBox.classList.remove("folded"); toggle.remove(); } });
    const parts = [descBox];
    if (d.ideas && d.ideas.length) {
      parts.push(section("Key ideas", h("ul", { class: "ideas" }, d.ideas.map(function (x) { return h("li", { text: x }); }))));
    }
    if (d.read && d.read.length) {
      parts.push(section("Read alongside", h("div", { class: "reads" }, d.read.map(function (r) {
        let host = "";
        try { host = new URL(r.url).hostname.replace(/^www\./, ""); } catch (e) { host = ""; }
        if (!safeHref(r.url)) return null;
        return h("a", { class: "read", href: safeHref(r.url), target: "_blank", rel: "noopener noreferrer" },
          h("span", { class: "r-type", text: r.type }),
          h("span", { class: "r-body" }, h("span", { class: "r-title", text: r.title }), h("span", { class: "r-host", text: host })));
      }))));
    }
    if (d.discussed && d.discussed.length) {
      parts.push(section("Where it's discussed", h("div", { class: "discussed" }, d.discussed.map(function (m) {
        const bits = [];
        if (m.points) bits.push(plural(m.points, "point"));
        if (m.comments) bits.push(plural(m.comments, "comment"));
        if (m.date) bits.push(m.date);
        if (!safeHref(m.url)) return null;
        return h("a", { class: "disc", href: safeHref(m.url), target: "_blank", rel: "noopener noreferrer" },
          h("span", { class: "d-src", text: m.source }),
          m.title ? h("span", { class: "d-title", text: m.title }) : null,
          bits.length ? h("span", { class: "d-meta", text: bits.join(" · ") }) : null);
      }))));
    }
    if (d.quotes && d.quotes.length) {
      parts.push(section("From the comments", h("div", { class: "quotes" }, d.quotes.map(function (q) {
        return h("blockquote", { class: "quote" }, q.text, h("footer", { text: "YouTube comment" + (q.likes ? " · " + plural(q.likes, "like") : "") }));
      }))));
    }
    box.replaceChildren.apply(box, parts);
  }

  function viewWatch(main) {
    const v = isId(state.arg) ? known(state.arg) : null;
    if (!v) { go("home"); return; }

    let player;
    if (CFG.embed) {
      player = h("div", { class: "player" }, h("iframe", {
        src: "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(v.id) + "?autoplay=1&rel=0&playsinline=1",
        title: v.title,
        allow: "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen",
        allowfullscreen: true, referrerpolicy: "strict-origin-when-cross-origin"
      }));
    } else {
      player = h("div", { class: "player" }, thumbFace(v, true),
        h("a", { class: "play-big", href: ytLink(v), target: "_blank", rel: "noopener", "aria-label": "Play on YouTube" }, icon("play")),
        h("div", { class: "player-note", text: CFG.note || "This page can't run YouTube's player here. On the site the video plays in place." }));
    }

    const who = h("div", { class: "w-sub" });
    const detailBox = h("div", null, h("p", { class: "detail-wait", text: "Loading details…" }));
    let saveBox = null;

    const likeBtn = actButton("liked", store.liked.indexOf(v.id) >= 0 ? "Liked" : "Like", store.liked.indexOf(v.id) >= 0, function () {
      const on = toggleIn(store.liked, v.id);
      if (on) { drop(store.disliked, v.id); dislikeBtn.setAttribute("aria-pressed", "false"); }
      likeBtn.setAttribute("aria-pressed", String(on));
      likeBtn.lastChild.textContent = on ? "Liked" : "Like";
    });
    const dislikeBtn = actButton("liked", "", store.disliked.indexOf(v.id) >= 0, function () {
      const on = toggleIn(store.disliked, v.id);
      if (on) { drop(store.liked, v.id); likeBtn.setAttribute("aria-pressed", "false"); likeBtn.lastChild.textContent = "Like"; }
      dislikeBtn.setAttribute("aria-pressed", String(on));
    }, { "aria-label": "Not for me", title: "Not for me: hide it from Home" });
    dislikeBtn.querySelector("svg").setAttribute("class", "i flip");
    const laterOn = store.later.indexOf(v.id) >= 0;
    const laterBtn = actButton(laterOn ? "check" : "later", "Watch later", laterOn, function () {
      const on = toggleIn(store.later, v.id);
      laterBtn.setAttribute("aria-pressed", String(on));
      laterBtn.replaceChild(icon(on ? "check" : "later"), laterBtn.querySelector("svg"));
    });
    // Our own full-screen button: some browsers (TVs, iPhones) won't let YouTube's player go full
    // screen from inside the page, but they will for the page's own player box. Where even that
    // isn't available, the player fills the window instead, with a button to come back.
    const fullBtn = actButton("expand", "Full screen", null, function () { enterFull(player); });
    const saveBtn = actButton("save", "Save", null, function () {
      if (saveBox) { saveBox.remove(); saveBox = null; saveBtn.setAttribute("aria-expanded", "false"); return; }
      saveBox = savePanel(v);
      row.after(saveBox);
      saveBtn.setAttribute("aria-expanded", "true");
    }, { "aria-expanded": "false" });

    const row = h("div", { class: "w-row" },
      h("div", { class: "w-who" }, h("button", { class: "ch-name", type: "button", onclick: function () { go("channel", v.channel); }, text: v.channel }), who),
      h("div", { class: "actions" },
        h("div", { class: "split" }, likeBtn, dislikeBtn), fullBtn, laterBtn, saveBtn,
        h("a", { class: "act", href: ytLink(v), target: "_blank", rel: "noopener" }, icon("external"), "YouTube")));

    const left = h("div", { class: "w-main" }, player, h("h1", { class: "w-title", text: v.title }), row, detailBox);

    const ctx = state.context && state.context.indexOf(v.id) >= 0 ? state.context : HOME;
    const sameTopic = byP(VIDEOS.filter(function (x) { return x.id !== v.id && x.subs.some(function (s) { return v.subs.indexOf(s) >= 0; }); }));
    const next = [];
    ctx.concat(sameTopic.map(function (x) { return x.id; }), byP(VIDEOS).map(function (x) { return x.id; })).forEach(function (id) {
      if (id !== v.id && next.indexOf(id) < 0 && BY_ID[id] && next.length < 12) next.push(id);
    });
    const rows = h("div", { class: "rows" });
    next.forEach(function (id) { rows.appendChild(videoRow(BY_ID[id], false)); });
    main.appendChild(h("div", { class: "watch" }, left, h("aside", { class: "w-next", "aria-label": "Up next" }, h("h2", { class: "next-head", text: "Up next" }), rows)));

    detail(v.id).then(function (d) {
      if (state.view !== "watch" || state.arg !== v.id) return;
      who.textContent = d.who && d.who !== v.channel ? d.who : "";
      fillDetails(detailBox, v, d);
    });
  }

  /* ---------- Navigation ---------- */
  function isNarrow() { return window.matchMedia("(max-width: 640px)").matches; }
  function searchFor(t) {
    const q = t.replace(/-/g, " ");
    state.query = q; state.cat = null; state.sub = null;
    document.getElementById("search-input").value = q;
    go("search");
  }
  function setCat(k, sub) {
    state.cat = k; state.sub = sub || null;
    go(["longs", "search"].indexOf(state.view) >= 0 ? state.view : "home");
  }
  function hashFor() {
    const parts = [state.view];
    if (state.arg != null) parts.push(state.arg);
    else if (state.view === "search" && state.query) parts.push(state.query);
    let hsh = "#/" + parts.map(encodeURIComponent).join("/");
    if (state.cat) hsh += "?cat=" + encodeURIComponent(state.cat) + (state.sub ? "&topic=" + encodeURIComponent(state.sub) : "");
    else if (state.view === "playlists" && state.plq) hsh += "?q=" + encodeURIComponent(state.plq);
    return hsh === "#/home" ? "#/" : hsh;
  }
  function readHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "");
    const qs = raw.indexOf("?");
    const path = (qs >= 0 ? raw.slice(0, qs) : raw).split("/").filter(Boolean).map(function (x) { try { return decodeURIComponent(x); } catch (e) { return x; } });
    const params = new URLSearchParams(qs >= 0 ? raw.slice(qs + 1) : "");
    const view = path[0] || "home";
    state.view = VIEWS.indexOf(view) >= 0 ? view : "home";
    state.arg = null;
    if (state.view === "search") { state.query = path[1] || ""; const box = document.getElementById("search-input"); if (box) box.value = state.query; }
    else if (path[1] != null) state.arg = path[1];
    const cat = params.get("cat");
    state.cat = cat && CATS[cat] ? cat : null;
    const sub = params.get("topic");
    state.sub = state.cat && sub && SUB_OF[sub] === state.cat ? sub : null;
    state.plq = state.view === "playlists" ? (params.get("q") || "").slice(0, 100) : "";
  }
  const VIEWS = ["home", "longs", "liked", "history", "later", "categories", "recent", "gems", "all", "channel", "playlists", "playlist", "settings", "about", "search", "watch"];
  function go(view, arg) {
    state.view = view;
    state.arg = arg == null ? null : arg;
    closeDrawer();
    const want = hashFor();
    if (location.hash !== want && !(want === "#/" && !location.hash)) history.pushState(null, "", want);
    render();
    window.scrollTo(0, 0);
  }
  function refresh() { history.replaceState(null, "", hashFor()); render(); }
  window.addEventListener("popstate", function () { readHash(); closeDrawer(); render(); });
  function enterFull(el) {
    const req = el.requestFullscreen || el.webkitRequestFullscreen || el.webkitEnterFullscreen;
    if (req) {
      try {
        const done = req.call(el);
        if (done && typeof done.catch === "function") done.catch(function () { fakeFull(el, true); });
        return;
      } catch (e) { /* fall through to the in-page version */ }
    }
    fakeFull(el, true);
  }
  function fakeFull(el, on) {
    document.documentElement.classList.toggle("player-max", on);
    let exit = el.querySelector(".player-exit");
    if (on && !exit) {
      exit = h("button", { class: "player-exit", type: "button", onclick: function () { fakeFull(el, false); } }, "Exit full screen");
      el.appendChild(exit);
      exit.focus();
    } else if (!on && exit) {
      exit.remove();
    }
  }
  document.addEventListener("keydown", function (e) {
    if ((e.key === "Escape" || e.key === "Backspace" || e.key === "GoBack") && document.documentElement.classList.contains("player-max")) {
      const p = document.querySelector(".player");
      if (p) { e.preventDefault(); fakeFull(p, false); }
    }
  });
  function render() {
    document.documentElement.classList.remove("player-max");
    renderSide();
    requestAnimationFrame(shadeSide);
    const old = document.getElementById("chip-scroll");
    const chipX = old ? old.scrollLeft : 0;
    const main = document.getElementById("main");
    observers.forEach(function (o) { o.disconnect(); });
    observers = [];
    main.replaceChildren();
    switch (state.view) {
      case "longs": viewLongs(main); break;
      case "liked": viewSaved(main, "liked", "Liked videos", "liked", "Videos you like show up here. Tap Like under any video."); break;
      case "history": viewHistory(main); break;
      case "later": viewSaved(main, "later", "Watch later", "later", "Save videos for later with the clock on any thumbnail."); break;
      case "categories": viewCategories(main); break;
      case "recent": viewRecent(main); break;
      case "gems": viewGems(main); break;
      case "all": viewAll(main); break;
      case "channel": viewChannel(main); break;
      case "playlists": viewPlaylists(main); break;
      case "playlist": viewPlaylist(main); break;
      case "settings": viewSettings(main); break;
      case "about": viewAbout(main); break;
      case "search": viewSearch(main); break;
      case "watch": viewWatch(main); break;
      default: viewHome(main);
    }
    const chips = document.getElementById("chip-scroll");
    if (chips) chips.scrollLeft = chipX;
  }
  /* The menu list fades out at an edge that still has more to scroll, and stops fading once
     you reach that end, so it's clear where the list stops. */
  function shadeSide() {
    const list = document.querySelector("#side .side-scroll");
    if (!list) return;
    const rest = list.scrollHeight - list.clientHeight;
    list.classList.toggle("shade-top", list.scrollTop > 2);
    list.classList.toggle("shade-bottom", rest > 2 && list.scrollTop < rest - 2);
  }
  window.addEventListener("resize", shadeSide);
  // While the menu is open on a phone, the page behind it doesn't scroll; only the menu does.
  function lockPage(on) { document.documentElement.classList.toggle("drawer-open", on); }
  function closeDrawer() {
    lockPage(false);
    document.getElementById("side").classList.remove("open");
    document.getElementById("scrim").classList.remove("open");
    document.getElementById("menu-btn").setAttribute("aria-expanded", "false");
  }

  document.getElementById("menu-btn").addEventListener("click", function () {
    const btn = document.getElementById("menu-btn");
    if (isNarrow()) {
      const side = document.getElementById("side");
      const open = !side.classList.contains("open");
      side.classList.toggle("open", open);
      document.getElementById("scrim").classList.toggle("open", open);
      lockPage(open);
      btn.setAttribute("aria-expanded", String(open));
      requestAnimationFrame(shadeSide);
    } else {
      const shell = document.getElementById("shell");
      shell.classList.toggle("full");
      btn.setAttribute("aria-expanded", String(shell.classList.contains("full")));
      requestAnimationFrame(shadeSide);
    }
  });
  document.getElementById("scrim").addEventListener("click", closeDrawer);
  document.getElementById("brand").addEventListener("click", function () { state.cat = null; state.sub = null; go("home"); });
  document.getElementById("about-btn").addEventListener("click", function () { go("about"); });
  document.getElementById("search-form").addEventListener("submit", function (e) {
    e.preventDefault();
    const q = document.getElementById("search-input").value.trim();
    if (!q) { go("home"); return; }
    state.query = q; state.cat = null; state.sub = null;
    go("search");
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });

  function boot() {
    const main = document.getElementById("main");
    main.replaceChildren(h("div", { class: "status-box", text: "Loading videos…" }));
    Promise.all([getJSON("taxonomy.json"), getJSON("index.json"), getJSON("feed.json")])
      .then(function (r) { setup(r[0], r[1], r[2]); readHash(); render(); })
      .catch(function () {
        main.replaceChildren(h("div", { class: "status-box" },
          h("p", { text: "The video list couldn't be loaded. Check your connection and try again." }),
          h("button", { class: "ghost-btn", type: "button", onclick: boot }, "Try again")));
      });
  }
  boot();
})();
