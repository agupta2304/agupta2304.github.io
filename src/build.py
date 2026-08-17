#!/usr/bin/env python3
"""Render the site from src/data (JSON) and src/posts (Markdown) into static HTML.

    python src/build.py            build once
    python src/build.py --serve    build, then serve the output on :8000
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader
from pygments.formatters import HtmlFormatter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA_DIR = SRC / "data"
POSTS_DIR = SRC / "posts"
TEMPLATE_DIR = SRC / "templates"
ASSETS_DIR = ROOT / "assets"

# Generated directories, wiped on every build so deleted posts do not linger.
GENERATED_DIRS = (ROOT / "blog", ROOT / "publications")

LINK_ORDER = ("pdf", "arxiv", "doi", "preprint", "code", "slides",
              "poster", "video", "bibtex", "site")
AUTHOR_LIMIT = 12   # lists longer than this get trimmed
AUTHOR_KEEP = 8     # to this many names, plus an "and N others"

STATS_FILE = DATA_DIR / "video-stats.json"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Venues with no parenthesised acronym to pull out.
VENUE_SHORT = {
    "arXiv preprint": "arXiv",
    "arXiv technical report": "arXiv",
    "International Journal of Data Mining and Bioinformatics": "IJDMB",
    "Microbial Informatics and Experimentation": "Journal",
}
TALK_SECTIONS = (("tutorial", "Conference tutorials"), ("invited", "Invited talks"), ("other", "Other"))
# Chip labels when a talk appears as evidence under a research thread.
TALK_KINDS = {"tutorial": "Tutorial", "invited": "Talk", "other": "Talk"}
MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")

# Seeded content ships with deliberate placeholders; flag any that survive a build.
PLACEHOLDER_RE = re.compile(r"TODO|20XX")

WRITTEN: list[Path] = []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load(name: str):
    with (DATA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def pretty_url(url: str) -> str:
    if url.startswith("mailto:"):
        return url[len("mailto:"):]
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


def ordered_links(raw: dict | None) -> dict:
    """Put link buttons in a stable order, keeping unknown keys at the end."""
    if not raw:
        return {}
    known = [k for k in LINK_ORDER if raw.get(k)]
    extra = [k for k in raw if k not in LINK_ORDER and raw.get(k)]
    return {k: raw[k] for k in known + extra}


def month_year(value: str) -> str:
    """'2026-11' -> 'Nov 2026'. Passes anything unrecognised straight through."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(value))
    if not match:
        return str(value)
    year, month = match.groups()
    return f"{MONTHS[int(month) - 1][:3]} {year}"


def long_date(value: datetime) -> str:
    return f"{MONTHS[value.month - 1]} {value.day}, {value.year}"


def venue_parts(venue: str) -> tuple[str, str, str]:
    """Split a venue into (acronym for the chip, full name, qualifier).

    'Empirical Methods ... (EMNLP), Industry Track' -> ('EMNLP', 'Empirical Methods
    ... (EMNLP)', 'Industry Track')
    """
    base, _, qualifier = venue.partition(", ")
    short = VENUE_SHORT.get(base)
    if not short:
        parenthesised = re.search(r"\(([^)]+)\)$", base)
        workshop = re.fullmatch(r"(\S+) Workshop at (\S+)", base)
        if parenthesised:
            short = parenthesised.group(1)
        elif workshop:
            short = f"{workshop.group(1)}@{workshop.group(2)}"
        else:
            short = base
    return short, base, qualifier


def compact_count(number: int) -> str:
    if number < 1000:
        return str(number)
    if number < 1_000_000:
        return f"{number / 1000:.1f}K".replace(".0K", "K")
    return f"{number / 1_000_000:.1f}M".replace(".0M", "M")


def youtube_id(url: str) -> str | None:
    found = re.search(r"(?:v=|youtu\.be/|/embed/)([\w-]{11})", url)
    return found.group(1) if found else None


