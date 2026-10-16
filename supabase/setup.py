"""One-command setup for the Seoni sample-plot map on Supabase.

    python3 supabase/setup.py            # everything .env allows: schema, data, page config, check
    python3 supabase/setup.py --schema   # only create / update the tables and functions
    python3 supabase/setup.py --data     # only (re)load the four sheets
    python3 supabase/setup.py --page     # only write the keys into seoni-sample-plots-google.html
    python3 supabase/setup.py --check    # only query the live project with the anon key

Reads .env next to this folder's parent (the repo root); see .env.example for the keys.
"""
import json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from load_data import build_rows, upsert  # noqa: E402

SHEETS = {
    'deployments': 'Seoni Sample plots - Deployment.csv',
    'treatment': 'Seoni Sample plots - Treatement sample pair.csv',
    'control_samples': 'Seoni Sample plots - Control_sampled_Plots.csv',
    'control_plots': 'Seoni Sample plots - control_plot.csv',
}
PK = {'deployments': 'plot_id', 'treatment_samples': 'plot_id', 'control_samples': 'control_plot_id', 'control_plots': 'plot_id'}
PAGE = os.path.join(ROOT, 'seoni-sample-plots-google.html')


def read_env():
    env = {}
    path = os.environ.get('SEONI_ENV', os.path.join(ROOT, '.env'))
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            v = v.strip().strip('"').strip("'")
            env[k.strip()] = v
    for k in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_DB_URL', 'SUPABASE_ACCESS_TOKEN', 'SUPABASE_SERVICE_ROLE_KEY', 'GOOGLE_MAPS_API_KEY', 'SEONI_SHEETS_DIR'):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.setdefault('SEONI_SHEETS_DIR', '~/Downloads')
    return path, env


def usable(v):
    return bool(v) and '[YOUR-' not in v and 'YOUR-' not in v


def curl(args, data=None):
    cmd = ['curl', '-sS', '-o', '-', '-w', '\n%{http_code}'] + args
    out = subprocess.run(cmd, input=data, capture_output=True, text=True)
    body, _, status = out.stdout.rpartition('\n')
    return status, body


def project_ref(url):
    m = re.match(r'https://([a-z0-9]+)\.supabase\.co', url or '')
    return m.group(1) if m else None


