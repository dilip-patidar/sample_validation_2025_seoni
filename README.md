# Seoni sample plots — map and sample verification

Interactive map of the SEY-K-25 (Seoni, Kharif 2025) basalt deployment plots with the
soil sample locations used for verification:

| Layer | Source sheet | Count |
|---|---|---|
| Deployment plots (polygons) | `Seoni Sample plots - Deployment.csv` | 25,218 |
| Sampled treatment plots + pre / EOY sample points | `Seoni Sample plots - Treatement sample pair.csv` | 600 pairs |
| Control plots (polygons) | `Seoni Sample plots - control_plot.csv` | 131 |
| Control pre / EOY sample points | `Seoni Sample plots - Control_sampled_Plots.csv` | 129 pairs |

Circles are treatment samples, diamonds are control samples; blue = pre-deployment,
orange = end of year. A **Samples** list and a stepper on the map step through every
sample pair (treatment + control) one by one, with a tick per sample.

There are two versions of the page:

## 1. `seoni-sample-plots-google.html` — Supabase + Google Maps (shared)

Reads everything from a Supabase project and shows it on Google Maps. Ticks are stored
in Supabase, so everyone who opens the page sees the same review state.

### One-time setup (one command)

1. Copy `.env.example` to `.env` (same folder; `.env` is git-ignored) and fill in:
   - `SUPABASE_DB_URL` — in the Supabase dashboard click **Connect** (top bar), choose
     **Session pooler**, copy the URI and put the database password in it
     (Project Settings → Database; reset it there if you do not know it). This is what
     creates the tables and loads the sheets. If you would rather not use the database
     password, set `SUPABASE_ACCESS_TOKEN` (account token, creates the tables) and
     `SUPABASE_SERVICE_ROLE_KEY` (Project Settings → API, loads the sheets) instead.
   - `GOOGLE_MAPS_API_KEY` — Google Cloud → APIs & Services → Credentials, with the
     *Maps JavaScript API* enabled. Restrict it to the URLs the page is served from
     (for a local check, `http://localhost:8765/*`).
   - `SEONI_SHEETS_DIR` — the folder holding the four sheet exports (default `~/Downloads`).
2. Run

   ```bash
   python3 supabase/setup.py
   ```

   It creates the five `seoni_` tables, functions and policies from
   [`supabase/schema.sql`](supabase/schema.sql), upserts the four sheets, writes the keys
   into `seoni-sample-plots-google.html` and finally queries the live project with the anon
   key to confirm the page will load. Re-run it (or `python3 supabase/setup.py --data`)
   whenever the sheets change; rows are upserted on their primary key.
   `psql` must be installed for the database connection (`brew install libpq`).
3. Serve the folder and open the page:

   ```bash
   python3 -m http.server 8765
   ```

   then browse to `http://localhost:8765/seoni-sample-plots-google.html`. Opening the
   file directly also works, but a Google Maps key restricted by referrer will reject a
   `file://` page.

The Supabase anon key is public by design (row level security limits it to reading the
data and writing review ticks); the database password, access token and service role key
stay in `.env` and never go into the page or into git.

### How it loads

Sample points, sampled treatment plots and control plots load once. The 25k deployment
polygons load for the area in view (from zoom level 13) through the
`seoni_deployments_in_bbox` function, so the page stays light.

## 2. `seoni-sample-plots-map.html` — self-contained (offline)

A single 4.5 MB file with all four sheets embedded, drawn with MapLibre over Esri
imagery or OpenStreetMap. Ticks are kept in the browser only. Rebuild it after the
sheets change:

```bash
python3 build.py \
  "$HOME/Downloads/Seoni Sample plots - Deployment.csv" \
  "$HOME/Downloads/Seoni Sample plots - Treatement sample pair.csv" \
  "$HOME/Downloads/Seoni Sample plots - Control_sampled_Plots.csv" \
  "$HOME/Downloads/Seoni Sample plots - control_plot.csv"
```

`template.html` is the page; `build.py` packs the data into it.

## Files

```
seoni-sample-plots-google.html   Supabase + Google Maps page (fill in SEONI_CONFIG)
seoni-sample-plots-map.html      self-contained MapLibre page (generated)
template.html, build.py          source of the self-contained page
supabase/setup.py                one-command setup driven by .env (schema, data, page keys, check)
supabase/schema.sql              tables, functions, policies
supabase/load_data.py            sheet parsing + REST upsert (used by setup.py; also runs on its own)
.env.example                     template for the keys
```

Note: the pages and the Supabase tables contain farmer names and IDs from the sheets.
