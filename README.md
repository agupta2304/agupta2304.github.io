# agupta2304.github.io

Personal site. Content lives in JSON and Markdown under `src/`; a small Python script renders
it to static HTML at the repository root, which is what GitHub Pages serves.

No framework, no CI, no client-side rendering. The only JavaScript is roughly twenty inline
lines driving the theme toggle.

## Themes

Cream is the default. A toggle in the nav switches to a dark theme and remembers the choice in
`localStorage`; an inline script in `<head>` applies it before first paint, so there is no
flash of the wrong palette. Without JavaScript the site stays cream and the button hides itself
rather than sitting there dead.

Both palettes are defined as custom properties at the top of `src/styles/style.css` — `:root`
holds the cream values, `[data-theme="dark"]` the dark ones. Change a colour in one place and
it propagates everywhere, including the syntax highlighting, which ships a matching Pygments
theme per palette. Printing always uses a monochrome palette regardless of the active theme.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Build and preview

```bash
.venv/bin/python src/build.py            # build once
.venv/bin/python src/build.py --serve    # build, then serve on http://localhost:8000
```

The build prints a count of remaining `TODO` / `20XX` placeholders so seeded stub content
cannot quietly go live.

To sanity-check the output before pushing:

```bash
.venv/bin/python src/check.py
```

It verifies HTML tag balance, that every internal link resolves, that the RSS feed is
well-formed, that both themes define the same colour tokens, and that no unexpected external
script sneaks in. It exits non-zero on failure.

## Editing content

| What | Where |
| --- | --- |
| Name, about text, contact links | `src/data/profile.json` |
| News items | `src/data/news.json` |
| Publications | `src/data/publications.json` |
| Talks and tutorials | `src/data/talks.json` |
| Jobs and education | `src/data/experience.json` |
| Mentors | `src/data/mentors.json` |
| Canonical site URL | `src/data/site.json` |
| Look and feel | `src/styles/style.css` |

### Adding a paper

Append an object to `src/data/publications.json`:

```json
{
  "title": "Paper Title",
  "authors": ["Coauthor One", "Aman Gupta"],
  "venue": "NeurIPS",
  "year": 2026,
  "selected": true,
  "note": "Oral",
  "links": { "arxiv": "https://arxiv.org/abs/...", "code": "https://github.com/..." }
}
```

Entries are grouped by year, newest first; within a year they keep the order in the file, so
reorder by moving objects around. `selected: true` puts a paper on the homepage — everything
appears on `/publications/` regardless. The author matching `name` in `profile.json` is bolded
automatically. `note` renders as a small badge and can be omitted. Recognised link keys are
`pdf`, `arxiv`, `code`, `slides`, `poster`, `video`, `bibtex`, and `site`; unknown keys still
render, just at the end.

Each entry shows a venue acronym in a left gutter so the list can be scanned by conference.
The acronym is derived from `venue`: the parenthesised part of, say, `International Conference
on Machine Learning (ICML)`, or `X Workshop at Y` becomes `X@Y`. Venues with no acronym to
extract are mapped in `VENUE_SHORT` in [src/build.py](src/build.py). The full venue name shows
on hover. A trailing qualifier after a comma, like `, Industry Track`, renders as a small badge
next to the authors rather than in the chip.

### Adding a news item

Prepend an entry to `src/data/news.json`:

```json
{ "date": "2026-11", "text": "Presented our tutorial at <a href=\"...\">CIKM 2026</a>." }
```

`date` is `YYYY-MM`. Entries sort newest first regardless of file order. `text` allows inline
HTML, with two conventions: wrap paper and talk titles in `<em>` and venues in `<strong>`. Both
render bold rather than italic, so the eye lands on the title and the conference. The homepage
shows the six most recent;
change `news_limit` in `src/data/site.json` to show more, or set it to `0` for all of them.

The section sits directly below About. To move it above, cut the `{% if news %}` block in
`src/templates/home.html` and paste it before the `id="about"` section.

### Adding a talk

Same idea in `src/data/talks.json`. `type` is `tutorial`, `invited`, or `other`, which controls
the grouping. `date` is `YYYY-MM` and is displayed as `Nov 2026`.

Add `"video": "https://www.youtube.com/watch?v=..."` and the entry gets a `video` link plus a
view count.

### Refreshing view counts

```bash
.venv/bin/python src/build.py --refresh-views
```

This reads the public view count off each talk's YouTube watch page and caches it in
`src/data/video-stats.json`, which is committed. Ordinary builds only read that cache, so they
need no network, no API key, and stay byte-for-byte reproducible. The rendered count carries
the as-of month in a tooltip, so a stale number is never presented as live. If YouTube changes
its markup the refresh warns and keeps the cached value rather than dropping the count.

You rarely need to run it by hand:
[.github/workflows/refresh-views.yml](.github/workflows/refresh-views.yml) does it every Monday,
commits only when a number actually moved, and asks Pages to rebuild. Run it on demand with
`gh workflow run refresh-views.yml`. This is the one piece of CI in the repo; the site itself
still deploys from committed HTML with no build step.

### Writing a post

Create `src/posts/YYYY-MM-DD-slug.md`:

```markdown
---
title: "Post title"
date: 2026-08-02
summary: "One sentence for the index page and the RSS feed."
math: false
draft: false
---

Body in Markdown.
```

It publishes at `/blog/<slug>/`. Set `draft: true` to keep it out of the build. Set
`math: true` to load KaTeX on that page only, which keeps every other page free of
third-party requests. Fenced code blocks are highlighted at build time by Pygments, with
separate light and dark themes baked into the stylesheet.

### Adding a headshot

Drop a square image at `assets/headshot.jpg`. If the file is missing the build falls back to
an "AG" monogram and says so, so a missing portrait never ships as a broken image. Change the
path or set it to `null` in `profile.json`.

## Publishing

```bash
.venv/bin/python src/build.py
git add -A
git commit -m "Update site"
git push
```

Generated HTML is committed on purpose — that is what GitHub Pages serves, and it keeps the
deploy free of any build infrastructure.

## GitHub Pages

This deploys to <https://agupta2304.github.io> from the root of `main`, already configured
under **Settings → Pages** (*Deploy from a branch*, `main`, `/ (root)`). A push goes live
within a minute or two.

`.nojekyll` at the root is required: without it GitHub Pages runs the output through Jekyll,
which ignores directories beginning with an underscore and can rewrite files unexpectedly.

### Custom domain

Put the bare domain in a `CNAME` file at the root, point a DNS `ALIAS`/`A` record at GitHub
Pages, then update `url` in `src/data/site.json` so canonical URLs and the RSS feed match.
