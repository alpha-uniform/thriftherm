"""Entity ids are English and language independent."""

import json
from pathlib import Path

from homeassistant.util import slugify

from custom_components.thriftherm.entity_ids import OBJECT_IDS

EN = json.loads((Path(__file__).parents[1] / "custom_components/thriftherm/translations/en.json").read_text())["entity"]


def test_object_ids_follow_the_english_names():
    """A renamed English entity name must come with a regenerated id table."""
    for platform, names in EN.items():
        for key, entry in names.items():
            assert OBJECT_IDS[platform][key] == slugify(entry["name"]), (platform, key)
    assert {p: set(m) for p, m in OBJECT_IDS.items()} == {p: set(m) for p, m in EN.items()}


def test_no_brand_names_or_mangled_umlauts_in_ids():
    for platform, ids in OBJECT_IDS.items():
        for key, object_id in ids.items():
            assert "midea" not in object_id, (platform, key)
            assert object_id.isascii()


def test_every_platform_can_get_an_english_id():
    """A new platform without an entry here would silently fall back to the translated name."""
    from custom_components.thriftherm.const import PLATFORMS
    from custom_components.thriftherm.entity import _PLATFORMS

    assert {domain for _cls, domain in _PLATFORMS} >= set(PLATFORMS)
    assert set(OBJECT_IDS) == set(PLATFORMS)
