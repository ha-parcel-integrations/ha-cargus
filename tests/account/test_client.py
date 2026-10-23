"""Tests for the MyCargus inbox client and its token lifecycle."""

import time
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.cargus.account import client as client_module
from custom_components.cargus.account.auth import (
    CargusAuthError,
    CargusInvalidCredentials,
    CargusTokens,
)
from custom_components.cargus.account.client import (
    SHIPMENT_TYPES,
    CargusAccountAuthError,
    CargusAccountClient,
    CargusAccountError,
    awb_of,
    describe_shape,
)

from .fakes import FakeResponse, FakeSession, inbox_row


def _warnings(caplog, phrase: str) -> int:
    return sum(
        r.levelname == "WARNING" and phrase in r.getMessage() for r in caplog.records
    )


FRESH = CargusTokens("fresh-access", "fresh-refresh", time.time() + 3600)
STALE = CargusTokens("stale-access", "stale-refresh", time.time() - 10)


def _client(session, *, tokens=FRESH, login=None, on_tokens=None):
    return CargusAccountClient(
        session,
        email="user@example.com",
        password="secret",
        phone="0712345678",
        tokens=tokens,
        on_tokens=on_tokens,
        login=login or AsyncMock(return_value=FRESH),
    )


def _empty_lists(count: int = len(SHIPMENT_TYPES)) -> list[FakeResponse]:
    return [FakeResponse(200, []) for _ in range(count)]


def test_awb_of_reads_every_spelling():
    assert awb_of({"awbBarCode": "1"}) == "1"
    assert awb_of({"barcode": 2}) == "2"
    assert awb_of({"awb": ""}) is None


def test_describe_shape_keeps_no_values():
    shape = describe_shape({"name": "Ion", "events": [{"date": "x"}], "n": None})
    assert shape == {
        "name": "str",
        "events": ["list(1)", "object(1 keys)"],
        "n": "NoneType",
    }
    assert describe_shape([{"date": "x"}]) == ["list(1)", {"date": "str"}]
    assert describe_shape([]) == "list(0)"
    assert describe_shape({"a": {"b": {"c": 1}}}) == {"a": {"b": "object(1 keys)"}}


async def test_inbox_merges_the_list_types_and_keeps_the_first_row():
    row = inbox_row("1000000101")
    session = FakeSession(
        FakeResponse(200, [row, {"noAwb": True}]),
        FakeResponse(200, {"data": [row]}),
        FakeResponse(204, b""),
        FakeResponse(200, []),
        FakeResponse(200, [inbox_row("1000000102")]),
    )
    client = _client(session)
    rows = await client.async_get_inbox()
    assert [r["awbBarCode"] for r in rows] == ["1000000101", "1000000102"]
    assert rows[0]["_shipment_type"] == 0
    assert rows[1]["_shipment_type"] == 4
    assert client.header_variant == "web"
    assert "AwbList[0]" in client.shapes
    _, url, kwargs = session.calls[0]
    assert url.endswith("/Awbs/AwbList")
    assert kwargs["params"] == {"shipmentType": 0, "lang": "EN"}
    assert kwargs["headers"]["Authorization"] == "Bearer fresh-access"


async def test_one_failing_list_type_fails_the_whole_inbox():
    session = FakeSession(FakeResponse(200, []), FakeResponse(500, b""))
    with pytest.raises(CargusAccountError, match="HTTP 500"):
        await _client(session).async_get_inbox()


async def test_unexpected_shape_fails_and_warns_once(caplog):
    client = _client(
        FakeSession(FakeResponse(200, {"unexpected": True}), FakeResponse(200, 1))
    )
    for _ in range(2):
        with pytest.raises(CargusAccountError, match="shape"):
            await client.async_get_inbox()
    assert _warnings(caplog, "unexpected shape") == 1

    # A full answer re-arms the warning.
    client._session = FakeSession(*_empty_lists())
    await client.async_get_inbox()
    assert client._warned == set()


async def test_refusal_warning_is_logged_once(caplog):
    client = _client(FakeSession(*[FakeResponse(400, b"") for _ in range(4)]))
    for _ in range(2):
        with pytest.raises(CargusAccountError):
            await client._async_get("Awbs/AwbList", {})
    assert _warnings(caplog, "every header variant") == 1


async def test_refused_web_headers_fall_back_to_the_app_headers():
    session = FakeSession(FakeResponse(400, b""), *_empty_lists())
    client = _client(session)
    await client.async_get_inbox()
    assert client.header_variant == "app"
    assert "K" in session.calls[0][2]["headers"]
    assert "seed" in session.calls[1][2]["headers"]
    # Remembered: the next request starts with the app headers.
    assert "seed" in session.calls[2][2]["headers"]


