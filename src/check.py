"""Structural checks on the generated site.

    python src/check.py

Exits non-zero if anything is wrong, so it works as a pre-push guard.
"""

import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

problems: list[str] = []


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

# stylesheet sanity
css = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
if css.count("{") != css.count("}"):
    problems.append(f"style.css: unbalanced braces ({css.count('{')} open, {css.count('}')} close)")
for needle, why in [
    ('[data-theme="dark"]', "dark theme block"),
    ("@media print", "print stylesheet"),
    ("max-width: 40rem", "mobile breakpoint"),
    ("codehilite", "syntax highlighting"),
]:
    if needle not in css:
        problems.append(f"style.css: missing {why}")

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

print(f"checked {len(pages)} pages, {sum(len(v) for v in all_refs.values())} references")
for p in problems:
    print(f"  FAIL  {p}")
print("all checks passed" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
