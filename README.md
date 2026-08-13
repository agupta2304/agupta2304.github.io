# agupta2304.github.io

Personal site. Content lives in JSON and Markdown under `src/`; a small Python script renders
it to static HTML at the repository root, which is what GitHub Pages serves.

No framework, no CI, no client-side rendering. The only JavaScript is roughly twenty inline
lines driving the theme toggle.

## Discoverability

The build emits, alongside the pages:

| File | Purpose |
| --- | --- |
| `sitemap.xml` | Every page, with `lastmod` on posts |
| `robots.txt` | Allows everything and points at the sitemap |
| `feed.xml` | RSS for posts |
| `llms.txt` | Plain-text digest of the site for agents |

Structured data is JSON-LD. The homepage carries a `@graph` of `Person` and `WebSite` plus a
`VideoObject` per recorded talk, each with its view count as an `interactionStatistic`. The
publications page carries a `CollectionPage` wrapping an `ItemList` of `ScholarlyArticle`
entries with full author lists, venues, and years. That block is emitted without indentation
because pretty-printing 36 papers costs roughly 50KB of pure whitespace.

`src/check.py` verifies all of it: that every JSON-LD block parses, that none contains a
placeholder, that the sitemap is well-formed and every entry resolves to a real built page, and
that `robots.txt` advertises the sitemap on the same origin as the canonical URLs.

`llms.txt` is not a standard, just a convention some agents look for. It costs nothing to emit.

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
well-formed, that both themes define the same colour tokens, that every text colour clears
WCAG AA against the surface it sits on, and that no unexpected external script sneaks in. It
exits non-zero on failure.

The contrast assertion exists because it caught a real regression: the smallest text on the
site sat at 3.45:1 against the cream background for a while. Thresholds and the reason for each
pair live in `CONTRAST_PAIRS` in [src/check.py](src/check.py) — 4.5:1 for body and metadata,
3:1 for the large decorative monogram.

There is also an opt-in network sweep, kept separate because it is slow and can fail for
reasons that are not your fault:

```bash
.venv/bin/python src/check.py --external
```

It fetches every outbound link and fails on a genuine 404. Publishers that refuse automated
requests — ACM, IEEE, Inderscience, Google Scholar — return 403 and are reported as warnings,
since a refusal says nothing about whether the link works in a browser.

It also handles a case a status code cannot catch: arXiv keeps serving the abstract page after
a paper is withdrawn, so the link returns 200 while the PDF is gone. A withdrawn paper is
allowed, but only if its entry carries a `note` saying so. Two of the papers here are in that
state, which is why they show a "Withdrawn by the authors" badge.

## Editing content

| What | Where |
| --- | --- |
| Name, thesis line, about text, service, contact links | `src/data/profile.json` |
| Research directions, highlights, and related work | `src/data/research.json` |
| News items | `src/data/news.json` |
| Publications | `src/data/publications.json` |
| Talks and tutorials | `src/data/talks.json` |
| Jobs and education | `src/data/experience.json` |
| Mentors | `src/data/mentors.json` |
| Canonical site URL, list limits | `src/data/site.json` |
| Look and feel | `src/styles/style.css` |

### The top of the homepage

`profile.thesis` is the primary research promise under the formal job title;
`profile.thesis_detail` is its smaller methodological line. Together they establish the research
identity without another keyword strip.

`profile.about` is deliberately one paragraph. Anything longer turns the 18rem sticky rail into
a wall of text and repeats the Experience section. Publication breadth now appears on the full
Publications page through `profile.venues_note`; program committees and patents live in Service.

Prose in the JSON data is rendered as Jinja before it reaches the templates, so a fact quoted in
more than one place has a single definition. The customer count appears in both the bio and the
Nubank role and is written `{{ profile.customer_scale }}` in each; update
`profile.customer_scale` and both move together. Keep that figure sourced — the current value
comes from Nu Holdings' most recent quarterly results.

### Research directions

`src/data/research.json` holds the four directions shown above News. Each has a `label`, a
question-led `blurb`, exactly one flagship `highlight`, and any mix of related `papers` and
`talks`:

