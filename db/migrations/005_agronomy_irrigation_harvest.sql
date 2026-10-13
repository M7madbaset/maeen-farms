-- Agronomic parameters are CONFIGURATION: sourced, versioned by effective date, and flagged
-- 'placeholder' until a qualified agronomist validates them.
CREATE TABLE agronomic_parameter (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  key text NOT NULL,
  stage text REFERENCES growth_stage(code),
  value numeric(14,6) NOT NULL,
  unit text NOT NULL,
  source text NOT NULL,
  status text NOT NULL DEFAULT 'placeholder' CHECK (status IN ('placeholder','validated')),
  validated_by uuid REFERENCES app_user(id), validated_at timestamptz,
  effective_from date NOT NULL DEFAULT current_date,
  effective_to date,
  origin data_origin_t NOT NULL DEFAULT 'configuration' CHECK (origin = 'configuration'),
  CHECK (status <> 'validated' OR (validated_by IS NOT NULL AND validated_at IS NOT NULL)),
  CHECK (effective_to IS NULL OR effective_to > effective_from),
  UNIQUE NULLS NOT DISTINCT (farm_id, key, stage, effective_from)
);

CREATE TABLE irrigation_plan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  plan_date date NOT NULL,
  mode text NOT NULL CHECK (mode IN ('simulation','advisory','controlled')),
  status text NOT NULL CHECK (status IN ('proposed','awaiting_approval','approved','executing','completed',
                                         'completed_advisory','rejected','failed_verification','blocked')),
  available_m3 numeric(12,3) CHECK (available_m3 IS NULL OR available_m3 >= 0),
  demand_m3 numeric(12,3) CHECK (demand_m3 IS NULL OR demand_m3 >= 0),
  shortage_m3 numeric(12,3) CHECK (shortage_m3 IS NULL OR shortage_m3 >= 0),
  workflow_id text,
  recommendation_id uuid REFERENCES recommendation(id),
  approval_id uuid REFERENCES approval(id),
  assumptions jsonb NOT NULL DEFAULT '[]',
  is_simulated boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT is_simulated OR mode = 'simulation'),                       -- simulated data can't drive real modes
  CHECK (status NOT IN ('approved','executing','completed') OR approval_id IS NOT NULL)
);

CREATE TABLE irrigation_plan_item (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES irrigation_plan(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  valve_equipment_id uuid REFERENCES equipment(id),
  sequence smallint NOT NULL CHECK (sequence > 0),
  planned_volume_m3 numeric(12,3) NOT NULL CHECK (planned_volume_m3 >= 0),
  planned_minutes numeric(8,2) NOT NULL CHECK (planned_minutes > 0),
  expected_flow_m3h numeric(10,3) NOT NULL CHECK (expected_flow_m3h > 0),
  UNIQUE (plan_id, block_id),
  UNIQUE (plan_id, sequence)
);

CREATE TABLE device_command (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key text NOT NULL UNIQUE,
  equipment_id uuid NOT NULL REFERENCES equipment(id),
  action text NOT NULL CHECK (action IN ('run_valve_timed','close_valve')),
  params jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL CHECK (status IN ('pending','sent','verified','failed_verification','rejected_by_hardware',
                                         'advisory_recorded','denied','expired')),
  requested_by_agent text NOT NULL,
  approval_id uuid REFERENCES approval(id),
  plan_item_id uuid REFERENCES irrigation_plan_item(id),
  workflow_id text,
  expires_at timestamptz NOT NULL,
  issued_at timestamptz,
  is_simulated boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  -- only the safe-state action may run without a human approval
  CHECK (action = 'close_valve' OR approval_id IS NOT NULL OR status IN ('denied','expired'))
);

CREATE FUNCTION device_command_require_commissioned() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT NEW.is_simulated AND NEW.status IN ('sent','verified','failed_verification')
     AND NOT EXISTS (SELECT 1 FROM equipment e WHERE e.id = NEW.equipment_id AND e.commissioned) THEN
    RAISE EXCEPTION 'real device command rejected: equipment % is not commissioned', NEW.equipment_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER device_command_commissioning BEFORE INSERT OR UPDATE ON device_command
  FOR EACH ROW EXECUTE FUNCTION device_command_require_commissioned();

CREATE TABLE device_execution_result (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  command_id uuid NOT NULL REFERENCES device_command(id),
  verified boolean NOT NULL,
  measured jsonb,
  detail jsonb NOT NULL DEFAULT '{}',
  finished_at timestamptz NOT NULL DEFAULT now(),
  origin data_origin_t NOT NULL DEFAULT 'derived_measurement',
  is_simulated boolean NOT NULL,
  CHECK (NOT verified OR measured IS NOT NULL)            -- "verified" requires telemetry evidence
);

CREATE TABLE irrigation_event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  valve_equipment_id uuid REFERENCES equipment(id),
  plan_item_id uuid REFERENCES irrigation_plan_item(id),
  command_id uuid REFERENCES device_command(id),
  started_at timestamptz NOT NULL,
  ended_at timestamptz,
  delivered_m3 numeric(12,3) CHECK (delivered_m3 IS NULL OR delivered_m3 >= 0),
  verification_status text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','verified','failed')),
  origin data_origin_t NOT NULL DEFAULT 'derived_measurement',
  is_simulated boolean NOT NULL,
  CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE irrigation_anomaly_case (
  workflow_id text PRIMARY KEY,
  farm_id uuid NOT NULL REFERENCES farm(id),
  valve_equipment_id uuid REFERENCES equipment(id),
  predicted text NOT NULL,
  confidence confidence_t NOT NULL,
  detected_at timestamptz NOT NULL DEFAULT now(),
  confirmed text CHECK (confirmed IN ('leak_confirmed','sensor_fault_confirmed','equipment_fault_confirmed','false_alarm')),
  confirmed_by uuid REFERENCES app_user(id), confirmed_at timestamptz, note text,
  is_simulated boolean NOT NULL,
  CHECK ((confirmed IS NULL) = (confirmed_by IS NULL))     -- a confirmation always names a human
);

CREATE TABLE fertigation_plan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  inventory_item_id uuid NOT NULL REFERENCES inventory_item(id),
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','awaiting_approval','approved','applied','rejected','cancelled')),
  dose_value numeric(12,4) NOT NULL CHECK (dose_value > 0),
  dose_unit text NOT NULL,
  method text,
  planned_for date,
  lab_sample_id uuid REFERENCES soil_water_sample(id),
  sop_document_id uuid REFERENCES document(id),
  recommendation_id uuid REFERENCES recommendation(id),
  approval_id uuid REFERENCES approval(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status NOT IN ('approved','applied') OR approval_id IS NOT NULL)   -- no dosing without authorization
);

