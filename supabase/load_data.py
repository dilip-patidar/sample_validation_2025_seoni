"""Load the four Seoni sheets into Supabase.

Usage:
  export SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
  export SUPABASE_SERVICE_ROLE_KEY="service-role key from Project Settings > API"
  python3 load_data.py "<Deployment.csv>" "<Treatement sample pair.csv>" "<Control_sampled_Plots.csv>" "<control_plot.csv>"

  python3 load_data.py ... --dry-run OUT_DIR   writes the rows as JSON files instead of uploading

Sheets:
  Deployment.csv              one row per basalt deployment plot, with its polygon
  Treatement sample pair.csv  pre + EOY soil sample codes and locations for the sampled treatment plots
  Control_sampled_Plots.csv   pre + EOY sample codes and locations for the control plots
  control_plot.csv            the control plot polygons (village given only as village_id)

Rows are upserted on their primary key, so re-running after the sheets change
updates existing rows and adds new ones; rows removed from a sheet stay in the
database until deleted by hand. Uploads go through curl so no Python packages
are needed. Run schema.sql in the Supabase SQL editor first (tables are named seoni_deployments, seoni_treatment_samples, seoni_control_samples, seoni_control_plots).
"""
import csv, json, os, sys, subprocess, tempfile

csv.field_size_limit(10**9)


def num(s):
    s = (s or '').strip()
    if s == '':
        return None
    try:
        return int(s)
    except ValueError:
        return float(s)


def text(s):
    s = (s or '').strip()
    return s or None


def latlon(s):
    lat, lon = (float(x) for x in s.split(','))
    return lat, lon


UNCLOSED = [0]


def close_rings(geom):
    """Return the polygon with every ring closed (first position repeated at the end)."""
    if not geom or geom.get('type') != 'Polygon':
        return geom
    rings = []
    for ring in geom['coordinates']:
        if ring and ring[0] != ring[-1]:
            ring = list(ring) + [ring[0]]
            UNCLOSED[0] += 1
        rings.append(ring)
    return {'type': 'Polygon', 'coordinates': rings}


def centroid(geom):
    ring = geom['coordinates'][0]
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def build_rows(dep_csv, trt_csv, ctl_csv=None, cplot_csv=None):
    """Return rows per table ready for upsert: deployments, treatment_samples, control_samples, control_plots."""
    deployments = []
    for d in csv.DictReader(open(dep_csv, newline='', encoding='utf-8')):
        g = (d.get('geometry') or '').strip()
        deployments.append({
            'plot_id': int(d['plot_id']),
            'deployment_id': d['deployment_id'].strip(),
            'village_name': d['village_name'].strip(),
            'farmer_id': text(d.get('farmer_id')),
            'crusher_code': text(d.get('crusher_code')),
            'deployment_ids': text(d.get('deployment_ids')),
            'n_deployments': num(d.get('n_deployments')) or 1,
            'area_ha': num(d.get('area')),
            'tonnage': num(d.get('tonnage')),
            'geometry': close_rings(json.loads(g)) if g else None,
        })

    treatment = []
    for t in csv.DictReader(open(trt_csv, newline='', encoding='utf-8')):
        plat, plon = latlon(t['pre_location'])
        elat, elon = latlon(t['eoy_location'])
        treatment.append({
            'plot_id': int(t['plot_id']),
            'deployment_id': text(t.get('deployment_id')),
            'village_name': t['village_name'].strip(),
            'farmer_id': text(t.get('farmer_id')),
            'farmer_name': text(t.get('farmer_name')),
            'status': text(t.get('status')),
            'crusher_code': text(t.get('crusher_code')),
            'deployment_ids': text(t.get('deployment_ids')),
            'n_deployments': num(t.get('n_deployments')),
            'pre_code': t['pre'].strip(), 'pre_lat': plat, 'pre_lon': plon,
            'eoy_code': t['eoy'].strip(), 'eoy_lat': elat, 'eoy_lon': elon,
        })

    controls = []
    if ctl_csv:
        for c in csv.DictReader(open(ctl_csv, newline='', encoding='utf-8')):
            if not (c.get('control_pre_location') or '').strip() or not (c.get('control_eoy_location') or '').strip():
                print(f"skipping control sample {c.get('control_plot_id')}: missing a location", file=sys.stderr)
                continue
            plat, plon = latlon(c['control_pre_location'])
            elat, elon = latlon(c['control_eoy_location'])
            controls.append({
                'control_plot_id': int(c['control_plot_id']),
                'deployment_id': text(c.get('deployment_id')),
                'village_name': c['village_name'].strip(),
                'farmer_name': text(c.get('farmer_name')),
                'pre_code': c['pre_ids'].strip(), 'pre_lat': plat, 'pre_lon': plon,
                'eoy_code': c['eoy_ids'].strip(), 'eoy_lat': elat, 'eoy_lon': elon,
            })

    control_plots = []
    if cplot_csv:
        # the control plot sheet has village_id only: take the name from the control sample for the
        # same plot, else from any sample sharing that village_id, else the nearest deployment village
        name_by_plot = {c['control_plot_id']: c['village_name'] for c in controls}
        rows = list(csv.DictReader(open(cplot_csv, newline='', encoding='utf-8')))
        name_by_vid = {}
        for r in rows:
            pid = int(r['plot_id'])
            if pid in name_by_plot and r.get('village_id'):
                name_by_vid[r['village_id'].strip()] = name_by_plot[pid]
        village_centers = {}
        for d in deployments:
            if d['geometry']:
                x, y = centroid(d['geometry'])
                acc = village_centers.setdefault(d['village_name'], [0.0, 0.0, 0])
                acc[0] += x; acc[1] += y; acc[2] += 1
        village_centers = {v: (a[0] / a[2], a[1] / a[2]) for v, a in village_centers.items() if a[2]}
        for r in rows:
            pid = int(r['plot_id'])
            g = (r.get('geometry') or '').strip()
            geom = close_rings(json.loads(g)) if g else None
            name = name_by_plot.get(pid) or name_by_vid.get((r.get('village_id') or '').strip())
            if not name and geom and village_centers:
                cx, cy = centroid(geom)
                name = min(village_centers, key=lambda v: (village_centers[v][0] - cx) ** 2 + (village_centers[v][1] - cy) ** 2)
                print(f'control plot {pid}: village taken from nearest deployment village ({name})', file=sys.stderr)
            loc = (r.get('location') or '').strip()
            llat, llon = latlon(loc) if loc else (None, None)
            control_plots.append({
                'plot_id': pid,
                'farmer_id': text(r.get('farmer_id')),
                'village_id': num(r.get('village_id')),
                'village_name': name,
                'base': text(r.get('Base')),
                'base_season_year': text(r.get('Base_Season_Year')),
                'season': text(r.get('Season')),
                'year': num(r.get('Year')),
                'control_plot_terrain': (r.get('control_plot_terrain') or '').strip().upper() == 'TRUE',
                'control_area_ha': num(r.get('control_area')),
                'calculated_area_ha': num(r.get('calculated_area')),
                'surveyor_name': text(r.get('surveyor_name')),
                'location_lat': llat, 'location_lon': llon,
                'geometry': geom,
            })
    return {'deployments': deployments, 'treatment_samples': treatment, 'control_samples': controls, 'control_plots': control_plots}


