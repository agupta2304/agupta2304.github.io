# amangupta.github.io

Personal site. Content lives in JSON and Markdown under `src/`; a small Python script renders
it to static HTML at the repository root, which is what GitHub Pages serves.

No framework, no CI, no client-side rendering. The homepage ships zero JavaScript.

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

## Editing content

| What | Where |
| --- | --- |
| Name, about text, contact links | `src/data/profile.json` |
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

### Adding a talk

Same idea in `src/data/talks.json`. `type` is `tutorial`, `invited`, or `other`, which controls
the grouping. `date` is `YYYY-MM` and is displayed as `Nov 2026`.

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

## First-time GitHub Pages setup

The repository must be named `<your-github-username>.github.io` for a user site. If your
username is not `amangupta`, rename the folder and repo to match:

```bash
cd .. && mv amangupta.github.io <username>.github.io
```

Then update `url` in `src/data/site.json`, rebuild, and:

```bash
git remote add origin git@github.com:<username>/<username>.github.io.git
git branch -M main
git push -u origin main
```

In the repository, go to **Settings → Pages** and set **Source** to *Deploy from a branch*,
branch `main`, folder `/ (root)`. The site is live at `https://<username>.github.io` within a
minute or two.

`.nojekyll` at the root is required: without it GitHub Pages runs the output through Jekyll,
which ignores directories beginning with an underscore and can rewrite files unexpectedly.

### Custom domain

Put the bare domain in a `CNAME` file at the root, point a DNS `ALIAS`/`A` record at GitHub
Pages, then update `url` in `src/data/site.json` so canonical URLs and the RSS feed match.
