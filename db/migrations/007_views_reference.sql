INSERT INTO growth_stage(code, description, sort_order) VALUES
  ('young','Young palms (establishment)',1), ('juvenile','Juvenile / pre-bearing',2), ('mature','Mature bearing palms',3);

INSERT INTO role(code, description) VALUES
  ('owner','Farm owner / general manager'), ('agronomist','Qualified agronomist'),
  ('irrigation_operator','Irrigation operator'), ('field_supervisor','Field supervisor'),
  ('maintenance_technician','Maintenance technician'), ('procurement_officer','Procurement officer'),
  ('finance_officer','Finance officer'), ('auditor','Auditor (read-only)'), ('admin','System administrator');

CREATE VIEW v_block_summary AS
SELECT b.id AS block_id, b.farm_id, b.code, b.area_feddan, b.palms_per_feddan,
       round(b.area_feddan * b.palms_per_feddan)::int AS palms_planned,
       (SELECT count(*) FROM palm p WHERE p.block_id = b.id AND p.status <> 'removed')::int AS palms_registered
FROM farm_block b;

CREATE VIEW v_stock_level AS
SELECT i.id AS item_id, i.farm_id, i.sku, i.unit, i.reorder_threshold,
       COALESCE(sum(m.quantity), 0) AS on_hand,
       COALESCE(sum(m.quantity), 0) <= i.reorder_threshold AS below_threshold
FROM inventory_item i LEFT JOIN stock_movement m ON m.item_id = i.id
GROUP BY i.id;

CREATE VIEW v_cost_by_status AS
SELECT farm_id, kind, category, status, sum(amount_egp) AS total_egp, count(*) AS n
FROM cost_transaction GROUP BY farm_id, kind, category, status;