def load_video_stats() -> dict:
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    return {}


def stats_via_api(ids: list[str], key: str) -> dict[str, dict]:
    """One batched call to the YouTube Data API. Works from anywhere, needs a key."""
    import urllib.request

    url = ("https://www.googleapis.com/youtube/v3/videos"
           f"?part=snippet,statistics&id={','.join(ids)}&key={key}")
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)

    found = {}
    for item in payload.get("items", []):
        counts, snippet = item.get("statistics", {}), item.get("snippet", {})
        if "viewCount" not in counts:
            continue
        found[item["id"]] = {
            "views": int(counts["viewCount"]),
            "title": snippet.get("title", ""),
            "uploaded": (snippet.get("publishedAt") or "")[:10],
        }
    return found


def stats_via_page(video: str) -> dict | None:
    """Scrape the watch page. Fine from an ordinary connection, but YouTube withholds
    the count from datacenter IPs, so this is the local path rather than the CI one."""
    import urllib.request

    request = urllib.request.Request(
        f"https://www.youtube.com/watch?v={video}&hl=en&gl=US",
        headers={"User-Agent": BROWSER_UA,
                 "Accept-Language": "en-US,en;q=0.9",
                 "Cookie": "CONSENT=YES+cb"})
    try:
        page = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"  warn: could not fetch {video} ({exc})")
        return None

    views = re.search(r'"viewCount":"(\d+)"', page)
    if not views:
        return None
    uploaded = re.search(r'"publishDate":(?:\{"simpleText":)?"(\d{4}-\d{2}-\d{2})', page)
    title = re.search(r'"videoDetails":\{.*?"title":"((?:[^"\\]|\\.)*)"', page, re.S)

    name = ""
    if title:
        # Decode as a JSON string so \uXXXX escapes resolve and literal UTF-8 survives.
        try:
            name = json.loads(f'"{title.group(1)}"')
        except json.JSONDecodeError:
            name = title.group(1)

    return {
        "views": int(views.group(1)),
        "title": name,
        "uploaded": uploaded.group(1) if uploaded else "",
    }


