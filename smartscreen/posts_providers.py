"""Pluggable data sources for the "top posts" page.

The interface is deliberately tiny:

    def get_posts(limit: int, options: dict) -> list[Post]

Choose a provider in config.json under "posts.provider":
  * "static"  - the strings in posts.options.items (good for testing)
  * "rss"     - any RSS/Atom feed URL in posts.options.url (no key needed)
  * "plugin"  - your own function, "module:function", e.g. "my_posts:get_posts".
                The module is imported from the app folder, so drop my_posts.py next to main.py.
                The function may return Post objects or plain strings.

TODO: an X (Twitter) provider. Do NOT hardcode credentials here. If you add one, read a
token from an environment variable or Windows Credential Manager (keyring package) and
implement it as a plugin so secrets never live in this repo or config.json.
"""
from __future__ import annotations

import importlib
import logging
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)


@dataclass
class Post:
    text: str
    meta: str = ""   # author, score, source, etc.


def _static(limit, options):
    items = options.get("items") or []
    return [Post(s) if isinstance(s, str) else Post(**s) for s in items][:limit]


def _rss(limit, options):
    url = options.get("url")
    if not url:
        raise ValueError("posts.options.url is required for the rss provider")
    req = urllib.request.Request(url, headers={"User-Agent": "smartscreen-local/0.1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        root = ET.fromstring(r.read())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    posts = []
    for item in root.iter("item"):                      # RSS 2.0
        posts.append(Post((item.findtext("title") or "").strip(),
                          (item.findtext("author") or item.findtext("category") or "").strip()))
    if not posts:                                        # Atom
        for e in root.findall("a:entry", ns):
            posts.append(Post((e.findtext("a:title", default="", namespaces=ns)).strip(),
                              (e.findtext("a:author/a:name", default="", namespaces=ns)).strip()))
    return [p for p in posts if p.text][:limit]


def _parse_dt(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s)                     # RSS pubDate
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))  # Atom ISO
    except Exception:
        return None


def _fetch_feed(url, label=""):
    """Return list of (Post, datetime|None) for one RSS/Atom feed."""
    req = urllib.request.Request(url, headers={"User-Agent": "smartscreen-local/0.1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        root = ET.fromstring(r.read())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for item in root.iter("item"):                          # RSS 2.0
        title = (item.findtext("title") or "").strip()
        dt = _parse_dt(item.findtext("pubDate"))
        if title:
            out.append((Post(title, label), dt))
    if not out:                                             # Atom
        for e in root.findall("a:entry", ns):
            title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
            dt = _parse_dt(e.findtext("a:updated", default="", namespaces=ns)
                           or e.findtext("a:published", default="", namespaces=ns))
            if title:
                out.append((Post(title, label), dt))
    return out


def _multi_rss(limit, options):
    """Rotate headlines across several labelled categories, each with one or more feeds.

    options.feeds = [{"label": "PHARMA", "urls": [...], "take": 2}, ...]
    Pulls the newest headlines per category and round-robins across categories so the
    screen always shows a mix. No key or login needed; refreshes on its own.
    """
    feeds = options.get("feeds") or []
    per_cat = []
    for f in feeds:
        label = (f.get("label") or "").strip()
        take = int(f.get("take", 3))
        collected = []
        for url in f.get("urls", []):
            try:
                collected.extend(_fetch_feed(url, label))
            except Exception as e:
                log.warning("feed failed (%s): %s", url, e)
        # newest first; items without a date fall to the back in original order
        collected.sort(key=lambda pd: pd[1] or datetime.min.replace(tzinfo=timezone.utc),
                       reverse=True)
        # dedup by title within the category
        seen, uniq = set(), []
        for post, _ in collected:
            key = post.text.lower()
            if key not in seen:
                seen.add(key)
                uniq.append(post)
        per_cat.append(uniq[:take])
    # round-robin across categories
    result, idx = [], 0
    while len(result) < limit and any(idx < len(c) for c in per_cat):
        for c in per_cat:
            if idx < len(c):
                result.append(c[idx])
                if len(result) >= limit:
                    break
        idx += 1
    return result[:limit]


def _plugin(limit, options):
    target = options.get("function", "")
    mod_name, _, fn_name = target.partition(":")
    if not mod_name or not fn_name:
        raise ValueError("posts.options.function must look like 'module:function'")
    fn = getattr(importlib.import_module(mod_name), fn_name)
    out = fn(limit, options)
    return [p if isinstance(p, Post) else Post(str(p)) for p in out][:limit]


PROVIDERS = {"static": _static, "rss": _rss, "multi_rss": _multi_rss, "plugin": _plugin}


def get_posts(provider: str, limit: int, options: dict) -> list[Post]:
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise ValueError(f"Unknown posts provider '{provider}'. Options: {', '.join(PROVIDERS)}")
    return fn(limit, options or {})
