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

### One-time setup

1. **Create the tables.** Open the project's SQL editor
   (`https://supabase.com/dashboard/project/<project-ref>/sql/new`), paste the whole of
   [`supabase/schema.sql`](supabase/schema.sql) and run it. It creates five tables
   (`seoni_deployments`, `seoni_treatment_samples`, `seoni_control_plots`,
   `seoni_control_samples`, `seoni_sample_reviews`), enables PostGIS, adds the functions the
   page calls and read-only row level security for the anon key (ticks are writable).
   Everything is prefixed `seoni_` so it sits beside tables you already have.
2. **Load the sheets.** The loader needs the *service role* key (Project Settings → API).
   Keep it out of the page and out of git:

   ```bash
   export SUPABASE_URL="https://<project-ref>.supabase.co"
   export SUPABASE_SERVICE_ROLE_KEY="<service role key>"
   python3 supabase/load_data.py \
     "$HOME/Downloads/Seoni Sample plots - Deployment.csv" \
     "$HOME/Downloads/Seoni Sample plots - Treatement sample pair.csv" \
     "$HOME/Downloads/Seoni Sample plots - Control_sampled_Plots.csv" \
     "$HOME/Downloads/Seoni Sample plots - control_plot.csv"
   ```

   Rows are upserted on their primary key, so re-run it whenever the sheets change.
   Only `curl` and Python 3 are needed.
3. **Keys in the page.** At the top of `seoni-sample-plots-google.html`, `SEONI_CONFIG`
   holds the Supabase URL, the anon key (public by design) and the Google Maps API key.
   The Google key needs the *Maps JavaScript API* enabled; restrict it to the URLs the
   page is served from (for a local check, `http://localhost:8765/*`).
4. **Open it.** Serve the folder and open the page, for example:

   ```bash
   python3 -m http.server 8765
   ```

   then browse to `http://localhost:8765/seoni-sample-plots-google.html`. Opening the
   file directly also works, but Google Maps keys restricted by referrer will reject a
   `file://` page.

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
supabase/schema.sql              tables, functions, policies (run once)
supabase/load_data.py            CSV → Supabase upsert (needs the service role key)
```

Note: the pages and the Supabase tables contain farmer names and IDs from the sheets.
