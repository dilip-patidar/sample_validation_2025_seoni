-- Seoni sample plots: Supabase schema (all objects are prefixed seoni_ so they sit beside existing tables)
-- Run once in the Supabase SQL editor (Database > SQL) on a fresh project.
-- Creates the three data tables, the shared review-tick table, the PostGIS
-- geometry column, the functions the map page calls, and read/write policies.

create extension if not exists postgis;

-- ---------------------------------------------------------------- deployments
create table if not exists public.seoni_deployments (
  plot_id        bigint primary key,
  deployment_id  text not null,
  village_name   text not null,
  farmer_id      text,
  crusher_code   text,
  deployment_ids text,
  n_deployments  integer not null default 1,
  area_ha        numeric,
  tonnage        numeric,
  geometry       jsonb,                       -- GeoJSON Polygon exactly as exported from the sheet
  geom           geometry(Polygon, 4326),     -- PostGIS copy of the same polygon, kept in sync by trigger
  loaded_at      timestamptz not null default now()
);

-- keeps the PostGIS column in step with the GeoJSON column (used by deployments and control_plots)
create or replace function public.seoni_sync_geom() returns trigger
language plpgsql as $$
begin
  new.geom := case when new.geometry is null then null
                   else st_setsrid(st_geomfromgeojson(new.geometry::text), 4326) end;
  return new;
end $$;

drop trigger if exists seoni_deployments_sync_geom on public.seoni_deployments;
create trigger seoni_deployments_sync_geom
  before insert or update of geometry on public.seoni_deployments
  for each row execute function public.seoni_sync_geom();

create index if not exists seoni_deployments_geom_idx on public.seoni_deployments using gist (geom);
create index if not exists seoni_deployments_village_idx on public.seoni_deployments (village_name);

-- ---------------------------------------------------------------- treatment samples (pre + EOY pair per sampled plot)
create table if not exists public.seoni_treatment_samples (
  plot_id        bigint primary key,
  deployment_id  text,
  village_name   text not null,
  farmer_id      text,
  farmer_name    text,
  status         text,
  crusher_code   text,
  deployment_ids text,
  n_deployments  integer,
  pre_code       text not null,
  pre_lat        double precision not null,
  pre_lon        double precision not null,
  eoy_code       text not null,
  eoy_lat        double precision not null,
  eoy_lon        double precision not null,
  loaded_at      timestamptz not null default now()
);
create index if not exists seoni_treatment_samples_village_idx on public.seoni_treatment_samples (village_name);

-- ---------------------------------------------------------------- control plots (polygons, from "control_plot.csv")
create table if not exists public.seoni_control_plots (
  plot_id              bigint primary key,
  farmer_id            text,
  village_id           integer,
  village_name         text,                  -- resolved by the loader: the sheet only carries village_id
  base                 text,
  base_season_year     text,
  season               text,
  year                 integer,
  control_plot_terrain boolean,
  control_area_ha      numeric,
  calculated_area_ha   numeric,
  surveyor_name        text,
  location_lat         double precision,
  location_lon         double precision,
  geometry             jsonb,
  geom                 geometry(Polygon, 4326),
  loaded_at            timestamptz not null default now()
);
drop trigger if exists seoni_control_plots_sync_geom on public.seoni_control_plots;
create trigger seoni_control_plots_sync_geom
  before insert or update of geometry on public.seoni_control_plots
  for each row execute function public.seoni_sync_geom();
create index if not exists seoni_control_plots_geom_idx on public.seoni_control_plots using gist (geom);
create index if not exists seoni_control_plots_village_idx on public.seoni_control_plots (village_name);

-- ---------------------------------------------------------------- control samples (pre + EOY pair per control plot, from "Control_sampled_Plots.csv")
create table if not exists public.seoni_control_samples (
  control_plot_id bigint primary key,
  deployment_id   text,
  village_name    text not null,
  farmer_name     text,
  pre_code        text not null,
  pre_lat         double precision not null,
  pre_lon         double precision not null,
  eoy_code        text not null,
  eoy_lat         double precision not null,
  eoy_lon         double precision not null,
  loaded_at       timestamptz not null default now()
);
create index if not exists seoni_control_samples_village_idx on public.seoni_control_samples (village_name);

