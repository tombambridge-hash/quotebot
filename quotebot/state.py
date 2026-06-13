"""SQLite persistence: leads, processed mail UIDs, bot state."""

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL,
    name TEXT, email TEXT, phone TEXT,
    address TEXT, postcode TEXT, message TEXT,
    panels INTEGER, batteries INTEGER, scaffold TEXT,
    roof_faces INTEGER, dc_strings INTEGER, ev_charger INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',          -- new | awaiting_info | project_created |
                                        -- proposal_generated | needs_manual | ignored | error
    quote_total INTEGER, quote_text TEXT,
    easypv_project_id TEXT, easypv_url TEXT,
    proposal_confirmed INTEGER DEFAULT 0,
    details TEXT, error TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""

# Columns added after v1 shipped. CREATE TABLE IF NOT EXISTS won't add columns
# to an existing leads table, so migrate additively — this preserves every
# existing row and the meta/last_uid tracking untouched.
_MIGRATIONS = [
    ("leads", "easypv_project_id", "TEXT"),
    ("leads", "proposal_confirmed", "INTEGER DEFAULT 0"),
    ("leads", "details", "TEXT"),
]


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        for table, column, decl in _MIGRATIONS:
            try:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass  # column already exists

    # ---- meta (e.g. last processed IMAP UID) ----
    def get_meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()

    # ---- leads ----
    def add_lead(self, fields: Dict[str, Any], raw: str) -> int:
        from . import lead_parser
        details = lead_parser.details_blob(fields)
        cur = self.db.execute(
            "INSERT INTO leads(created,name,email,phone,address,postcode,message,"
            "panels,batteries,scaffold,roof_faces,dc_strings,ev_charger,status,"
            "details,raw) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), fields.get("name"), fields.get("email"),
             fields.get("phone"), fields.get("address"), fields.get("postcode"),
             fields.get("message"), fields.get("panels"), fields.get("batteries"),
             fields.get("scaffold"), fields.get("roof_faces"),
             fields.get("dc_strings"), int(bool(fields.get("ev_charger"))),
             "new", json.dumps(details) if details else None, raw))
        self.db.commit()
        return int(cur.lastrowid)

    def get_lead(self, lead_id: int) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

    def update_lead(self, lead_id: int, **kw: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE leads SET {cols} WHERE id=?",
                        (*kw.values(), lead_id))
        self.db.commit()

    def recent_leads(self, limit: int = 10) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def lead_summary(self, row: sqlite3.Row) -> str:
        bits = [f"#{row['id']} {row['name'] or 'unknown'}",
                row["postcode"] or row["address"] or "",
                f"status={row['status']}"]
        if row["quote_total"]:
            bits.append(f"£{row['quote_total']:,}")
        if row["easypv_url"]:
            bits.append(row["easypv_url"])
        return " | ".join(b for b in bits if b)

    def dump_lead(self, row: sqlite3.Row) -> str:
        d = {k: row[k] for k in row.keys() if k != "raw" and row[k] is not None}
        return json.dumps(d, indent=2, default=str)
