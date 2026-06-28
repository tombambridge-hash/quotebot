import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot.providers import ProviderError, get_provider
from quotebot.easypv import EasyPV
from quotebot.pylon import Pylon, _extract_id


def test_get_provider_defaults_to_easypv():
    assert isinstance(get_provider({}), EasyPV)
    assert isinstance(get_provider({"provider": "easypv"}), EasyPV)


def test_get_provider_pylon():
    assert isinstance(get_provider({"provider": "pylon"}), Pylon)


def test_get_provider_unknown_raises():
    with pytest.raises(ProviderError):
        get_provider({"provider": "nope"})


def test_pylon_project_url_uses_template():
    pv = Pylon({"pylon": {"app_url": "https://app.getpylon.com/",
                          "project_url_template": "{app_url}/projects/{id}"}})
    assert pv.project_url("P9") == "https://app.getpylon.com/projects/P9"


def test_pylon_extract_id_shapes():
    assert _extract_id({"id": 7}) == "7"
    assert _extract_id({"leadId": "L1"}) == "L1"
    assert _extract_id({"lead": {"id": "n1"}}) == "n1"
    assert _extract_id({"data": {"opportunity_id": "o2"}}) == "o2"
    assert _extract_id({"custom": "c", "id": "i"}, preferred="custom") == "c"
    assert _extract_id({"nope": 1}) is None
