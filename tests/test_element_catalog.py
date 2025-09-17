import pytest

from agent.element_catalog import (
    get_catalog_for_prompt,
    get_expected_version,
    reset_cache,
    update_cache_from_signature,
    INDEX_MODE_ENABLED,
)


@pytest.mark.skipif(not INDEX_MODE_ENABLED, reason="Index mode disabled")
def test_get_catalog_for_prompt_formats_entries(monkeypatch):
    reset_cache()

    def fake_get(refresh=False):
        return {
            "abbreviated": [
                {
                    "index": 0,
                    "role": "button",
                    "tag": "button",
                    "primary_label": "送信",
                    "secondary_label": "",
                    "section_hint": "フォーム",
                    "state_hint": "",
                    "href_short": "",
                }
            ],
            "full": [],
            "metadata": {"url": "https://example.com", "title": "Example"},
            "catalog_version": "abc123",
            "index_mode_enabled": True,
        }

    monkeypatch.setattr("agent.element_catalog.vnc.get_element_catalog", fake_get)

    info = get_catalog_for_prompt()
    catalog = info["catalog"]
    prompt_text = info["prompt_text"]

    assert catalog["catalog_version"] == "abc123"
    assert "[0]" in prompt_text
    assert "button" in prompt_text


@pytest.mark.skipif(not INDEX_MODE_ENABLED, reason="Index mode disabled")
def test_update_cache_from_signature_triggers_refresh(monkeypatch):
    reset_cache()

    catalogs = [
        {
            "abbreviated": [],
            "full": [],
            "metadata": {"catalog_version": "old-version"},
            "catalog_version": "old-version",
            "index_mode_enabled": True,
        },
        {
            "abbreviated": [],
            "full": [],
            "metadata": {"catalog_version": "new-version"},
            "catalog_version": "new-version",
            "index_mode_enabled": True,
        },
    ]

    call_count = {"value": 0}

    def fake_get(refresh=False):
        call_count["value"] += 1
        index = 0 if call_count["value"] == 1 else 1
        return catalogs[index]

    monkeypatch.setattr("agent.element_catalog.vnc.get_element_catalog", fake_get)

    assert get_expected_version() == "old-version"
    assert call_count["value"] == 1

    update_cache_from_signature({"catalog_version": "new-version"})

    assert get_expected_version() == "new-version"
    assert call_count["value"] == 2

    reset_cache()
