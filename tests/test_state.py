import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot.state import Store

# The original v1 leads schema, before easypv_project_id / proposal_confirmed /
# details columns were added.
V1_SCHEMA = """
CREATE TABLE leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL,
    name TEXT, email TEXT, phone TEXT,
    address TEXT, postcode TEXT, message TEXT,
    panels INTEGER, batteries INTEGER, scaffold TEXT,
    roof_faces INTEGER, dc_strings INTEGER, ev_charger INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    quote_total INTEGER, quote_text TEXT,
    easypv_url TEXT, error TEXT,
    raw TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def test_add_lead_stores_details_and_new_columns(tmp_path):
    store = Store(str(tmp_path / "state.db"))
    fields = {"name": "Ross", "address": "12 X St, Newport",
              "postcode": "NP19 8AZ", "property_type": "terraced",
              "epc_rating": "D", "annual_kwh": "4200"}
    lead_id = store.add_lead(fields, raw="raw body")
    row = store.get_lead(lead_id)
    assert row["name"] == "Ross"
    assert row["easypv_project_id"] is None
    assert row["proposal_confirmed"] == 0
    assert '"epc_rating": "D"' in row["details"]
    store.update_lead(lead_id, easypv_project_id="proj123", proposal_confirmed=1)
    assert store.get_lead(lead_id)["easypv_project_id"] == "proj123"


def test_migration_preserves_existing_v1_db(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO leads(name, postcode, status) VALUES(?,?,?)",
                 ("Old Lead", "SA31 1AA", "quoted"))
    conn.execute("INSERT INTO meta(key, value) VALUES(?,?)", ("last_uid", "4242"))
    conn.commit()
    conn.close()

    # Opening with the v2 Store must migrate additively, never reset.
    store = Store(path)
    assert store.get_meta("last_uid") == "4242"          # UID preserved exactly
    row = store.get_lead(1)
    assert row["name"] == "Old Lead"                      # existing row preserved
    assert row["easypv_project_id"] is None               # new column added
    assert row["proposal_confirmed"] == 0
    # And the new columns are writable.
    store.update_lead(1, easypv_project_id="p9", status="proposal_generated")
    assert store.get_lead(1)["easypv_project_id"] == "p9"


def test_last_uid_round_trip(tmp_path):
    path = str(tmp_path / "s.db")
    Store(path).set_meta("last_uid", "100")
    assert Store(path).get_meta("last_uid") == "100"      # survives reopen
