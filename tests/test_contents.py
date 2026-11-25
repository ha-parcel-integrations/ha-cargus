"""Tests for the contents helpers and the per-entry store."""

from unittest.mock import patch

from custom_components.cargus.contents import (
    CONTENTS_MAX_LEN,
    CONTENTS_STORE_MAX_CODES,
    ContentsStore,
    clean_contents,
    join_contents,
    normalize_code,
)


def test_clean_contents_trims_and_collapses_whitespace():
    assert clean_contents("  Running \n  shoes ") == "Running shoes"


def test_clean_contents_empty_or_not_text_is_none():
    assert clean_contents("   ") is None
    assert clean_contents(None) is None
    assert clean_contents(5) is None


def test_clean_contents_drops_filler_and_caps_length():
    assert clean_contents("N/A", frozenset({"n/a"})) is None
    assert len(clean_contents("x" * 500)) == CONTENTS_MAX_LEN


def test_join_contents_drops_empties_and_repeats():
    assert join_contents(["Shoes", " ", "Socks", "Shoes"]) == "Shoes; Socks"
    assert join_contents([None, ""]) is None


def test_normalize_code_trims_and_uppercases():
    assert normalize_code(" ab12 ") == "AB12"


async def test_store_set_replace_clear_and_unchanged(hass):
    store = ContentsStore(hass, "e1")
    assert store.async_set("1000000999", "Shoes") is True
    assert store.async_set("1000000999", " Shoes ") is False
    assert store.async_set("1000000999", "Socks") is True
    assert store.get("1000000999") == "Socks"
    assert store.async_set("1000000999", "  ") is True
    assert store.get("1000000999") is None
    assert store.async_set("1000000999", None) is False


async def test_store_caps_at_200_codes_dropping_the_oldest(hass):
    store = ContentsStore(hass, "e1")
    for number in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"C{number}", "Shoes")
    assert store.get("C0") is None
    assert store.get("C1") == "Shoes"
    assert store.get(f"C{CONTENTS_STORE_MAX_CODES}") == "Shoes"


async def test_store_loads_what_it_saved_and_skips_junk(hass):
    with patch(
        "homeassistant.helpers.storage.Store.async_load",
        return_value={"by_code": {"ab1": " Shoes ", "ab2": "  ", "ab3": 7}},
    ):
        store = ContentsStore(hass, "e1")
        await store.async_load()
    assert store.get("AB1") == "Shoes"
    assert store.get("AB2") is None
    assert store.get("AB3") is None


async def test_an_entry_without_a_store_file_loads_empty(hass):
    store = ContentsStore(hass, "never-written")
    await store.async_load()
    assert store.get("ANY") is None


async def test_store_remove_empties_it(hass):
    store = ContentsStore(hass, "e1")
    store.async_set("A1", "Shoes")
    await store.async_remove()
    assert store.get("A1") is None