CREATE TABLE fertilizer_application_record (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES fertigation_plan(id),
  applied_at timestamptz NOT NULL,
  applied_by uuid NOT NULL REFERENCES employee(id),
  quantity numeric(14,3) NOT NULL CHECK (quantity > 0),
  unit text NOT NULL,
  stock_movement_id uuid REFERENCES stock_movement(id),
  evidence_media_id uuid REFERENCES media_object(id),
  origin data_origin_t NOT NULL DEFAULT 'human_confirmed'
);

CREATE TABLE pest_disease_observation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  palm_id uuid REFERENCES palm(id),
  observed_at timestamptz NOT NULL,
  observer_employee_id uuid REFERENCES employee(id),
  description text NOT NULL,
  suspected_agent text,
  ai_confidence confidence_t,                              -- model output, never a diagnosis
  ai_recommendation_id uuid REFERENCES recommendation(id),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','inspection_requested','confirmed','dismissed','resolved')),
  confirmed_diagnosis text,
  confirmed_by uuid REFERENCES app_user(id), confirmed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'confirmed' OR (confirmed_diagnosis IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))
);
CREATE TABLE pest_observation_media (
  observation_id uuid NOT NULL REFERENCES pest_disease_observation(id),
  media_id uuid NOT NULL REFERENCES media_object(id),
  PRIMARY KEY (observation_id, media_id)
);

CREATE TABLE agronomic_operation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  op_type text NOT NULL CHECK (op_type IN ('pruning','pollination','thinning','bagging','other')),
  status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','done','cancelled')),
  planned_for date,
  completed_at timestamptz,
  sop_document_id uuid REFERENCES document(id),
  work_order_id uuid REFERENCES work_order(id),
  CHECK (status <> 'done' OR completed_at IS NOT NULL)
);

CREATE TABLE harvest_forecast (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  season smallint NOT NULL CHECK (season BETWEEN 2000 AND 2100),
  forecast_kg numeric(14,2) NOT NULL CHECK (forecast_kg >= 0),
  p10_kg numeric(14,2), p90_kg numeric(14,2),
  model_version text NOT NULL,
  method text,
  assumptions jsonb NOT NULL DEFAULT '[]',
  origin data_origin_t NOT NULL DEFAULT 'model_prediction' CHECK (origin = 'model_prediction'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((p10_kg IS NULL OR p10_kg <= forecast_kg) AND (p90_kg IS NULL OR p90_kg >= forecast_kg))
);

CREATE TABLE harvest_actual (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  block_id uuid NOT NULL REFERENCES farm_block(id),
  harvested_on date NOT NULL,
  quantity_kg numeric(14,2) NOT NULL CHECK (quantity_kg > 0),
  recorded_by uuid REFERENCES employee(id),
  origin data_origin_t NOT NULL DEFAULT 'human_confirmed',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE product_batch (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  batch_code text NOT NULL,
  harvest_actual_id uuid NOT NULL REFERENCES harvest_actual(id),   -- traceability: batch -> harvest -> block
  block_id uuid NOT NULL REFERENCES farm_block(id),
  harvested_on date NOT NULL,
  grade text,
  quantity_kg numeric(14,2) NOT NULL CHECK (quantity_kg > 0),
  status text NOT NULL DEFAULT 'in_storage' CHECK (status IN ('in_storage','packed','shipped','quarantine','written_off')),
  storage_location text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (farm_id, batch_code)
);

CREATE TABLE quality_inspection (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id uuid NOT NULL REFERENCES product_batch(id),
  inspected_at timestamptz NOT NULL,
  inspector_employee_id uuid REFERENCES employee(id),
  grade text,
  defects jsonb NOT NULL DEFAULT '{}',
  moisture_pct numeric(5,2) CHECK (moisture_pct IS NULL OR moisture_pct BETWEEN 0 AND 100),
  passed boolean NOT NULL,
  media_id uuid REFERENCES media_object(id)
);

CREATE TABLE batch_loss (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id uuid NOT NULL REFERENCES product_batch(id),
  quantity_kg numeric(14,2) NOT NULL CHECK (quantity_kg > 0),
  reason text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
