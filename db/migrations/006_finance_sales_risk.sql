CREATE TABLE budget (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  fiscal_year smallint NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),
  category text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('capex','opex')),
  amount_egp egp_t NOT NULL CHECK (amount_egp >= 0),
  scenario text NOT NULL DEFAULT 'base',
  UNIQUE (farm_id, fiscal_year, category, kind, scenario)
);

-- status separates what is actually booked from what is merely expected.
CREATE TABLE cost_transaction (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  txn_date date NOT NULL,
  kind text NOT NULL CHECK (kind IN ('capex','opex')),
  category text NOT NULL,
  amount_egp egp_t NOT NULL,
  status text NOT NULL CHECK (status IN ('estimate','forecast','commitment','posted')),
  block_id uuid REFERENCES farm_block(id),
  batch_id uuid REFERENCES product_batch(id),
  purchase_request_id uuid REFERENCES purchase_request(id),
  source_ref text,
  posted_by uuid REFERENCES app_user(id), posted_at timestamptz,
  is_simulated boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'posted' OR (posted_by IS NOT NULL AND posted_at IS NOT NULL))
);
CREATE INDEX cost_transaction_cat_ix ON cost_transaction (farm_id, category, status);

CREATE TABLE customer (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  name text NOT NULL,
  country text,
  contact jsonb NOT NULL DEFAULT '{}',
  UNIQUE (farm_id, name)
);

CREATE TABLE customer_order (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  customer_id uuid NOT NULL REFERENCES customer(id),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_approval','confirmed','partially_shipped','shipped','cancelled')),
  promised_date date,
  total_egp egp_t CHECK (total_egp IS NULL OR total_egp >= 0),
  committed_by uuid REFERENCES app_user(id), committed_at timestamptz,       -- binding commitment = human only
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status NOT IN ('confirmed','partially_shipped','shipped') OR (committed_by IS NOT NULL AND committed_at IS NOT NULL))
);
CREATE TRIGGER customer_order_updated BEFORE UPDATE ON customer_order FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE customer_order_line (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id uuid NOT NULL REFERENCES customer_order(id),
  grade text,
  quantity_kg numeric(14,2) NOT NULL CHECK (quantity_kg > 0),
  unit_price_egp egp_t CHECK (unit_price_egp IS NULL OR unit_price_egp >= 0)
);

CREATE TABLE shipment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id uuid NOT NULL REFERENCES customer_order(id),
  status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_transit','delivered','cancelled')),
  shipped_at timestamptz,
  carrier text, tracking_ref text
);
CREATE TABLE shipment_line (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shipment_id uuid NOT NULL REFERENCES shipment(id),
  batch_id uuid NOT NULL REFERENCES product_batch(id),
  quantity_kg numeric(14,2) NOT NULL CHECK (quantity_kg > 0)
);

CREATE TABLE risk (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  category text NOT NULL CHECK (category IN ('water','heat','equipment','crop_loss','supply','connectivity','safety','financial','other')),
  description text NOT NULL,
  likelihood smallint NOT NULL CHECK (likelihood BETWEEN 1 AND 5),
  impact smallint NOT NULL CHECK (impact BETWEEN 1 AND 5),
  score smallint GENERATED ALWAYS AS (likelihood * impact) STORED,
  owner_user_id uuid REFERENCES app_user(id),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','mitigating','accepted','closed')),
  contingency_document_id uuid REFERENCES document(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE incident (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  farm_id uuid NOT NULL REFERENCES farm(id),
  risk_id uuid REFERENCES risk(id),
  kind text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
  detected_at timestamptz NOT NULL,
  resolved_at timestamptz,
  workflow_id text,
  description text NOT NULL,
  resolution text,
  lessons_document_id uuid REFERENCES document(id),
  CHECK (resolved_at IS NULL OR resolved_at >= detected_at)
);
