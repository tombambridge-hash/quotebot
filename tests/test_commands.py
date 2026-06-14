import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot import commands
from quotebot.state import Store

CFG = {"pricing": {"panel_wattage_kw": 0.44,
                   "defaults": {"roof_faces": 1, "dc_strings": 1,
                                "scaffold": "two_storey"}},
       "leads": {}}

FORWARDED = """\
Begin forwarded message:

From: Formspree <noreply@formspree.io>
Subject: New submission from solar enquiry form

Name: Stuart Scully
Email: stuart@example.com
Phone: 07700 900456
Address: 4 Example Road, Bristol
Postcode: BS5 0HE
Message: Quote please for 12 panels and 1 battery, two storey house
"""


def _bot(store):
    return lambda lead_id: f"pipeline ran for #{lead_id}"


def test_price_command_storeys_one_uses_bungalow_rate():
    store = Store(":memory:")
    out = commands.handle("price panels=6 batteries=0 storeys=1",
                          store, CFG, _bot(store))
    assert "Scaffolding (bungalow)" in out


def test_forwarded_email_ingested_as_lead():
    store = Store(":memory:")
    out = commands.handle(FORWARDED, store, CFG, _bot(store),
                          subject="Fwd: New submission from solar enquiry form")
    assert out == "pipeline ran for #1"
    row = store.get_lead(1)
    assert row["name"] == "Stuart Scully"
    assert row["panels"] == 12
    assert row["batteries"] == 1


def test_lead_command_without_address_asks_for_more():
    store = Store(":memory:")
    out = commands.handle("lead\nName: Bob\nEmail: bob@example.com",
                          store, CFG, _bot(store), subject="Bot")
    assert "spec 1" in out
    assert store.get_lead(1)["status"] == "awaiting_info"


def test_lead_command_with_address_runs_pipeline():
    store = Store(":memory:")
    body = ("lead\nName: Sara\nEmail: sara@example.com\n"
            "Address: 9 Hill Road, Newport, NP20 1AA")
    out = commands.handle(body, store, CFG, _bot(store), subject="Bot")
    assert out == "pipeline ran for #1"


def test_spec_command_runs_pipeline():
    store = Store(":memory:")
    store.add_lead({"name": "Jane"}, raw="")
    out = commands.handle("spec 1 panels=9 batteries=0 storeys=2",
                          store, CFG, _bot(store), subject="Bot")
    assert out == "pipeline ran for #1"
    assert store.get_lead(1)["panels"] == 9
