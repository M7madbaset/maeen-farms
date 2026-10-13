CREATE TABLE equipment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  code text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('well','pump','valve','filter','meter','controller','weather_station','vehicle','tool')),
  name text,
  block_id uuid REFERENCES farm_block(id),
  geom geometry(Point, 4326),
  rated_flow_m3h numeric(10,3) CHECK (rated_flow_m3h IS NULL OR rated_flow_m3h > 0),
  commissioned boolean NOT NULL DEFAULT false,            -- real-hardware control allowed ONLY when true
  commissioned_at timestamptz,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','maintenance','out_of_service','retired')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (farm_id, code),
  CHECK (NOT commissioned OR commissioned_at IS NOT NULL)
);
CREATE INDEX equipment_geom_gix ON equipment USING gist (geom);

CREATE TABLE irrigation_zone (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  code text NOT NULL,
  valve_equipment_id uuid REFERENCES equipment(id),
  area_feddan numeric(10,3) CHECK (area_feddan IS NULL OR area_feddan > 0),
  geom geometry(Polygon, 4326),
  UNIQUE (block_id, code)
);
CREATE INDEX irrigation_zone_geom_gix ON irrigation_zone USING gist (geom);

CREATE TABLE sensor (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  code text NOT NULL,
  equipment_id uuid REFERENCES equipment(id),
  block_id uuid REFERENCES farm_block(id),
  zone_id uuid REFERENCES irrigation_zone(id),
  measurand text NOT NULL,                                -- e.g. flow, pressure, soil_moisture, eto
  unit text NOT NULL,
  valid_min double precision,
  valid_max double precision,
  max_age_seconds integer NOT NULL DEFAULT 900 CHECK (max_age_seconds > 0),
  calibration_due date,
  geom geometry(Point, 4326),
  is_simulated boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (farm_id, code),
  CHECK (valid_min IS NULL OR valid_max IS NULL OR valid_min < valid_max)
);
CREATE INDEX sensor_geom_gix ON sensor USING gist (geom);

CREATE TABLE sensor_reading (                              -- raw observations only; derived values live elsewhere
  sensor_id uuid NOT NULL REFERENCES sensor(id),
  ts timestamptz NOT NULL,
  value double precision,
  unit text NOT NULL,
  quality quality_status_t NOT NULL DEFAULT 'unvalidated',
  origin data_origin_t NOT NULL DEFAULT 'raw_observation',
  is_simulated boolean NOT NULL,
  ingest_id text,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (sensor_id, ts),
  CHECK (value IS NOT NULL OR quality = 'missing')       -- a missing value is recorded as missing, never invented
) PARTITION BY RANGE (ts);
CREATE TABLE sensor_reading_default PARTITION OF sensor_reading DEFAULT;

CREATE FUNCTION ensure_sensor_reading_partition(month_start date) RETURNS text LANGUAGE plpgsql AS $$
DECLARE m date := date_trunc('month', month_start)::date; pname text := format('sensor_reading_%s', to_char(m, 'YYYY_MM'));
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF sensor_reading FOR VALUES FROM (%L) TO (%L)',
                 pname, m, (m + interval '1 month')::date);
  RETURN pname;
END $$;

CREATE TABLE weather_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  ts timestamptz NOT NULL,
  eto_mm_day numeric(6,2) CHECK (eto_mm_day IS NULL OR eto_mm_day >= 0),
  temp_c numeric(5,2), humidity_pct numeric(5,2) CHECK (humidity_pct IS NULL OR humidity_pct BETWEEN 0 AND 100),
  wind_ms numeric(5,2) CHECK (wind_ms IS NULL OR wind_ms >= 0), rain_mm numeric(6,2) CHECK (rain_mm IS NULL OR rain_mm >= 0),
  source text NOT NULL,
  origin data_origin_t NOT NULL DEFAULT 'raw_observation',
  quality quality_status_t NOT NULL DEFAULT 'unvalidated',
  is_simulated boolean NOT NULL,
  UNIQUE (farm_id, ts, source)
);

CREATE TABLE weather_forecast (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  issued_at timestamptz NOT NULL,
  valid_date date NOT NULL,
  eto_mm_day numeric(6,2) CHECK (eto_mm_day IS NULL OR eto_mm_day >= 0),
  temp_max_c numeric(5,2), rain_mm numeric(6,2) CHECK (rain_mm IS NULL OR rain_mm >= 0),
  provider text NOT NULL,
  origin data_origin_t NOT NULL DEFAULT 'model_prediction' CHECK (origin = 'model_prediction'),
  is_simulated boolean NOT NULL,
  UNIQUE (farm_id, issued_at, valid_date, provider)
);

CREATE TABLE soil_water_sample (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid REFERENCES farm_block(id),
  source_equipment_id uuid REFERENCES equipment(id),     -- e.g. the well a water sample came from
  sample_type text NOT NULL CHECK (sample_type IN ('soil','water')),
  taken_at timestamptz NOT NULL,
  depth_cm integer CHECK (depth_cm IS NULL OR depth_cm >= 0),
  lab_name text, lab_reference text,
  ec_dsm numeric(8,3) CHECK (ec_dsm IS NULL OR ec_dsm >= 0),
  ph numeric(4,2) CHECK (ph IS NULL OR ph BETWEEN 0 AND 14),
  tds_mgl numeric(10,2) CHECK (tds_mgl IS NULL OR tds_mgl >= 0),
  analytes jsonb NOT NULL DEFAULT '{}',                  -- each analyte carries its own unit
  report_media_id uuid REFERENCES media_object(id),
  origin data_origin_t NOT NULL DEFAULT 'raw_observation',
  quality quality_status_t NOT NULL DEFAULT 'unvalidated',
  is_simulated boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (block_id IS NOT NULL OR source_equipment_id IS NOT NULL)
);

