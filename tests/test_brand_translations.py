"""Validate shipped locales and Home Assistant's local branding resolution."""

import json
import re
from pathlib import Path

import pytest
from homeassistant.components.brands import _read_brand_file

BASE = Path(__file__).parents[1] / "custom_components/navel_gateway"


def flatten(value, prefix=""):
    result = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict):
            result.update(flatten(item, f"{path}."))
        else:
            result[path] = item
    return result


@pytest.mark.parametrize("language", ["en", "de", "fr", "nl", "pt"])
def test_translation_keys_and_placeholders(language):
    reference = json.loads((BASE / "translations/en.json").read_text())
    assert json.loads((BASE / "strings.json").read_text()) == reference
    expected = flatten(reference)
    actual = flatten(json.loads((BASE / f"translations/{language}.json").read_text()))
    assert actual.keys() == expected.keys()
    for key, text in actual.items():
        assert isinstance(text, str) and text.strip(), key
        assert set(re.findall(r"\{[^{}]+\}", text)) == set(
            re.findall(r"\{[^{}]+\}", expected[key])
        ), key


@pytest.mark.parametrize(
    "filename",
    ["icon.png", "icon@2x.png", "dark_icon.png", "dark_icon@2x.png", "logo.png"],
)
def test_home_assistant_resolves_bundled_brand(filename):
    data = _read_brand_file(BASE / "brand", filename)
    assert data is not None
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