def upsert(url, key, table, rows, batch=500):
    """POST rows to PostgREST in batches with merge-duplicates (= upsert on the primary key)."""
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(chunk, f)
            path = f.name
        try:
            out = subprocess.run([
                'curl', '-sS', '-o', '-', '-w', '\n%{http_code}', '-X', 'POST', f'{url}/rest/v1/{table}',
                '-H', f'apikey: {key}', '-H', f'Authorization: Bearer {key}',
                '-H', 'Content-Type: application/json',
                '-H', 'Prefer: resolution=merge-duplicates,return=minimal',
                '--data-binary', f'@{path}'], capture_output=True, text=True)
        finally:
            os.unlink(path)
        body, _, status = out.stdout.rpartition('\n')
        if out.returncode != 0 or not status.startswith('2'):
            sys.exit(f'{table} rows {i}-{i + len(chunk)}: HTTP {status or out.returncode}\n{body or out.stderr}')
        print(f'{table}: {min(i + batch, len(rows))}/{len(rows)} rows', flush=True)


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    dry = argv[argv.index('--dry-run') + 1] if '--dry-run' in argv else None
    if len(args) < 2:
        sys.exit(__doc__)
    rows = build_rows(args[0], args[1], args[2] if len(args) > 2 else None, args[3] if len(args) > 3 else None)
    for table, r in rows.items():
        print(f'{table}: {len(r)} rows prepared')
    if UNCLOSED[0]:
        print(f'closed {UNCLOSED[0]} polygon rings whose last point did not repeat the first')
    if dry:
        os.makedirs(dry, exist_ok=True)
        for table, r in rows.items():
            json.dump(r, open(os.path.join(dry, table + '.json'), 'w', encoding='utf-8'))
        print('dry run: JSON written to', dry)
        return
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not url or not key:
        sys.exit('set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment (or use --dry-run OUT_DIR)')
    for table in ('deployments', 'treatment_samples', 'control_samples', 'control_plots'):
        if rows[table]:
            upsert(url, key, 'seoni_' + table, rows[table])   # tables are prefixed seoni_ in schema.sql
    print('done')


if __name__ == '__main__':
    main(sys.argv[1:])
