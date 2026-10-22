"""Tests for the Cargus tracking client and its request headers."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.cargus.tracking import api as api_module, headers
from custom_components.cargus.tracking.api import (
    CargusApiClient,
    CargusApiError,
    CargusGateError,
)
from tests.payloads import ACTIVE_CODE, active_sample


class _Content:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


def _session(status: int, body: bytes | object = b"", headers_=None) -> MagicMock:
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    response = MagicMock()
    response.status = status
    response.headers = headers_ or {}
    response.content = _Content(body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_found_parcel_is_returned_with_the_code_in_the_url():
    session = _session(200, active_sample())
    parcel = await CargusApiClient(session).async_get_parcel(ACTIVE_CODE)
    assert parcel["awbBarcode"] == ACTIVE_CODE
    url = session.get.call_args[0][0]
    assert f"BarCode={ACTIVE_CODE}" in url
    assert url.endswith("lang=EN")
    sent = session.get.call_args.kwargs["headers"]
    assert set(sent) == {"k", "seed", "s", "Accept"}
    assert sent["s"] == "M"
    assert sent["Accept"] == "application/json"


async def test_headers_differ_per_request():
    session = _session(200, active_sample())
    client = CargusApiClient(session)
    await client.async_get_parcel(ACTIVE_CODE)
    await client.async_get_parcel(ACTIVE_CODE)
    first, second = (c.kwargs["headers"] for c in session.get.call_args_list)
    assert first["k"] != second["k"]
    assert first["seed"] != second["seed"]


async def test_request_headers_are_never_logged(caplog):
    session = _session(400, b'"Bad Request"')
    with caplog.at_level(logging.DEBUG), pytest.raises(CargusGateError):
        await CargusApiClient(session).async_get_parcel(ACTIVE_CODE)
    sent = session.get.call_args.kwargs["headers"]
    assert sent["k"] not in caplog.text
    assert sent["seed"] not in caplog.text


async def test_unbuildable_headers_are_a_gate_error(monkeypatch):
    monkeypatch.setattr(headers, "_HEADER_TOKEN", "")
    session = _session(200, active_sample())
    with pytest.raises(CargusGateError):
        await CargusApiClient(session).async_get_parcel(ACTIVE_CODE)
    session.get.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_gate_refusal_is_never_not_found(status):
    with pytest.raises(CargusGateError) as err:
        await CargusApiClient(_session(status, b'"Bad Request"')).async_get_parcel(
            ACTIVE_CODE
        )
    assert err.value.status_code == status


async def test_204_is_not_found():
    assert await CargusApiClient(_session(204)).async_get_parcel(ACTIVE_CODE) is None


async def test_empty_200_body_is_not_found():
    assert (
        await CargusApiClient(_session(200, b"  ")).async_get_parcel(ACTIVE_CODE)
        is None
    )


async def test_bom_prefixed_body_is_parsed():
    body = b"\xef\xbb\xbf" + json.dumps(active_sample()).encode()
    parcel = await CargusApiClient(_session(200, body)).async_get_parcel(ACTIVE_CODE)
    assert parcel["awbBarcode"] == ACTIVE_CODE


async def test_barcode_mismatch_is_an_error():
    with pytest.raises(CargusApiError, match="different parcel"):
        await CargusApiClient(
            _session(200, active_sample("1000000555"))
        ).async_get_parcel(ACTIVE_CODE)


@pytest.mark.parametrize("status", [404, 410])
async def test_moved_route_is_an_update_error(status):
    with pytest.raises(CargusApiError) as err:
        await CargusApiClient(_session(status)).async_get_parcel(ACTIVE_CODE)
    assert not isinstance(err.value, CargusGateError)
    assert err.value.status_code == status


async def test_html_on_200_is_a_retryable_error():
    with pytest.raises(CargusApiError, match="HTML"):
        await CargusApiClient(_session(200, b"<html></html>")).async_get_parcel(
            ACTIVE_CODE
        )


@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe", b"[1]"])
async def test_non_json_or_non_object_body_is_an_error(body):
    with pytest.raises(CargusApiError):
        await CargusApiClient(_session(200, body)).async_get_parcel(ACTIVE_CODE)


async def test_oversized_body_is_refused():
    body = b" " * (api_module.MAX_BODY_BYTES + 1)
    with pytest.raises(CargusApiError, match="too large"):
        await CargusApiClient(_session(200, body)).async_get_parcel(ACTIVE_CODE)


async def test_429_carries_retry_after_seconds():
    with pytest.raises(CargusApiError) as err:
        await CargusApiClient(
            _session(429, headers_={"Retry-After": "30"})
        ).async_get_parcel(ACTIVE_CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 30.0


@pytest.mark.parametrize("value", [None, "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_429_without_seconds_has_no_retry_after(value):
    headers_ = {"Retry-After": value} if value else {}
    with pytest.raises(CargusApiError) as err:
        await CargusApiClient(_session(429, headers_=headers_)).async_get_parcel(
            ACTIVE_CODE
        )
    assert err.value.retry_after is None


async def test_5xx_is_a_retryable_error():
    with pytest.raises(CargusApiError) as err:
        await CargusApiClient(_session(503)).async_get_parcel(ACTIVE_CODE)
    assert err.value.status_code == 503


async def test_timeout_becomes_an_api_error():
    session = MagicMock()
    session.get = MagicMock(side_effect=TimeoutError)
    with pytest.raises(CargusApiError, match="timeout"):
        await CargusApiClient(session).async_get_parcel(ACTIVE_CODE)


async def test_client_errors_propagate():
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientConnectionError)
    with pytest.raises(aiohttp.ClientError):
        await CargusApiClient(session).async_get_parcel(ACTIVE_CODE)