def refresh_video_stats(ids: list[str], stats: dict) -> dict:
    """Update cached view counts. Only runs under --refresh-views.

    Prefers the Data API when YOUTUBE_API_KEY is set, since that is the only route
    that works from CI, and falls back to the watch page otherwise. A video that
    cannot be read keeps its cached value rather than losing its count.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    fetched: dict[str, dict] = {}

    if key:
        try:
            fetched = stats_via_api(ids, key)
            print(f"  via the YouTube Data API: read {len(fetched)} of {len(ids)}")
        except Exception as exc:
            print(f"  warn: Data API call failed ({exc}); trying the watch pages")
    else:
        print("  no YOUTUBE_API_KEY set; reading the watch pages instead")

    for video in ids:
        if video in fetched:
            continue
        found = stats_via_page(video)
        if found is None:
            print(f"  warn: no view count available for {video}; keeping cached value")
            continue
        fetched[video] = found

    for video, record in sorted(fetched.items()):
        stats[video] = {**record, "checked": today}
        print(f"  {video}: {record['views']:,} views")

    STATS_FILE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stats


# --------------------------------------------------------------------------
# content loading
# --------------------------------------------------------------------------

def load_publications(me: str) -> list[dict]:
    pubs = load("publications.json")
    for p in pubs:
        p["links"] = ordered_links(p.get("links"))
        # Open versions first, then the DOI as a last resort. Including the DOI matters
        # because research threads show only the linked title, so a DOI-only paper would
        # otherwise be a dead end there.
        p["primary_link"] = next(
            (p["links"][k]
             for k in ("arxiv", "pdf", "preprint", "site", "code", "doi")
             if k in p["links"]),
            None,
        )
        p["authors_shown"], p["authors_more"] = elide_authors(p.get("authors", []), me)
        p["venue_short"], p["venue_full"], qualifier = venue_parts(p.get("venue", ""))
        p["badges"] = [b for b in (qualifier, p.get("note")) if b]
    # Stable sort on year alone, so ordering within a year stays as authored in the JSON.
    pubs.sort(key=lambda p: p.get("year", 0), reverse=True)
    return pubs


def group_by_year(pubs: list[dict]) -> list[tuple[int, list[dict]]]:
    groups: dict[int, list[dict]] = {}
    for p in pubs:
        groups.setdefault(p.get("year", 0), []).append(p)
    return sorted(groups.items(), key=lambda kv: kv[0], reverse=True)


def load_research(pubs: list[dict], talks: list[dict]) -> list[dict]:
    """Resolve each thread's flagship, papers, and talks by title.

    Threads name their evidence rather than restating it, so venue, date, and link
    metadata are only ever edited in publications.json or talks.json. A missing or
    duplicated flagship is a hard error: silently dropping it would weaken the
    research narrative without anyone noticing.

    Papers and talks are flattened to the same shape here so the template renders one
    list without branching on what each entry happens to be.
    """
    papers_by_title = {p["title"]: p for p in pubs}
    talks_by_title = {t["title"]: t for t in talks}
    threads = load("research.json")
    seen_highlights = set()

    def paper_entry(paper: dict) -> dict:
        # A tutorial published in a conference's proceedings would otherwise show a
        # bare "KDD 2023" chip and read as a research paper, so label it explicitly.
        where = f"{paper['venue_short']} {paper['year']}"
        return {
            "kind": "paper",
            "chip": f"Tutorial @ {where}" if "Tutorials Track" in paper["venue"] else where,
            "chip_title": paper["venue_full"],
            "title": paper["title"],
            "url": paper.get("primary_link"),
            "links": paper["links"],
            "detail": None,
        }

    for thread in threads:
        spec = thread.get("highlight")
        if not isinstance(spec, dict):
            raise SystemExit(
                f"research.json: {thread.get('label', 'unnamed thread')!r} needs one "
                "'highlight' object"
            )
        title = spec.get("paper")
        contribution = spec.get("contribution")
        if not isinstance(title, str) or not title.strip():
            raise SystemExit(
                f"research.json: {thread.get('label', 'unnamed thread')!r} highlight "
                "needs a publication title"
            )
        if not isinstance(contribution, str) or not contribution.strip():
            raise SystemExit(f"research.json: highlight {title!r} needs a contribution")
        if title in seen_highlights:
            raise SystemExit(f"research.json: highlight {title!r} appears in multiple threads")
        if title in thread.get("papers", []):
            raise SystemExit(
                f"research.json: highlight {title!r} is repeated in its related papers"
            )
        paper = papers_by_title.get(title)
        if paper is None:
            raise SystemExit(
                f"research.json: no publication titled {title!r}. "
                "Titles must match publications.json exactly."
            )
        seen_highlights.add(title)
        thread["highlight"] = {
            **paper_entry(paper),
            "contribution": contribution.strip(),
        }

        entries = []
        for title in thread.get("papers", []):
            paper = papers_by_title.get(title)
            if paper is None:
                raise SystemExit(
                    f"research.json: no publication titled {title!r}. "
                    "Titles must match publications.json exactly."
                )
            entries.append(paper_entry(paper))

        for title in thread.get("talks", []):
            talk = talks_by_title.get(title)
            if talk is None:
                raise SystemExit(
                    f"research.json: no talk titled {title!r}. "
                    "Titles must match talks.json exactly."
                )
            # A paper's chip is "venue year", so a talk's says both what it is and
            # where: "Tutorial @ CIKM 2026". The event's leading segment carries the
            # venue, so whatever follows the comma goes under the title instead of
            # being repeated in the chip.
            venue, _, qualifier = talk["event"].partition(", ")
            kind = TALK_KINDS.get(talk.get("type", "other"), "Talk")
            detail = " &middot; ".join(p for p in (qualifier, talk["date"]) if p)
            entries.append({
                "kind": "talk",
                "chip": f"{kind} @ {venue}",
                "chip_title": talk["event"],
                "title": talk["title"],
                "url": talk["links"].get("video"),
                "detail": detail,
            })

        thread["entries"] = entries
    return threads


def load_writing() -> list[dict]:
    """Pieces published on someone else's blog. Listed rather than reproduced, since
    the canonical copy lives at the outlet."""
    items = load("writing.json")
    items.sort(key=lambda w: str(w.get("date", "")), reverse=True)
    for item in items:
        item["when"] = month_year(item.get("date", ""))
    return items


def writing_index(posts: list[dict], external: list[dict]) -> tuple[list[dict], list[dict]]:
    """One date-ordered list for /blog/, mixing posts here with pieces published
    elsewhere. Splitting them into two lists left the single local post orphaned in an
    unlabelled section above a labelled one, and put a note about the website above
    real engineering writing.

    Posts marked `minor` in their front matter drop out of the list and are mentioned
    in a footnote instead, so site housekeeping does not head the page.
    """
    entries = []
    for p in posts:
        if p.get("minor"):
            continue
        entries.append({
            "title": p["title"], "url": p["url"], "when": p["date_display"],
            "sort": p["date"].strftime("%Y-%m"), "outlet": None,
            "note": None, "summary": p.get("summary"),
        })
    for w in external:
        entries.append({
            "title": w["title"], "url": w["url"], "when": w["when"],
            "sort": str(w.get("date", "")), "outlet": w["outlet"],
            "note": w.get("note"), "summary": w.get("summary"),
        })
    entries.sort(key=lambda e: e["sort"], reverse=True)
    return entries, [p for p in posts if p.get("minor")]


def load_news(limit: int) -> list[dict]:
    items = load("news.json")
    items.sort(key=lambda n: str(n.get("date", "")), reverse=True)
    for item in items:
        item["when"] = month_year(item.get("date", ""))
    return items[:limit] if limit else items


def elide_authors(authors: list[str], me: str) -> tuple[list[str], int]:
    """Long industry author lists swamp the entry, so trim them but always keep `me`."""
    if len(authors) <= AUTHOR_LIMIT:
        return authors, 0
    kept = authors[:AUTHOR_KEEP]
    if me in authors and me not in kept:
        kept = kept[:AUTHOR_KEEP - 1] + [me]
    return kept, len(authors) - len(kept)


def load_talks(video_stats: dict) -> list[dict]:
    talks = load("talks.json")
    for t in talks:
        links = dict(t.get("links") or {})
        if t.get("video"):
            links["video"] = t["video"]
            t["video_id"] = youtube_id(t["video"])
            cached = video_stats.get(t["video_id"] or "")
            if cached:
                t["video_meta"] = cached
                t["views"] = compact_count(cached["views"])
                t["views_as_of"] = month_year(cached["checked"][:7])
        thumbnail = t.get("thumbnail")
        if thumbnail:
            target = (ROOT / thumbnail.lstrip("/")).resolve()
            try:
                target.relative_to(ROOT.resolve())
            except ValueError:
                target = None
            if (
                not thumbnail.startswith("/")
                or target is None
                or target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif"}
                or not target.is_file()
            ):
                raise SystemExit(
                    f"talks.json: thumbnail for {t['title']!r} must be a root-relative "
                    f"path to an existing file (got {thumbnail!r})"
                )
        t["featured_recording"] = bool(t.get("video") and thumbnail)
        t["links"] = ordered_links(links)
        t["sort_key"] = str(t.get("date", ""))
        t["date"] = month_year(t.get("date", ""))
    talks.sort(key=lambda t: t["sort_key"], reverse=True)

    groups = []
    for key, label in TALK_SECTIONS:
        # Key is "entries", not "items": Jinja would resolve `group.items` to dict.items.
        entries = [t for t in talks if t.get("type", "other") == key]
        if entries:
            groups.append({"label": label, "entries": entries})
    return groups


def load_posts() -> list[dict]:
    md = markdown.Markdown(
        extensions=["extra", "smarty", "sane_lists", "toc", "codehilite"],
        extension_configs={"codehilite": {"css_class": "codehilite", "guess_lang": False}},
    )

    posts = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta: dict = {}
        body = raw
        if raw.startswith("---"):
            _, front, body = raw.split("---", 2)
            meta = yaml.safe_load(front) or {}

        if meta.get("draft"):
            continue

        slug = meta.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
        published = meta.get("date")
        if isinstance(published, str):
            published = datetime.fromisoformat(published)
        elif hasattr(published, "year") and not isinstance(published, datetime):
            published = datetime(published.year, published.month, published.day)
        if published is None:
            raise SystemExit(f"{path.name}: front matter needs a 'date'")
        published = published.replace(tzinfo=timezone.utc)

        md.reset()
        posts.append({
            "title": meta.get("title", slug),
            "summary": meta.get("summary", ""),
            "math": bool(meta.get("math")),
            # Housekeeping rather than writing: still published, but kept out of the
            # main list on /blog/ so it does not outrank substantive pieces.
            "minor": bool(meta.get("minor")),
            "date": published,
            "date_display": long_date(published),
            "slug": slug,
            "url": f"/blog/{slug}/",
            "html": md.convert(body.strip()),
        })

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    WRITTEN.append(path)
    print(f"  {path.relative_to(ROOT)}")


def report_placeholders() -> None:
    counts = {}
    for path in WRITTEN:
        if path.suffix not in (".html", ".xml"):
            continue
        found = len(PLACEHOLDER_RE.findall(path.read_text(encoding="utf-8")))
        if found:
            counts[path] = found
    if not counts:
        return
    print(f"\n{sum(counts.values())} placeholders still on the site — edit src/data/*.json:")
    for path, found in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {found:3d}  {path.relative_to(ROOT)}")


def expand_prose(profile: dict, experience: dict, env: Environment) -> None:
    """Render the prose in the JSON data as Jinja, so a figure quoted in more than one
    place has a single definition. The customer count appears in both the bio and the
    Nubank role, and the two drifting apart reads as carelessness to a visitor.
    """
    def render(text: str) -> str:
        return env.from_string(text).render(profile=profile)

    profile["about"] = [render(p) for p in profile["about"]]
    for group in ("roles", "education"):
        for role in experience.get(group, []):
            if role.get("note"):
                role["note"] = render(role["note"])


def build_stylesheet() -> None:
    """Concatenate the hand-written CSS with a Pygments theme per site theme.

    Light is the default, so it goes in unscoped; the dark theme is prefixed with the
    data-theme selector, which also gives it the specificity to win when active.
    """
    css = (SRC / "styles" / "style.css").read_text(encoding="utf-8")
    light = HtmlFormatter(style="friendly").get_style_defs(".codehilite")
    dark = HtmlFormatter(style="nord").get_style_defs('[data-theme="dark"] .codehilite')
    combined = (
        f"{css}\n\n/* ---- syntax highlighting (generated) ---- */\n"
        f"{light}\n\n{dark}\n"
    )
    write(ASSETS_DIR / "style.css", combined)


def build_feed(posts: list[dict], profile: dict, site: dict) -> None:
    # Derived from the newest post rather than the clock, so rebuilds stay byte-identical.
    latest = posts[0]["date"] if posts else datetime(1970, 1, 1, tzinfo=timezone.utc)
    built = latest.strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = []
    for p in posts[:20]:
        link = site["url"] + p["url"]
        items.append(
            "    <item>\n"
            f"      <title>{escape(p['title'])}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(link)}</guid>\n"
            f"      <pubDate>{p['date'].strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>\n"
            f"      <description>{escape(p['summary'] or p['title'])}</description>\n"
            "    </item>"
        )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{escape(profile['name'])} — posts</title>\n"
        f"    <link>{escape(site['url'])}/</link>\n"
        f"    <description>{escape(profile['meta_description'])}</description>\n"
        "    <language>en-us</language>\n"
        f"    <lastBuildDate>{built}</lastBuildDate>\n"
        f'    <atom:link href="{escape(site["url"])}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )
    write(ROOT / "feed.xml", feed)


def home_schema(profile: dict, site: dict, talks: list[dict]) -> str:
    """Person and WebSite, plus a VideoObject per recorded talk carrying its view count."""
    person_id = site["url"] + "/#person"
    person = {
        "@type": "Person",
        "@id": person_id,
        "name": profile["name"],
        "url": site["url"] + "/",
        "jobTitle": profile["role"],
        "description": profile["meta_description"],
        "worksFor": {"@type": "Organization", "name": profile["affiliation"]},
        "sameAs": [u for u in profile["links"].values() if u.startswith("http")],
    }
    if profile.get("headshot"):
        person["image"] = site["url"] + profile["headshot"]
    if profile.get("knows_about"):
        person["knowsAbout"] = profile["knows_about"]
    if profile.get("alumni_of"):
        person["alumniOf"] = [{"@type": "CollegeOrUniversity", "name": n}
                              for n in profile["alumni_of"]]

    graph: list[dict] = [person, {
        "@type": "WebSite",
        "@id": site["url"] + "/#website",
        "url": site["url"] + "/",
        "name": profile["name"],
        "inLanguage": "en",
        "publisher": {"@id": person_id},
    }]

    for talk in talks:
        meta = talk.get("video_meta")
        if not meta:
            continue
        video = {
            "@type": "VideoObject",
            "name": meta.get("title") or talk["title"],
            "description": f"{talk['title']} — {talk['event']}",
            "url": talk["links"]["video"],
            "embedUrl": f"https://www.youtube.com/embed/{talk['video_id']}",
            "thumbnailUrl": f"https://i.ytimg.com/vi/{talk['video_id']}/hqdefault.jpg",
            "author": {"@id": person_id},
            "interactionStatistic": {
                "@type": "InteractionCounter",
                "interactionType": "https://schema.org/WatchAction",
                "userInteractionCount": meta["views"],
            },
        }
        if meta.get("uploaded"):
            video["uploadDate"] = meta["uploaded"]
        graph.append(video)

    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      indent=2, ensure_ascii=False)


def publications_schema(pubs: list[dict], profile: dict, site: dict) -> str:
    """An ordered ScholarlyArticle list, so the full record is machine-readable."""
    articles = []
    for p in pubs:
        article = {
            "@type": "ScholarlyArticle",
            "name": p["title"],
            "author": [{"@type": "Person", "name": a} for a in p.get("authors", [])],
        }
        if p.get("year"):
            article["datePublished"] = str(p["year"])
        if p.get("venue_full"):
            article["isPartOf"] = {"@type": "CreativeWork", "name": p["venue_full"]}
        if p.get("primary_link"):
            article["url"] = p["primary_link"]
        articles.append(article)

    # Emitted compactly: pretty-printing 36 papers with full author lists costs ~50KB
    # of whitespace, and no human reads this block.
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": f"Publications — {profile['name']}",
        "url": site["url"] + "/publications/",
        "about": {"@type": "Person", "name": profile["name"], "url": site["url"] + "/"},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(articles),
            "itemListElement": [{"@type": "ListItem", "position": i, "item": a}
                                for i, a in enumerate(articles, start=1)],
        },
    }, separators=(",", ":"), ensure_ascii=False)


def build_sitemap(site: dict, posts: list[dict]) -> None:
    entries = [(site["url"] + "/", None),
               (site["url"] + "/publications/", None),
               (site["url"] + "/blog/", None)]
    entries += [(site["url"] + p["url"], p["date"].date().isoformat()) for p in posts]

    body = "".join(
        "  <url>\n"
        f"    <loc>{escape(loc)}</loc>\n"
        + (f"    <lastmod>{lastmod}</lastmod>\n" if lastmod else "")
        + "  </url>\n"
        for loc, lastmod in entries)
    write(ROOT / "sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          f"{body}</urlset>\n")


def build_robots(site: dict) -> None:
    write(ROOT / "robots.txt",
          f"User-agent: *\nAllow: /\n\nSitemap: {site['url']}/sitemap.xml\n")


def build_llms_txt(profile: dict, site: dict, pubs: list[dict], research: list[dict],
                   talk_groups: list[dict], posts: list[dict],
                   writing: list[dict]) -> None:
    """A plain-text digest for agents. Not a standard, but cheap and harmless."""
    def strip_tags(text: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()

    lines = [f"# {profile['name']}", "", f"> {profile['meta_description']}", ""]
    if profile.get("thesis"):
        lines += [profile["thesis"], ""]
    if profile.get("thesis_detail"):
        lines += [profile["thesis_detail"], ""]
    lines += [strip_tags(p) + "\n" for p in profile["about"]]
    if profile.get("venues_note"):
        lines += [profile["venues_note"], ""]

    lines += ["## Pages", "",
              f"- [Home]({site['url']}/): research directions, news, talks, writing, "
              "experience, service, mentors",
              f"- [Publications]({site['url']}/publications/): complete list of "
              f"{len(pubs)} publications",
              f"- [Writing]({site['url']}/blog/): {len(posts)} post"
              f"{'' if len(posts) == 1 else 's'}",
              f"- [Feed]({site['url']}/feed.xml): RSS", ""]

    lines += ["## Research directions", ""]
    for thread in research:
        lines += [f"### {thread['label']}", "", thread["blurb"], ""]
        highlight = thread["highlight"]
        highlight_link = f" {highlight['url']}" if highlight.get("url") else ""
        lines += [
            f"Flagship: {highlight['title']} ({highlight['chip']}).{highlight_link}",
            "",
            highlight["contribution"],
            "",
        ]
        if thread["entries"]:
            lines += ["Related work:", ""]
        for entry in thread["entries"]:
            # A paper's chip already says venue and year, so only a talk's extra
            # detail is worth a parenthetical here.
            where = f" ({strip_tags(entry['detail'])})" if entry["detail"] else ""
            link = f" {entry['url']}" if entry.get("url") else ""
            lines.append(f"- [{entry['chip']}] {entry['title']}{where}.{link}")
        lines.append("")

    lines += ["## Talks", ""]
    for group in talk_groups:
        for t in group["entries"]:
            video = f" {t['links']['video']}" if t["links"].get("video") else ""
            lines.append(f"- {t['title']} — {t['event']}, {t['date']}.{video}")
    lines.append("")

    if writing:
        lines += ["## Writing elsewhere", ""]
        for w in writing:
            lines.append(f"- {w['title']} ({w['outlet']}, {w['when']}). {w['url']}")
        lines.append("")

    if profile.get("service"):
        lines += ["## Service", ""]
        lines += [f"- {row['label']}: {row['value']}" for row in profile["service"]]
        lines.append("")

    lines += ["## Elsewhere", ""]
    lines += [f"- {label}: {href}" for label, href in profile["links"].items()]
    write(ROOT / "llms.txt", "\n".join(lines) + "\n")


def build(refresh_views: bool = False) -> None:
    for directory in GENERATED_DIRS:
        if directory.exists():
            shutil.rmtree(directory)

    profile = load("profile.json")
    site = load("site.json")
    site["year"] = datetime.now().year
    site["url"] = site["url"].rstrip("/")

    # Never ship a broken portrait: fall back to the monogram if the file is absent.
    headshot = profile.get("headshot")
    if headshot and not (ROOT / headshot.lstrip("/")).exists():
        print(f"  note: {headshot} not found, using the monogram instead")
        profile["headshot"] = None

    # The CV is a file to drop in rather than a URL, so the link appears on its own
    # once the file exists and stays hidden until then.
    cv = profile.get("cv")
    if cv and not (ROOT / cv.lstrip("/")).exists():
        print(f"  note: {cv} not found, leaving the CV out of the Elsewhere list")
        profile["cv"] = None

    publications = load_publications(profile["name"])
    video_stats = load_video_stats()
    if refresh_views:
        wanted = [youtube_id(t["video"]) for t in load("talks.json") if t.get("video")]
        print("refreshing view counts:")
        video_stats = refresh_video_stats([v for v in wanted if v], video_stats)

    talk_groups = load_talks(video_stats)
    all_talks = [t for group in talk_groups for t in group["entries"]]
    home_talk_groups = []
    for group in talk_groups:
        featured_entries = [t for t in group["entries"] if t["featured_recording"]]
        entries = [t for t in group["entries"] if not t["featured_recording"]]
        if featured_entries or entries:
            home_talk_groups.append({
                "label": group["label"],
                "featured_entries": featured_entries,
                "entries": entries,
            })
    research = load_research(publications, all_talks)
    news = load_news(site.get("news_limit", 6))
    experience = load("experience.json")
    mentors = load("mentors.json")
    posts = load_posts()
    writing = load_writing()
    writing_entries, minor_posts = writing_index(posts, writing)

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pretty_url"] = pretty_url
    expand_prose(profile, experience, env)

    shared = {
        "profile": profile,
        "site": site,
        "total_publications": len(publications),
        # One colophon is not a body of writing, and linking it from the nav promises
        # essays the site cannot deliver. Pieces published elsewhere count, since the
        # page is worth a click once it lists them.
        "writing_linked": len(posts) + len(writing) >= site.get("writing_min_posts", 3),
    }

    print("building:")
    build_stylesheet()

    write(ROOT / "index.html", env.get_template("home.html").render(
        page={"path": "/"},
        research=research,
        talk_groups=home_talk_groups,
        writing_entries=writing_entries[:site.get("home_writing_limit", 3)],
        news=news,
        experience=experience.get("roles", []),
        education=experience.get("education", []),
        mentors=mentors.get("people", []),
        mentors_intro=mentors.get("intro"),
        person_schema=home_schema(profile, site, all_talks),
        **shared,
    ))

    write(ROOT / "publications" / "index.html", env.get_template("publications.html").render(
        page={"path": "/publications/"},
        all_groups=group_by_year(publications),
        page_schema=publications_schema(publications, profile, site),
        **shared,
    ))

    write(ROOT / "blog" / "index.html", env.get_template("blog_index.html").render(
        page={"path": "/blog/"},
        entries=writing_entries,
        minor_posts=minor_posts,
        **shared,
    ))

    post_template = env.get_template("post.html")
    for post in posts:
        write(ROOT / "blog" / post["slug"] / "index.html", post_template.render(
            page={"path": post["url"], "og_type": "article"},
            post=post,
            **shared,
        ))

    build_feed(posts, profile, site)
    build_sitemap(site, posts)
    build_robots(site)
    build_llms_txt(profile, site, publications, research, talk_groups, posts, writing)
    print(f"done: {len(publications)} publications, {len(posts)} posts")
    unlinked = [p["title"] for p in publications if not p.get("links")]
    if unlinked:
        print(f"\n{len(unlinked)} publications have no link yet:")
        for title in unlinked:
            print(f"  {title[:68]}")
    report_placeholders()


def serve(port: int) -> None:
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    print(f"\nserving http://localhost:{port}  (ctrl-c to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="serve the site after building")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-views", action="store_true",
                        help="re-read YouTube view counts into src/data/video-stats.json")
    args = parser.parse_args()

    build(refresh_views=args.refresh_views)
    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