async def test_both_header_variants_refused():
    session = FakeSession(FakeResponse(400, b""), FakeResponse(403, b""))
    with pytest.raises(CargusAccountError) as err:
        await _client(session)._async_get("Awbs/AwbList", {})
    assert err.value.status_code == 403


async def test_a_401_renews_the_token_once(monkeypatch):
    monkeypatch.setattr(
        client_module, "async_refresh", AsyncMock(side_effect=CargusAuthError("no"))
    )
    login = AsyncMock(return_value=CargusTokens("new", "r", time.time() + 3600))
    session = FakeSession(FakeResponse(401, b""), FakeResponse(200, [1]))
    assert await _client(session, login=login)._async_get("Awbs/AwbList", {}) == [1]
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer fresh-access"
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer new"

    session = FakeSession(FakeResponse(401, b""), FakeResponse(401, b""))
    with pytest.raises(CargusAccountError, match="401"):
        await _client(session, login=login)._async_get("Awbs/AwbList", {})


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (FakeResponse(500, b""), "HTTP 500"),
        (FakeResponse(200, b"<html>"), "HTML"),
        (FakeResponse(200, b"{nope"), "unparseable"),
        (FakeResponse(200, b"x" * (client_module.MAX_BODY_BYTES + 1)), "too large"),
        (TimeoutError(), "timeout"),
        (aiohttp.ClientConnectionError("down"), "network"),
    ],
)
async def test_unusable_answers_are_errors(answer, message):
    with pytest.raises(CargusAccountError, match=message):
        await _client(FakeSession(answer))._async_get("Awbs/AwbList", {})


async def test_empty_and_bom_bodies():
    client = _client(
        FakeSession(FakeResponse(200, b"  "), FakeResponse(200, b"\xef\xbb\xbf[1]"))
    )
    assert await client._async_get("x", {}) is None
    assert await client._async_get("x", {}) == [1]


async def test_full_awb_is_probed_with_the_direction():
    session = FakeSession(FakeResponse(200, {"awbBarcode": "1", "events": []}))
    client = _client(session)
    await client.async_get_full_awb("1", for_sender=True)
    assert session.calls[0][2]["params"]["forSender"] == "true"
    assert client.shapes["FullAwb"] == {"awbBarcode": "str", "events": "list(0)"}


async def test_cached_token_is_reused():
    client = _client(FakeSession())
    assert await client._async_access_token() == "fresh-access"
    assert client.last_token_source == "cache"


async def test_stale_token_is_refreshed_and_stored(monkeypatch):
    stored = []
    refreshed = CargusTokens("r-access", "r-refresh", time.time() + 3600)
    monkeypatch.setattr(
        client_module, "async_refresh", AsyncMock(return_value=refreshed)
    )
    client = _client(FakeSession(), tokens=STALE, on_tokens=stored.append)
    assert await client._async_access_token() == "r-access"
    assert stored == [refreshed]
    assert client.last_token_source == "refresh"


async def test_refused_refresh_falls_back_to_a_sign_in(monkeypatch):
    monkeypatch.setattr(
        client_module, "async_refresh", AsyncMock(side_effect=CargusAuthError("no"))
    )
    login = AsyncMock(return_value=FRESH)
    client = _client(FakeSession(), tokens=STALE, login=login)
    assert await client._async_access_token() == "fresh-access"
    login.assert_awaited_once_with("user@example.com", "secret")
    assert client.last_token_source == "login"


async def test_sign_in_failures():
    client = _client(
        FakeSession(),
        tokens=None,
        login=AsyncMock(side_effect=CargusInvalidCredentials),
    )
    with pytest.raises(CargusAccountAuthError):
        await client._async_access_token()

    client = _client(
        FakeSession(), tokens=None, login=AsyncMock(side_effect=CargusAuthError("x"))
    )
    with pytest.raises(CargusAccountError) as err:
        await client._async_access_token()
    assert not isinstance(err.value, CargusAccountAuthError)


async def test_sign_in_stores_the_tokens():
    stored = []
    client = _client(FakeSession(), tokens=None, on_tokens=stored.append)
    await client.async_sign_in()
    assert stored == [FRESH]
    assert client.last_token_source == "login"


async def test_auth_error_in_the_inbox_is_not_swallowed():
    client = _client(
        FakeSession(),
        tokens=None,
        login=AsyncMock(side_effect=CargusInvalidCredentials),
    )
    with pytest.raises(CargusAccountAuthError):
        await client.async_get_inbox()