```json
{
  "label": "Reliable AI agents",
  "blurb": "The research question and point of view, in one or two sentences.",
  "highlight": {
    "paper": "Building Customer Support AI Agents at 100M-User Scale: An Evaluation-Driven Framework",
    "contribution": "One abstract-grounded sentence explaining the central contribution."
  },
  "papers": [],
  "talks": ["Building Production LLM Agents: An Evaluation-Driven Playbook"]
}
```

The flagship and related evidence are named by title and resolved against `publications.json`
and `talks.json`, so venue, date, and link metadata are only edited once. A missing flagship,
duplicate flagship, title mismatch, empty contribution, or flagship repeated in its own related
list **fails the build** rather than silently weakening the narrative.

Each flagship renders as venue, title, contribution, and every available paper/code/site link.
The contribution is plain text and Jinja-autoescaped. Write it from the paper's abstract rather
than promotional copy, and explain the result rather than merely restating the title.

Related papers and talks render as one compact list, papers first, with a chip in the left
gutter. A paper's
chip is its venue and year — `KDD 2026` — which already implies a paper. A talk's names both
what it is and where: `Tutorial @ CIKM 2026`, from `type` plus the part of `event` before the
first comma. Whatever follows that comma drops under the title with the date rather than being
repeated, so `event: "CIKM 2026, half-day tutorial"` gives a `Tutorial @ CIKM 2026` chip and a
`half-day tutorial · Nov 2026` line. A tutorial is therefore never mistaken for a conference
paper. Talk titles link to the recording when there is one.

A tutorial published in a conference's proceedings gets the same treatment from the other
direction: a paper whose venue carries the `Tutorials Track` qualifier renders as
`Tutorial @ KDD 2023` rather than a bare `KDD 2023`, so it cannot be mistaken for a research
paper whether it is listed as a publication or as a talk.

The gutter is sized to keep the longest of those chips on one line. If you add a talk whose
event name is long, either shorten what precedes the comma or widen `grid-template-columns` on
`.thread__papers li`; the chip wraps rather than overflowing, but a two-line chip splits the
venue from its year and reads worse.

Directions are ordered as written and intentionally mirror the two-line thesis. Keep one
flagship per direction; additional evidence belongs under Related work.

### Adding a paper

Append an object to `src/data/publications.json`:

```json
{
  "title": "Paper Title",
  "authors": ["Coauthor One", "Aman Gupta"],
  "venue": "NeurIPS",
  "year": 2026,
  "note": "Oral",
  "links": { "arxiv": "https://arxiv.org/abs/...", "code": "https://github.com/..." }
}
```

Entries are grouped by year, newest first; within a year they keep the order in the file, so
reorder by moving objects around. Every paper appears on `/publications/`; homepage curation is
controlled by `research.json`, not a second bibliography. The author matching `name` in
`profile.json` is bolded automatically. `note` renders as a small badge and can be omitted.
Recognised link keys are
`pdf`, `arxiv`, `code`, `slides`, `poster`, `video`, `bibtex`, and `site`; unknown keys still
render, just at the end.

The title itself links to the first available of `arxiv`, `pdf`, `preprint`, `site`, `code`,
`doi` — open versions first, with the DOI as a last resort. The DOI is included because research
directions link their flagship and related titles, so a DOI-only paper would otherwise be a dead
end. The build lists any paper with no link whatsoever.

Papers are grouped by year into native `<details>` elements, so the years expand and collapse
with no JavaScript and stay keyboard-accessible. The three most recent groups start open and
older ones start shut, each showing its paper count so a collapsed year still tells you what is
inside. Change the number with `years_expanded` in `src/data/site.json`, or set it high enough to
cover every year to have them all open.

Collapsed years are still fully in the markup, so they remain searchable in-page and visible to
crawlers. They also still print: browsers that support `::details-content` get that from CSS, and
a `beforeprint` handler opens the rest and closes them again afterwards, so a printed CV never
silently loses the older half of the list.

