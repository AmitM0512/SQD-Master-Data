# SQD Live Dashboard

Two static sites (plain HTML/CSS/JS, no build step, no dependencies):

- **`index.html`** — a real-time metrics dashboard driven by a client-side
  data simulator, useful as a live-tracking demo/template.
- **`okr.html`** — the SQD 2026 OKR dashboard: monthly progress of
  Objectives & Key Results (supplier quality complaints, audit status,
  APQP status, safe launch, EOS, AI Assistant work), rendered from
  `data/okr-data.json`.

They link to each other from the top-right of the header.

## Run it locally

No build step needed. From the repo root:

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

(Any static file server works — `npx serve`, VS Code's Live Server, etc.
Opening `index.html` directly also works, but a local server avoids browser
restrictions on some APIs.)

## What it does

- **Stat tiles**: CPU load, memory usage, network throughput, requests/sec,
  active users, and error rate, each with a live sparkline and delta vs. the
  previous tick.
- **Live chart**: CPU vs. memory over the last 60 samples, with a hover
  crosshair + tooltip, a toggleable legend, and a "view as table" fallback.
- **Live feed**: a scrolling log of simulated events.
- **Controls**: pause/resume, update interval (1s/2s/5s), reset, and a
  light/dark theme toggle (persisted in `localStorage`).

## Connecting real data (index.html)

All simulated data generation lives in `js/app.js`. To track a real source:

1. Replace the `step()` random-walk function with a `fetch()` call to your
   API, or to a JSON file you keep updated in this repo (e.g. commit an
   updated `data.json` from a script or cron job, and poll it on an
   interval — this needs no backend and still works on GitHub Pages).
2. Feed the fetched values into the same `metrics`/`errorRate` objects and
   call `pushHistory(...)` — the chart, sparklines, and feed all read from
   those objects, so no other code needs to change.

## OKR dashboard (okr.html)

`okr.html` reads `data/okr-data.json`, which is generated from the uploaded
OKR tracker workbook by `scripts/parse_okr.py`. To refresh it after the
workbook is updated:

```bash
pip install openpyxl
python3 scripts/parse_okr.py path/to/OKR.xlsx
```

This overwrites `data/okr-data.json`. Commit the result.

Notes on how the sections map to the source workbook's Key Results (KRs):

- **Supplier Quality Complaints** — KR2 (supplier DMN incident count) and
  KR5 (mass appearance / batch quality issues).
- **Audit Status** — KR3, parsed by audit type (VDA 6.3, CQI, PCPA, PSA);
  any additional named audit (e.g. a customer-specific audit) is bucketed
  under "Customer specific / other".
- **APQP Status** (Kick-off, OTS, PPAP, Run rate) — cross-referenced from
  KR7 (new sourcing / run @ rate), KR8 (PPAP & OTS delivery), KR9 (PPAP
  first-time approval) and KR11 (MQS). The source sheet tracks monthly
  activity totals, not a per-project table, so this view is an aggregate,
  not a true project-by-project plan-vs-actual. **SOP** isn't tracked as a
  separate field in the source sheet — the panel says so rather than
  showing invented numbers.
- **Safe Launch Status** — KR10 (run @ rate and safe-launch pass/fail
  counts).
- **EOS** — KR13 (MPJ EOS average score).
- **AI Assistant Work** — KR14 (AI Assistant task count vs. target).

Every section also shows the raw monthly text from the workbook (the
"Note" column), since several of these counts are parsed with best-effort
regexes out of free-text monthly notes rather than dedicated numeric
columns — a month left blank in a chart means the note text didn't match
a recognized pattern, not that the value is zero.

## Deploying via GitHub Pages

A workflow at `.github/workflows/deploy-pages.yml` deploys this site
automatically on every push to `main`, using GitHub's official Pages
Actions (no third-party action, no secrets to configure).

One-time setup (repository owner):

1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Merge this branch into `main` (or push to `main`) — the workflow runs
   automatically and publishes the site. You can also trigger it manually
   from the **Actions** tab (`Deploy static site to GitHub Pages` →
   **Run workflow**).

Once enabled, the live URL appears in the workflow run summary and under
**Settings → Pages**, typically
`https://<owner>.github.io/<repo>/`.

## Project structure

```
index.html                live-metrics dashboard markup
okr.html                   OKR dashboard markup
css/styles.css             shared theming tokens (light/dark) and layout
css/okr.css                OKR dashboard-specific styles
js/app.js                  live-metrics data simulation + chart/feed rendering
js/charts.js               shared canvas chart helpers (line/bar)
js/okr.js                  OKR dashboard rendering
data/okr-data.json         parsed OKR data consumed by okr.html
scripts/parse_okr.py       regenerates data/okr-data.json from the OKR workbook
.github/workflows/         CI: deploy to GitHub Pages on push to main
```
