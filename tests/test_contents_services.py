"""Tests for update_contents, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components import cargus
from custom_components.cargus.account.coordinator import CargusAccountCoordinator
from custom_components.cargus.const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PARCELS,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    CONF_TOKEN_EXPIRES_AT,
    CONF_TRACKING_CODE,
    DOMAIN,
    SOURCE_ACCOUNT,
)
from custom_components.cargus.tracking.coordinator import CargusCoordinator

from .account.fakes import inbox_row, trace

IN_AWB = "1000000101"
OUT_AWB = "1000000202"
OTHER_AWB = "1000000303"
UNLISTED = "1000000404"

INBOX = "custom_components.cargus.account.client.CargusAccountClient.async_get_inbox"
FULL = "custom_components.cargus.account.client.CargusAccountClient.async_get_full_awb"
TRACE = "custom_components.cargus.tracking.api.CargusApiClient.async_get_parcel"
SHOES = "Running shoes"


async def _trace(awb):
    return trace(awb, ("2026-10-22T10:00:00", "Consolidation scan"))


def _hub(parcels: list[str], unique_id: str = DOMAIN) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in parcels]},
    )


def _account(email: str = "user@example.com") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"account:{email}",
        data={
            CONF_SOURCE: SOURCE_ACCOUNT,
            CONF_EMAIL: email,
            CONF_PASSWORD: "secret",
            CONF_PHONE: "0712345678",
            CONF_ACCESS_TOKEN: "a",
            CONF_REFRESH_TOKEN: "r",
            CONF_TOKEN_EXPIRES_AT: 9_999_999_999,
        },
        options={},
    )


def _rows():
    return [inbox_row(IN_AWB, isSender=False), inbox_row(OUT_AWB, isSender=True)]


async def _setup(hass, *entries: MockConfigEntry) -> None:
    for entry in entries:
        entry.add_to_hass(hass)
        with (
            patch(INBOX, AsyncMock(return_value=_rows())),
            patch(FULL, AsyncMock(return_value={"events": []})),
            patch(TRACE, side_effect=_trace),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()


async def _call(hass, service: str, data: dict) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str, *, outgoing: bool = False):
    coordinator = entry.runtime_data.coordinator
    parcels = [*coordinator.data, *coordinator.delivered]
    if outgoing:
        parcels += [*coordinator.outgoing, *coordinator.delivered_outgoing]
    return {p["barcode"]: p for p in parcels}[code]["contents"]


# --- hub -------------------------------------------------------------------


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    assert _contents(entry, IN_AWB) is None


async def test_update_contents_sets_replaces_and_clears_on_the_hub(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: IN_AWB, "contents": " Running  shoes "},
    )
    assert _contents(entry, IN_AWB) == SHOES

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": "Socks"}
    )
    assert _contents(entry, IN_AWB) == "Socks"

    await _call(hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": "  "})
    assert _contents(entry, IN_AWB) is None

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    )
    await _call(hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB})
    assert _contents(entry, IN_AWB) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: f" {IN_AWB} ", "contents": SHOES}
    )
    assert _contents(entry, IN_AWB) == SHOES


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass, _hub([IN_AWB]))
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: "  "})


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: UNLISTED, "contents": SHOES}
    )
    assert entry.runtime_data.coordinator.contents_store.get(UNLISTED) == SHOES
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: IN_AWB}]


async def test_update_contents_never_polls_and_notifies_once_per_change(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    with (
        patch(
            f"{CargusCoordinator.__module__}.CargusCoordinator.async_request_refresh",
            new=AsyncMock(),
        ) as refresh,
        patch(TRACE, new=AsyncMock()) as fetch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: IN_AWB}, blocking=True
        )
        assert listeners.call_count == 2
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: IN_AWB}, blocking=True
        )
        assert listeners.call_count == 2
    refresh.assert_not_called()
    fetch.assert_not_called()


async def test_an_unchanged_value_writes_nothing(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    store = entry.runtime_data.coordinator.contents_store
    data = {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    await _call(hass, "update_contents", data)
    with patch.object(store._store, "async_delay_save") as save:
        await _call(hass, "update_contents", data)
    save.assert_not_called()


async def test_track_parcel_with_contents_stores_it_and_still_refreshes(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    with patch(TRACE, side_effect=_trace) as fetch:
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: OTHER_AWB, "contents": SHOES},
        )
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
    assert OTHER_AWB in [c.args[0] for c in fetch.call_args_list]
    assert _contents(entry, OTHER_AWB) == SHOES


async def test_track_parcel_on_a_tracked_code_reapplies_without_polling(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    with patch(TRACE, new=AsyncMock()) as fetch:
        await _call(
            hass, "track_parcel", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
        )
        await _call(
            hass, "track_parcel", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
        )
    fetch.assert_not_called()
    assert _contents(entry, IN_AWB) == SHOES


async def test_track_parcel_without_contents_leaves_the_store_alone(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    )
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: IN_AWB})
    assert _contents(entry, IN_AWB) == SHOES


async def test_untrack_drops_the_contents(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    )
    with patch(TRACE, side_effect=_trace):
        await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: IN_AWB})
    assert entry.runtime_data.coordinator.contents_store.get(IN_AWB) is None


def _handler(hass, service: str):
    return hass.services.async_services()[DOMAIN][service].job.target


async def test_a_hub_that_is_not_loaded_is_refused(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    track, untrack = _handler(hass, "track_parcel"), _handler(hass, "untrack_parcel")
    await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED

    contents_call = type(
        "Call", (), {"data": {CONF_TRACKING_CODE: OTHER_AWB, "contents": SHOES}}
    )()
    with pytest.raises(ServiceValidationError):
        await track(contents_call)
    # Untracking never needs the store, so an unloaded hub is simply edited.
    await untrack(type("Call", (), {"data": {CONF_TRACKING_CODE: IN_AWB}})())
    assert entry.options[CONF_PARCELS] == []


# --- account and routing ---------------------------------------------------


async def test_update_contents_on_an_account_reaches_both_directions_without_poll(
    hass,
):
    account = _account()
    await _setup(hass, account)
    coordinator = account.runtime_data.coordinator
    assert isinstance(coordinator, CargusAccountCoordinator)
    with (
        patch(
            f"{CargusCoordinator.__module__}.CargusCoordinator.async_request_refresh",
            new=AsyncMock(),
        ) as refresh,
        patch(INBOX, new=AsyncMock()) as inbox,
        patch(TRACE, new=AsyncMock()) as fetch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        # a single loaded entry is targeted directly, even for an unlisted code
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: UNLISTED, "contents": "A"}
        )
        assert listeners.call_count == 1
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
        )
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: OUT_AWB, "contents": "Socks"}
        )
        assert listeners.call_count == 3
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: OUT_AWB, "contents": "Socks"}
        )
        assert listeners.call_count == 3
    refresh.assert_not_called()
    inbox.assert_not_called()
    fetch.assert_not_called()
    assert _contents(account, IN_AWB) == SHOES
    assert _contents(account, OUT_AWB, outgoing=True) == "Socks"
    assert coordinator.contents_store.get(UNLISTED) == "A"


async def test_the_poll_overlays_the_stored_value_on_both_directions(hass):
    account = _account()
    await _setup(hass, account)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: OUT_AWB, "contents": SHOES}
    )
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(FULL, AsyncMock(return_value={"events": []})),
        patch(TRACE, side_effect=_trace),
    ):
        await account.runtime_data.coordinator.async_refresh()
    assert _contents(account, OUT_AWB, outgoing=True) == SHOES
    assert _contents(account, IN_AWB, outgoing=True) is None


async def test_known_codes_list_what_each_entry_lists(hass):
    hub, account = _hub([OTHER_AWB]), _account()
    await _setup(hass, hub, account)
    assert hub.runtime_data.coordinator.known_codes == {OTHER_AWB}
    assert account.runtime_data.coordinator.known_codes == {IN_AWB, OUT_AWB}


async def test_two_entries_listing_the_same_code_both_get_the_value(hass):
    hub, account = _hub([IN_AWB]), _account()
    await _setup(hass, hub, account)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    )
    assert _contents(hub, IN_AWB) == SHOES
    assert _contents(account, IN_AWB) == SHOES


async def test_a_code_no_entry_lists_needs_an_entry(hass):
    hub, account = _hub([IN_AWB]), _account()
    await _setup(hass, hub, account)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: UNLISTED, "contents": SHOES}
        )
    assert err.value.translation_key == "update_contents_entry_required"


async def test_config_entry_id_targets_exactly_that_entry(hass):
    hub, account = _hub([IN_AWB]), _account()
    await _setup(hass, hub, account)
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: UNLISTED,
            "contents": SHOES,
            "config_entry_id": account.entry_id,
        },
    )
    assert account.runtime_data.coordinator.contents_store.get(UNLISTED) == SHOES
    assert hub.runtime_data.coordinator.contents_store.get(UNLISTED) is None

    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: IN_AWB,
            "contents": "Socks",
            "config_entry_id": hub.entry_id,
        },
    )
    assert _contents(hub, IN_AWB) == "Socks"
    assert _contents(account, IN_AWB) is None


async def test_an_unknown_config_entry_id_is_rejected(hass):
    await _setup(hass, _hub([IN_AWB]))
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES, "config_entry_id": "nope"},
        )


async def test_an_unloaded_entry_is_never_targeted(hass):
    hub, account, spare = _hub([IN_AWB]), _account(), _account("two@example.com")
    await _setup(hass, hub, account, spare)
    assert await hass.config_entries.async_unload(spare.entry_id)

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: IN_AWB, "contents": SHOES}
    )
    assert _contents(hub, IN_AWB) == SHOES
    assert _contents(account, IN_AWB) == SHOES

    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {
                CONF_TRACKING_CODE: IN_AWB,
                "contents": SHOES,
                "config_entry_id": spare.entry_id,
            },
        )


async def test_nothing_loaded_means_no_target(hass):
    account, hub = _account(), _hub([IN_AWB])
    await _setup(hass, account, hub)
    handler = _handler(hass, "update_contents")
    await hass.config_entries.async_unload(account.entry_id)
    await hass.config_entries.async_unload(hub.entry_id)
    call = type("Call", (), {"data": {CONF_TRACKING_CODE: IN_AWB}})()
    with pytest.raises(ServiceValidationError):
        await handler(call)


async def test_services_follow_the_entries_that_need_them(hass):
    hub, account = _hub([IN_AWB]), _account()
    await _setup(hass, hub, account)
    assert hass.services.has_service(DOMAIN, "track_parcel")

    await hass.config_entries.async_unload(hub.entry_id)
    assert not hass.services.has_service(DOMAIN, "track_parcel")
    assert hass.services.has_service(DOMAIN, "update_contents")

    await hass.config_entries.async_unload(account.entry_id)
    assert not hass.services.has_service(DOMAIN, "update_contents")


async def test_an_account_alone_registers_only_update_contents(hass):
    await _setup(hass, _account())
    assert hass.services.has_service(DOMAIN, "update_contents")
    assert not hass.services.has_service(DOMAIN, "track_parcel")
    assert not hass.services.has_service(DOMAIN, "untrack_parcel")


async def test_removing_an_entry_deletes_its_contents_file(hass):
    entry = _hub([IN_AWB])
    await _setup(hass, entry)
    with patch("custom_components.cargus.ContentsStore.async_remove") as remove:
        await cargus.async_remove_entry(hass, entry)
    remove.assert_awaited_once()
