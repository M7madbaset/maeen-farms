CREATE TABLE media_object (                  -- pointer to object storage (images, PDFs); bytes live in MinIO/S3
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bucket text NOT NULL,
  object_key text NOT NULL,
  sha256 char(64) NOT NULL,
  content_type text NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  uploaded_by uuid REFERENCES app_user(id),
  uploaded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bucket, object_key)
);

CREATE TABLE document (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  kind text NOT NULL CHECK (kind IN ('sop','lab_report','manual','policy','incident_report','contingency_plan','other')),
  title text NOT NULL,
  language text NOT NULL DEFAULT 'ar' CHECK (language IN ('ar','en','mixed')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Only 'approved' versions are authoritative farm policy; drafts/generated text never are.
CREATE TABLE document_version (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES document(id),
  version integer NOT NULL CHECK (version > 0),
  media_id uuid REFERENCES media_object(id),
  body_text text,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','in_review','approved','retired')),
  authored_by uuid REFERENCES app_user(id),
  approved_by uuid REFERENCES app_user(id),
  approved_at timestamptz,
  sha256 char(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, version),
  CHECK (media_id IS NOT NULL OR body_text IS NOT NULL),
  CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CHECK (approved_by IS NULL OR approved_by IS DISTINCT FROM authored_by)   -- author cannot approve own SOP
);

CREATE TABLE sop (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  code text NOT NULL,
  title text NOT NULL,
  document_id uuid NOT NULL UNIQUE REFERENCES document(id),
  UNIQUE (farm_id, code)
);

CREATE TABLE agent_execution (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id text NOT NULL,
  agent_version text NOT NULL,
  correlation_id text NOT NULL,
  workflow_id text,
  status text NOT NULL CHECK (status IN ('running','ok','blocked','failed','timeout')),
  input jsonb NOT NULL DEFAULT '{}',
  output jsonb,
  model_id text,                              -- NULL for purely deterministic agents
  prompt_version text,
  config_version text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  is_simulated boolean NOT NULL,
  CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX agent_execution_corr_ix ON agent_execution (correlation_id);

CREATE TABLE recommendation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  agent_execution_id uuid NOT NULL REFERENCES agent_execution(id),
  kind text NOT NULL,
  summary text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '[]',
  confidence confidence_t NOT NULL,
  risk_category risk_category_t NOT NULL,
  requires_approval boolean NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN
    ('proposed','approved','rejected','executed','expired','failed_verification','blocked')),
  outcome jsonb,
  origin data_origin_t NOT NULL DEFAULT 'ai_recommendation' CHECK (origin = 'ai_recommendation'),
  is_simulated boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (risk_category <> 'prohibited' OR status IN ('blocked','rejected')),
  CHECK (NOT (risk_category = 'approval_required') OR requires_approval)
);
CREATE TRIGGER recommendation_updated BEFORE UPDATE ON recommendation FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE approval (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recommendation_id uuid REFERENCES recommendation(id),
  workflow_id text,
  action text NOT NULL,
  requested_by_agent text NOT NULL,
  approver_user_id uuid NOT NULL REFERENCES app_user(id),   -- humans only: agents cannot approve
  decision text NOT NULL CHECK (decision IN ('approved','rejected')),
  decided_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  command_fingerprints text[] NOT NULL DEFAULT '{}',        -- approval bound to exact commands
  note text,
  CHECK (expires_at > decided_at)
);

CREATE TABLE authorization_policy (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version integer NOT NULL UNIQUE CHECK (version > 0),
  document jsonb NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
  created_by uuid NOT NULL REFERENCES app_user(id),
  activated_by uuid REFERENCES app_user(id),
  activated_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (activated_by IS DISTINCT FROM created_by),          -- separation of duties
  CHECK (status <> 'active' OR (activated_by IS NOT NULL AND activated_at IS NOT NULL))
);
CREATE UNIQUE INDEX authorization_policy_one_active ON authorization_policy ((true)) WHERE status = 'active';

-- ---------------------------------------------------------------------------------------------
-- Tamper-evident, append-only audit log, hash-chained inside the database.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE audit_event (
  id bigint PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor text NOT NULL,
  event_type text NOT NULL,
  correlation_id text,
  payload jsonb NOT NULL DEFAULT '{}',
  is_simulated boolean NOT NULL DEFAULT false,
  prev_hash char(64) NOT NULL,
  hash char(64) NOT NULL UNIQUE
);
CREATE SEQUENCE audit_event_id_seq OWNED BY audit_event.id;
CREATE INDEX audit_event_corr_ix ON audit_event (correlation_id);

CREATE FUNCTION audit_row_hash(prev text, t timestamptz, a text, e text, c text, p jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(digest(prev || to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || a || e
                       || COALESCE(c, '') || p::text, 'sha256'), 'hex')
$$;

CREATE FUNCTION audit_event_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last_hash text;
BEGIN
  PERFORM pg_advisory_xact_lock(727001);                     -- serialise: id order == chain order
  NEW.id := nextval('audit_event_id_seq');
  SELECT hash INTO last_hash FROM audit_event ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := COALESCE(last_hash, repeat('0', 64));
  NEW.hash := audit_row_hash(NEW.prev_hash, NEW.ts, NEW.actor, NEW.event_type, NEW.correlation_id, NEW.payload);
  RETURN NEW;
END $$;
CREATE TRIGGER audit_event_chain BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION audit_event_before_insert();

CREATE FUNCTION audit_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_event is append-only (% blocked)', TG_OP USING ERRCODE = 'integrity_constraint_violation'; END $$;
CREATE TRIGGER audit_event_no_change BEFORE UPDATE OR DELETE ON audit_event FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event FOR EACH STATEMENT EXECUTE FUNCTION audit_event_immutable();

-- Returns NULL if the chain is intact, otherwise the id of the first bad row.
CREATE FUNCTION verify_audit_chain() RETURNS bigint LANGUAGE plpgsql STABLE AS $$
DECLARE r record; expect_prev text := repeat('0', 64);
BEGIN
  FOR r IN SELECT * FROM audit_event ORDER BY id LOOP
    IF r.prev_hash <> expect_prev
       OR r.hash <> audit_row_hash(r.prev_hash, r.ts, r.actor, r.event_type, r.correlation_id, r.payload) THEN
      RETURN r.id;
    END IF;
    expect_prev := r.hash;
  END LOOP;
  RETURN NULL;
END $$;

CREATE TABLE notification (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid REFERENCES farm(id),
  user_id uuid REFERENCES app_user(id),
  audience_role text,
  severity text NOT NULL CHECK (severity IN ('info','high','critical')),
  locale text NOT NULL CHECK (locale IN ('ar','en')),
  title text,
  body text NOT NULL,
  channel text NOT NULL DEFAULT 'in_app' CHECK (channel IN ('in_app','sms','whatsapp','email','push')),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','acknowledged')),
  workflow_id text,
  is_simulated boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  CHECK (user_id IS NOT NULL OR audience_role IS NOT NULL)
);
