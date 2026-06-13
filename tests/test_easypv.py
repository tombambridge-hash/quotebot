import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot import computer_use
from quotebot.easypv import EasyPV, _extract_project_id, _project_name


def test_tool_spec_sonnet_uses_new_header():
    spec = computer_use.tool_spec("claude-sonnet-4-6")
    assert spec["beta"] == "computer-use-2025-11-24"
    assert spec["tool_type"] == "computer_20251124"
    assert spec["zoom"] is True


def test_tool_spec_haiku_uses_older_header():
    spec = computer_use.tool_spec("claude-haiku-4-5")
    assert spec["beta"] == "computer-use-2025-01-24"
    assert spec["tool_type"] == "computer_20250124"
    assert spec["zoom"] is False


def test_tool_spec_opus_is_hires():
    spec = computer_use.tool_spec("claude-opus-4-8")
    assert spec["beta"] == "computer-use-2025-11-24"
    assert spec["max_long_edge"] == 2576


def test_scale_factor_shrinks_large_screens():
    # A 2560x1440 (or Retina 5120x2880) screen must be scaled under 1568px.
    s = computer_use.scale_factor(2560, 1440)
    assert s < 1.0
    assert max(2560 * s, 1440 * s) <= 1568 + 1
    # Small images aren't upscaled.
    assert computer_use.scale_factor(1000, 700) == 1.0


def test_extract_project_id_various_shapes():
    assert _extract_project_id({"projectId": "abc"}) == "abc"
    assert _extract_project_id({"id": 42}) == "42"
    assert _extract_project_id({"project": {"id": "p1"}}) == "p1"
    assert _extract_project_id({"data": {"projectId": "d9"}}) == "d9"
    assert _extract_project_id({"nope": 1}) is None


def test_project_url_and_name():
    pv = EasyPV({"easypv": {"base_url": "https://easy-pv.co.uk/"}})
    assert pv.project_url("XYZ") == "https://easy-pv.co.uk/project/XYZ"
    assert _project_name({"name": "Ross", "postcode": "NP19 8AZ"}) == "Ross - NP19 8AZ"