Each entry shows a venue acronym in a left gutter so the list can be scanned by conference.
The chip fills the gutter to a uniform width and is set in the body ink rather than a muted grey
— it is a scanning aid, so it has to be legible at 0.72rem. The gutter is sized for the longest
acronym in use, currently `OPT@NeurIPS`; if you add a longer one, widen the
`grid-template-columns` on `.pub` to match. On narrow screens the gutter collapses and the chip
becomes a compact pill above the title instead of a full-width band.
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

News sits below Featured research. To lead with it instead, swap the two blocks in
`src/templates/home.html`.

### Adding a talk

Same idea in `src/data/talks.json`. `type` is `tutorial`, `invited`, or `other`, which controls
the grouping. `date` is `YYYY-MM` and is displayed as `Nov 2026`.

Add `"video": "https://www.youtube.com/watch?v=..."` and the entry gets a `video` link plus a
view count. Add a root-relative `"thumbnail": "/assets/talk-name.jpg"` as well to feature the
recording visually on the homepage; use a self-hosted 16:9 image. Featured recordings are
removed from the compact text list below them so each talk appears only once.

### Refreshing view counts

```bash
.venv/bin/python src/build.py --refresh-views
```

This reads the public view count off each talk's YouTube watch page and caches it in
`src/data/video-stats.json`, which is committed. Ordinary builds only read that cache, so they
need no network, no API key, and stay byte-for-byte reproducible. The rendered count carries
the as-of month in a tooltip, so a stale number is never presented as live. If YouTube changes
its markup the refresh warns and keeps the cached value rather than dropping the count.

It scrapes the watch page, which needs no credentials. If you set a `YOUTUBE_API_KEY`
environment variable it uses the YouTube Data API instead, but that is optional and only
matters from a network YouTube withholds counts from.

**Run this by hand, on your own machine, and commit the result.** There is deliberately no
scheduled job for it — see the note below.

### Why there is no CI

This repository has no GitHub Actions, and should not gain any.