-- Work, workforce, maintenance ------------------------------------------------------------------
CREATE TABLE work_order (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  kind text NOT NULL CHECK (kind IN ('irrigation','maintenance','agronomy','harvest','inspection','other')),
  title text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','assigned','in_progress','pending_review','done','cancelled')),
  priority smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  due_at timestamptz,
  block_id uuid REFERENCES farm_block(id),
  equipment_id uuid REFERENCES equipment(id),
  source_recommendation_id uuid REFERENCES recommendation(id),
  created_by uuid REFERENCES app_user(id),
  reviewed_by uuid REFERENCES app_user(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'done' OR reviewed_by IS NOT NULL)    -- supervisor review before closing
);
CREATE TRIGGER work_order_updated BEFORE UPDATE ON work_order FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE task (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  work_order_id uuid NOT NULL REFERENCES work_order(id),
  assigned_employee_id uuid REFERENCES employee(id),
  description text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','done','blocked','cancelled')),
  started_at timestamptz, completed_at timestamptz,
  evidence_media_id uuid REFERENCES media_object(id),
  exception_note text,
  CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE attendance_record (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id uuid NOT NULL REFERENCES employee(id),
  work_date date NOT NULL,
  check_in timestamptz, check_out timestamptz,
  recorded_by uuid REFERENCES app_user(id),
  UNIQUE (employee_id, work_date),
  CHECK (check_out IS NULL OR check_in IS NULL OR check_out >= check_in)
);

CREATE TABLE maintenance_event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  equipment_id uuid NOT NULL REFERENCES equipment(id),
  kind text NOT NULL CHECK (kind IN ('preventive','corrective','inspection','calibration')),
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','in_progress','done','verified','cancelled')),
  scheduled_for date, performed_at timestamptz,
  performed_by uuid REFERENCES employee(id),
  work_order_id uuid REFERENCES work_order(id),
  verified_by uuid REFERENCES app_user(id), verified_at timestamptz,
  notes text,
  CHECK (status <> 'verified' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL AND performed_at IS NOT NULL))
);

-- Inventory & procurement -----------------------------------------------------------------------
CREATE TABLE inventory_item (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  sku text NOT NULL,
  name_en text NOT NULL, name_ar text,
  category text NOT NULL CHECK (category IN ('fertilizer','chemical','spare_part','consumable','packaging','fuel','other')),
  unit text NOT NULL,
  reorder_threshold numeric(14,3) NOT NULL DEFAULT 0 CHECK (reorder_threshold >= 0),
  UNIQUE (farm_id, sku)
);

CREATE TABLE supplier (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  name text NOT NULL,
  contact jsonb NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true,
  UNIQUE (farm_id, name)
);

CREATE TABLE stock_movement (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id uuid NOT NULL REFERENCES inventory_item(id),
  movement_type text NOT NULL CHECK (movement_type IN ('receipt','issue','adjustment','transfer_in','transfer_out')),
  quantity numeric(14,3) NOT NULL CHECK (quantity <> 0),          -- signed: receipts +, issues -
  unit text NOT NULL,
  moved_at timestamptz NOT NULL DEFAULT now(),
  ref_type text, ref_id uuid,
  performed_by uuid REFERENCES employee(id),
  notes text,
  CHECK ((movement_type IN ('receipt','transfer_in') AND quantity > 0)
      OR (movement_type IN ('issue','transfer_out') AND quantity < 0)
      OR movement_type = 'adjustment')
);
CREATE INDEX stock_movement_item_ix ON stock_movement (item_id, moved_at);

CREATE TABLE purchase_request (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  supplier_id uuid REFERENCES supplier(id),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_approval','approved','rejected','committed','cancelled')),
  requested_by_agent text,
  requested_by_user uuid REFERENCES app_user(id),
  total_estimate_egp egp_t CHECK (total_estimate_egp IS NULL OR total_estimate_egp >= 0),
  source_recommendation_id uuid REFERENCES recommendation(id),
  approved_by uuid REFERENCES app_user(id), approved_at timestamptz,
  committed_by uuid REFERENCES app_user(id), committed_at timestamptz,      -- humans only
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (requested_by_agent IS NOT NULL OR requested_by_user IS NOT NULL),
  CHECK (approved_by IS NULL OR approved_by IS DISTINCT FROM requested_by_user),         -- segregation of duties
  CHECK (committed_by IS NULL OR committed_by IS DISTINCT FROM approved_by),
  CHECK (status NOT IN ('approved','committed') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CHECK (status <> 'committed' OR (committed_by IS NOT NULL AND committed_at IS NOT NULL))
);
CREATE TRIGGER purchase_request_updated BEFORE UPDATE ON purchase_request FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE purchase_request_line (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES purchase_request(id),
  item_id uuid NOT NULL REFERENCES inventory_item(id),
  quantity numeric(14,3) NOT NULL CHECK (quantity > 0),
  unit text NOT NULL,
  unit_price_egp egp_t CHECK (unit_price_egp IS NULL OR unit_price_egp >= 0)
);

CREATE TABLE supplier_quotation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES purchase_request(id),
  supplier_id uuid NOT NULL REFERENCES supplier(id),
  total_egp egp_t NOT NULL CHECK (total_egp >= 0),
  valid_until date,
  document_media_id uuid REFERENCES media_object(id),
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (request_id, supplier_id)
);
