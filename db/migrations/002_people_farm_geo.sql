CREATE TABLE growth_stage (
  code text PRIMARY KEY,
  description text NOT NULL,
  sort_order smallint NOT NULL DEFAULT 0
);

CREATE TABLE farm (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  location_name text,
  timezone text NOT NULL DEFAULT 'Africa/Cairo',
  currency char(3) NOT NULL DEFAULT 'EGP',
  boundary geometry(MultiPolygon, 4326),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX farm_boundary_gix ON farm USING gist (boundary);
CREATE TRIGGER farm_updated BEFORE UPDATE ON farm FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE role (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  description text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employee (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  full_name text NOT NULL,
  phone text,
  job_title text,
  active boolean NOT NULL DEFAULT true,
  hired_on date,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username text NOT NULL UNIQUE,
  password_hash text,                       -- NULL when authenticating via external IdP (OIDC)
  external_subject text UNIQUE,
  employee_id uuid REFERENCES employee(id),
  locale text NOT NULL DEFAULT 'ar' CHECK (locale IN ('ar','en')),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_role (
  user_id uuid NOT NULL REFERENCES app_user(id),
  role_id uuid NOT NULL REFERENCES role(id),
  granted_by uuid REFERENCES app_user(id),
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role_id),
  CHECK (granted_by IS DISTINCT FROM user_id)            -- nobody grants their own role
);

CREATE TABLE farm_block (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  code text NOT NULL,
  area_feddan numeric(10,3) NOT NULL CHECK (area_feddan > 0),
  palms_per_feddan integer NOT NULL DEFAULT 50 CHECK (palms_per_feddan > 0),  -- planning assumption, per block
  growth_stage text NOT NULL REFERENCES growth_stage(code),
  irrigation_priority integer NOT NULL CHECK (irrigation_priority > 0),
  min_irrigation_fraction numeric(4,3) NOT NULL DEFAULT 0.5 CHECK (min_irrigation_fraction BETWEEN 0 AND 1),
  geom geometry(Polygon, 4326),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (farm_id, code)
);
CREATE INDEX farm_block_geom_gix ON farm_block USING gist (geom);
CREATE TRIGGER farm_block_updated BEFORE UPDATE ON farm_block FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- One row per palm (NOT one agent per palm).
CREATE TABLE palm (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  tag_code text NOT NULL,
  geom geometry(Point, 4326),
  planted_on date,
  variety text NOT NULL DEFAULT 'Medjool',
  status text NOT NULL DEFAULT 'alive' CHECK (status IN ('alive','stressed','removed','dead')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (farm_id, tag_code)
);
CREATE INDEX palm_block_ix ON palm (block_id);
CREATE INDEX palm_geom_gix ON palm USING gist (geom);

CREATE TABLE palm_lifecycle_record (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  palm_id uuid NOT NULL REFERENCES palm(id),
  stage text NOT NULL REFERENCES growth_stage(code),
  event_type text NOT NULL CHECK (event_type IN ('planted','stage_change','pruned','pollinated','thinned','treated','inspected','removed','other')),
  recorded_on date NOT NULL,
  recorded_by uuid REFERENCES employee(id),
  notes text,
  origin data_origin_t NOT NULL DEFAULT 'human_confirmed',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX palm_lifecycle_palm_ix ON palm_lifecycle_record (palm_id, recorded_on);