-- ---------------------------------------------------------------- review ticks, shared by everyone who opens the page
create table if not exists public.seoni_sample_reviews (
  sample_key  text primary key,               -- 't:<plot_id>' or 'c:<control_plot_id>'
  kind        text not null check (kind in ('treatment', 'control')),
  ref_id      bigint not null,
  reviewer    text,
  note        text,
  reviewed_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- functions the page calls (POST /rest/v1/rpc/<name>)
create or replace function public.seoni_totals() returns jsonb
language sql stable as $$
  select jsonb_build_object(
    'plots',            (select count(*) from public.seoni_deployments),
    'plots_no_geometry',(select count(*) from public.seoni_deployments where geom is null),
    'area_ha',          (select round(coalesce(sum(area_ha), 0), 1) from public.seoni_deployments),
    'tonnage',          (select round(coalesce(sum(tonnage), 0), 1) from public.seoni_deployments),
    'villages',         (select count(distinct village_name) from public.seoni_deployments),
    'sampled_plots',    (select count(*) from public.seoni_treatment_samples),
    'control_plots',    (select count(*) from public.seoni_control_plots),
    'control_area_ha',  (select round(coalesce(sum(control_area_ha), 0), 1) from public.seoni_control_plots),
    'control_samples',  (select count(*) from public.seoni_control_samples),
    'deployment_ids',   (select coalesce(jsonb_agg(distinct deployment_id), '[]') from public.seoni_deployments),
    'bbox',             (select jsonb_build_array(st_xmin(e), st_ymin(e), st_xmax(e), st_ymax(e))
                           from (select st_extent(geom)::geometry as e from public.seoni_deployments) x)
  );
$$;

create or replace function public.seoni_village_stats() returns jsonb
language sql stable as $$
  select coalesce(jsonb_agg(to_jsonb(v) order by v.name), '[]') from (
    select d.village_name as name,
           count(*)                                   as plots,
           round(coalesce(sum(d.area_ha), 0), 1)      as area_ha,
           round(coalesce(sum(d.tonnage), 0), 1)      as tonnage,
           avg(st_x(st_centroid(d.geom)))             as lon,
           avg(st_y(st_centroid(d.geom)))             as lat,
           jsonb_build_array(st_xmin(st_extent(d.geom)::geometry), st_ymin(st_extent(d.geom)::geometry),
                             st_xmax(st_extent(d.geom)::geometry), st_ymax(st_extent(d.geom)::geometry)) as bbox,
           (select count(*) from public.seoni_treatment_samples t where t.village_name = d.village_name) as sampled,
           (select count(*) from public.seoni_control_samples c where c.village_name = d.village_name)   as controls,
           (select count(*) from public.seoni_control_plots p where p.village_name = d.village_name)     as control_plots
    from public.seoni_deployments d
    group by d.village_name) v;
$$;

-- crusher codes as the page filters them: upper-cased, trimmed, comma-joined in sheet order
create or replace function public.seoni_crusher_norm(code text) returns text
language sql immutable as $$
  select array_to_string(array(select upper(trim(t.x)) from unnest(string_to_array(coalesce(code, ''), ',')) as t(x) where trim(t.x) <> ''), ', ');
$$;

create or replace function public.seoni_crusher_stats() returns jsonb
language sql stable as $$
  select coalesce(jsonb_agg(to_jsonb(c) order by c.crusher), '[]') from (
    select public.seoni_crusher_norm(crusher_code) as crusher, count(*) as plots
    from public.seoni_deployments where coalesce(crusher_code, '') <> '' group by 1) c;
$$;

-- one deployment polygon as a GeoJSON Feature
create or replace function public.seoni_plot_feature(d public.seoni_deployments) returns jsonb
language sql stable as $$
  select jsonb_build_object(
    'type', 'Feature', 'id', d.plot_id,
    'properties', jsonb_build_object(
      'id', d.plot_id, 'village', d.village_name, 'crusher', d.crusher_code, 'area_ha', d.area_ha,
      'tonnage', d.tonnage, 'n_deployments', d.n_deployments, 'deployment_ids', d.deployment_ids,
      'farmer_id', d.farmer_id,
      'sampled', exists (select 1 from public.seoni_treatment_samples t where t.plot_id = d.plot_id)),
    'geometry', st_asgeojson(d.geom, 6)::jsonb);
$$;

-- the sampled plots (the ones with a treatment pair): loaded once by the page
create or replace function public.seoni_sampled_plots_geojson() returns jsonb
language sql stable as $$
  select jsonb_build_object('type', 'FeatureCollection',
    'features', coalesce(jsonb_agg(public.seoni_plot_feature(d)), '[]'))
  from public.seoni_deployments d
  where d.geom is not null
    and exists (select 1 from public.seoni_treatment_samples t where t.plot_id = d.plot_id);
$$;

-- unsampled deployment plots inside a bounding box: loaded by the page for the area in view
create or replace function public.seoni_deployments_in_bbox(
  min_lon double precision, min_lat double precision, max_lon double precision, max_lat double precision,
  max_rows integer default 6000) returns jsonb
language sql stable as $$
  select jsonb_build_object('type', 'FeatureCollection',
    'features', coalesce((select jsonb_agg(q.f) from (
                   select public.seoni_plot_feature(d) as f
                   from public.seoni_deployments d
                   where d.geom && st_makeenvelope(min_lon, min_lat, max_lon, max_lat, 4326)
                     and not exists (select 1 from public.seoni_treatment_samples t where t.plot_id = d.plot_id)
                   limit max_rows) q), '[]'),
    'truncated', (select count(*) from public.seoni_deployments x
                   where x.geom && st_makeenvelope(min_lon, min_lat, max_lon, max_lat, 4326)
                     and not exists (select 1 from public.seoni_treatment_samples t where t.plot_id = x.plot_id)) > max_rows);
$$;

-- all control plot polygons (131 or so): loaded once by the page
create or replace function public.seoni_control_plots_geojson() returns jsonb
language sql stable as $$
  select jsonb_build_object('type', 'FeatureCollection',
    'features', coalesce(jsonb_agg(jsonb_build_object(
      'type', 'Feature', 'id', c.plot_id,
      'properties', jsonb_build_object(
        'id', c.plot_id, 'village', c.village_name, 'farmer_id', c.farmer_id, 'control_area_ha', c.control_area_ha,
        'calculated_area_ha', c.calculated_area_ha, 'surveyor', c.surveyor_name, 'terrain', c.control_plot_terrain,
        'season', c.season, 'year', c.year,
        'sampled', exists (select 1 from public.seoni_control_samples s where s.control_plot_id = c.plot_id)),
      'geometry', st_asgeojson(c.geom, 6)::jsonb)), '[]'))
  from public.seoni_control_plots c where c.geom is not null;
$$;

-- one plot by id (search box)
create or replace function public.seoni_plot_geojson(p_plot_id bigint) returns jsonb
language sql stable as $$
  select public.seoni_plot_feature(d) from public.seoni_deployments d where d.plot_id = p_plot_id and d.geom is not null;
$$;

-- ---------------------------------------------------------------- row level security
alter table public.seoni_deployments       enable row level security;
alter table public.seoni_treatment_samples enable row level security;
alter table public.seoni_control_samples   enable row level security;
alter table public.seoni_control_plots     enable row level security;
alter table public.seoni_sample_reviews    enable row level security;

drop policy if exists "read deployments"       on public.seoni_deployments;
drop policy if exists "read treatment samples" on public.seoni_treatment_samples;
drop policy if exists "read control samples"   on public.seoni_control_samples;
drop policy if exists "read control plots"     on public.seoni_control_plots;
drop policy if exists "read reviews"           on public.seoni_sample_reviews;
drop policy if exists "insert reviews"         on public.seoni_sample_reviews;
drop policy if exists "update reviews"         on public.seoni_sample_reviews;
drop policy if exists "delete reviews"         on public.seoni_sample_reviews;

create policy "read deployments"       on public.seoni_deployments       for select to anon, authenticated using (true);
create policy "read treatment samples" on public.seoni_treatment_samples for select to anon, authenticated using (true);
create policy "read control samples"   on public.seoni_control_samples   for select to anon, authenticated using (true);
create policy "read control plots"     on public.seoni_control_plots     for select to anon, authenticated using (true);
-- anyone holding the anon key can tick and untick; tighten to `authenticated` once sign-in is added
create policy "read reviews"   on public.seoni_sample_reviews for select to anon, authenticated using (true);
create policy "insert reviews" on public.seoni_sample_reviews for insert to anon, authenticated with check (true);
create policy "update reviews" on public.seoni_sample_reviews for update to anon, authenticated using (true) with check (true);
create policy "delete reviews" on public.seoni_sample_reviews for delete to anon, authenticated using (true);

grant usage on schema public to anon, authenticated;
grant select on public.seoni_deployments, public.seoni_treatment_samples, public.seoni_control_samples, public.seoni_control_plots to anon, authenticated;
grant select, insert, update, delete on public.seoni_sample_reviews to anon, authenticated;
grant execute on function public.seoni_totals(), public.seoni_village_stats(), public.seoni_crusher_stats(), public.seoni_crusher_norm(text), public.seoni_sampled_plots_geojson(), public.seoni_control_plots_geojson(),
  public.seoni_deployments_in_bbox(double precision, double precision, double precision, double precision, integer),
  public.seoni_plot_geojson(bigint), public.seoni_plot_feature(public.seoni_deployments) to anon, authenticated;
