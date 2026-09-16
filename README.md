# SQD Live Dashboard

A real-time data tracking dashboard, built as a static site (plain HTML/CSS/JS,
no build step, no dependencies). It ships with a client-side data simulator so
it runs as a working demo out of the box — swap in a real data source when
you're ready.

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

## Connecting real data

All simulated data generation lives in `js/app.js`. To track a real source:

1. Replace the `step()` random-walk function with a `fetch()` call to your
   API, or to a JSON file you keep updated in this repo (e.g. commit an
   updated `data.json` from a script or cron job, and poll it on an
   interval — this needs no backend and still works on GitHub Pages).
2. Feed the fetched values into the same `metrics`/`errorRate` objects and
   call `pushHistory(...)` — the chart, sparklines, and feed all read from
   those objects, so no other code needs to change.

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
index.html               dashboard markup
css/styles.css            theming (light/dark) and layout
js/app.js                 data simulation + chart/feed rendering
.github/workflows/        CI: deploy to GitHub Pages on push to main
```
