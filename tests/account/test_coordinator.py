"""Tests for the account entry: setup, polling, events and diagnostics."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cargus.account import parcels as account_parcels
from custom_components.cargus.account.auth import CargusTokens
from custom_components.cargus.account.client import (
    CargusAccountAuthError,
    CargusAccountError,
)
from custom_components.cargus.const import (
    CONF_ACCESS_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_SOURCE,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
    MID_INTERVAL_MINUTES,
    SOURCE_ACCOUNT,
    STAGGER_MINUTES,
    ParcelStatus,
)
from custom_components.cargus.diagnostics import async_get_config_entry_diagnostics
from custom_components.cargus.tracking.api import CargusApiError, CargusGateError

from .fakes import inbox_row, trace

IN_AWB = "1000000101"
OUT_AWB = "1000000202"
NEW_AWB = "1000000303"

INBOX = "custom_components.cargus.account.client.CargusAccountClient.async_get_inbox"
FULL = "custom_components.cargus.account.client.CargusAccountClient.async_get_full_awb"
TRACE = "custom_components.cargus.tracking.api.CargusApiClient.async_get_parcel"


@pytest.fixture(autouse=True)
def reset_one_shots(monkeypatch):
    monkeypatch.setattr(account_parcels, "_window_logged", set())


def _entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cargus (user@example.com)",
        unique_id="account:user@example.com",
        data={
            CONF_SOURCE: SOURCE_ACCOUNT,
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_PHONE: "0712345678",
            CONF_ACCESS_TOKEN: "a",
            CONF_REFRESH_TOKEN: "r",
            CONF_TOKEN_EXPIRES_AT: 9_999_999_999,
            **data,
        },
        options={},
    )


def _rows():
    return [
        inbox_row(IN_AWB, isSender=False),
        inbox_row(OUT_AWB, isSender=True),
        inbox_row(NEW_AWB, isSender=False, status="New"),
    ]


def _traces(**overrides):
    replies = {
        IN_AWB: trace(IN_AWB, ("2026-10-22T10:00:00", "Consolidation scan")),
        OUT_AWB: trace(OUT_AWB, ("2026-10-22T10:00:00", "Pickup shipment")),
        NEW_AWB: None,
        **overrides,
    }

    async def answer(awb):
        reply = replies[awb]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    return answer


async def _setup(hass, entry, rows=None, traces=None):
    entry.add_to_hass(hass)
    with (
        patch(INBOX, AsyncMock(return_value=_rows() if rows is None else rows)),
        patch(FULL, AsyncMock(return_value={"events": []})),
        patch(TRACE, side_effect=traces or _traces()),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_splits_incoming_and_outgoing(hass):
    entry = await _setup(hass, _entry())
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data.coordinator

    assert [p["barcode"] for p in coordinator.data] == [IN_AWB, NEW_AWB]
    assert [p["barcode"] for p in coordinator.outgoing] == [OUT_AWB]
    new = next(p for p in coordinator.data if p["barcode"] == NEW_AWB)
    assert new["status"] is ParcelStatus.REGISTERED

    assert (
        hass.states.get("sensor.cargus_user_example_com_incoming_parcels").state == "2"
    )
    assert (
        hass.states.get("sensor.cargus_user_example_com_outgoing_parcels").state == "1"
    )
    assert hass.states.get(f"sensor.cargus_user_example_com_parcel_{OUT_AWB}")
    # Account entries take no codes, so no services.
    assert not hass.services.has_service(DOMAIN, "track_parcel")

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_refreshed_tokens_are_persisted(hass):
    entry = await _setup(hass, _entry())
    entry.runtime_data.coordinator.account_client._store(
        CargusTokens("new-a", "new-r", 123.0), "refresh"
    )
    assert entry.data[CONF_ACCESS_TOKEN] == "new-a"
    assert entry.data[CONF_REFRESH_TOKEN] == "new-r"
    assert entry.data[CONF_TOKEN_EXPIRES_AT] == 123.0


async def test_entry_without_stored_tokens_signs_in(hass):
    entry = await _setup(hass, _entry(**{CONF_ACCESS_TOKEN: None}))
    assert entry.runtime_data.coordinator.account_client._tokens is None


async def test_rejected_password_starts_reauth(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(INBOX, AsyncMock(side_effect=CargusAccountAuthError("no"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_unreachable_inbox_retries_setup(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(INBOX, AsyncMock(side_effect=CargusAccountError("down"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_status_changes_fire_events_per_direction(hass):
    entry = await _setup(hass, _entry())
    coordinator = entry.runtime_data.coordinator
    events = []
    for name in (
        "cargus_parcel_status_changed",
        "cargus_parcel_delivered",
        "cargus_outgoing_parcel_status_changed",
        "cargus_outgoing_parcel_delivered",
        "cargus_parcel_registered",
    ):
        hass.bus.async_listen(name, lambda event: events.append(event))

    later = _traces(
        **{
            IN_AWB: trace(IN_AWB, ("2026-10-23T08:00:00", "With courier")),
            OUT_AWB: trace(OUT_AWB, ("2026-10-23T12:00:00", "Delivered")),
        }
    )
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(TRACE, side_effect=later),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    kinds = sorted(event.event_type for event in events)
    assert kinds == ["cargus_outgoing_parcel_delivered", "cargus_parcel_status_changed"]
    assert coordinator.delivered_outgoing[0]["barcode"] == OUT_AWB
    assert OUT_AWB in coordinator.delivered_codes

    # Next poll: the delivered outgoing parcel is not traced again, and an
    # unchanged outgoing status fires nothing.
    trace_mock = AsyncMock(side_effect=later)
    with patch(INBOX, AsyncMock(return_value=_rows())), patch(TRACE, trace_mock):
        await coordinator.async_refresh()
    assert OUT_AWB not in [call.args[0] for call in trace_mock.await_args_list]
    assert len(events) == 2


async def test_outgoing_status_change_fires_status_changed(hass):
    entry = await _setup(hass, _entry())
    events = []
    hass.bus.async_listen(
        "cargus_outgoing_parcel_status_changed", lambda event: events.append(event)
    )
    moved = _traces(
        **{OUT_AWB: trace(OUT_AWB, ("2026-10-23T08:00:00", "With courier"))}
    )
    with patch(INBOX, AsyncMock(return_value=_rows())), patch(TRACE, side_effect=moved):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
    assert events[0].data["new_status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_trace_failures_keep_last_good_and_raise_the_repair(hass):
    entry = await _setup(hass, _entry())
    coordinator = entry.runtime_data.coordinator
    failing = _traces(
        **{IN_AWB: CargusGateError("HTTP 400"), OUT_AWB: CargusApiError("HTTP 500")}
    )
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(TRACE, side_effect=failing),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    incoming = {p["barcode"]: p for p in coordinator.data}
    assert incoming[IN_AWB]["status"] is ParcelStatus.IN_TRANSIT
    assert ir.async_get(hass).async_get_issue(DOMAIN, "request_refused")

    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(TRACE, side_effect=_traces()),
    ):
        await coordinator.async_refresh()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "request_refused")


async def test_unexpected_trace_errors_propagate(hass):
    entry = await _setup(hass, _entry())
    coordinator = entry.runtime_data.coordinator
    broken = _traces(**{IN_AWB: RuntimeError("bug")})
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(TRACE, side_effect=broken),
    ):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_full_awb_is_probed_once_per_direction(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    full = AsyncMock(side_effect=[CargusAccountError("no"), {"events": []}])
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(FULL, full),
        patch(TRACE, side_effect=_traces()),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.coordinator.async_refresh()
    assert [call.kwargs["for_sender"] for call in full.await_args_list] == [False, True]


async def test_window_reaches_the_calendar_sensor(hass):
    row = inbox_row(IN_AWB, arrivingTimeFrom="10:00:00", arrivingTimeTo="14:00:00")
    out_today = _traces(
        **{IN_AWB: trace(IN_AWB, ("2026-10-23T08:00:00", "With courier"))}
    )
    entry = await _setup(hass, _entry(), rows=[row], traces=out_today)
    parcel = entry.runtime_data.coordinator.data[0]
    assert parcel["planned_from"] == "2026-10-23T10:00:00+03:00"
    state = hass.states.get("sensor.cargus_user_example_com_next_delivery")
    assert state.state.startswith("2026-10-23T07:00:00")


async def test_settings_change_reloads_the_account(hass):
    entry = await _setup(hass, _entry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "settings"
    with (
        patch(INBOX, AsyncMock(return_value=_rows())),
        patch(FULL, AsyncMock(return_value={})),
        patch(TRACE, side_effect=_traces()),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "delivered_filter_type": "parcels",
                "delivered_filter_amount": 3,
                "include_history": True,
            },
        )
    assert result["type"] == "create_entry"
    reload.assert_called_once_with(entry.entry_id)
    assert entry.options["include_history"] is True


async def test_diagnostics_hide_credentials_and_inbox_values(hass):
    entry = await _setup(hass, _entry())
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    data = diagnostics["entry_data"]
    for key in ("email", "password", "phone", "access_token", "refresh_token"):
        assert data[key] == "**REDACTED**"
    assert diagnostics["counts"]["inbox"] == 3
    assert diagnostics["counts"]["outgoing_active"] == 1
    assert diagnostics["account"]["header_variant"] is None
    inbox = diagnostics["incoming"][0]["raw"]["inbox"]
    assert inbox["awbBarCode"] == "str"
    assert diagnostics["outgoing"][0]["barcode"] == "**REDACTED**"


async def test_failed_poll_drops_the_overnight_interval(hass):
    entry = await _setup(hass, _entry())
    coordinator = entry.runtime_data.coordinator
    coordinator.update_interval = timedelta(hours=5, minutes=40)

    noon = datetime(2026, 1, 15, 12, tzinfo=UTC)
    with (
        patch.object(coordinator, "_async_poll", side_effect=TimeoutError),
        patch(
            "custom_components.cargus.account.coordinator.dt_util.now",
            return_value=noon,
        ),
        pytest.raises(TimeoutError),
    ):
        await coordinator._async_update_data()

    assert coordinator.update_interval is not None
    assert coordinator.update_interval <= timedelta(
        minutes=MID_INTERVAL_MINUTES + STAGGER_MINUTES
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
