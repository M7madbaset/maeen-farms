"""Live PostgreSQL/PostGIS tests. Skipped unless MAEEN_TEST_PG_ADMIN_DSN is set, e.g.
   MAEEN_TEST_PG_ADMIN_DSN="host=localhost port=5432 user=postgres password=... dbname=postgres"
A throw-away database is created, migrated, and dropped for the test session."""
import os, threading, uuid
import pytest

psycopg = pytest.importorskip("psycopg")
ADMIN = os.environ.get("MAEEN_TEST_PG_ADMIN_DSN")
pytestmark = pytest.mark.skipif(not ADMIN, reason="MAEEN_TEST_PG_ADMIN_DSN not set")

from maeen.pgmigrate import migrate, migration_files, MigrationError  # noqa: E402
from psycopg import errors as E  # noqa: E402


@pytest.fixture(scope="session")
def dsn():
    name = "maeen_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    test_dsn = ADMIN.replace("dbname=postgres", f"dbname={name}")
    migrate(test_dsn, seed=True)
    yield test_dsn
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def conn(dsn):
    c = psycopg.connect(dsn)
    yield c
    c.rollback(); c.close()


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def violates(conn, sql, params=(), kind=E.IntegrityError):
    with pytest.raises(kind):
        with conn.transaction():
            conn.execute(sql, params)


def mk_farm(conn):
    return one(conn, "INSERT INTO farm(code,name) VALUES (%s,'t') RETURNING id", ("F" + uuid.uuid4().hex[:8],))
def mk_user(conn):
    return one(conn, "INSERT INTO app_user(username) VALUES (%s) RETURNING id", ("u" + uuid.uuid4().hex[:8],))
def mk_block(conn, farm, code="T1"):
    return one(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,growth_stage,irrigation_priority) "
                     "VALUES (%s,%s,10,'mature',1) RETURNING id", (farm, code))
def mk_equip(conn, farm, kind="valve", commissioned=False):
    return one(conn, "INSERT INTO equipment(farm_id,code,kind,commissioned,commissioned_at) VALUES "
                     "(%s,%s,%s,%s,CASE WHEN %s THEN now() END) RETURNING id",
               (farm, "E" + uuid.uuid4().hex[:8], kind, commissioned, commissioned))
def mk_exec(conn):
    return one(conn, "INSERT INTO agent_execution(agent_id,agent_version,correlation_id,status,is_simulated) "
                     "VALUES ('a','1','c','ok',true) RETURNING id")
def mk_approval(conn, user):
    return one(conn, "INSERT INTO approval(action,requested_by_agent,approver_user_id,decision,expires_at) "
                     "VALUES ('request_valve_command','irrigation_water',%s,'approved',now()+interval '1 hour') RETURNING id", (user,))


# --- migrations -------------------------------------------------------------------------------------
def test_migrations_idempotent_and_checksummed(dsn, tmp_path):
    assert migrate(dsn) == []                                    # already applied by the fixture
    with psycopg.connect(dsn) as c:
        assert one(c, "SELECT count(*) FROM schema_migration") == len(migration_files())
    d = tmp_path / "m"; d.mkdir()
    for f in migration_files(): (d / f.name).write_text(f.read_text())
    (d / "001_extensions_domains.sql").write_text("-- tampered\n")
    with pytest.raises(MigrationError, match="checksum"):
        migrate(dsn, directory=d)


REQUIRED = """farm farm_block palm palm_lifecycle_record irrigation_zone soil_water_sample sensor sensor_reading
weather_observation weather_forecast irrigation_plan irrigation_event fertigation_plan fertilizer_application_record
pest_disease_observation agronomic_operation harvest_forecast harvest_actual product_batch quality_inspection employee role
work_order task equipment maintenance_event inventory_item stock_movement supplier purchase_request budget
cost_transaction customer_order shipment risk incident sop document_version agent_execution recommendation approval
authorization_policy device_command device_execution_result audit_event notification""".split()


