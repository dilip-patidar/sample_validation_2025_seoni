"""Build the Seoni sample-plot map.

Reads the deployment CSV (plot polygons) and the treatment sample-pair CSV
(pre / EOY soil sample codes and locations), packs them into a compact JSON
blob and inlines it into template.html -> seoni-sample-plots-map.html.

Usage:
  python3 build.py "<Deployment.csv>" "<Treatement sample pair.csv>" ["<Control_sampled_Plots.csv>" "<control_plot.csv>"]

The optional third and fourth CSVs hold the control plots: their pre + EOY
sample codes and locations, and their polygons (village given only as an id).
"""
import csv, json, math, sys, os, collections, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
dep_path, trt_path = sys.argv[1], sys.argv[2]
ctl_path = sys.argv[3] if len(sys.argv) > 3 else None
cplot_path = sys.argv[4] if len(sys.argv) > 4 else None
out_path = os.path.join(HERE, 'seoni-sample-plots-map.html')
csv.field_size_limit(10**9)

SCALE = 1e6  # coordinates stored as integer micro-degrees, delta-encoded per ring


def to_m(lat):
    """metres per degree of lon / lat at this latitude (planar, fine for < 50 km)."""
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xi:
                inside = not inside
    return inside


def point_in_polygon(x, y, rings):
    if not point_in_ring(x, y, rings[0]):
        return False
    return not any(point_in_ring(x, y, h) for h in rings[1:])


def dist_to_ring_m(lon, lat, ring):
    mx, my = to_m(lat)
    best = float('inf')
    n = len(ring)
    px, py = lon * mx, lat * my
    for i in range(n):
        ax, ay = ring[i][0] * mx, ring[i][1] * my
        bx, by = ring[(i + 1) % n][0] * mx, ring[(i + 1) % n][1] * my
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            d = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        best = min(best, d)
    return best


def encode_ring(ring):
    """round to 6 dp, drop consecutive duplicates, delta-encode as flat ints."""
    pts = []
    for x, y in ring:
        p = (int(round(x * SCALE)), int(round(y * SCALE)))
        if pts and pts[-1] == p:
            continue
        pts.append(p)
    flat, px, py = [], 0, 0
    for x, y in pts:
        flat.append(x - px)
        flat.append(y - py)
        px, py = x, y
    return flat


def norm_crusher(s):
    return ', '.join(sorted({p.strip().upper() for p in s.split(',') if p.strip()}))


# ---------------------------------------------------------------- deployments
deps = list(csv.DictReader(open(dep_path, newline='', encoding='utf-8')))
villages = sorted({d['village_name'].strip() for d in deps})
v_idx = {v: i for i, v in enumerate(villages)}
crushers = sorted({norm_crusher(d['crusher_code']) for d in deps})
c_idx = {c: i for i, c in enumerate(crushers)}

plots = []           # compact rows for the page
rings_by_plot = {}   # plot_id -> rings (float) for the point-in-polygon checks
no_geom = []
v_acc = collections.defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0.0])  # lon sum, lat sum, n, area, tonnage
bbox_by_plot = {}    # plot_id -> (minx, miny, maxx, maxy) for the control-point containment check
for d in deps:
    pid = d['plot_id'].strip()
    g = d['geometry'].strip()
    if not g:
        no_geom.append({'plot_id': pid, 'village': d['village_name'].strip(), 'farmer_id': d['farmer_id'].strip()})
        continue
    gj = json.loads(g)
    polys = [gj['coordinates']] if gj['type'] == 'Polygon' else gj['coordinates']
    rings = polys[0]  # all rows in this export are single Polygons
    rings_by_plot[pid] = rings
    xs = [q[0] for q in rings[0]]; ys = [q[1] for q in rings[0]]
    bbox_by_plot[pid] = (min(xs), min(ys), max(xs), max(ys))
    area = float(d['area'] or 0)
    ton = float(d['tonnage'] or 0)
    outer = rings[0]
    cx = sum(p[0] for p in outer) / len(outer)
    cy = sum(p[1] for p in outer) / len(outer)
    acc = v_acc[d['village_name'].strip()]
    acc[0] += cx; acc[1] += cy; acc[2] += 1; acc[3] += area; acc[4] += ton
    plots.append([
        int(pid),
        v_idx[d['village_name'].strip()],
        c_idx[norm_crusher(d['crusher_code'])],
        round(area, 3),
        ton if ton != int(ton) else int(ton),
        int(d['n_deployments'] or 1),
        d['deployment_ids'].strip(),
        d['farmer_id'].strip(),
        [encode_ring(r) for r in rings],
    ])

