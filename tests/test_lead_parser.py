import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot.lead_parser import has_specs, parse_lead
from quotebot.email_monitor import _html_to_text

FORMSPREE_PLAIN = """\
New submission from your form:

Name: Ross Michael
Email: ross@example.com
Phone: 07700 900123
Address: 12 Myrddin Crescent, Carmarthen
Postcode: SA31 1AA
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


def test_lead_without_specs_flagged():
    f = parse_lead("Name: Bob\nEmail: bob@example.com\nMessage: please call me")
    assert not has_specs(f)
