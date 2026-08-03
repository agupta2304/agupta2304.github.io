#!/usr/bin/env python3
"""Render the site from src/data (JSON) and src/posts (Markdown) into static HTML.

    python src/build.py            build once
    python src/build.py --serve    build, then serve the output on :8000
"""

from __future__ import annotations

import argparse
import json
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

LINK_ORDER = ("pdf", "arxiv", "doi", "code", "slides", "poster", "video", "bibtex", "site")
AUTHOR_LIMIT = 12   # lists longer than this get trimmed
AUTHOR_KEEP = 8     # to this many names, plus an "and N others"

STATS_FILE = DATA_DIR / "video-stats.json"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Venues with no parenthesised acronym to pull out.
VENUE_SHORT = {
    "arXiv preprint": "arXiv",
    "International Journal of Data Mining and Bioinformatics": "IJDMB",
    "Microbial Informatics and Experimentation": "Journal",
}
TALK_SECTIONS = (("tutorial", "Conference tutorials"), ("invited", "Invited talks"), ("other", "Other"))
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


def refresh_video_stats(ids: list[str], stats: dict) -> dict:
    """Read public view counts off the watch pages and cache them.

    Only runs under --refresh-views. The cache is committed, so ordinary builds
    need no network and stay byte-for-byte reproducible.
    """
    import urllib.request

    today = datetime.now(timezone.utc).date().isoformat()
    for video in ids:
        request = urllib.request.Request(
            f"https://www.youtube.com/watch?v={video}", headers={"User-Agent": BROWSER_UA})
        try:
            page = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
        except Exception as exc:
            print(f"  warn: could not fetch {video} ({exc}); keeping cached value")
            continue
        found = re.search(r'"viewCount":"(\d+)"', page)
        if not found:
            print(f"  warn: no view count in the page for {video}; keeping cached value")
            continue
        stats[video] = {"views": int(found.group(1)), "checked": today}
        print(f"  {video}: {int(found.group(1)):,} views")

    STATS_FILE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return stats


# --------------------------------------------------------------------------
# content loading
# --------------------------------------------------------------------------

def load_publications(me: str) -> list[dict]:
    pubs = load("publications.json")
    for p in pubs:
        p["links"] = ordered_links(p.get("links"))
        p["primary_link"] = next(
            (p["links"][k] for k in ("arxiv", "pdf", "site", "code") if k in p["links"]),
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
            cached = video_stats.get(youtube_id(t["video"]) or "")
            if cached:
                t["views"] = compact_count(cached["views"])
                t["views_as_of"] = month_year(cached["checked"][:7])
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


def build_stylesheet() -> None:
    """Concatenate the hand-written CSS with a Pygments theme per site theme.

    Cream is the default, so it goes in unscoped; the dark theme is prefixed with the
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


def person_schema(profile: dict, site: dict) -> str:
    schema = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": profile["name"],
        "url": site["url"] + "/",
        "jobTitle": profile["role"],
        "worksFor": {"@type": "Organization", "name": profile["affiliation"]},
        "sameAs": [u for u in profile["links"].values() if u.startswith("http")],
    }
    if profile.get("headshot"):
        schema["image"] = site["url"] + profile["headshot"]
    return json.dumps(schema, indent=2, ensure_ascii=False)


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

    publications = load_publications(profile["name"])
    selected = [p for p in publications if p.get("selected")]
    video_stats = load_video_stats()
    if refresh_views:
        wanted = [youtube_id(t["video"]) for t in load("talks.json") if t.get("video")]
        print("refreshing view counts:")
        video_stats = refresh_video_stats([v for v in wanted if v], video_stats)

    talk_groups = load_talks(video_stats)
    news = load_news(site.get("news_limit", 6))
    experience = load("experience.json")
    mentors = load("mentors.json")
    posts = load_posts()

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pretty_url"] = pretty_url

    shared = {
        "profile": profile,
        "site": site,
        "total_publications": len(publications),
        "selected_count": len(selected),
    }

    print("building:")
    build_stylesheet()

    write(ROOT / "index.html", env.get_template("home.html").render(
        page={"path": "/"},
        selected_groups=group_by_year(selected),
        talk_groups=talk_groups,
        news=news,
        experience=experience.get("roles", []),
        education=experience.get("education", []),
        mentors=mentors.get("people", []),
        mentors_intro=mentors.get("intro"),
        person_schema=person_schema(profile, site),
        **shared,
    ))

    write(ROOT / "publications" / "index.html", env.get_template("publications.html").render(
        page={"path": "/publications/"},
        all_groups=group_by_year(publications),
        **shared,
    ))

    write(ROOT / "blog" / "index.html", env.get_template("blog_index.html").render(
        page={"path": "/blog/"},
        posts=posts,
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
    print(f"done: {len(publications)} publications, {len(posts)} posts")
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
