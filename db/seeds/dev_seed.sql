-- DEV ONLY. Illustrative 40-feddan layout (4 x 10 feddan). Numbers are PLACEHOLDERS, not farm facts.
INSERT INTO farm(code, name, location_name) VALUES ('MAEEN-WM','MAeen','West Minya, Egypt') ON CONFLICT DO NOTHING;
INSERT INTO farm_block(farm_id, code, area_feddan, palms_per_feddan, growth_stage, irrigation_priority, min_irrigation_fraction)
SELECT f.id, v.code, 10, 50, v.stage, v.prio, v.minf
FROM farm f, (VALUES ('B1','mature',1,0.60), ('B2','mature',2,0.60), ('B3','juvenile',3,0.55), ('B4','young',4,0.50)) AS v(code,stage,prio,minf)
WHERE f.code = 'MAEEN-WM' ON CONFLICT DO NOTHING;
INSERT INTO agronomic_parameter(farm_id, key, stage, value, unit, source)
SELECT f.id, 'kc', v.stage, v.val, 'dimensionless', 'illustrative placeholder - NOT validated by an agronomist'
FROM farm f, (VALUES ('mature',0.95), ('juvenile',0.70), ('young',0.45)) AS v(stage,val)
WHERE f.code = 'MAEEN-WM' ON CONFLICT DO NOTHING;
