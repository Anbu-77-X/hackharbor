"""Loads the YAML config and compiles the taxonomy into matchers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _term_regex(term: str) -> re.Pattern:
    escaped = re.escape(term.lower())
    return re.compile(r"(?<![\w])" + escaped + r"(?![\w])", re.IGNORECASE)


@dataclass
class Terms:
    items: list[tuple[re.Pattern, float, str]]

    @classmethod
    def build(cls, mapping: dict | None) -> "Terms":
        items = []
        for term, weight in (mapping or {}).items():
            items.append((_term_regex(str(term)), float(weight), str(term)))
        return cls(items)


@dataclass
class Topic:
    key: str
    name: str
    terms: Terms
    search: list[str]


@dataclass
class Category:
    key: str
    name: str
    terms: Terms
    topics: list[Topic]
    fresh_months: float
    classic_max_years: float
    classic_min_score: float
    requires_security_context: bool = False

    def topic(self, key: str) -> Topic | None:
        return next((t for t in self.topics if t.key == key), None)


@dataclass
class Taxonomy:
    categories: list[Category]
    security: Terms
    reject: list[re.Pattern]
    types: list[dict]
    topic_owner: dict[str, str] = field(default_factory=dict)

    def security_score(self, text: str) -> float:
        """How much security vocabulary a short text (a title) has."""
        return sum(w for rx, w, _ in self.security.items if rx.search(text or ""))

    def names_security_topic(self, text: str) -> bool:
        """True when a short text uses a strong, unambiguous term from a security-only category
        (reverse engineering, kerberoasting, XSS...). AI, Cloud, Fundamentals and Hardware terms
        don't count: they're just as common in general tech."""
        if not hasattr(self, "_strong"):
            self._strong = [rx for c in self.categories
                            if c.key in ("bounty", "red", "exploit", "blue", "crypto", "ctf")
                            for terms in [c.terms] + [t.terms for t in c.topics]
                            for rx, w, _ in terms.items if w >= 4]
        return any(rx.search(text or "") for rx in self._strong)

    def category(self, key: str) -> Category | None:
        return next((c for c in self.categories if c.key == key), None)

    @property
    def keys(self) -> list[str]:
        return [c.key for c in self.categories]

    def public(self) -> dict:
        """What the site needs to draw chips and labels, and the terms behind each category and
        topic so search can find a video by its subject even when the title doesn't say it."""
        def words(terms) -> list[str]:
            return [name for _, w, name in terms.items if w > 0]
        return {
            "categories": [
                {"key": c.key, "name": c.name, "terms": words(c.terms),
                 "topics": [{"key": t.key, "name": t.name, "terms": words(t.terms)} for t in c.topics]}
                for c in self.categories
            ],
            "types": self.types,
        }

    def keywords(self, text: str) -> list[str]:
        """Taxonomy terms found in a short text (a title and its tags), in one pass."""
        if not hasattr(self, "_finder"):
            names = {name.lower() for c in self.categories for terms in [c.terms] + [t.terms for t in c.topics]
                     for _, w, name in terms.items if w > 0}
            names |= {name.lower() for _, w, name in self.security.items if w > 0}
            alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
            self._finder = re.compile(rf"(?<![\w])({alt})(?![\w])", re.I)
        seen: list[str] = []
        for m in self._finder.findall(text or ""):
            k = m.lower()
            if k not in seen:
                seen.append(k)
        return seen


@dataclass
class Config:
    root: Path
    taxonomy: Taxonomy
    channels: list[dict]
    sources: dict
    rules: dict

    def rule(self, *path, default=None):
        node = self.rules
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load(root: str | Path) -> Config:
    root = Path(root)
    tax_raw = _load_yaml(root / "taxonomy.yml")
    categories = []
    owner = {}
    for c in tax_raw.get("categories", []):
        topics = []
        for t in c.get("topics", []) or []:
            if t["key"] in owner:
                raise ValueError(f"topic key {t['key']} is used twice")
            owner[t["key"]] = c["key"]
            topics.append(Topic(t["key"], t["name"], Terms.build(t.get("terms")), list(t.get("search") or [])))
        categories.append(Category(
            key=c["key"],
            name=c["name"],
            terms=Terms.build(c.get("terms")),
            topics=topics,
            fresh_months=float(c.get("fresh_months", 18)),
            classic_max_years=float(c.get("classic_max_years", 8)),
            classic_min_score=float(c.get("classic_min_score", 8.5)),
            requires_security_context=bool(c.get("requires_security_context", False)),
        ))
    taxonomy = Taxonomy(
        categories=categories,
        security=Terms.build(tax_raw.get("security_terms")),
        reject=[_term_regex(t) for t in tax_raw.get("reject_terms", [])],
        types=list(tax_raw.get("types", [])),
        topic_owner=owner,
    )
    channels = _load_yaml(root / "channels.yml").get("channels", []) or []
    sources = _load_yaml(root / "sources.yml")
    rules = _load_yaml(root / "rules.yml")
    return Config(root=root, taxonomy=taxonomy, channels=channels, sources=sources, rules=rules)
