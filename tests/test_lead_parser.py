import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot.lead_parser import (details_blob, has_address, has_specs,
                                  parse_lead)
from quotebot.email_monitor import _html_to_text

# Legacy "label: value" format (also what email_monitor produces from HTML).
FORMSPREE_PLAIN = """\
New submission from your form:

Name: Ross Michael
Email: ross@example.com
Phone: 07700 900123
Address: 12 Myrddin Crescent, Carmarthen, SA31 1AA
Message: Hi, I'd like a quote for 9 panels on my two storey house, no battery.
"""

FORMSPREE_HTML = """\
<html><body><table>
<tr><td>Full Name</td><td>Jane Doe</td></tr>
<tr><td>email</td><td>jane@example.com</td></tr>
<tr><td>Property type</td><td>Bungalow</td></tr>
<tr><td>Message</td><td>Looking at 6 panels and 1 battery please</td></tr>
</table></body></html>
"""

# Current Formspree TWO-LINE format: label on one line, value on the next.
FORMSPREE_TWO_LINE = """\
New submission from Solar Enquiry

name
Ross Michael
email
ross@example.com
phone
07700 900123
address
12 Walden Grange Close, Newport, NP19 8AZ
property_type
terraced
ownership
owner
year_built
1990
epc_rating
D
bedrooms
3
occupants
4
roof_type
tile
roof_orientation
south
roof_pitch
35
roof_size
40
shading
none
roof_age
15
roof_notes
clear roof, no velux
annual_kwh
4200
monthly_bill
120
interested_in
solar and battery
preferred_size
6kW
timeline
3 months
"""


def test_parse_plain_lead():
    f = parse_lead(FORMSPREE_PLAIN)
    assert f["name"] == "Ross Michael"
    assert f["email"] == "ross@example.com"
    assert f["postcode"] == "SA31 1AA"
    assert f["panels"] == 9
    assert f["batteries"] == 0
    assert f["scaffold"] == "two_storey"
    assert has_specs(f)


def test_parse_html_lead():
    body = _html_to_text(FORMSPREE_HTML)
    f = parse_lead(body, aliases={"name": ["Full Name"]})
    assert f["name"] == "Jane Doe"
    assert f["panels"] == 6
    assert f["batteries"] == 1
    assert f["scaffold"] == "bungalow"


def test_parse_two_line_formspree():
    f = parse_lead(FORMSPREE_TWO_LINE)
    assert f["name"] == "Ross Michael"
    assert f["email"] == "ross@example.com"
    assert f["phone"] == "07700 900123"
    assert f["address"] == "12 Walden Grange Close, Newport, NP19 8AZ"
    # Postcode is not a separate field — it comes out of the address.
    assert f["postcode"] == "NP19 8AZ"
    assert f["property_type"] == "terraced"
    assert f["ownership"] == "owner"
    assert f["roof_orientation"] == "south"
    assert f["annual_kwh"] == "4200"
    assert f["monthly_bill"] == "120"
    assert f["preferred_size"] == "6kW"
    assert f["timeline"] == "3 months"
    assert f["roof_notes"] == "clear roof, no velux"
    assert has_address(f)


def test_two_line_details_blob_captured():
    f = parse_lead(FORMSPREE_TWO_LINE)
    d = details_blob(f)
    assert d["epc_rating"] == "D"
    assert d["bedrooms"] == "3"
    assert d["roof_pitch"] == "35"
    # Canonical columns are not duplicated into the details blob.
    assert "name" not in d
    assert "address" not in d


def test_postcode_from_address_two_line():
    f = parse_lead("address\n5 High Street, Cardiff CF10 1AA\n")
    assert f["postcode"] == "CF10 1AA"
    assert has_address(f)


def test_has_address_requires_postcode():
    f = parse_lead("name\nBob\nemail\nbob@example.com\n")
    assert not has_address(f)
    assert not has_specs(f)