It previously had a weekly workflow that polled YouTube for view counts and committed them.
GitHub restricted the account over it: the
[Additional Product Terms](https://docs.github.com/site-policy/github-terms/github-terms-for-additional-products-and-features)
prohibit using Actions for work unrelated to building or testing the repository itself, and a
scheduled job whose only purpose is to fetch from a third-party site is squarely that. The site
was offline until the workflow was removed.

Refreshing a view count is a two-minute manual task a few times a year. It is not worth a
scheduled runner, and definitely not worth the account.

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
`minor: true` for site housekeeping like the colophon — it still publishes, and stays in the
feed and the sitemap, but drops out of the main list on `/blog/` into a footnote, so a note
about the website cannot outrank real writing on date order alone. Set
`math: true` to load KaTeX on that page only, which keeps every other page free of
third-party requests. Fenced code blocks are highlighted at build time by Pygments, with
separate light and dark themes baked into the stylesheet.

**Writing is hidden from the nav until there are three pieces.** Once that threshold is met, the
nav points to a compact homepage preview; its “See all writing” link opens the complete
date-ordered page. The page, feed, and post URLs are always built and stay in the sitemap, so
nothing 404s while the nav link is withheld. Change the threshold with `writing_min_posts` in
`src/data/site.json`, and control the preview length with `home_writing_limit`.

### Writing published elsewhere

`src/data/writing.json` lists pieces that appeared on someone else's blog. They join the posts
in one date-ordered list on `/blog/` and link to the original rather than being reproduced here,
since the canonical copy belongs to the outlet:

```json
{
  "title": "Building AI agents for 131 million customers",
  "outlet": "Building Nubank",
  "date": "2026-03",
  "url": "https://building.nubank.com/...",
  "note": "with Daniel Braithwaite",
  "summary": "One or two sentences on what the piece argues."
}
```

`date` is `YYYY-MM` and entries sort newest first regardless of file order, interleaved with
posts written here — an outlet name marks the ones published elsewhere. `note` is for
co-authors, worth filling in since none of these are single-author pieces and the list should not
imply otherwise. These count toward the nav threshold above, because a Writing page that lists
them is worth a click even when only one post lives on this site.

### Adding a headshot

Drop a square image at `assets/headshot.jpg`. If the file is missing the build falls back to
an "AG" monogram and says so, so a missing portrait never ships as a broken image. Change the
path or set it to `null` in `profile.json`.

The portrait renders at 96px, which a 200x200 source covers at 2x for retina screens. If you
want it larger you need a larger source first: at 120px a 200px image is only 1.67x and will
visibly soften.

### Adding a CV

Drop a PDF at `assets/cv.pdf` and a CV link appears at the top of the Elsewhere list. Until the
file exists the build says so and leaves the link out, the same rule as the portrait — so there
is never a link to a missing download. Change the path or set `cv` to `null` in `profile.json`.

Printing the homepage already produces a serviceable CV: the print stylesheet drops the nav and
chrome, collapses to one column, renders monochrome, and expands link URLs inline.

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

This deploys to <https://amangupta.dev> from the root of `main`, already configured under
**Settings → Pages** (*Deploy from a branch*, `main`, `/ (root)`). The default
<https://agupta2304.github.io> address redirects to the custom domain. A push goes live within
a minute or two.

`.nojekyll` at the root is required: without it GitHub Pages runs the output through Jekyll,
which ignores directories beginning with an underscore and can rewrite files unexpectedly.

### Custom domain

`CNAME` contains `amangupta.dev`. Its apex has GitHub Pages' four `A` records, while `www`
is a `CNAME` to `agupta2304.github.io`; GitHub redirects both hostnames to the canonical apex.
The matching `url` in `src/data/site.json` drives canonical URLs and the RSS feed.

`amangupta.dev` is verified to the `agupta2304` GitHub account and HTTPS is enforced. Keep the
`_github-pages-challenge-agupta2304` TXT record in DNS permanently so another GitHub account
cannot claim the domain if Pages is ever disabled or reconfigured.

## Security notes

The site is static, has no forms, no backend, and no user input, so most of the usual web
surface does not exist here. The parts that are worth stating:

**No secrets in the repository, and no credentials in CI.** Nothing the build needs is
authenticated. The optional `YOUTUBE_API_KEY` is read from your local environment when
refreshing view counts by hand and never enters a data file, a committed page, or a log line.
There are no Actions secrets because there are no Actions.

**Prose in the JSON data is rendered as a Jinja template.** That is what makes
`{{ profile.customer_scale }}` work in `profile.json` and `experience.json`. It also means those
files are executable template input rather than inert strings, so treat them with the same care
as the templates themselves. This is safe because every one of those files is author-written and
version-controlled — there is no path by which a visitor, an API, or any external system can
reach them. Do not extend this rendering to content fetched from elsewhere.

**The `--external` sweep makes outbound requests** to URLs stored in the repo's own data. It is
opt-in, never part of an ordinary build, restricted to `http` and `https`, sends no credentials
or cookies, issues only read-only GETs with a 20-second timeout, and caps each response read so
a hostile or enormous body cannot exhaust memory. It is a local pre-push convenience, and it
must stay local — running outbound fetches from a GitHub runner is what cost this account its
Pages hosting once already.

**Third-party requests from the served pages** are limited to KaTeX, and only on posts that set
`math: true`. `src/check.py` fails if any other external script appears on any page, which is
what keeps an analytics snippet or a font CDN from creeping in unnoticed.

Talk thumbnails are committed under `assets/` rather than loaded from YouTube. Merely opening
the homepage therefore sends YouTube no visitor IP address or referrer; that request happens
only after someone chooses to follow a video link.

The Mentors and Writing navigation additions are root-relative links rendered entirely from
version-controlled data. They introduce no client-side script, user input, or runtime request.

The two-line tagline is stored as separate plain-text fields and remains Jinja-autoescaped;
formatting is applied by the template rather than by placing HTML in profile data.

Research contribution summaries are likewise plain text and autoescaped. Flagship titles are
resolved by exact lookup against version-controlled publication data; both the build and
structural checker reject missing, duplicate, or self-repeated highlights before deployment.
