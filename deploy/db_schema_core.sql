-- Core schema sketch for Geoscan planner
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE spec_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    url text,
    source_kind text NOT NULL,
    published_at timestamptz,
    retrieved_at timestamptz NOT NULL DEFAULT now(),
    sha256 text,
    priority integer NOT NULL DEFAULT 100,
    notes text
);

CREATE TABLE uav_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer text NOT NULL,
    model_name text NOT NULL,
    vehicle_class text NOT NULL CHECK (vehicle_class IN ('fixed_wing','multirotor','vtol','other')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (manufacturer, model_name)
);

CREATE TABLE uav_model_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    uav_model_id uuid NOT NULL REFERENCES uav_models(id),
    revision_name text NOT NULL,
    valid_from date,
    valid_to date,
    specs jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_id uuid REFERENCES spec_sources(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payload_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer text,
    model_name text NOT NULL,
    payload_type text NOT NULL,
    specs jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_id uuid REFERENCES spec_sources(id)
);

CREATE TABLE uav_payload_compatibility (
    uav_revision_id uuid NOT NULL REFERENCES uav_model_revisions(id),
    payload_id uuid NOT NULL REFERENCES payload_models(id),
    constraints jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (uav_revision_id, payload_id)
);

CREATE TABLE scenes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    input_crs text NOT NULL DEFAULT 'EPSG:4326',
    status text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scene_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id uuid NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    asset_type text NOT NULL,
    object_uri text NOT NULL,
    sha256 text NOT NULL,
    size_bytes bigint,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE survey_areas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id uuid NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    geom geometry(MultiPolygon,4326) NOT NULL,
    survey_type text NOT NULL,
    requirements jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX survey_areas_geom_gix ON survey_areas USING gist (geom);

CREATE TABLE airspace_constraints (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id uuid NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    geom geometry(MultiPolygon,4326) NOT NULL,
    min_alt_m double precision,
    max_alt_m double precision,
    active_from timestamptz,
    active_to timestamptz,
    constraint_type text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX airspace_constraints_geom_gix ON airspace_constraints USING gist (geom);

CREATE TABLE landing_sites (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id uuid NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    name text NOT NULL,
    role text NOT NULL CHECK (role IN ('start','landing','reserve','both')),
    geom geometry(Point,4326) NOT NULL,
    constraints jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX landing_sites_geom_gix ON landing_sites USING gist (geom);

CREATE TABLE planning_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id uuid NOT NULL REFERENCES scenes(id),
    objective text NOT NULL CHECK (objective IN ('makespan','total_flight')),
    status text NOT NULL,
    solver_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

CREATE TABLE plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    planning_job_id uuid NOT NULL REFERENCES planning_jobs(id),
    status text NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sorties (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    uav_instance_id text NOT NULL,
    sortie_index integer NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL,
    start_site_id uuid REFERENCES landing_sites(id),
    landing_site_id uuid REFERENCES landing_sites(id),
    route geometry(LineStringZ,4326) NOT NULL,
    resource_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (plan_id, uav_instance_id, sortie_index)
);
CREATE INDEX sorties_route_gix ON sorties USING gist (route);

CREATE TABLE safety_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL UNIQUE REFERENCES plans(id) ON DELETE CASCADE,
    status text NOT NULL,
    coverage_percent double precision,
    nfz_violations integer NOT NULL DEFAULT 0,
    obstacle_violations integer NOT NULL DEFAULT 0,
    airspace_time_violations integer NOT NULL DEFAULT 0,
    vehicle_conflicts integer NOT NULL DEFAULT 0,
    min_horizontal_separation_m double precision,
    min_vertical_separation_m double precision,
    min_energy_margin_fraction double precision,
    reserve_landing_reachability boolean,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recommendations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    planning_job_id uuid NOT NULL REFERENCES planning_jobs(id) ON DELETE CASCADE,
    recommendation_type text NOT NULL,
    rank integer NOT NULL,
    reason text NOT NULL,
    proposed_change jsonb NOT NULL,
    predicted_effect jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE export_artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    format text NOT NULL,
    object_uri text NOT NULL,
    sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
