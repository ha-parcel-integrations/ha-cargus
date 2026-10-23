"""Tests for the Cargus config and options flow."""

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cargus.account.auth import (
    CargusAuthError,
    CargusInvalidCredentials,
    CargusTokens,
)
from custom_components.cargus.account.client import CargusAccountError
from custom_components.cargus.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.cargus.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)


def test_normalize_tracking_code_strips_every_separator():
    assert normalize_tracking_code(" 1012 345-600. ") == "1012345600"
    assert normalize_tracking_code("1012\u00a0345\u2013600") == "1012345600"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_needs_nine_or_ten_digits():
    assert valid_tracking_code("1012345600")
    assert valid_tracking_code("123456789")
    assert not valid_tracking_code("12345678")
    assert not valid_tracking_code("12345678901")
    assert not valid_tracking_code("ABC1234567")
    assert not valid_tracking_code(
        "\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
    )
    assert not valid_tracking_code("")


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the tracking hub is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "menu"
    assert result["menu_options"] == ["account", "tracking"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "tracking"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Cargus"
    assert result["data"] == {"source": "tracking"}
    assert result["options"][CONF_PARCELS] == []


async def test_second_tracking_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "tracking"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["1012345600"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "1012345600"}]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces are cleaned up."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["1012 345 600"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "1012345600"}]


async def test_options_rejects_a_malformed_code_without_saving(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["abc"]}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_tracking_code"}


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "1011111100"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["1011111100", "1011 111 100"]}
    )
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "1011111100"}]


async def test_options_remove_parcel(hass):
    entry = _hub(
        [
            {CONF_TRACKING_CODE: "1011111100"},
            {CONF_TRACKING_CODE: "1022222200"},
        ]
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["1022222200"]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"1022222200"}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: "1011111100"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5


SIGN_IN = "custom_components.cargus.account.client.CargusAccountClient.async_sign_in"
INBOX = "custom_components.cargus.account.client.CargusAccountClient.async_get_inbox"
ACCOUNT_INPUT = {
    "email": " User@Example.com ",
    "password": "secret",
    "phone": "07 1234 5678",
}


def _signed_in(client):
    client._store(CargusTokens("a", "r", 1.0), "login")


async def _account_form(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "account"}
    )


async def test_account_flow_creates_the_entry(hass):
    result = await _account_form(hass)
    assert result["step_id"] == "account"
    with (
        patch(SIGN_IN, autospec=True, side_effect=_signed_in),
        patch(INBOX, AsyncMock(return_value=[])),
        patch("custom_components.cargus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], ACCOUNT_INPUT
        )
    assert result["type"] == "create_entry"
    assert result["title"] == "Cargus (user@example.com)"
    assert result["data"] == {
        "source": "account",
        "email": "user@example.com",
        "password": "secret",
        "phone": "0712345678",
        "access_token": "a",
        "refresh_token": "r",
        "token_expires_at": 1.0,
    }
    assert result["result"].unique_id == "account:user@example.com"


async def test_account_flow_rejects_a_short_phone(hass):
    result = await _account_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ACCOUNT_INPUT, "phone": "0712"}
    )
    assert result["errors"] == {"phone": "invalid_phone"}


async def test_account_flow_errors(hass):
    cases = [
        (CargusInvalidCredentials("no"), None, "invalid_auth"),
        (CargusAuthError("down"), None, "cannot_connect"),
        (None, CargusAccountError("x", status_code=400), "invalid_phone"),
        (None, CargusAccountError("x", status_code=500), "cannot_connect"),
    ]
    for sign_in_error, inbox_error, expected in cases:
        result = await _account_form(hass)
        with (
            patch(SIGN_IN, AsyncMock(side_effect=sign_in_error)),
            patch(INBOX, AsyncMock(side_effect=inbox_error, return_value=[])),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], ACCOUNT_INPUT
            )
        assert result["errors"] == {"base": expected}, expected
        hass.config_entries.flow.async_abort(result["flow_id"])
        # The password is never offered back as a suggested value.
        for field in result["data_schema"].schema:
            if field == "password":
                assert not (field.description or {}).get("suggested_value")


async def test_same_account_twice_is_rejected(hass):
    MockConfigEntry(
        domain=DOMAIN, unique_id="account:user@example.com", data={"source": "account"}
    ).add_to_hass(hass)
    result = await _account_form(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ACCOUNT_INPUT
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reauth_stores_the_new_password(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account:user@example.com",
        data={
            "source": "account",
            "email": "user@example.com",
            "password": "old",
            "phone": "0712345678",
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(SIGN_IN, AsyncMock(side_effect=CargusInvalidCredentials("no"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "still-wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with (
        patch(SIGN_IN, autospec=True, side_effect=_signed_in),
        patch(INBOX, AsyncMock(return_value=[])),
        patch("custom_components.cargus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "new"}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"
    assert entry.data["access_token"] == "a"
