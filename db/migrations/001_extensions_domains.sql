-- MAeen FarmOS schema v1. Forward-only migrations. Requires PostgreSQL >= 15 (NULLS NOT DISTINCT), PostGIS.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Provenance: every observation/derivation/prediction/recommendation declares WHAT KIND of fact it is.
CREATE DOMAIN data_origin_t AS text CHECK (VALUE IN
  ('raw_observation','derived_measurement','model_prediction','human_confirmed','ai_recommendation','configuration'));
CREATE DOMAIN quality_status_t AS text CHECK (VALUE IN ('good','suspect','bad','missing','unvalidated'));
CREATE DOMAIN risk_category_t AS text CHECK (VALUE IN ('autonomous','approval_required','prohibited'));
CREATE DOMAIN confidence_t AS numeric(4,3) CHECK (VALUE >= 0 AND VALUE <= 1);
CREATE DOMAIN egp_t AS numeric(16,2);

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
