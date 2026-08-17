"""Structural checks on the generated site.

    python src/check.py               structure, links, palettes, contrast
    python src/check.py --external    also fetch every outbound link

Exits non-zero if anything is wrong, so it works as a pre-push guard. The network
sweep is opt-in because it is slow and can fail for reasons that are not our fault.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

CHECK_EXTERNAL = "--external" in sys.argv[1:]

problems: list[str] = []
warnings: list[str] = []


class Balance(HTMLParser):
    def __init__(self, label):
        super().__init__(convert_charrefs=True)
        self.label = label
        self.stack = []
        self.refs = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.refs.append(d[key])
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            problems.append(f"{self.label}: stray </{tag}> at line {self.getpos()[0]}")
            return
        open_tag, line = self.stack.pop()
        if open_tag != tag:
            problems.append(
                f"{self.label}: </{tag}> at line {self.getpos()[0]} closes <{open_tag}> opened at line {line}")


pages = sorted(ROOT.glob("*.html")) + sorted(ROOT.glob("*/index.html")) + sorted(ROOT.glob("*/*/index.html"))
pages = [p for p in pages if ".venv" not in p.parts]

all_refs: dict[str, list[str]] = {}
for page in pages:
    label = str(page.relative_to(ROOT))
    parser = Balance(label)
    parser.feed(page.read_text(encoding="utf-8"))
    if parser.stack:
        problems.append(f"{label}: unclosed {[t for t, _ in parser.stack]}")
    all_refs[label] = parser.refs

# internal links must resolve to a real file
for label, refs in all_refs.items():
    for ref in refs:
        parsed = urlparse(ref)
        if parsed.scheme or ref.startswith("#") or ref.startswith("mailto:"):
            continue
        path = parsed.path
        if not path.startswith("/"):
            problems.append(f"{label}: relative link {ref!r} (site expects root-absolute)")
            continue
        target = ROOT / path.lstrip("/")
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            problems.append(f"{label}: dead link {ref!r}")

# in-page anchors referenced by the nav must exist on the homepage
home = (ROOT / "index.html").read_text(encoding="utf-8")
for anchor in re.findall(r'href="/#([\w-]+)"', home):
    if f'id="{anchor}"' not in home:
        problems.append(f"index.html: nav points at #{anchor} but no such id")

# Research directions must each have one distinct flagship contribution, and that
# flagship must not be repeated in the compact related-work list.
research = json.loads((ROOT / "src" / "data" / "research.json").read_text(encoding="utf-8"))
publications = json.loads(
    (ROOT / "src" / "data" / "publications.json").read_text(encoding="utf-8")
)
publication_titles = {p["title"] for p in publications}
if any("selected" in publication for publication in publications):
    problems.append(
        "publications.json: obsolete 'selected' flags remain after homepage bibliography removal"
    )

profile = json.loads((ROOT / "src" / "data" / "profile.json").read_text(encoding="utf-8"))
venues_note = profile.get("venues_note", "")
major_venues = ("ICML", "NeurIPS", "ICLR", "KDD", "WWW", "CIKM", "EMNLP")
if not all(venue in venues_note for venue in major_venues):
    problems.append("profile.json: venues_note omits a major publication venue")
if any(venue in venues_note for venue in ("BIBM", "SPOT@", "OPT@", "MLG@")):
    problems.append("profile.json: venues_note should contain major venues only")
if "Across these areas, I have <a href=\"/publications/\">published at" not in home:
    problems.append("index.html: major venues are not integrated into the About prose")
if 'class="about__venues"' in home:
    problems.append("index.html: major venues still render as a separate subtitle")
if 'class="thesis__venues"' in home:
    problems.append("index.html: venue list still crowds the thesis headline")

highlight_titles = set()
for index, thread in enumerate(research, start=1):
    label = thread.get("label") or f"thread {index}"
    highlight = thread.get("highlight")
    if not isinstance(highlight, dict):
        problems.append(f"research.json: {label!r} has no highlight object")
        continue
    title = highlight.get("paper")
    contribution = highlight.get("contribution")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"research.json: {label!r} highlight has no paper title")
        continue
    if title not in publication_titles:
        problems.append(f"research.json: highlight {title!r} is not in publications.json")
    if title in highlight_titles:
        problems.append(f"research.json: highlight {title!r} appears more than once")
    highlight_titles.add(title)
    if title in thread.get("papers", []):
        problems.append(f"research.json: highlight {title!r} repeats in related work")
    if not isinstance(contribution, str) or not contribution.strip():
        problems.append(f"research.json: highlight {title!r} has no contribution")

expected_highlights = len(research)
rendered_highlights = home.count('class="thread__highlight"')
rendered_contributions = home.count('class="thread__contribution"')
if rendered_highlights != expected_highlights:
    problems.append(
        f"index.html: expected {expected_highlights} research highlights, found "
        f"{rendered_highlights}"
    )
if rendered_contributions != expected_highlights:
    problems.append(
        f"index.html: expected {expected_highlights} contribution summaries, found "
        f"{rendered_contributions}"
    )
if 'id="papers"' in home or 'href="/#papers"' in home:
    problems.append("index.html: obsolete homepage Selected Papers section or nav link remains")
if 'href="/publications/">publications</a>' not in home:
    problems.append("index.html: nav does not link directly to the publications page")

# Every collapsible year must keep a heading inside its <summary>. Turning those
# into plain spans once removed all 11 years from the publications page outline,
# leaving one heading on the whole page.
for page in pages:
    label = str(page.relative_to(ROOT))
    text = page.read_text(encoding="utf-8")
    groups = re.findall(r"<summary[^>]*>(.*?)</summary>", text, re.S)
    headless = [g for g in groups if not re.search(r"<h[1-6][\s>]", g)]
    if headless:
        problems.append(f"{label}: {len(headless)} of {len(groups)} year summaries have "
                        "no heading, so they are missing from the document outline")

# feed must be well-formed XML with items
try:
    tree = ET.parse(ROOT / "feed.xml")
    items = tree.findall(".//item")
    if not items:
        problems.append("feed.xml: no <item> entries")
    for item in items:
        for field in ("title", "link", "pubDate", "guid"):
            if item.find(field) is None:
                problems.append(f"feed.xml: item missing <{field}>")
except ET.ParseError as exc:
    problems.append(f"feed.xml: not well-formed ({exc})")

# every JSON-LD block must parse, and must not carry placeholder text
for page in pages:
    label = str(page.relative_to(ROOT))
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            page.read_text(encoding="utf-8"), re.S):
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:
            problems.append(f"{label}: JSON-LD does not parse ({exc})")
            continue
        if "TODO" in block:
            problems.append(f"{label}: JSON-LD contains a TODO placeholder")

# canonical origin, taken from the homepage, must be used consistently
canonical = re.search(r'<link rel="canonical" href="(https?://[^/"]+)', home)
origin = canonical.group(1) if canonical else None
if not origin:
    problems.append("index.html: no canonical URL")

# sitemap must be well-formed, on the canonical origin, and point at real pages
sitemap_path = ROOT / "sitemap.xml"
if not sitemap_path.exists():
    problems.append("sitemap.xml: missing")
else:
    try:
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        locs = [e.text for e in ET.parse(sitemap_path).getroot().iter(f"{ns}loc")]
        if not locs:
            problems.append("sitemap.xml: no <loc> entries")
        for loc in locs:
            if origin and not loc.startswith(origin):
                problems.append(f"sitemap.xml: {loc} is not on {origin}")
            target = ROOT / urlparse(loc).path.lstrip("/")
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                problems.append(f"sitemap.xml: {loc} does not resolve to a built page")
    except ET.ParseError as exc:
        problems.append(f"sitemap.xml: not well-formed ({exc})")

# robots must advertise the sitemap on the same origin
robots_path = ROOT / "robots.txt"
if not robots_path.exists():
    problems.append("robots.txt: missing")
else:
    robots = robots_path.read_text(encoding="utf-8")
    if "Sitemap:" not in robots:
        problems.append("robots.txt: no Sitemap directive")
    elif origin and f"Sitemap: {origin}/sitemap.xml" not in robots:
        problems.append(f"robots.txt: Sitemap directive does not match {origin}")

llms_path = ROOT / "llms.txt"
if not llms_path.exists():
    problems.append("llms.txt: missing")
else:
    llms = llms_path.read_text(encoding="utf-8")
    if "## Selected papers" in llms:
        problems.append("llms.txt: obsolete Selected papers section remains")
    if llms.count("Flagship:") != expected_highlights:
        problems.append(
            f"llms.txt: expected {expected_highlights} flagships, "
            f"found {llms.count('Flagship:')}"
        )

# Search engines cut the description off around 155-160 characters. That is a
# convention rather than a rule, so an overlong one is a warning, not a failure.
for page in pages:
    label = str(page.relative_to(ROOT))
    found = re.search(r'<meta name="description" content="([^"]*)"',
                      page.read_text(encoding="utf-8"))
    if not found:
        problems.append(f"{label}: no meta description")
    elif len(found.group(1)) > 160:
        warnings.append(f"{label}: meta description is {len(found.group(1))} characters, "
                        "so search results will truncate it")

# stylesheet sanity
css = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
if css.count("{") != css.count("}"):
    problems.append(f"style.css: unbalanced braces ({css.count('{')} open, {css.count('}')} close)")
for needle, why in [
    ('[data-theme="dark"]', "dark theme block"),
    ("@media print", "print stylesheet"),
    ("max-width: 40rem", "mobile breakpoint"),
    ("max-height: calc(100svh - 2.75rem)", "viewport-bounded desktop rail"),
    ("overflow-y: auto", "independently scrollable desktop rail"),
    ("codehilite", "syntax highlighting"),
]:
    if needle not in css:
        problems.append(f"style.css: missing {why}")
if css.count("max-height: none") < 2:
    problems.append("style.css: rail height is not reset for both stacked and print layouts")

# both palettes must define the same colour tokens
root_block = re.search(r":root \{(.*?)\n\}", css, re.S)
dark_block = re.search(r'\[data-theme="dark"\] \{(.*?)\n\}', css, re.S)
if root_block and dark_block:
    default_tokens = set(re.findall(r"(--[\w-]+):\s*#", root_block.group(1)))
    dark_tokens = set(re.findall(r"(--[\w-]+):\s*#", dark_block.group(1)))
    if default_tokens != dark_tokens:
        problems.append(f"style.css: palette mismatch, missing in dark {sorted(default_tokens - dark_tokens)}, "
                        f"extra {sorted(dark_tokens - default_tokens)}")
else:
    problems.append("style.css: could not locate both palette blocks")

# every text colour must clear WCAG AA against the surface it sits on, in both
# palettes. The smallest text on the site uses --faint, which is exactly where this
# regressed before, so the ratios are asserted rather than eyeballed.
CONTRAST_PAIRS = [
    ("ink", "bg", 4.5, "body text"),
    ("muted", "bg", 4.5, "secondary prose"),
    ("faint", "bg", 4.5, "dates, section labels, venue metadata"),
    ("accent", "bg", 4.5, "links"),
    ("ink", "mark", 4.5, "venue chips"),
    ("muted", "mark", 4.5, "publication badges"),
    # The monogram is a 1.9rem decorative glyph and aria-hidden, so the large-text
    # threshold applies. It only renders when there is no portrait.
    ("faint", "mark", 3.0, "masthead monogram (large, decorative)"),
]


def relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(one: str, two: str) -> float:
    a, b = relative_luminance(one), relative_luminance(two)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def palette_tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


if root_block and dark_block:
    palettes = {"light": palette_tokens(root_block.group(1)),
                "dark": palette_tokens(dark_block.group(1))}
    for name, tokens in palettes.items():
        for fg, bg, minimum, why in CONTRAST_PAIRS:
            if f"--{fg}" not in tokens or f"--{bg}" not in tokens:
                problems.append(f"style.css: {name} palette has no --{fg} or --{bg}")
                continue
            ratio = contrast_ratio(tokens[f"--{fg}"], tokens[f"--{bg}"])
            if ratio < minimum:
                problems.append(
                    f"style.css: {name} --{fg} on --{bg} is {ratio:.2f}:1, "
                    f"below the {minimum}:1 needed for {why}")

# syntax highlighting needs a rule per theme
if ".codehilite .k " not in css and ".codehilite .k{" not in css:
    problems.append("style.css: no default syntax highlighting rules")
if '[data-theme="dark"] .codehilite' not in css:
    problems.append("style.css: no dark syntax highlighting rules")

# theme toggle must be present and wired on every page
for page in pages:
    label = str(page.relative_to(ROOT))
    text = page.read_text(encoding="utf-8")
    if 'class="theme-toggle"' not in text:
        problems.append(f"{label}: no theme toggle button")
    if "localStorage.getItem(\"theme\")" not in text:
        problems.append(f"{label}: missing pre-paint theme script")
    if text.index("localStorage.getItem(\"theme\")") > text.index("</head>"):
        problems.append(f"{label}: theme script runs after </head>, will flash")
    if "hidden" not in text.split('class="theme-toggle"')[1][:60]:
        problems.append(f"{label}: toggle is not hidden by default (dead button without JS)")

# KaTeX only where a post asked for it
math_posts = [p for p in pages if "blog" in p.parts and p.parent.name != "blog"]
for page in math_posts:
    text = page.read_text(encoding="utf-8")
    if "$$" in text and "katex" not in text:
        problems.append(f"{page.relative_to(ROOT)}: display math but KaTeX not loaded")
if "katex" in home:
    problems.append("index.html: KaTeX leaked onto the homepage")
for page in pages:
    label = str(page.relative_to(ROOT))
    for src in re.findall(r'<script[^>]+src="([^"]+)"', page.read_text(encoding="utf-8")):
        if "katex" not in src:
            problems.append(f"{label}: unexpected external script {src}")

# ---------------------------------------------------------------------------
# opt-in network sweep
# ---------------------------------------------------------------------------

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 20
# Publishers that refuse automated requests outright. A refusal from these says
# nothing about whether the link works in a browser, so it is not a failure.
BOT_BLOCKED = (403, 406, 429, 503)
# Some hosts reject a request with no Accept header outright: nateshpillai.com answers
# 406 to bare curl and 200 to anything that looks like a browser. Sending the headers a
# browser would send avoids reporting live pages as dead.
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
BODY_LIMIT = 200_000   # enough to see an arXiv withdrawal notice, bounded on purpose


def fetch(url: str, want_body: bool = False) -> tuple[int | None, str]:
    """GET a URL, returning (status, body). Read-only, bounded, http(s) only."""
    import urllib.error
    import urllib.request

    if urlparse(url).scheme not in ("http", "https"):
        return None, "unsupported scheme"
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = ""
            if want_body:
                body = response.read(BODY_LIMIT).decode("utf-8", "replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:                      # DNS, TLS, timeout, reset
        return None, str(exc)


def sweep_external() -> None:
    from concurrent.futures import ThreadPoolExecutor

    # Our own canonical and og:url references point at the deployed copy. Whether
    # that is up is a deploy question, and the live host rate-limits bursts, so the
    # sweep stays on genuinely outbound links.
    outbound = sorted({
        ref for refs in all_refs.values() for ref in refs
        if urlparse(ref).scheme in ("http", "https")
        and not (origin and ref.startswith(origin))
    })
    print(f"fetching {len(outbound)} outbound links...")

    def probe(url: str) -> tuple[str, int | None, str]:
        status, detail = fetch(url)
        return url, status, detail

    with ThreadPoolExecutor(max_workers=8) as pool:
        for url, status, detail in pool.map(probe, outbound):
            if status is None:
                warnings.append(f"{url} could not be reached ({detail})")
            elif status in BOT_BLOCKED:
                warnings.append(f"{url} returned {status} (refuses automated requests)")
            elif status >= 400:
                problems.append(f"dead outbound link: {url} returned {status}")

    # arXiv keeps serving the abstract page after a withdrawal, so a 200 is not
    # enough: this is the exact defect that shipped unnoticed. A withdrawn paper is
    # allowed, but only if its entry says so.
    try:
        pubs = json.loads((ROOT / "src" / "data" / "publications.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"publications.json: could not read ({exc})")
        return

    abs_links = [(p, p.get("links", {})["arxiv"]) for p in pubs
                 if p.get("links", {}).get("arxiv", "").find("/abs/") != -1]

    def withdrawal(pair):
        paper, url = pair
        _, body = fetch(url, want_body=True)
        return paper, url, "has been withdrawn" in body.lower()

    with ThreadPoolExecutor(max_workers=8) as pool:
        for paper, url, withdrawn in pool.map(withdrawal, abs_links):
            if not withdrawn:
                continue
            if "withdrawn" in str(paper.get("note", "")).lower():
                warnings.append(f"{paper['title'][:56]}: withdrawn on arXiv, labelled as such")
            else:
                problems.append(
                    f"{paper['title'][:56]}: arXiv record at {url} is withdrawn, "
                    "but the entry does not say so")


if CHECK_EXTERNAL:
    sweep_external()

total_refs = sum(len(v) for v in all_refs.values())
print(f"checked {len(pages)} pages, {total_refs} references")
for w in warnings:
    print(f"  warn  {w}")
for p in problems:
    print(f"  FAIL  {p}")
print("all checks passed" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