# ---------------------------------------------------------------- schema
def apply_schema(env):
    sql = open(os.path.join(HERE, 'schema.sql'), encoding='utf-8').read()
    if usable(env.get('SUPABASE_DB_URL')):
        if not shutil.which('psql'):
            sys.exit('psql is not installed (brew install libpq, or postgresql); or set SUPABASE_ACCESS_TOKEN instead')
        print('schema: applying schema.sql with psql …', flush=True)
        r = subprocess.run(['psql', env['SUPABASE_DB_URL'], '-v', 'ON_ERROR_STOP=1', '-q', '-f', os.path.join(HERE, 'schema.sql')], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit('schema failed:\n' + (r.stderr or r.stdout))
        print('schema: OK (tables, functions and policies are in place)')
        return True
    if usable(env.get('SUPABASE_ACCESS_TOKEN')) and project_ref(env.get('SUPABASE_URL')):
        print('schema: applying schema.sql through the Supabase Management API …', flush=True)
        status, body = curl(['-X', 'POST', f"https://api.supabase.com/v1/projects/{project_ref(env['SUPABASE_URL'])}/database/query",
                             '-H', f"Authorization: Bearer {env['SUPABASE_ACCESS_TOKEN']}", '-H', 'Content-Type: application/json', '--data-binary', '@-'],
                            data=json.dumps({'query': sql}))
        if not status.startswith('2'):
            sys.exit(f'schema failed: HTTP {status}\n{body[:800]}')
        print('schema: OK (tables, functions and policies are in place)')
        return True
    print('schema: skipped. Put SUPABASE_DB_URL (or SUPABASE_ACCESS_TOKEN) in .env, or paste supabase/schema.sql into')
    print(f"        https://supabase.com/dashboard/project/{project_ref(env.get('SUPABASE_URL')) or '<ref>'}/sql/new and run it.")
    return False


# ---------------------------------------------------------------- data
def sql_literal(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v, separators=(',', ':')).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def load_rows(env):
    d = os.path.expanduser(env['SEONI_SHEETS_DIR'])
    paths = {k: os.path.join(d, v) for k, v in SHEETS.items()}
    missing = [p for p in paths.values() if not os.path.exists(p)]
    if missing:
        sys.exit('data: these sheets were not found (set SEONI_SHEETS_DIR in .env):\n  ' + '\n  '.join(missing))
    rows = build_rows(paths['deployments'], paths['treatment'], paths['control_samples'], paths['control_plots'])
    for t, r in rows.items():
        print(f'data: {t}: {len(r)} rows from the sheet')
    if usable(env.get('SUPABASE_DB_URL')):
        print('data: upserting with psql …', flush=True)
        with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False, encoding='utf-8') as f:
            f.write('set client_min_messages = warning;\n')
            for table, r in rows.items():
                if not r:
                    continue
                cols = list(r[0].keys())
                pk = PK[table]
                sets = ', '.join(f'{c} = excluded.{c}' for c in cols if c != pk)
                for i in range(0, len(r), 500):
                    f.write(f'insert into public.seoni_{table} ({", ".join(cols)}) values\n')
                    f.write(',\n'.join('(' + ', '.join(sql_literal(x[c]) for c in cols) + ')' for x in r[i:i + 500]))
                    f.write(f'\non conflict ({pk}) do update set {sets};\n')
            path = f.name
        try:
            res = subprocess.run(['psql', env['SUPABASE_DB_URL'], '-v', 'ON_ERROR_STOP=1', '-q', '-f', path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        if res.returncode != 0:
            sys.exit('data load failed:\n' + (res.stderr or res.stdout))
        print('data: OK')
        return True
    if usable(env.get('SUPABASE_SERVICE_ROLE_KEY')):
        print('data: upserting through the REST API …', flush=True)
        for table in ('deployments', 'treatment_samples', 'control_samples', 'control_plots'):
            if rows[table]:
                upsert(env['SUPABASE_URL'].rstrip('/'), env['SUPABASE_SERVICE_ROLE_KEY'], 'seoni_' + table, rows[table])
        print('data: OK')
        return True
    print('data: skipped. Put SUPABASE_DB_URL (or SUPABASE_SERVICE_ROLE_KEY) in .env.')
    return False


# ---------------------------------------------------------------- page config
def write_page(env):
    s = open(PAGE, encoding='utf-8').read()
    changed = []
    for key in ('SUPABASE_URL', 'SUPABASE_ANON_KEY', 'GOOGLE_MAPS_API_KEY'):
        if not usable(env.get(key)):
            continue
        new, n = re.subn(rf"(\n\s*{key}: ')[^']*(',)", lambda m: m.group(1) + env[key] + m.group(2), s)
        if n != 1:
            sys.exit(f'page: could not find {key} in SEONI_CONFIG of {PAGE}')
        if new != s:
            changed.append(key)
        s = new
    open(PAGE, 'w', encoding='utf-8').write(s)
    print('page: ' + (f"updated {', '.join(changed)} in seoni-sample-plots-google.html" if changed else 'SEONI_CONFIG already up to date'))
    if not usable(env.get('GOOGLE_MAPS_API_KEY')):
        print('page: GOOGLE_MAPS_API_KEY is empty, the map will show a "for development purposes only" watermark until it is set')


# ---------------------------------------------------------------- check with the anon key
def check(env):
    url, key = env.get('SUPABASE_URL', '').rstrip('/'), env.get('SUPABASE_ANON_KEY', '')
    if not usable(url) or not usable(key):
        print('check: skipped, SUPABASE_URL / SUPABASE_ANON_KEY are not set in .env')
        return False
    status, body = curl(['-X', 'POST', f'{url}/rest/v1/rpc/seoni_totals', '-H', f'apikey: {key}', '-H', f'Authorization: Bearer {key}',
                         '-H', 'Content-Type: application/json', '-d', '{}'])
    if status != '200':
        print(f'check: the page would fail: HTTP {status} {body[:300]}')
        return False
    t = json.loads(body)
    print(f"check: OK — {t['plots']} deployment plots, {t['sampled_plots']} sampled, {t['control_plots']} control plots, "
          f"{t['control_samples']} control sample pairs, {t['villages']} villages")
    return True


def main(argv):
    path, env = read_env()
    print(f'.env: {path}' + ('' if os.path.exists(path) else ' (not found; copy .env.example to .env)'))
    only = [a for a in argv if a.startswith('--')]
    do = lambda step: not only or f'--{step}' in only
    if do('schema'):
        apply_schema(env)
    if do('data'):
        load_rows(env)
    if do('page'):
        write_page(env)
    if do('check'):
        check(env)


if __name__ == '__main__':
    main(sys.argv[1:])
