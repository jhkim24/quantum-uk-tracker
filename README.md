# UK Quantum Internship Tracker

An auto-updating tracker for undergraduate quantum-computing summer internships in the UK. Runs as a GitHub Action on a weekly schedule, scrapes ~15 known programme pages, classifies their status (open/closed/unknown), flags content changes, and deploys a static site to GitHub Pages.

## What it does

- **Tracks 15 known programmes** (Oxford ALP UROP, UCL Quantum CDT, Riverlane, Phasecraft, NQCC, Quantum Dice, Oxford Instruments, OQC, Quantinuum, PQShield, Quantum Motion, NPL, STFC, EPSRC vacation internships, QIntern). Edit `data/programmes.json` to add or remove.
- **Discovers candidates** via DuckDuckGo searches for UK quantum internships not in the registry. Results land in `data/discoveries.json` for manual review — never auto-added.
- **Detects content changes** by hashing the relevant text region of each page; flags any page whose content shifted since the last run.
- **Classifies status** using regex over page text — looks for "applications open", "applications closed", "deadline X", and similar.
- **Renders a static site** with filterable cards, status pills, last-checked timestamps, and a discoveries section.

## One-time setup

### 1. Create a new GitHub repo

```
gh repo create quantum-uk-tracker --public --source=. --remote=origin --push
```

Or via the web UI — just push the contents of this folder to a new public repo.

### 2. Enable GitHub Pages

In the repo settings:

1. Settings → Pages → Source = **GitHub Actions** (NOT "Deploy from a branch")
2. Save.

### 3. Run the workflow once manually

Actions tab → "Scrape & Deploy" → Run workflow → main branch → Run.

That first run will:
- Fetch each programme page,
- Write `data/status.json` and `data/discoveries.json`,
- Commit those updates,
- Build and deploy the site.

After ~2 minutes the site is live at `https://<your-username>.github.io/<repo-name>/`.

### 4. (Optional) Adjust the schedule

Default is **Mondays at 06:00 UTC**. Edit `.github/workflows/scrape.yml`:

```yaml
schedule:
  - cron: "0 6 * * 1"   # weekly Monday 06:00 UTC
```

Daily would be `0 6 * * *` but is not recommended — these pages don't change daily and frequent hits risk getting your IP rate-limited.

## Local development

```
# install
pip install -r scraper/requirements.txt

# run scraper
python scraper/run.py

# run discovery
python scraper/discover.py

# preview site
cd site
mkdir -p data && cp ../data/*.json data/
python3 -m http.server 8000
# open http://localhost:8000
```

## Adding a new programme

1. Edit `data/programmes.json`. Each entry needs:
   - `id` — short kebab-case slug, must be unique
   - `name`, `org`, `category` (`academic` / `industry` / `national-lab` / `remote`)
   - `tags` — array of short tags for filtering
   - `url` — page to scrape
   - `selector_hint` — CSS selector for the relevant page region (usually `"main"`)
   - `eligibility`, `pay`, `duration`, `typical_deadline`, `apply_route`, `notes`

2. Commit and push. The workflow re-runs on push and the site updates automatically.

## How status classification works

The scraper extracts the text inside the `selector_hint` region (defaulting to `<main>`), then runs ordered regex patterns:

- **CLOSED** wins over open: matches `applications closed`, `closed to applications`, `not currently accepting`, etc.
- **OPEN** matches `applications open`, `apply now`, `closing date: X`, `deadline: X`.
- **UNKNOWN** if neither matches — the page is just a generic landing page.
- **ERROR** if the fetch failed (HTTP 4xx/5xx, timeout, etc.).

Status is a heuristic. Always click through to the source URL before applying.

## File layout

```
.
├── .github/workflows/scrape.yml   # weekly Action: scrape + commit + deploy
├── scraper/
│   ├── run.py                     # main scraper
│   ├── discover.py                # broad-search discovery
│   ├── test_smoke.py              # quick sanity check
│   └── requirements.txt
├── data/
│   ├── programmes.json            # CURATED — edit by hand
│   ├── status.json                # GENERATED — overwritten each run
│   ├── discoveries.json           # GENERATED — review and promote to programmes.json
│   └── history.json               # GENERATED — run-level summary
├── site/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── README.md
```

## Limitations & known issues

- **JS-rendered pages** (Workday, Greenhouse, some careers portals) won't extract well — they ship a near-empty HTML shell that fills in with JavaScript. The scraper sees the shell. For these, `selector_hint` and the snippet won't be useful; rely on the URL link itself.
- **Bot detection.** A small number of sites (some `.ac.uk`, some Cloudflare-protected ones) reject non-browser HTTP requests with 403. GitHub Actions runners generally aren't blocked, but if a specific site is, it'll show as "Fetch Err" in the dashboard. The fix is usually to find an alternative landing page on the same domain.
- **Discovery is conservative.** DuckDuckGo HTML results aren't comprehensive, and the relevance filters are strict. Expect a small number of discoveries per run rather than dozens. False negatives are preferred over noise.
- **Not a replacement for human judgement.** Always read the actual programme page before applying. Status pills are best-effort heuristics; deadlines move; eligibility rules change.

## License

MIT — do whatever you want, no warranty.