village_meta = []
for v in villages:
    a = v_acc[v]
    village_meta.append({
        'name': v,
        'center': [round(a[0] / a[2], 6), round(a[1] / a[2], 6)] if a[2] else None,
        'plots': a[2],
        'area_ha': round(a[3], 1),
        'tonnage': round(a[4], 1),
        'sampled': 0,
        'controls': 0,
    })

# ---------------------------------------------------------------- samples
trt = list(csv.DictReader(open(trt_path, newline='', encoding='utf-8')))
samples = []
issues = []
dists = []
for t in trt:
    pid = t['plot_id'].strip()
    plat, plon = (float(x) for x in t['pre_location'].split(','))
    elat, elon = (float(x) for x in t['eoy_location'].split(','))
    mx, my = to_m(plat)
    dist = math.hypot((plon - elon) * mx, (plat - elat) * my)
    dists.append(dist)
    rings = rings_by_plot.get(pid)
    pre_in = eoy_in = None
    pre_out_m = eoy_out_m = 0.0
    if rings:
        pre_in = point_in_polygon(plon, plat, rings)
        eoy_in = point_in_polygon(elon, elat, rings)
        if not pre_in:
            pre_out_m = round(dist_to_ring_m(plon, plat, rings[0]), 1)
        if not eoy_in:
            eoy_out_m = round(dist_to_ring_m(elon, elat, rings[0]), 1)
    else:
        issues.append({'kind': 'no_plot_geometry', 'plot_id': pid})
    if pre_in is False or eoy_in is False:
        issues.append({'kind': 'outside_plot', 'plot_id': pid, 'village': t['village_name'].strip(),
                       'pre_outside_m': pre_out_m if pre_in is False else 0,
                       'eoy_outside_m': eoy_out_m if eoy_in is False else 0})
    village_meta[v_idx[t['village_name'].strip()]]['sampled'] += 1
    samples.append([
        int(pid),
        t['farmer_name'].strip(),
        t['status'].strip(),
        t['pre'].strip(), round(plat, 7), round(plon, 7),
        t['eoy'].strip(), round(elat, 7), round(elon, 7),
        round(dist, 2),
        1 if pre_in else 0,
        1 if eoy_in else 0,
        pre_out_m, eoy_out_m,
        v_idx[t['village_name'].strip()],
        c_idx[norm_crusher(t['crusher_code'])],
        t['deployment_ids'].strip(),
    ])

# ---------------------------------------------------------------- control samples (pre + EOY pair per control plot)
controls = []
def ensure_village(vname):
    if vname not in v_idx:
        v_idx[vname] = len(villages); villages.append(vname)
        village_meta.append({'name': vname, 'center': None, 'plots': 0, 'area_ha': 0, 'tonnage': 0, 'sampled': 0, 'controls': 0, 'control_plots': 0})
    return v_idx[vname]
for m in village_meta:
    m['control_plots'] = 0
if ctl_path:
    for c in csv.DictReader(open(ctl_path, newline='', encoding='utf-8')):
        cid = c['control_plot_id'].strip()
        if not c['control_pre_location'].strip() or not c['control_eoy_location'].strip():
            issues.append({'kind': 'control_no_location', 'control_plot_id': cid, 'village': c['village_name'].strip()})
            continue
        plat, plon = (float(x) for x in c['control_pre_location'].split(','))
        elat, elon = (float(x) for x in c['control_eoy_location'].split(','))
        vi = ensure_village(c['village_name'].strip())
        village_meta[vi]['controls'] += 1
        controls.append([int(cid), vi, c['farmer_name'].strip(), c['pre_ids'].strip(), round(plat, 7), round(plon, 7),
                         c['eoy_ids'].strip(), round(elat, 7), round(elon, 7)])