def test_all_required_entities_exist(conn):
    have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert set(REQUIRED) <= have, set(REQUIRED) - have


def test_every_table_has_primary_key(conn):
    rows = conn.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition
          AND c.relname NOT IN ('spatial_ref_sys')
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid AND i.indisprimary)""").fetchall()
    assert rows == []


def test_provenance_columns_on_observation_like_tables(conn):
    for t in ["sensor_reading", "weather_observation", "weather_forecast", "soil_water_sample", "agent_execution",
              "recommendation", "irrigation_plan", "device_command", "device_execution_result", "irrigation_event",
              "notification", "irrigation_anomaly_case"]:
        r = conn.execute("SELECT is_nullable FROM information_schema.columns WHERE table_name=%s AND column_name='is_simulated'", (t,)).fetchone()
        assert r and r[0] == "NO", f"{t}.is_simulated must exist and be NOT NULL"
    for t in ["sensor_reading", "weather_observation", "soil_water_sample", "recommendation", "harvest_forecast"]:
        assert one(conn, "SELECT count(*) FROM information_schema.columns WHERE table_name=%s AND column_name='origin'", (t,)) == 1


def test_every_geometry_column_has_gist_index(conn):
    cols = conn.execute("SELECT f_table_name, f_geometry_column FROM geometry_columns WHERE f_table_schema='public'").fetchall()
    assert len(cols) == 6          # farm, farm_block, palm, equipment, irrigation_zone, sensor
    for t, col in cols:
        n = one(conn, "SELECT count(*) FROM pg_indexes WHERE tablename=%s AND indexdef ILIKE %s", (t, f"%gist%({col})%"))
        assert n >= 1, f"{t}.{col} lacks a GiST index"


# --- seed / reference ----------------------------------------------------------------------------------
def test_seed_matches_planning_assumptions(conn):
    rows = conn.execute("SELECT code, area_feddan, palms_planned FROM v_block_summary ORDER BY code").fetchall()
    assert [(r[0], float(r[1]), r[2]) for r in rows[:4]] == [("B1", 10, 500), ("B2", 10, 500), ("B3", 10, 500), ("B4", 10, 500)]
    assert one(conn, "SELECT count(*) FROM agronomic_parameter WHERE status='validated'") == 0
    assert one(conn, "SELECT count(*) FROM role") == 9


# --- integrity rules (negative tests) -------------------------------------------------------------------
def test_block_and_value_constraints(conn):
    f = mk_farm(conn)
    violates(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,growth_stage,irrigation_priority) VALUES (%s,'X',0,'mature',1)", (f,))
    violates(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,palms_per_feddan,growth_stage,irrigation_priority) VALUES (%s,'X',5,0,'mature',1)", (f,))
    violates(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,growth_stage,irrigation_priority) VALUES (%s,'X',5,'martian',1)", (f,))
    b = mk_block(conn, f)
    violates(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,growth_stage,irrigation_priority) VALUES (%s,'T1',5,'mature',1)", (f,))  # unique
    assert b


def test_sensor_readings_never_invent_missing_values(conn):
    f = mk_farm(conn)
    s = one(conn, "INSERT INTO sensor(farm_id,code,measurand,unit) VALUES (%s,'S1','flow','m3/h') RETURNING id", (f,))
    violates(conn, "INSERT INTO sensor_reading(sensor_id,ts,value,unit,quality,is_simulated) VALUES (%s,now(),NULL,'m3/h','good',true)", (s,))
    conn.execute("INSERT INTO sensor_reading(sensor_id,ts,value,unit,quality,is_simulated) VALUES (%s,now(),NULL,'m3/h','missing',true)", (s,))
    violates(conn, "INSERT INTO sensor_reading(sensor_id,ts,value,unit,quality,is_simulated) VALUES (%s,now(),1,'m3/h','amazing',true)", (s,), E.CheckViolation)
    violates(conn, "INSERT INTO sensor_reading(sensor_id,ts,value,unit) VALUES (%s,now(),1,'m3/h')", (s,))      # is_simulated required


def test_sensor_reading_partitions_and_primary_key(conn):
    f = mk_farm(conn)
    s = one(conn, "INSERT INTO sensor(farm_id,code,measurand,unit) VALUES (%s,'S2','flow','m3/h') RETURNING id", (f,))
    name = one(conn, "SELECT ensure_sensor_reading_partition('2031-03-15')")
    assert name == "sensor_reading_2031_03"
    conn.execute("INSERT INTO sensor_reading(sensor_id,ts,value,unit,is_simulated) VALUES (%s,'2031-03-20 10:00+00',5,'m3/h',true)", (s,))
    conn.execute("INSERT INTO sensor_reading(sensor_id,ts,value,unit,is_simulated) VALUES (%s,'2035-01-01 00:00+00',6,'m3/h',true)", (s,))
    parts = {r[0] for r in conn.execute("SELECT tableoid::regclass::text FROM sensor_reading WHERE sensor_id=%s", (s,))}
    assert parts == {"sensor_reading_2031_03", "sensor_reading_default"}
    violates(conn, "INSERT INTO sensor_reading(sensor_id,ts,value,unit,is_simulated) VALUES (%s,'2031-03-20 10:00+00',7,'m3/h',true)", (s,))


def test_recommendation_is_always_labelled_ai_and_prohibited_never_proceeds(conn):
    f, ex = mk_farm(conn), mk_exec(conn)
    q = ("INSERT INTO recommendation(farm_id,agent_execution_id,kind,summary,confidence,risk_category,"
         "requires_approval,is_simulated,origin,status) VALUES (%s,%s,'k','s',%s,%s,%s,true,%s,%s)")
    ok = (f, ex, 0.5, "autonomous", False, "ai_recommendation", "proposed")
    conn.execute(q, ok)
    violates(conn, q, (f, ex, 0.5, "autonomous", False, "human_confirmed", "proposed"))      # AI output can't pose as fact
    violates(conn, q, (f, ex, 1.5, "autonomous", False, "ai_recommendation", "proposed"), E.CheckViolation)
    violates(conn, q, (f, ex, 0.5, "prohibited", False, "ai_recommendation", "proposed"))    # prohibited can only be blocked/rejected
    violates(conn, q, (f, ex, 0.5, "approval_required", False, "ai_recommendation", "proposed"))
    conn.execute(q, (f, ex, 0.5, "prohibited", False, "ai_recommendation", "blocked"))


def test_purchase_segregation_of_duties_and_agents_cannot_commit(conn):
    f, u1, u2, u3 = mk_farm(conn), mk_user(conn), mk_user(conn), mk_user(conn)
    ins = ("INSERT INTO purchase_request(farm_id,status,requested_by_agent,requested_by_user,approved_by,approved_at,committed_by,committed_at) "
           "VALUES (%s,%s,%s,%s,%s,{a},%s,{c})")
    violates(conn, ins.format(a="now()", c="NULL"), (f, "approved", None, u1, u1, None))                                           # approver == requester
    violates(conn, ins.format(a="now()", c="now()"), (f, "committed", None, u1, u2, u2))                                          # committer == approver
    violates(conn, ins.format(a="NULL", c="NULL"), (f, "approved", "procurement", None, None, None))                              # approved w/o approver
    violates(conn, ins.format(a="NULL", c="NULL"), (f, "draft", None, None, None, None))                                           # no requester at all
    conn.execute(ins.format(a="now()", c="now()"), (f, "committed", "procurement", None, u2, u3))                                  # agent requests, 2 humans decide


def test_policy_changes_need_second_person_and_single_active(conn):
    u1, u2 = mk_user(conn), mk_user(conn)
    nv = one(conn, "SELECT coalesce(max(version),0)+1 FROM authorization_policy")
    violates(conn, "INSERT INTO authorization_policy(version,document,status,created_by,activated_by,activated_at) VALUES (%s,'{}','active',%s,%s,now())", (nv, u1, u1))
    conn.execute("INSERT INTO authorization_policy(version,document,status,created_by,activated_by,activated_at) VALUES (%s,'{}','active',%s,%s,now())", (nv, u1, u2))
    violates(conn, "INSERT INTO authorization_policy(version,document,status,created_by,activated_by,activated_at) VALUES (%s,'{}','active',%s,%s,now())", (nv + 1, u1, u2))


def test_device_commands_need_approval_idempotency_and_commissioning(conn):
    f, u = mk_farm(conn), mk_user(conn)
    sim_valve, real_valve, ok_valve = mk_equip(conn, f), mk_equip(conn, f), mk_equip(conn, f, commissioned=True)
    ap = mk_approval(conn, u)
    q = ("INSERT INTO device_command(idempotency_key,equipment_id,action,status,requested_by_agent,approval_id,expires_at,is_simulated) "
         "VALUES (%s,%s,%s,%s,'irrigation_water',%s,now()+interval '15 min',%s)")
    violates(conn, q, ("k1", sim_valve, "run_valve_timed", "sent", None, True))                      # no approval
    conn.execute(q, ("k2", sim_valve, "close_valve", "verified", None, True))                        # safe-state needs none
    conn.execute(q, ("k3", sim_valve, "run_valve_timed", "denied", None, True))                      # denial is recordable
    violates(conn, q, ("k3", sim_valve, "close_valve", "sent", None, True))                          # duplicate idempotency key
    violates(conn, q, ("k4", real_valve, "run_valve_timed", "sent", ap, False))                      # real + not commissioned
    conn.execute(q, ("k5", ok_valve, "run_valve_timed", "sent", ap, False))                          # real + commissioned
    conn.execute(q, ("k6", sim_valve, "run_valve_timed", "verified", ap, True))


def test_other_authorization_and_evidence_rules(conn):
    f, u, ex = mk_farm(conn), mk_user(conn), mk_exec(conn)
    b = mk_block(conn, f)
    item = one(conn, "INSERT INTO inventory_item(farm_id,sku,name_en,category,unit) VALUES (%s,'N1','Urea','fertilizer','kg') RETURNING id", (f,))
    violates(conn, "INSERT INTO approval(action,requested_by_agent,approver_user_id,decision,expires_at,decided_at) VALUES ('a','x',%s,'approved',now()-interval '1 h',now())", (u,))
    violates(conn, "INSERT INTO fertigation_plan(farm_id,block_id,inventory_item_id,status,dose_value,dose_unit) VALUES (%s,%s,%s,'approved',5,'kg')", (f, b, item))
    violates(conn, "INSERT INTO fertigation_plan(farm_id,block_id,inventory_item_id,dose_value,dose_unit) VALUES (%s,%s,%s,0,'kg')", (f, b, item))
    violates(conn, "INSERT INTO irrigation_plan(farm_id,plan_date,mode,status,is_simulated) VALUES (%s,current_date,'controlled','proposed',true)", (f,))
    violates(conn, "INSERT INTO pest_disease_observation(farm_id,block_id,observed_at,description,status) VALUES (%s,%s,now(),'x','confirmed')", (f, b))
    conn.execute("INSERT INTO pest_disease_observation(farm_id,block_id,observed_at,description,ai_confidence) VALUES (%s,%s,now(),'x',0.62)", (f, b))
    violates(conn, "INSERT INTO stock_movement(item_id,movement_type,quantity,unit) VALUES (%s,'issue',5,'kg')", (item,))
    violates(conn, "INSERT INTO stock_movement(item_id,movement_type,quantity,unit) VALUES (%s,'receipt',-5,'kg')", (item,))
    violates(conn, "INSERT INTO cost_transaction(farm_id,txn_date,kind,category,amount_egp,status) VALUES (%s,current_date,'opex','x',10,'posted')", (f,))
    violates(conn, "INSERT INTO cost_transaction(farm_id,txn_date,kind,category,amount_egp,status) VALUES (%s,current_date,'opex','x',10,'maybe')", (f,))
    cust = one(conn, "INSERT INTO customer(farm_id,name) VALUES (%s,'c') RETURNING id", (f,))
    violates(conn, "INSERT INTO customer_order(farm_id,customer_id,status) VALUES (%s,%s,'confirmed')", (f, cust))
    violates(conn, "INSERT INTO equipment(farm_id,code,kind,commissioned) VALUES (%s,'Z','valve',true)", (f,))
    d = one(conn, "INSERT INTO document(farm_id,kind,title) VALUES (%s,'sop','t') RETURNING id", (f,))
    violates(conn, "INSERT INTO document_version(document_id,version,body_text,status) VALUES (%s,1,'t','approved')", (d,))   # approved w/o approver
    assert one(conn, "INSERT INTO risk(farm_id,category,description,likelihood,impact) VALUES (%s,'water','d',4,5) RETURNING score", (f,)) == 20


def test_sop_author_cannot_approve_own_version(conn):
    f, u1, u2 = mk_farm(conn), mk_user(conn), mk_user(conn)
    d = one(conn, "INSERT INTO document(farm_id,kind,title) VALUES (%s,'sop','t') RETURNING id", (f,))
    q = "INSERT INTO document_version(document_id,version,body_text,status,authored_by,approved_by,approved_at) VALUES (%s,%s,'x','approved',%s,%s,now())"
    violates(conn, q, (d, 1, u1, u1))
    conn.execute(q, (d, 1, u1, u2))


def test_verified_result_requires_measurements(conn):
    f, u = mk_farm(conn), mk_user(conn)
    v = mk_equip(conn, f); ap = mk_approval(conn, u)
    cmd = one(conn, "INSERT INTO device_command(idempotency_key,equipment_id,action,status,requested_by_agent,approval_id,expires_at,is_simulated) "
                    "VALUES (%s,%s,'run_valve_timed','verified','a',%s,now()+interval '1 h',true) RETURNING id", ("r" + uuid.uuid4().hex, v, ap))
    violates(conn, "INSERT INTO device_execution_result(command_id,verified,is_simulated) VALUES (%s,true,true)", (cmd,))
    conn.execute("INSERT INTO device_execution_result(command_id,verified,measured,is_simulated) VALUES (%s,true,'{\"delivered_m3\":12.5}',true)", (cmd,))


# --- spatial -------------------------------------------------------------------------------------------
def test_postgis_palm_in_block_queries(conn):
    f = mk_farm(conn)
    b = one(conn, "INSERT INTO farm_block(farm_id,code,area_feddan,growth_stage,irrigation_priority,geom) VALUES "
                  "(%s,'G1',10,'mature',1,ST_GeomFromText('POLYGON((30.7 28.1,30.71 28.1,30.71 28.11,30.7 28.11,30.7 28.1))',4326)) RETURNING id", (f,))
    for i, (x, y) in enumerate([(30.705, 28.105), (30.702, 28.102), (31.5, 29.0)]):
        conn.execute("INSERT INTO palm(farm_id,block_id,tag_code,geom) VALUES (%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))", (f, b, f"P{i}", x, y))
    inside = one(conn, "SELECT count(*) FROM palm p JOIN farm_block fb ON ST_Within(p.geom, fb.geom) WHERE fb.id=%s", (b,))
    assert inside == 2                                       # the third palm is mis-registered, query reveals it
    assert one(conn, "SELECT ST_Area(geom::geography) > 1e6 FROM farm_block WHERE id=%s", (b,))


# --- views, text, numerics -------------------------------------------------------------------------------
def test_views_and_arabic_text_and_exact_money(conn):
    f = mk_farm(conn)
    item = one(conn, "INSERT INTO inventory_item(farm_id,sku,name_en,name_ar,category,unit,reorder_threshold) VALUES (%s,'K1','Potash','سماد البوتاس','fertilizer','kg',100) RETURNING id", (f,))
    conn.execute("INSERT INTO stock_movement(item_id,movement_type,quantity,unit) VALUES (%s,'receipt',150,'kg'),(%s,'issue',-80,'kg')", (item, item))
    on_hand, below = conn.execute("SELECT on_hand, below_threshold FROM v_stock_level WHERE item_id=%s", (item,)).fetchone()
    assert float(on_hand) == 70 and below is True
    assert one(conn, "SELECT name_ar FROM inventory_item WHERE id=%s", (item,)) == "سماد البوتاس"
    for st, amt in [("estimate", "100.10"), ("forecast", "200.20"), ("commitment", "300.30")]:
        conn.execute("INSERT INTO cost_transaction(farm_id,txn_date,kind,category,amount_egp,status) VALUES (%s,current_date,'opex','fert',%s,%s)", (f, amt, st))
    rows = dict(conn.execute("SELECT status, total_egp FROM v_cost_by_status WHERE farm_id=%s", (f,)).fetchall())
    assert str(rows["estimate"]) == "100.10" and "posted" not in rows           # nothing is 'posted' unless a human posted it
    conn.execute("INSERT INTO notification(severity,locale,body,audience_role,is_simulated) VALUES ('high','ar','نقص في المياه ٤٥ م³','owner',true)")
    assert "٤٥" in one(conn, "SELECT body FROM notification WHERE locale='ar' ORDER BY created_at DESC LIMIT 1")


# --- audit chain ---------------------------------------------------------------------------------------
def audit(conn, n=3, tag="t"):
    for i in range(n):
        conn.execute("INSERT INTO audit_event(actor,event_type,correlation_id,payload) VALUES (%s,'x','c1',%s::jsonb)", (f"{tag}{i}", '{"i": %d, "ar": "نقص"}' % i))


def test_audit_chain_links_and_verifies(conn):
    audit(conn)
    rows = conn.execute("SELECT id, prev_hash, hash FROM audit_event ORDER BY id").fetchall()
    for a, b in zip(rows, rows[1:]): assert b[1] == a[2]
    assert rows[0][1] == "0" * 64 or one(conn, "SELECT count(*) FROM audit_event") > 3
    assert one(conn, "SELECT verify_audit_chain()") is None


def test_audit_hash_is_independent_of_session_timezone(conn):
    conn.execute("SET TIME ZONE 'Asia/Riyadh'"); audit(conn, 2, "riyadh")
    conn.execute("SET TIME ZONE 'America/Los_Angeles'"); audit(conn, 2, "la")
    conn.execute("SET TIME ZONE 'UTC'")
    assert one(conn, "SELECT verify_audit_chain()") is None


def test_audit_is_append_only(conn):
    audit(conn, 2)
    for sql in ["UPDATE audit_event SET actor='evil'", "DELETE FROM audit_event", "TRUNCATE audit_event"]:
        violates(conn, sql)


def test_privileged_tampering_is_detected_by_verify(conn):
    audit(conn, 3, "tamper")
    conn.execute("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_change")          # attacker with DDL rights
    target = one(conn, "SELECT id FROM audit_event WHERE actor='tamper1'")
    conn.execute("UPDATE audit_event SET payload='{\"i\": 999}' WHERE id=%s", (target,))
    assert one(conn, "SELECT verify_audit_chain()") == target


def test_audit_chain_survives_concurrent_writers(dsn):
    def worker(tag):
        with psycopg.connect(dsn, autocommit=True) as c:
            for i in range(15):
                c.execute("INSERT INTO audit_event(actor,event_type,payload) VALUES (%s,'conc','{}')", (f"{tag}{i}",))
    ts = [threading.Thread(target=worker, args=(f"w{k}",)) for k in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    with psycopg.connect(dsn) as c:
        assert one(c, "SELECT verify_audit_chain()") is None
        assert one(c, "SELECT count(*) FROM audit_event WHERE event_type='conc'") == 60
        ids = [r[0] for r in c.execute("SELECT id FROM audit_event ORDER BY id")]
        assert ids == list(range(ids[0], ids[0] + len(ids)))