# ---------------------------------------------------------------- control plot polygons (village resolved from the samples, else nearest village)
control_plots = []
if cplot_path:
    name_by_plot = {c[0]: villages[c[1]] for c in controls}
    rows = list(csv.DictReader(open(cplot_path, newline='', encoding='utf-8')))
    name_by_vid = {r['village_id'].strip(): name_by_plot[int(r['plot_id'])] for r in rows if int(r['plot_id']) in name_by_plot and r.get('village_id')}
    centers = {m['name']: m['center'] for m in village_meta if m['center']}
    for r in rows:
        pid = int(r['plot_id'])
        g = (r.get('geometry') or '').strip()
        if not g:
            issues.append({'kind': 'control_plot_no_geometry', 'control_plot_id': str(pid)})
            continue
        gj = json.loads(g)
        rings = gj['coordinates'] if gj['type'] == 'Polygon' else gj['coordinates'][0]
        name = name_by_plot.get(pid) or name_by_vid.get((r.get('village_id') or '').strip())
        if not name and centers:
            ring = rings[0]; cx = sum(q[0] for q in ring) / len(ring); cy = sum(q[1] for q in ring) / len(ring)
            name = min(centers, key=lambda v: (centers[v][0] - cx) ** 2 + (centers[v][1] - cy) ** 2)
        vi = ensure_village(name or 'Unknown')
        village_meta[vi]['control_plots'] += 1
        try: area = float(r.get('control_area') or 0)
        except ValueError: area = 0
        try: calc = float(r.get('calculated_area') or 0)
        except ValueError: calc = 0
        control_plots.append([pid, vi, (r.get('farmer_id') or '').strip(), area, calc, (r.get('surveyor_name') or '').strip(),
                              1 if (r.get('control_plot_terrain') or '').strip().upper() == 'TRUE' else 0,
                              [encode_ring(x) for x in rings]])

dists.sort()
data = {
    'meta': {
        'deployment_id': sorted({d['deployment_id'] for d in deps}),
        'built': datetime.date.today().isoformat(),
        'sources': [os.path.basename(x) for x in (dep_path, trt_path, ctl_path, cplot_path) if x],
        'plot_fields': ['plot_id', 'village', 'crusher', 'area_ha', 'tonnage', 'n_deployments', 'deployment_ids', 'farmer_id', 'rings'],
        'sample_fields': ['plot_id', 'farmer_name', 'status', 'pre', 'pre_lat', 'pre_lon', 'eoy', 'eoy_lat', 'eoy_lon',
                          'pre_eoy_m', 'pre_in_plot', 'eoy_in_plot', 'pre_outside_m', 'eoy_outside_m', 'village', 'crusher', 'deployment_ids'],
        'control_fields': ['control_plot_id', 'village', 'farmer_name', 'pre_code', 'pre_lat', 'pre_lon', 'eoy_code', 'eoy_lat', 'eoy_lon'],
        'control_plot_fields': ['control_plot_id', 'village', 'farmer_id', 'control_area_ha', 'calculated_area_ha', 'surveyor', 'terrain', 'rings'],
        'scale': SCALE,
        'totals': {
            'plots': len(plots),
            'plots_no_geometry': len(no_geom),
            'area_ha': round(sum(p[3] for p in plots), 1),
            'tonnage': round(sum(p[4] for p in plots), 1),
            'sampled_plots': len(samples),
            'sampled_in_deployment': sum(1 for s in samples if s[0] in {p[0] for p in plots}),
            'pre_eoy_m_median': round(dists[len(dists) // 2], 2) if dists else None,
            'pre_eoy_m_max': round(dists[-1], 2) if dists else None,
            'samples_outside_plot': sum(1 for i in issues if i['kind'] == 'outside_plot'),
            'control_samples': len(controls),
            'control_plots': len(control_plots),
            'control_area_ha': round(sum(c[3] for c in control_plots), 1),
        },
    },
    'villages': village_meta,
    'crushers': crushers,
    'plots': plots,
    'samples': samples,
    'controls': controls,
    'control_plots': control_plots,
    'issues': issues,
    'no_geometry': no_geom,
}

blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
tpl = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
marker = '/*__DATA__*/'
assert marker in tpl, 'template.html is missing the /*__DATA__*/ marker'
html = tpl.replace(marker, blob.replace('</script', '<\\/script'))
open(out_path, 'w', encoding='utf-8').write(html)
print(f'wrote {out_path}  ({len(html)/1e6:.2f} MB; data {len(blob)/1e6:.2f} MB)')
print(json.dumps(data['meta']['totals'], indent=1))
print('issues:', json.dumps(issues, indent=1))
print('no geometry:', no_geom)
